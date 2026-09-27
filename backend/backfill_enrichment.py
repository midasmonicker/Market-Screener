import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta

import pandas as pd

from screener import (
    DB_PATH,
    calculate_universe_rs_scores,
    check_market_regime,
    export_web_data,
    fetch_finnhub_earnings_calendar,
    get_connection,
    process_single_ticker_screener,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BackfillEnrichment")

def _json_loads(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {}


def _pending_signal_rows(conn):
    query = """
        SELECT id, symbol, timestamp, details
        FROM buy_signals
        ORDER BY timestamp DESC, symbol ASC
    """
    return conn.execute(query).fetchall()


def _compute_spy_return_63d(df_all, date_str):
    if df_all.empty or "SPY" not in df_all["symbol"].unique().tolist():
        return None
    spy_df = df_all[df_all["symbol"] == "SPY"].copy()
    spy_df = spy_df[spy_df["timestamp"] <= date_str].sort_values("timestamp")
    if len(spy_df) < 2:
        return None
    n = min(63, len(spy_df))
    try:
        start_close = float(spy_df["close"].iloc[-n])
        end_close = float(spy_df["close"].iloc[-1])
        return ((end_close - start_close) / start_close) * 100.0
    except Exception:
        return None


def _compute_signal_payload_for_date(conn, symbol, date_str, earnings_map=None):
    df_all = pd.read_sql(
        """
        SELECT symbol, timestamp, open, high, low, close, volume, source
        FROM daily_bars
        WHERE symbol = ? AND timestamp <= ?
        ORDER BY symbol, timestamp ASC
        """,
        conn,
        params=(symbol, date_str),
    )
    if df_all.empty:
        return None

    active_tickers = pd.read_sql(
        "SELECT symbol FROM tickers WHERE is_active = 1",
        conn,
    )["symbol"].tolist()
    active_set = set(active_tickers)

    all_bars = pd.read_sql(
        """
        SELECT symbol, timestamp, open, high, low, close, volume, source
        FROM daily_bars
        WHERE timestamp <= ?
        ORDER BY symbol, timestamp ASC
        """,
        conn,
        params=(date_str,),
    )
    grouped = {sym: group for sym, group in all_bars.groupby("symbol") if sym in active_set}

    market_regime = check_market_regime(date_str, conn=conn)
    rs_scores = calculate_universe_rs_scores(grouped, date_str)
    spy_return_63d = _compute_spy_return_63d(all_bars, date_str)
    all_trading_days = sorted(all_bars["timestamp"].unique().tolist())

    if earnings_map is None:
        try:
            from screener import fetch_finnhub_symbol_earnings
            earnings_map = {symbol: fetch_finnhub_symbol_earnings(symbol)}
        except Exception:
            earnings_map = {}

    res = process_single_ticker_screener(
        symbol,
        df_all,
        date_str,
        rs_scores.get(symbol),
        market_regime,
        spy_return_63d,
        earnings_map,
        all_trading_days,
    )
    if not res:
        return None

    _, _, _, _, _, _, details_json = res
    return _json_loads(details_json)


def backfill_missing_signal_enrichment(dry_run=False, export_after=False):
    conn = get_connection()
    pending_rows = _pending_signal_rows(conn)
    summary = {
        "pending": len(pending_rows),
        "updated": 0,
        "skipped": 0,
        "missing_after_recalc": 0,
    }

    if not pending_rows:
        logger.info("No historical signal rows require enrichment backfill.")
        if export_after:
            export_web_data(output_dir="../frontend/public/data")
        return summary

    logger.info("Found %d signal rows needing enrichment backfill.", len(pending_rows))

    unique_symbols = sorted({symbol for _, symbol, _, _ in pending_rows})
    earnings_cache = {}
    try:
        from screener import fetch_finnhub_symbol_earnings
        for sym in unique_symbols:
            earnings_cache[sym] = fetch_finnhub_symbol_earnings(sym)
    except Exception as e:
        logger.warning("Could not preload symbol earnings cache for backfill: %s", e)
        earnings_cache = {}

    for signal_id, symbol, date_str, details in pending_rows:
        existing = _json_loads(details)
        logger.info("Processing signal %s (%s) on %s", signal_id, symbol, date_str)

        computed = _compute_signal_payload_for_date(conn, symbol, date_str, earnings_cache.get(symbol, {}))
        if not computed:
            logger.warning("Signal %s (%s, %s) could not be re-evaluated; leaving as-is.", signal_id, symbol, date_str)
            summary["skipped"] += 1
            continue

        if "near_earnings" not in computed or "earnings_date" not in computed:
            logger.warning(
                "Signal %s (%s, %s) is missing earnings fields after recompute.",
                signal_id,
                symbol,
                date_str,
            )
            summary["missing_after_recalc"] += 1
            continue

        merged = {
            **existing,
            "near_earnings": computed["near_earnings"],
            "earnings_date": computed["earnings_date"],
        }

        if dry_run:
            logger.info(
                "[DRY RUN] Would refresh earnings for signal %s (%s, %s): earnings_date=%s near_earnings=%s",
                signal_id,
                symbol,
                date_str,
                merged["earnings_date"],
                merged["near_earnings"],
            )
            continue

        conn.execute(
            "UPDATE buy_signals SET details = ? WHERE id = ?",
            (json.dumps(merged), signal_id),
        )
        summary["updated"] += 1

    if not dry_run:
        conn.commit()
        logger.info("Backfill complete. Updated %d signals.", summary["updated"])
    else:
        logger.info("Dry run complete. %d pending rows would be updated.", len(pending_rows))

    if export_after:
        export_web_data(output_dir="../frontend/public/data")

    conn.close()
    return summary


def _parse_args():
    parser = argparse.ArgumentParser(description="Backfill missing signal enrichment fields for historical rows.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be updated without writing changes.")
    parser.add_argument("--export", action="store_true", help="Re-export frontend JSON after backfill completes.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    summary = backfill_missing_signal_enrichment(dry_run=args.dry_run, export_after=args.export)
    print(json.dumps(summary, indent=2))
