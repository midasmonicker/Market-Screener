import os
import sys
import argparse
import datetime
import sqlite3
import json
import logging
import pandas as pd
import numpy as np
from screener import (
    get_connection,
    init_db,
    run_screener_engine,
    DB_PATH
)

def clear_signals_for_date(date_str, conn=None):
    """Deletes existing buy_signals for target date_str before re-screening."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM buy_signals WHERE timestamp = ? AND checkpoint_id = 'eod'",
        (date_str,),
    )
    conn.commit()
    if close_conn:
        conn.close()

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("OutcomesModule")

def init_outcomes_table(conn=None):
    """Ensures signal_outcomes table exists."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            signal_id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            signal_date DATE NOT NULL,
            ret_1d REAL,
            ret_5d REAL,
            ret_10d REAL,
            ret_20d REAL,
            max_drawdown_20d REAL,
            max_runup_20d REAL,
            spy_ret_5d REAL,
            spy_ret_20d REAL,
            excess_5d REAL,
            excess_20d REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (signal_id) REFERENCES buy_signals(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_date ON signal_outcomes(signal_date)")
    conn.commit()
    if close_conn:
        conn.close()

def update_signal_outcomes(conn=None):
    """
    Fills/updates outcome fields in signal_outcomes for past signals where subsequent daily bars exist.
    Calculations:
      - Returns measured from signal-day close: ((bar_close - entry_close) / entry_close) * 100.0
      - Max drawdown & runup up to 20 trading days post-signal:
          max_drawdown_20d: min percentage from entry close over the first <= 20 days (e.g. -4.5%)
          max_runup_20d: max percentage from entry close over the first <= 20 days (e.g. +12.3%)
      - SPY return over the same matching horizons (spy_ret_5d, spy_ret_20d)
      - Excess returns: stock_return - spy_return
      - STRICT LOOKAHEAD PREVENTION: No horizon uses bars later than its stated horizon count.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    init_outcomes_table(conn)
    cur = conn.cursor()

    # Query all buy_signals joined with existing signal_outcomes
    cur.execute("""
        SELECT b.id, b.symbol, b.timestamp, b.close_price,
               o.ret_1d, o.ret_5d, o.ret_10d, o.ret_20d
        FROM buy_signals b
        LEFT JOIN signal_outcomes o ON b.id = o.signal_id
        WHERE b.checkpoint_id = 'eod'
          AND (o.ret_20d IS NULL OR o.signal_id IS NULL)
        ORDER BY b.timestamp ASC
    """)
    pending_signals = cur.fetchall()

    if not pending_signals:
        logger.info("All signal outcomes are fully calculated or up-to-date.")
        if close_conn:
            conn.close()
        return 0

    logger.info("Evaluating forward outcomes for %d pending signals...", len(pending_signals))

    # Preload SPY bars for benchmark excess returns
    cur.execute("""
        SELECT timestamp, close 
        FROM daily_bars 
        WHERE symbol = 'SPY' 
        ORDER BY timestamp ASC
    """)
    spy_rows = cur.fetchall()
    spy_dates = [r[0] for r in spy_rows]
    spy_closes = {r[0]: float(r[1]) for r in spy_rows}

    updated_count = 0
    records_to_upsert = []

    for sig in pending_signals:
        sig_id, sym, sig_date, entry_close, o_1d, o_5d, o_10d, o_20d = sig
        if not entry_close or entry_close <= 0:
            continue

        # Fetch subsequent bars for this ticker strictly AFTER signal_date, ordered chronologically
        cur.execute("""
            SELECT timestamp, open, high, low, close 
            FROM daily_bars 
            WHERE symbol = ? AND timestamp > ? 
            ORDER BY timestamp ASC 
            LIMIT 20
        """, (sym, sig_date))
        subsequent_bars = cur.fetchall()
        n_bars = len(subsequent_bars)

        if n_bars == 0:
            # No future bars available yet
            continue

        # Compute forward returns strictly from subsequent bars (1-indexed day 1 to 20)
        # Day 1: index 0
        ret_1d = round(((float(subsequent_bars[0][4]) - entry_close) / entry_close) * 100.0, 2) if n_bars >= 1 else None
        ret_5d = round(((float(subsequent_bars[4][4]) - entry_close) / entry_close) * 100.0, 2) if n_bars >= 5 else None
        ret_10d = round(((float(subsequent_bars[9][4]) - entry_close) / entry_close) * 100.0, 2) if n_bars >= 10 else None
        ret_20d = round(((float(subsequent_bars[19][4]) - entry_close) / entry_close) * 100.0, 2) if n_bars >= 20 else None

        # Max drawdown and max runup over the available forward bars up to 20 bars
        bars_up_to_20 = subsequent_bars[:min(20, n_bars)]
        lows = [float(b[3]) for b in bars_up_to_20]
        highs = [float(b[2]) for b in bars_up_to_20]
        
        min_low = min(lows)
        max_high = max(highs)

        max_drawdown_20d = round(((min_low - entry_close) / entry_close) * 100.0, 2)
        max_runup_20d = round(((max_high - entry_close) / entry_close) * 100.0, 2)

        # SPY benchmark returns matching exact trading day horizons
        # Find subsequent SPY bars matching the stock's forward dates or next chronological SPY bars
        subsequent_spy = [b for b in spy_rows if b[0] > sig_date][:min(20, n_bars)]
        spy_base_close = spy_closes.get(sig_date)
        
        # If SPY close on exact signal_date is not in DB, use the closest prior SPY bar
        if not spy_base_close:
            prior_spy = [b for b in spy_rows if b[0] <= sig_date]
            spy_base_close = prior_spy[-1][1] if prior_spy else None

        spy_ret_5d = None
        excess_5d = None
        if len(subsequent_spy) >= 5 and spy_base_close and spy_base_close > 0:
            spy_5d_close = subsequent_spy[4][1]
            spy_ret_5d = round(((spy_5d_close - spy_base_close) / spy_base_close) * 100.0, 2)
            if ret_5d is not None:
                excess_5d = round(ret_5d - spy_ret_5d, 2)

        spy_ret_20d = None
        excess_20d = None
        if len(subsequent_spy) >= 20 and spy_base_close and spy_base_close > 0:
            spy_20d_close = subsequent_spy[19][1]
            spy_ret_20d = round(((spy_20d_close - spy_base_close) / spy_base_close) * 100.0, 2)
            if ret_20d is not None:
                excess_20d = round(ret_20d - spy_ret_20d, 2)

        records_to_upsert.append((
            sig_id,
            sym,
            sig_date,
            ret_1d,
            ret_5d,
            ret_10d,
            ret_20d,
            max_drawdown_20d,
            max_runup_20d,
            spy_ret_5d,
            spy_ret_20d,
            excess_5d,
            excess_20d
        ))

    if records_to_upsert:
        cur.executemany("""
            INSERT INTO signal_outcomes (
                signal_id, symbol, signal_date,
                ret_1d, ret_5d, ret_10d, ret_20d,
                max_drawdown_20d, max_runup_20d,
                spy_ret_5d, spy_ret_20d,
                excess_5d, excess_20d,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(signal_id) DO UPDATE SET
                ret_1d = excluded.ret_1d,
                ret_5d = excluded.ret_5d,
                ret_10d = excluded.ret_10d,
                ret_20d = excluded.ret_20d,
                max_drawdown_20d = excluded.max_drawdown_20d,
                max_runup_20d = excluded.max_runup_20d,
                spy_ret_5d = excluded.spy_ret_5d,
                spy_ret_20d = excluded.spy_ret_20d,
                excess_5d = excluded.excess_5d,
                excess_20d = excluded.excess_20d,
                updated_at = CURRENT_TIMESTAMP
        """, records_to_upsert)
        conn.commit()
        updated_count = len(records_to_upsert)
        logger.info("Upserted forward outcome records for %d signals.", updated_count)

    if close_conn:
        conn.close()

    return updated_count

def export_setup_stats(output_path="../frontend/public/data/setup_stats.json", conn=None):
    """
    Exports summary statistics per setup_name:
      - signal_count: Total signals generated for setup
      - hit_rate_5d: % of 5d returns > 0
      - hit_rate_20d: % of 20d returns > 0
      - median_excess_5d: Median excess return vs SPY at 5d
      - avg_excess_5d: Mean excess return vs SPY at 5d
      - median_excess_20d: Median excess return vs SPY at 20d
      - avg_excess_20d: Mean excess return vs SPY at 20d
      - median_max_drawdown: Median 20d max drawdown %
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    query = """
        SELECT b.setup_name,
               o.ret_1d, o.ret_5d, o.ret_10d, o.ret_20d,
               o.max_drawdown_20d, o.max_runup_20d,
               o.spy_ret_5d, o.spy_ret_20d,
               o.excess_5d, o.excess_20d
        FROM buy_signals b
        JOIN signal_outcomes o ON b.id = o.signal_id
        WHERE b.checkpoint_id = 'eod'
    """
    df = pd.read_sql(query, conn)
    if close_conn:
        conn.close()

    setup_stats = {}
    if df.empty:
        logger.warning("No signal outcomes found in database to compile setup_stats.")
    else:
        for setup_name, group in df.groupby("setup_name"):
            if setup_name == "Momentum Breakdown":
                group = group.copy()
                # Coerce to numeric (float64/NaN) before negating: a SQL NULL in an
                # all-null column comes back from pandas as an object-dtype Series of
                # Python None, and unary negation on None raises TypeError. This bit
                # a "Momentum Breakdown" group the first time it had a signal without
                # a completed 20d outcome yet (e.g. a signal too recent to have 20
                # trading days of forward data).
                raw_ret_5d = pd.to_numeric(group["ret_5d"], errors="coerce")
                raw_ret_20d = pd.to_numeric(group["ret_20d"], errors="coerce")
                raw_max_runup_20d = pd.to_numeric(group["max_runup_20d"], errors="coerce")
                group["ret_5d"] = -raw_ret_5d
                group["ret_20d"] = -raw_ret_20d
                group["excess_5d"] = group["spy_ret_5d"] - raw_ret_5d
                group["excess_20d"] = group["spy_ret_20d"] - raw_ret_20d
                group["max_drawdown_20d"] = -raw_max_runup_20d

            total_signals = len(group)

            # 5-day metrics
            ret_5d_valid = group["ret_5d"].dropna()
            n_5d = len(ret_5d_valid)
            hit_rate_5d = round((float((ret_5d_valid > 0).mean()) * 100.0), 1) if n_5d > 0 else None

            excess_5d_valid = group["excess_5d"].dropna()
            med_excess_5d = round(float(excess_5d_valid.median()), 2) if not excess_5d_valid.empty else None
            avg_excess_5d = round(float(excess_5d_valid.mean()), 2) if not excess_5d_valid.empty else None

            # 20-day metrics
            ret_20d_valid = group["ret_20d"].dropna()
            n_20d = len(ret_20d_valid)
            hit_rate_20d = round((float((ret_20d_valid > 0).mean()) * 100.0), 1) if n_20d > 0 else None

            excess_20d_valid = group["excess_20d"].dropna()
            med_excess_20d = round(float(excess_20d_valid.median()), 2) if not excess_20d_valid.empty else None
            avg_excess_20d = round(float(excess_20d_valid.mean()), 2) if not excess_20d_valid.empty else None

            # Drawdown metrics
            dd_valid = group["max_drawdown_20d"].dropna()
            med_max_drawdown = round(float(dd_valid.median()), 2) if not dd_valid.empty else None

            setup_stats[setup_name] = {
                "signal_count": total_signals,
                "completed_5d_count": n_5d,
                "hit_rate_5d": hit_rate_5d,
                "completed_20d_count": n_20d,
                "hit_rate_20d": hit_rate_20d,
                "median_excess_5d": med_excess_5d,
                "avg_excess_5d": avg_excess_5d,
                "median_excess_20d": med_excess_20d,
                "avg_excess_20d": avg_excess_20d,
                "median_max_drawdown": med_max_drawdown
            }

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "setups": setup_stats
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    logger.info("Saved setup performance statistics to %s", output_path)
    return payload

def run_backfill(min_history_bars=50):
    """
    Replays the screener over stored daily_bars history:
      1. Finds all distinct chronological dates in daily_bars with sufficient history.
      2. Replays run_screener_engine for each date.
      3. Computes forward outcomes via update_signal_outcomes.
      4. Exports setup_stats.json.
    """
    logger.info("=" * 60)
    logger.info("STARTING SCREENER REPLAY & OUTCOME BACKFILL")
    logger.info("=" * 60)

    conn = get_connection()
    init_db("schema.sql")
    init_outcomes_table(conn)

    cur = conn.cursor()
    # Find all distinct trading dates in daily_bars ordered chronologically
    cur.execute("SELECT DISTINCT timestamp FROM daily_bars ORDER BY timestamp ASC")
    all_dates = [r[0] for r in cur.fetchall()]
    conn.close()

    if len(all_dates) < min_history_bars:
        logger.warning(
            "Insufficient historical dates (%d < %d) to run replay backfill.",
            len(all_dates), min_history_bars
        )
        return False

    # Backtest replay starts once we have at least 50 historical trading sessions
    replay_dates = all_dates[min_history_bars - 1:]
    logger.info("Historical date range: %s to %s (%d trading dates).", all_dates[0], all_dates[-1], len(all_dates))
    logger.info("Replaying screener across %d historical dates (from %s)...", len(replay_dates), replay_dates[0])

    total_signals_generated = 0
    for idx, d_str in enumerate(replay_dates, start=1):
        clear_signals_for_date(d_str)
        sigs = run_screener_engine(d_str)
        if sigs:
            total_signals_generated += len(sigs)
            logger.info("[%d/%d] %s: Generated %d signals", idx, len(replay_dates), d_str, len(sigs))

    logger.info("Replay completed: Generated %d signals across historical dates.", total_signals_generated)

    # Step 2: Compute all forward outcomes
    logger.info("Updating forward outcome metrics for all generated signals...")
    updated_outcomes = update_signal_outcomes()
    logger.info("Outcomes updated: %d records processed.", updated_outcomes)

    # Step 3: Export setup stats
    stats = export_setup_stats()
    logger.info("Backfill complete.")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest and Outcome Tracking Module")
    parser.add_argument("--backfill", action="store_true", help="Replay screener over stored daily_bars and backfill outcomes")
    parser.add_argument("--export-only", action="store_true", help="Export setup_stats.json without replaying")
    args = parser.parse_args()

    init_db()
    if args.backfill:
        run_backfill()
    elif args.export_only:
        export_setup_stats()
    else:
        # Default behavior: update pending outcomes and export
        update_signal_outcomes()
        export_setup_stats()
