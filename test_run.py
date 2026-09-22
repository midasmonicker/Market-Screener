import sqlite3
import pandas as pd
from screener import (
    init_db,
    refresh_ticker_universe,
    ingest_historical_bars_for_ticker,
    run_screener_engine,
    get_connection
)

def run_test():
    print("=" * 60)
    print("STEP 1: Initializing Database & Seeding Universe")
    print("=" * 60)
    init_db("schema.sql")
    ticker_count = refresh_ticker_universe()
    print(f"Total tickers available in universe: {ticker_count}")

    print("\n" + "=" * 60)
    print("STEP 2: Ingesting Historical Bars for Core Watchlist")
    print("=" * 60)
    watchlist = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD"]
    total_bars = 0
    for sym in watchlist:
        bars = ingest_historical_bars_for_ticker(sym, "2025-05-01", "2026-03-20")
        print(f"  [{sym}] Ingested {bars} daily bars")
        total_bars += bars

    print(f"\nTotal bars loaded into SQLite: {total_bars}")

    print("\n" + "=" * 60)
    print("STEP 3: Running Technical Calculation Engine (Pandas_TA)")
    print("=" * 60)
    # Test screening across multiple historical market days to verify signal triggers
    test_dates = ["2025-09-19", "2025-10-31", "2026-02-04", "2026-03-20"]
    for d in test_dates:
        count = run_screener_engine(d)
        print(f"Date {d}: Found {count} signals")

    print("\n" + "=" * 60)
    print("STEP 4: Verifying SQLite Database Contents")
    print("=" * 60)
    conn = get_connection()
    df_tickers = pd.read_sql("SELECT count(*) as count FROM tickers", conn)
    df_bars = pd.read_sql("SELECT count(*) as count FROM daily_bars", conn)
    df_signals = pd.read_sql("SELECT * FROM buy_signals ORDER BY timestamp DESC, symbol ASC", conn)
    
    print(f"Tickers count: {df_tickers['count'].iloc[0]}")
    print(f"Daily bars count: {df_bars['count'].iloc[0]}")
    print(f"Buy signals generated: {len(df_signals)}")
    print("\nGenerated Buy Signals Table:")
    print(df_signals.to_string(index=False))

    conn.close()

if __name__ == "__main__":
    run_test()
