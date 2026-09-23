import os
import sys
import argparse
import datetime
import logging
from screener import (
    init_db,
    refresh_ticker_universe,
    ingest_daily_bars,
    run_screener_engine,
    export_web_data,
    get_connection
)
from alerts import send_discord_alerts

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("DailyScreener")

def is_market_day(target_date):
    """Simple weekend check; can be extended with pandas_market_calendars."""
    return target_date.weekday() < 5

def run_daily_pipeline(date_str=None, force_failover=False, dry_run=False):
    """
    Sequential execution pipeline:
    1. Initialize DB & verify tables
    2. Refresh Ticker Universe
    3. Ingest Market Daily Bars (Polygon -> Alpaca -> yfinance)
    4. Compute Technical Indicators (Pandas_TA: RVOL, RSI, MAs)
    5. Evaluate Rules and Emit Buy Signals
    6. Dispatch Alert to Discord Webhook
    """
    logger.info("=" * 65)
    logger.info("STARTING DAILY STOCK SCREENER PIPELINE")
    logger.info("=" * 65)

    today = datetime.date.today()
    if date_str:
        try:
            target_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            logger.error("Invalid date format: %s. Use YYYY-MM-DD.", date_str)
            return False
    else:
        # If running on weekend, default to last Friday
        target_date = today
        if target_date.weekday() == 5: # Saturday
            target_date -= datetime.timedelta(days=1)
        elif target_date.weekday() == 6: # Sunday
            target_date -= datetime.timedelta(days=2)
        date_str = target_date.isoformat()

    logger.info("Target execution date: %s", date_str)

    # Step 1: Database verification
    logger.info("[Step 1/6] Verifying SQLite database structure...")
    init_db("schema.sql")

    # Step 2: Universe Seeding
    logger.info("[Step 2/6] Refreshing ticker universe...")
    universe_size = refresh_ticker_universe()
    logger.info("Universe active tickers: %d", universe_size)

    # Step 3: Daily Bars Ingestion with multi-layer failover
    logger.info("[Step 3/6] Ingesting daily market bars (force_failover=%s)...", force_failover)
    bars_ingested = ingest_daily_bars(date_str, force_failover=force_failover)
    logger.info("Ingestion completed: %d bars stored.", bars_ingested)

    # Step 4 & 5: Indicator Computation & Signal Generation
    logger.info("[Step 4 & 5/6] Computing technical indicators and evaluating setups...")
    signals = run_screener_engine(date_str)
    signals_count = len(signals)
    logger.info("Screener engine completed: %d buy signals generated.", signals_count)

    # Step 6: Dispatch Alerts to Discord Webhook
    logger.info("[Step 6/7] Generating and dispatching Discord alerts (dry_run=%s)...", dry_run)
    alert_payload = send_discord_alerts(date_str=date_str, dry_run=dry_run)
    logger.info("Alert process completed.")

    # Step 7: Export Web Data Payloads (Phase 5)
    logger.info("[Step 7/7] Exporting static JSON payloads for web visualization...")
    web_export = export_web_data(output_dir="public/data")
    logger.info("Export completed: %d signals, %d tickers exported to public/data/", 
                web_export["signals_count"], web_export["tickers_count"])

    logger.info("=" * 65)
    logger.info("DAILY SCREENER PIPELINE FINISHED SUCCESSFULLY")
    logger.info("=" * 65)
    return {
        "date": date_str,
        "universe_size": universe_size,
        "bars_ingested": bars_ingested,
        "signals_generated": signals_count,
        "alert_payload": alert_payload,
        "web_export": web_export
    }

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
