import argparse
import datetime
import logging
import time
from pathlib import Path

import pandas as pd
import pandas_ta as ta
import pandas_market_calendars as mcal

import screener
from run_daily import get_latest_completed_trading_date


BACKEND_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("HistorySeed")


def get_trading_sessions(end_date, session_count):
    nyse = mcal.get_calendar("NYSE")
    calendar_days = max(400, session_count * 2)
    while True:
        start_date = end_date - datetime.timedelta(days=calendar_days)
        schedule = nyse.schedule(start_date=start_date.isoformat(), end_date=end_date.isoformat())
        sessions = [session.date() for session in schedule.index]
        if len(sessions) >= session_count:
            return sessions[-session_count:]
        calendar_days *= 2


def count_spy_sessions(start_date, end_date):
    conn = screener.get_connection()
    try:
        row = conn.execute(
            """
            SELECT count(DISTINCT timestamp), max(timestamp)
            FROM daily_bars
            WHERE symbol = 'SPY' AND timestamp >= ? AND timestamp <= ?
            """,
            (start_date.isoformat(), end_date.isoformat())
        ).fetchone()
        return row[0] or 0, row[1]
    finally:
        conn.close()


def verify_indicator_history(end_date, expected_sessions):
    conn = screener.get_connection()
    try:
        symbols = pd.read_sql_query(
            """
            SELECT symbol, count(DISTINCT timestamp) AS session_count
            FROM daily_bars
            WHERE timestamp <= ?
            GROUP BY symbol
            HAVING count(DISTINCT timestamp) >= ?
            ORDER BY CASE WHEN symbol = 'SPY' THEN 0 WHEN symbol = 'AAPL' THEN 1 ELSE 2 END, symbol
            LIMIT 2
            """,
            conn,
            params=(end_date.isoformat(), expected_sessions)
        )["symbol"].tolist()

        if "SPY" not in symbols:
            raise RuntimeError("Seed verification failed: SPY does not have the requested history.")

        for symbol in symbols:
            bars = pd.read_sql_query(
                """
                SELECT close, volume
                FROM daily_bars
                WHERE symbol = ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                conn,
                params=(symbol, end_date.isoformat())
            )
            close = bars["close"]
            volume = bars["volume"]
            volume_sma = ta.sma(volume, length=20)
            rvol = volume / volume_sma.shift(1) if volume_sma is not None else None
            measures = {
                "RSI14": ta.rsi(close, length=14),
                "EMA20": ta.ema(close, length=20),
                "SMA50": ta.sma(close, length=50),
                "SMA200": ta.sma(close, length=200),
                "RVOL20": rvol,
            }
            invalid = [
                name for name, values in measures.items()
                if values is None or pd.isna(values.iloc[-1])
            ]
            if invalid:
                raise RuntimeError(f"Seed verification failed for {symbol}: invalid {', '.join(invalid)}.")
            logger.info("Verified %s indicator lookbacks with %d bars.", symbol, len(bars))

        regime = screener.check_market_regime(end_date.isoformat(), conn=conn)
        if regime == "Unknown":
            raise RuntimeError("Seed verification failed: SPY market regime is Unknown.")
        logger.info("Verified SPY market regime for %s: %s.", end_date.isoformat(), regime)
    finally:
        conn.close()


def seed_history(session_count=200, end_date=None, database=None, request_interval=12.1):
    if session_count < 200:
        raise ValueError("At least 200 trading sessions are required for the SMA200 lookback.")
    if request_interval < 0:
        raise ValueError("request_interval must not be negative")

    database_path = Path(database) if database else BACKEND_DIR / screener.DB_PATH
    if not database_path.is_absolute():
        database_path = BACKEND_DIR / database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    screener.DB_PATH = str(database_path)
    screener.init_db(str(BACKEND_DIR / "schema.sql"))

    target_date = end_date or get_latest_completed_trading_date()
    sessions = get_trading_sessions(target_date, session_count)
    existing_count, latest_spy_date = count_spy_sessions(sessions[0], target_date)
    if existing_count >= session_count and latest_spy_date == target_date.isoformat():
        logger.info(
            "Database already has %d SPY sessions through %s; historical seed is not needed.",
            existing_count, latest_spy_date
        )
        verify_indicator_history(target_date, session_count)
        return 0

    universe_size = screener.refresh_ticker_universe()
    logger.info("Seeding %d NYSE sessions for %d active tickers, %s through %s.",
                session_count, universe_size, sessions[0], target_date)

    seed_started = time.perf_counter()
    last_request_started = None
    total_bars = 0
    failed_dates = []
    for index, session in enumerate(sessions, start=1):
        if last_request_started is not None and request_interval:
            wait = request_interval - (time.perf_counter() - last_request_started)
            if wait > 0:
                time.sleep(wait)

        last_request_started = time.perf_counter()
        date_str = session.isoformat()
        bars_ingested = screener.ingest_daily_bars(date_str)
        total_bars += bars_ingested
        if bars_ingested == 0:
            failed_dates.append(date_str)
        logger.info(
            "Seed progress %d/%d: %s, %d bars (total %d, elapsed %.1fs).",
            index, session_count, date_str, bars_ingested, total_bars,
            time.perf_counter() - seed_started
        )

    seeded_sessions, latest_spy_date = count_spy_sessions(sessions[0], target_date)
    if seeded_sessions < session_count or latest_spy_date != target_date.isoformat():
        raise RuntimeError(
            f"History seed incomplete: {seeded_sessions}/{session_count} SPY sessions through "
            f"{latest_spy_date}; failed ingestion dates: {failed_dates}"
        )

    verify_indicator_history(target_date, session_count)
    conn = screener.get_connection()
    try:
        bar_count = conn.execute("SELECT count(*) FROM daily_bars").fetchone()[0]
    finally:
        conn.close()
    logger.info(
        "History seed complete: %d sessions, %d bars in %.1fs; database size %.1f MiB.",
        seeded_sessions, bar_count, time.perf_counter() - seed_started,
        database_path.stat().st_size / (1024 * 1024)
    )
    return total_bars


def main():
    parser = argparse.ArgumentParser(description="Seed daily bars for technical-indicator lookbacks.")
    parser.add_argument("--trading-days", type=int, default=200)
    parser.add_argument("--end-date", type=datetime.date.fromisoformat, default=None)
    parser.add_argument("--database", type=Path, default=None, help="SQLite path (defaults to backend/stock_screener.db).")
    parser.add_argument("--request-interval", type=float, default=12.1,
                        help="Minimum seconds between daily provider requests (default: 12.1).")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    try:
        seed_history(
            session_count=args.trading_days,
            end_date=args.end_date,
            database=args.database,
            request_interval=args.request_interval
        )
        logger.info("History seed process returned normally.")
    finally:
        logging.shutdown()


if __name__ == "__main__":
    main()