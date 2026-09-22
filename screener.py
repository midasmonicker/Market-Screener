import os
import sys
import time
import datetime
import sqlite3
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

DB_PATH = "stock_screener.db"
FMP_API_KEY = os.getenv("FMP_API_KEY") or os.getenv("FMP_KEY", "")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET") or os.getenv("ALPACA_SECRET_KEY", "")

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db(schema_path="schema.sql"):
    """Initialize database tables and indexes from schema file."""
    logger.info("Initializing database from %s...", schema_path)
    conn = get_connection()
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.close()
    logger.info("Database initialized successfully.")

def refresh_ticker_universe():
    """
    Step 1: Seed universe using FMP (Price > $5, Market Cap > $300M).
    Falls back to Polygon active common stocks if FMP endpoint is restricted.
    """
    logger.info("Refreshing ticker universe...")
    tickers = []
    
    # Primary: FMP stock screener
    if FMP_API_KEY:
        try:
            url = f"https://financialmodelingprep.com/api/v3/stock-screener?marketCapMoreThan=300000000&priceMoreThan=5&isEtf=false&isActivelyTraded=true&apikey={FMP_API_KEY}"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    for x in data:
                        sym = x.get("symbol", "")
                        if sym and "." not in sym:
                            tickers.append((
                                sym,
                                x.get("companyName", sym),
                                float(x.get("marketCap") or 0),
                                x.get("sector", "Unknown"),
                                1
                            ))
                    logger.info("Fetched %d tickers from primary FMP screener.", len(tickers))
            else:
                logger.warning("FMP screener responded with HTTP %d: %s", res.status_code, res.text[:120])
        except Exception as e:
            logger.warning("FMP screener fetch error: %s", e)

    # Fallback: Polygon Reference Tickers
    if not tickers and POLYGON_API_KEY:
        logger.info("Using Polygon active common stocks to seed ticker universe...")
        try:
            url = f"https://api.polygon.io/v3/reference/tickers?market=stocks&type=CS&active=true&limit=1000&apiKey={POLYGON_API_KEY}"
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                data = res.json()
                results = data.get("results", [])
                for r in results:
                    sym = r.get("ticker", "")
                    if sym and "." not in sym:
                        tickers.append((
                            sym,
                            r.get("name", sym),
                            0.0,
                            r.get("primary_exchange", "US"),
                            1
                        ))
                logger.info("Fetched %d tickers from Polygon reference API.", len(tickers))
            else:
                logger.warning("Polygon reference API returned HTTP %d", res.status_code)
        except Exception as e:
            logger.error("Failed to fetch tickers from Polygon: %s", e)

    # Core major liquid equities fallback guarantee
    core_stocks = [
        ("AAPL", "Apple Inc.", 3000000000000.0, "Technology", 1),
        ("MSFT", "Microsoft Corp.", 3000000000000.0, "Technology", 1),
        ("NVDA", "NVIDIA Corp.", 2500000000000.0, "Technology", 1),
        ("AMZN", "Amazon.com Inc.", 2000000000000.0, "Consumer Cyclical", 1),
        ("GOOGL", "Alphabet Inc.", 2000000000000.0, "Technology", 1),
        ("META", "Meta Platforms Inc.", 1500000000000.0, "Technology", 1),
        ("TSLA", "Tesla Inc.", 800000000000.0, "Consumer Cyclical", 1),
        ("AMD", "Advanced Micro Devices Inc.", 300000000000.0, "Technology", 1),
    ]
    tickers.extend(core_stocks)

    conn = get_connection()
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO tickers (symbol, name, market_cap, sector, is_active, last_updated)
        VALUES (?, ?, ?, ?, ?, date('now'))
        ON CONFLICT(symbol) DO UPDATE SET 
            market_cap=CASE WHEN excluded.market_cap > 0 THEN excluded.market_cap ELSE tickers.market_cap END,
            name=excluded.name,
            is_active=1,
            last_updated=date('now')
    """, tickers)
    conn.commit()
    conn.close()
    logger.info("Total active tickers seeded in database: %d", len(tickers))
    return len(tickers)

def fetch_polygon_grouped_daily(date_str, max_retries=2):
    """
    Primary: Fetch entire US market in 1 API call via Polygon Grouped Daily.
    """
    if not POLYGON_API_KEY:
        return []
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}?adjusted=true&apiKey={POLYGON_API_KEY}"
    for attempt in range(max_retries):
        try:
            res = requests.get(url, timeout=20)
            if res.status_code == 200:
                results = res.json().get("results", [])
                records = []
                for r in results:
                    records.append((
                        r["T"], date_str, r["o"], r["h"], r["l"], r["c"], r["v"], r.get("vw", r["c"])
                    ))
                return records
            elif res.status_code == 429:
                logger.warning("Polygon rate limit 429 hit on %s (attempt %d).", date_str, attempt + 1)
                if attempt < max_retries - 1:
                    time.sleep(12)
            else:
                logger.error("Polygon API error on %s: HTTP %d %s", date_str, res.status_code, res.text[:100])
                break
        except Exception as e:
            logger.error("Polygon request error on %s: %s", date_str, e)
            time.sleep(3)
    return []

def fetch_alpaca_batch_daily(symbols, date_str, chunk_size=100):
    """
    Failover 1: Fetch batch daily bars for universe symbols via Alpaca Data API v2.
    """
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        logger.warning("Alpaca credentials not configured for failover.")
        return []

    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_API_SECRET
    }
    
    # Alpaca expects start and end timestamps (e.g. 2026-03-20T00:00:00Z to 2026-03-20T23:59:59Z)
    start_iso = f"{date_str}T00:00:00Z"
    end_iso = f"{date_str}T23:59:59Z"
    records = []

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        sym_str = ",".join(chunk)
        url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={sym_str}&timeframe=1Day&start={start_iso}&end={end_iso}&limit=10000"
        try:
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200:
                data = res.json().get("bars", {})
                for sym, bars in data.items():
                    if bars:
                        b = bars[-1]
                        records.append((
                            sym, date_str, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), int(b["v"]), float(b.get("vw", b["c"]))
                        ))
            else:
                logger.warning("Alpaca batch query returned HTTP %d for chunk %d", res.status_code, i // chunk_size)
        except Exception as e:
            logger.error("Alpaca request error for chunk: %s", e)
        time.sleep(0.3)

    return records

def fetch_yfinance_bulk_daily(symbols, date_str, chunk_size=80):
    """
    Failover 2: Fetch bulk daily bars using yfinance download.
    """
    records = []
    try:
        import yfinance as yf
        # Calculate next day for yfinance exclusive end parameter
        curr_d = datetime.date.fromisoformat(date_str)
        next_d = (curr_d + datetime.timedelta(days=1)).isoformat()

        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i + chunk_size]
            sym_str = " ".join(chunk)
            df = yf.download(sym_str, start=date_str, end=next_d, interval="1d", group_by="ticker", progress=False, threads=True)
            if df.empty:
                continue

            for sym in chunk:
                try:
                    if len(chunk) == 1:
                        sym_df = df
                    elif sym in df.columns.levels[0]:
                        sym_df = df[sym]
                    else:
                        continue
                    
                    if not sym_df.empty:
                        row = sym_df.iloc[-1]
                        o = float(row.get("Open", 0))
                        h = float(row.get("High", 0))
                        l = float(row.get("Low", 0))
                        c = float(row.get("Close", 0))
                        v = int(row.get("Volume", 0))
                        if c > 0:
                            records.append((sym, date_str, o, h, l, c, v, c))
                except Exception:
                    continue
    except Exception as e:
        logger.error("yfinance bulk download error: %s", e)
    return records

def ingest_daily_bars(date_str, force_failover=False):
    """
    Step 3: Save bars and prune historical records older than 250 days.
    Sequentially tests:
      1. Polygon Grouped Daily (1 API call)
      2. Alpaca batch daily bars (Failover 1)
      3. yfinance bulk download (Failover 2)
    """
    logger.info("Ingesting daily bars for date: %s (force_failover=%s)", date_str, force_failover)
    bars = []
    source_used = "Polygon Grouped Daily"

    if not force_failover:
        bars = fetch_polygon_grouped_daily(date_str)

    if not bars:
        logger.warning("Primary Polygon ingestion unavailable or forced failover. Initiating Failover Sequence 1 (Alpaca)...")
        conn = get_connection()
        universe_symbols = pd.read_sql("SELECT symbol FROM tickers WHERE is_active = 1", conn)["symbol"].tolist()
        conn.close()

        bars = fetch_alpaca_batch_daily(universe_symbols, date_str)
        source_used = "Alpaca Batch API"

    if not bars:
        logger.warning("Alpaca failover produced 0 bars. Initiating Failover Sequence 2 (yfinance)...")
        conn = get_connection()
        universe_symbols = pd.read_sql("SELECT symbol FROM tickers WHERE is_active = 1", conn)["symbol"].tolist()
        conn.close()

        bars = fetch_yfinance_bulk_daily(universe_symbols, date_str)
        source_used = "yfinance Bulk Download"

    if not bars:
        logger.error("All ingestion pipelines (Polygon, Alpaca, yfinance) failed to retrieve bars for %s.", date_str)
        return 0

    conn = get_connection()
    cur = conn.cursor()
    cur.executemany("""
        INSERT OR REPLACE INTO daily_bars (symbol, timestamp, open, high, low, close, volume, vwap)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, bars)
    
    # Prune data older than 250 trading days (~360 calendar days)
    cur.execute("""
        DELETE FROM daily_bars 
        WHERE timestamp < date('now', '-360 days')
    """)
    conn.commit()
    conn.close()
    logger.info("Successfully ingested %d bars using [%s] for %s.", len(bars), source_used, date_str)
    return len(bars)

def ingest_historical_bars_for_ticker(symbol, start_date, end_date):
    """
    Ingest historical bars for an individual ticker from Polygon daily aggregates.
    """
    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start_date}/{end_date}?adjusted=true&sort=asc&apiKey={POLYGON_API_KEY}"
    for _ in range(3):
        try:
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                results = res.json().get("results", [])
                records = []
                for r in results:
                    d_str = datetime.datetime.fromtimestamp(r["t"] / 1000.0, datetime.timezone.utc).strftime("%Y-%m-%d")
                    records.append((
                        symbol, d_str, r["o"], r["h"], r["l"], r["c"], r["v"], r.get("vw", r["c"])
                    ))
                if records:
                    conn = get_connection()
                    cur = conn.cursor()
                    cur.executemany("""
                        INSERT OR REPLACE INTO daily_bars (symbol, timestamp, open, high, low, close, volume, vwap)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, records)
                    conn.commit()
                    conn.close()
                return len(records)
            elif res.status_code == 429:
                time.sleep(12)
            else:
                break
        except Exception:
            time.sleep(2)
    return 0

def process_single_ticker_screener(symbol, df_symbol, date_str):
    """
    Compute technical indicators for a single symbol using pandas_ta and evaluate rules.
    """
    if len(df_symbol) < 50:
        return None

    try:
        df = df_symbol.copy()
        df["sma_200"] = ta.sma(df["close"], length=200)
        df["sma_50"] = ta.sma(df["close"], length=50)
        df["ema_20"] = ta.ema(df["close"], length=20)
        df["rsi"] = ta.rsi(df["close"], length=14)
        df["vol_sma_20"] = ta.sma(df["volume"], length=20)
        df["rvol"] = df["volume"] / df["vol_sma_20"]

        latest = df.iloc[-1]
        close_price = float(latest["close"])
        rvol = float(latest["rvol"]) if pd.notnull(latest["rvol"]) else 0.0
        rsi = float(latest["rsi"]) if pd.notnull(latest["rsi"]) else 0.0
        sma_50 = float(latest["sma_50"]) if pd.notnull(latest["sma_50"]) else None
        
        # Signal Rule: Momentum Breakout Setup
        is_above_smas = (close_price > sma_50) if sma_50 is not None else False
        is_rvol_high = rvol >= 1.5
        is_rsi_valid = 50 <= rsi <= 75
        is_new_20d_high = close_price >= df["close"].tail(20).max() * 0.99
        
        if is_above_smas and is_rvol_high and is_rsi_valid and is_new_20d_high:
            return (
                date_str,
                symbol,
                "Momentum Breakout",
                round(close_price, 2),
                round(rvol, 2),
                round(rsi, 2)
            )
    except Exception as e:
        logger.debug("Error computing TA for %s: %s", symbol, e)
    return None

def run_screener_engine(date_str, max_workers=4):
    """
    Step 4 & 5: Load local history into Pandas, compute TA via Pandas_TA,
    and generate buy signals with multi-threaded worker support.
    """
    logger.info("Running technical analysis engine for date %s...", date_str)
    conn = get_connection()
    
    query = """
        SELECT symbol, timestamp, open, high, low, close, volume 
        FROM daily_bars 
        WHERE timestamp <= ?
        ORDER BY symbol, timestamp ASC
    """
    df_all = pd.read_sql(query, conn, params=(date_str,))
    active_tickers = pd.read_sql("SELECT symbol FROM tickers WHERE is_active = 1", conn)["symbol"].tolist()
    conn.close()

    if df_all.empty:
        logger.warning("No bars found in daily_bars on or before %s", date_str)
        return []

    active_set = set(active_tickers)
    grouped = {sym: group for sym, group in df_all.groupby("symbol") if sym in active_set}
    logger.info("Loaded historical bars for %d active universe tickers.", len(grouped))

    signals = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_single_ticker_screener, sym, df_sym, date_str): sym 
            for sym, df_sym in grouped.items()
        }
        for future in as_completed(futures):
            res = future.result()
            if res:
                signals.append(res)

    logger.info("Generated %d buy signals for %s.", len(signals), date_str)

    if signals:
        conn = get_connection()
        cur = conn.cursor()
        cur.executemany("""
            INSERT INTO buy_signals (timestamp, symbol, setup_name, close_price, rvol, rsi)
            VALUES (?, ?, ?, ?, ?, ?)
        """, signals)
        conn.commit()
        conn.close()

    return signals

if __name__ == "__main__":
    init_db()
    refresh_ticker_universe()
