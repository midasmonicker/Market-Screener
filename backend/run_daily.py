import os
import sys
import argparse
import datetime
import logging
import sqlite3
import time
from zoneinfo import ZoneInfo
import pandas_market_calendars as mcal
from screener import (
    init_db,
    refresh_ticker_universe,
    ingest_daily_bars,
    run_screener_engine,
    update_post_breakout_performance,
    get_performance_summary,
    export_web_data,
    backfill_ticker_sectors,
    get_connection,
    DB_PATH
)
from alerts import (
    send_discord_alerts,
    send_telegram_alert,
    send_discord_failure_alert,
    send_discord_stale_alert,
    get_recent_signals
)
from outcomes import update_signal_outcomes, export_setup_stats

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("DailyScreener")

def get_latest_completed_trading_date(now_utc=None):
    """Return the latest NYSE session whose scheduled close has passed."""
    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    elif now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")

    now_utc = now_utc.astimezone(datetime.timezone.utc)
    ny_date = now_utc.astimezone(ZoneInfo("America/New_York")).date()
    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(
        start_date=(ny_date - datetime.timedelta(days=14)).isoformat(),
        end_date=ny_date.isoformat()
    )
    completed_sessions = schedule[schedule["market_close"] <= now_utc]
    if completed_sessions.empty:
        raise RuntimeError(f"No completed NYSE session found as of {now_utc.isoformat()}")
    return completed_sessions.index[-1].date()

def check_us_market_holiday_and_schedule(target_date):
    """
    Evaluates whether the given date is an active US equity market trading day using pandas_market_calendars.
    Returns: (is_trading_day: bool, expected_trading_day: str, reason: str)
    """
    nyse = mcal.get_calendar("NYSE")
    
    # Query calendar around target date
    start_check = target_date - datetime.timedelta(days=14)
    sched = nyse.schedule(start_date=start_check.isoformat(), end_date=target_date.isoformat())
    
    if sched.empty:
        return False, target_date.isoformat(), "No market sessions found in recent window."

    valid_trading_days = set(sched.index.strftime("%Y-%m-%d"))
    target_date_str = target_date.isoformat()
    latest_trading_day_str = sched.index[-1].strftime("%Y-%m-%d")

    if target_date_str in valid_trading_days:
        return True, target_date_str, "Active trading session"
    else:
        # Determine whether weekend or holiday
        if target_date.weekday() >= 5:
            reason = f"Weekend ({target_date.strftime('%A')})"
        else:
            reason = f"US Market Holiday (NYSE closed on {target_date_str})"
        return False, latest_trading_day_str, reason

def verify_data_freshness(expected_date_str, dry_run=False):
    """
    Validates that the latest bar date in SQLite matches expected_date_str for the majority of the universe.
    If majority is not fresh, sends Discord DATA STALE alert and raises RuntimeError (exiting non-zero).
    """
    logger.info("[Data Freshness Check] Validating bar freshness against expected trading day %s...", expected_date_str)
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT count(*) FROM tickers WHERE is_active = 1")
    universe_count = cur.fetchone()[0] or 1

    # Count bars on expected_date_str
    cur.execute("SELECT count(DISTINCT symbol) FROM daily_bars WHERE timestamp = ?", (expected_date_str,))
    fresh_bars_count = cur.fetchone()[0] or 0

    # Find the most frequent latest bar date in the DB
    cur.execute("""
        SELECT timestamp, count(*) as bar_count
        FROM daily_bars
        GROUP BY timestamp
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    most_common_row = cur.fetchone()
    most_common_date = most_common_row[0] if most_common_row else "None"
    conn.close()

    logger.info(
        "[Data Freshness Check] Fresh bars for %s: %d / %d active universe tickers (most recent in DB: %s).",
        expected_date_str, fresh_bars_count, universe_count, most_common_date
    )

    # Threshold: At least 50% of the active universe must have bars on expected_date_str
    if fresh_bars_count < (universe_count * 0.5):
        logger.error(
            "DATA STALE: Only %d/%d tickers have bars for expected trading day %s (Latest in DB: %s).",
            fresh_bars_count, universe_count, expected_date_str, most_common_date
        )
        send_discord_stale_alert(
            expected_date=expected_date_str,
            actual_date=most_common_date,
            fresh_count=fresh_bars_count,
            universe_count=universe_count,
            dry_run=dry_run
        )
        raise RuntimeError(
            f"DATA STALE: Latest bar date in DB ({most_common_date}) is not the expected trading day ({expected_date_str}). "
            f"Only {fresh_bars_count}/{universe_count} tickers present."
        )
    
    logger.info("[Data Freshness Check] PASSED: %d/%d tickers updated for %s.", fresh_bars_count, universe_count, expected_date_str)
    return True

def clear_existing_signals_for_date(date_str):
    """
    Ensures idempotency: Deletes existing buy_signals for target date_str before re-screening.
    Re-running for the same date will not accumulate duplicate signals.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM buy_signals WHERE timestamp = ?", (date_str,))
    existing_count = cur.fetchone()[0] or 0
    if existing_count > 0:
        logger.info("[Idempotency] Removing %d existing buy_signals for %s to prevent duplicates.", existing_count, date_str)
        cur.execute("DELETE FROM buy_signals WHERE timestamp = ?", (date_str,))
        conn.commit()
    conn.close()
    return existing_count

def run_daily_pipeline(date_str=None, force_failover=False, dry_run=False):
    """
    Sequential execution pipeline with holiday check, data freshness validation,
    idempotent signal generation, and Discord failure alert wrapping.
    """
    pipeline_started = time.perf_counter()
    logger.info("=" * 65)
    logger.info("STARTING DAILY STOCK SCREENER PIPELINE")
    logger.info("=" * 65)

    current_step = "Target Date & Market Holiday Resolution"

    try:
        if date_str:
            try:
                target_date = datetime.date.fromisoformat(date_str)
            except ValueError:
                logger.error("Invalid date format: %s. Use YYYY-MM-DD.", date_str)
                return False
        else:
            target_date = get_latest_completed_trading_date()

        # Step 0: Check for US market holiday using pandas_market_calendars
        is_trading_day, expected_day_str, reason = check_us_market_holiday_and_schedule(target_date)
        if not is_trading_day and not date_str:
            logger.info("US Market Closed today (%s: %s). Skipping daily screener cleanly.", target_date.isoformat(), reason)
            logger.info("Pipeline exited cleanly (code 0).")
            return {"status": "skipped", "reason": reason, "date": target_date.isoformat()}
        elif not is_trading_day and date_str:
            logger.warning("Target date %s is marked as non-trading day (%s), but explicit date flag was provided. Proceeding.", date_str, reason)

        date_str = target_date.isoformat()
        logger.info("Target execution date: %s", date_str)

        # Step 1: Database verification & schema migration
        current_step = "[Step 1/8] Verifying SQLite database structure and migrations"
        logger.info(current_step)
        init_db("schema.sql")

        # Step 2: Universe Seeding
        current_step = "[Step 2/8] Refreshing ticker universe"
        logger.info(current_step)
        stage_started = time.perf_counter()
        universe_size = refresh_ticker_universe()
        logger.info(
            "Universe active tickers: %d (refresh %.2fs).",
            universe_size, time.perf_counter() - stage_started
        )

        # Step 3: Daily Bars Ingestion with multi-layer failover
        current_step = f"[Step 3/8] Ingesting daily market bars for {date_str}"
        logger.info("%s (force_failover=%s)...", current_step, force_failover)
        stage_started = time.perf_counter()
        bars_ingested = ingest_daily_bars(date_str, force_failover=force_failover)
        logger.info(
            "Ingestion completed: %d bars stored (%.2fs).",
            bars_ingested, time.perf_counter() - stage_started
        )

        # Step 3.5: Data Freshness Check
        current_step = f"[Step 3.5/8] Data Freshness Validation for {date_str}"
        verify_data_freshness(date_str, dry_run=dry_run)

        # Step 4 & 5: Clear existing signals for date (Idempotency) & Screen
        current_step = f"[Step 4 & 5/8] Computing indicators and screening for {date_str}"
        logger.info(current_step)
        clear_existing_signals_for_date(date_str)
        signals = run_screener_engine(date_str)
        signals_count = len(signals)
        logger.info("Screener engine completed: %d buy signals passed filters.", signals_count)

        # Step 6: Post-Breakout Performance & Outcome Tracking Engine (Phase 8 & Backtest)
        current_step = "[Step 6/8] Updating post-breakout performance and forward outcomes tracking"
        logger.info(current_step)
        perf_summary = update_post_breakout_performance()
        if perf_summary:
            logger.info(
                "Performance Win Rates | 5d: %s%% (n=%d) | 10d: %s%% (n=%d) | 20d: %s%% (n=%d)",
                perf_summary["horizon_5d"]["win_rate"],
                perf_summary["horizon_5d"]["count"],
                perf_summary["horizon_10d"]["win_rate"],
                perf_summary["horizon_10d"]["count"],
                perf_summary["horizon_20d"]["win_rate"],
                perf_summary["horizon_20d"]["count"]
            )

        # Update signal_outcomes table for forward horizons (1d, 5d, 10d, 20d, drawdown, SPY excess)
        outcomes_updated = update_signal_outcomes()
        logger.info("Signal outcomes tracking: %d outcome records updated.", outcomes_updated)

        # Step 7: Dispatch Alerts to Discord Webhook and Telegram Bot
        current_step = "[Step 7/8] Generating and dispatching alerts"
        logger.info("%s (dry_run=%s)...", current_step, dry_run)
        alert_payload = send_discord_alerts(date_str=date_str, dry_run=dry_run)

        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        telegram_payload = None
        active_signals = signals if signals else get_recent_signals(date_str)
        if telegram_bot_token and telegram_chat_id:
            logger.info("Dispatching Telegram alert notification...")
            telegram_payload = send_telegram_alert(
                active_signals,
                bot_token=telegram_bot_token,
                chat_id=telegram_chat_id,
                dry_run=dry_run,
                target_date=date_str
            )
        elif dry_run:
            logger.info("[DRY RUN] Simulating Telegram alert notification...")
            telegram_payload = send_telegram_alert(
                active_signals,
                bot_token="DRY_RUN_TOKEN",
                chat_id="DRY_RUN_CHAT_ID",
                dry_run=True,
                target_date=date_str
            )
        else:
            logger.info("Telegram credentials not configured. Skipping Telegram alert.")

        logger.info("Alert process completed.")

        # Step 8: Export Web Data Payloads (Phase 5, 7, 8 & Setup Stats)
        current_step = "[Step 8/8] Exporting static JSON payloads for web visualization"
        logger.info(current_step)
        stage_started = time.perf_counter()
        web_export = export_web_data(output_dir="../frontend/public/data")
        setup_stats_export = export_setup_stats(output_path="../frontend/public/data/setup_stats.json")
        logger.info(
            "Export completed: %d signals, %d tickers, setup_stats.json, regime.json exported "
            "to frontend/public/data/ (%.2fs).",
            web_export["signals_count"], web_export["tickers_count"],
            time.perf_counter() - stage_started
        )

        total_seconds = time.perf_counter() - pipeline_started
        logger.info("=" * 65)
        logger.info("DAILY SCREENER PIPELINE FINISHED SUCCESSFULLY")
        logger.info("End-to-end pipeline runtime: %.2fs (%.2f minutes).", total_seconds, total_seconds / 60.0)
        logger.info("=" * 65)
        return {
            "date": date_str,
            "universe_size": universe_size,
            "bars_ingested": bars_ingested,
            "signals_generated": signals_count,
            "performance_summary": perf_summary,
            "setup_stats": setup_stats_export,
            "alert_payload": alert_payload,
            "telegram_payload": telegram_payload,
            "web_export": web_export
        }

    except Exception as err:
        logger.exception("Pipeline failure encountered at '%s': %s", current_step, err)
        try:
            send_discord_failure_alert(
                step_name=current_step,
                error_message=str(err),
                date_str=date_str,
                dry_run=dry_run
            )
        except Exception as alert_err:
            logger.error("Failed to dispatch Discord failure alert: %s", alert_err)
        # Re-raise the exception to terminate execution non-zero
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily Stock Screener Automated Entrypoint")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    parser.add_argument("--force-failover", action="store_true", help="Force failover to Alpaca/yfinance")
    parser.add_argument("--dry-run", action="store_true", help="Run without sending real webhook requests")
    args = parser.parse_args()

    run_daily_pipeline(
        date_str=args.date,
        force_failover=args.force_failover,
        dry_run=args.dry_run
    )

