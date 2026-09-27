import os
import sys
import time
import datetime
import sqlite3
import json
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
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# ── Configurable Liquidity Filter Constants ────────────────────────────────────
MIN_LAST_CLOSE = 5.0                # Minimum last close price ($5.00)
MIN_AVG_DOLLAR_VOLUME = 5000000.0   # Minimum 20-day average dollar volume ($5M)

# ── Composite Score Weights (must sum to 1.0) ──────────────────────────────────
# Score = RVOL component + RS-vs-SPY component + Breakout Proximity + Trend Strength
WEIGHT_RVOL              = 0.25
WEIGHT_RS                = 0.30
WEIGHT_BREAKOUT_PROXIMITY = 0.25
WEIGHT_TREND_STRENGTH    = 0.20

# Earnings proximity window (calendar days on each side of report date to flag)
EARNINGS_PROXIMITY_DAYS = 1


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db(schema_path="schema.sql"):
    """Initialize database tables and indexes from schema file, and perform column migrations."""
    logger.info("Initializing database from %s...", schema_path)
    conn = get_connection()
    with open(schema_path, "r") as f:
        conn.executescript(f.read())

    cur = conn.cursor()

    # Auto-migration: Ensure daily_bars has nullable source column
    cur.execute("PRAGMA table_info(daily_bars)")
    daily_bars_cols = {row[1] for row in cur.fetchall()}
    if "source" not in daily_bars_cols:
        logger.info("Migrating daily_bars table: Adding missing column source (TEXT)")
        cur.execute("ALTER TABLE daily_bars ADD COLUMN source TEXT")

    # Phase 8 Auto-migration: Ensure post-breakout performance tracking columns exist
    cur.execute("PRAGMA table_info(buy_signals)")
    existing_cols = {row[1] for row in cur.fetchall()}
    performance_columns = [
        ("price_5d", "REAL"),
        ("price_10d", "REAL"),
        ("price_20d", "REAL"),
        ("return_5d_pct", "REAL"),
        ("return_10d_pct", "REAL"),
        ("return_20d_pct", "REAL"),
    ]
    for col_name, col_type in performance_columns:
        if col_name not in existing_cols:
            logger.info("Migrating buy_signals table: Adding missing column %s (%s)", col_name, col_type)
            cur.execute(f"ALTER TABLE buy_signals ADD COLUMN {col_name} {col_type}")

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")


# ── Universe Seeding ───────────────────────────────────────────────────────────

def refresh_ticker_universe():
    """
    Step 1: Seed universe using FMP (Price > $5, Market Cap > $300M).
    Falls back to Polygon active common stocks (paginated via next_url) if FMP is restricted.
    """
    logger.info("Refreshing ticker universe...")
    tickers = []

    # Primary: FMP stock screener
    if FMP_API_KEY:
        try:
            url = (
                f"https://financialmodelingprep.com/api/v3/stock-screener"
                f"?marketCapMoreThan=300000000&priceMoreThan=5&isEtf=false"
                f"&isActivelyTraded=true&apikey={FMP_API_KEY}"
            )
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

    # Fallback: Polygon Reference Tickers (Paginated via next_url)
    if not tickers and POLYGON_API_KEY:
        logger.info("Using Polygon active common stocks to seed ticker universe (paginating)...")
        next_url = (
            f"https://api.polygon.io/v3/reference/tickers"
            f"?market=stocks&type=CS&active=true&limit=1000&apiKey={POLYGON_API_KEY}"
        )
        page_count = 0
        max_retries = 3

        while next_url:
            page_count += 1
            if "apiKey=" not in next_url:
                delimiter = "&" if "?" in next_url else "?"
                url_to_fetch = f"{next_url}{delimiter}apiKey={POLYGON_API_KEY}"
            else:
                url_to_fetch = next_url

            success = False
            for attempt in range(max_retries):
                try:
                    res = requests.get(url_to_fetch, timeout=20)
                    if res.status_code == 200:
                        data = res.json()
                        results = data.get("results", [])
                        for r in results:
                            if r.get("type") == "CS" and r.get("active", True):
                                sym = r.get("ticker", "")
                                if sym and "." not in sym:
                                    tickers.append((
                                        sym,
                                        r.get("name", sym),
                                        0.0,
                                        r.get("primary_exchange", "US"),
                                        1
                                    ))
                        next_url = data.get("next_url")
                        logger.info(
                            "Polygon universe page %d fetched (%d tickers gathered so far).",
                            page_count, len(tickers)
                        )
                        success = True
                        break
                    elif res.status_code == 429:
                        logger.warning(
                            "Polygon rate limit 429 on universe pagination (attempt %d). Waiting 12s...",
                            attempt + 1
                        )
                        time.sleep(12)
                    else:
                        logger.warning(
                            "Polygon reference API returned HTTP %d: %s",
                            res.status_code, res.text[:100]
                        )
                        next_url = None
                        break
                except Exception as e:
                    logger.error("Failed to fetch tickers from Polygon on page %d: %s", page_count, e)
                    time.sleep(3)

            if not success:
                logger.warning("Stopping Polygon universe pagination due to repeated fetch errors.")
                break

            if next_url:
                time.sleep(0.5)

        logger.info(
            "Finished Polygon reference pagination. Total tickers fetched: %d across %d pages.",
            len(tickers), page_count
        )

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
        ("SPY", "SPDR S&P 500 ETF Trust", 550000000000.0, "Index ETF", 1),
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


# ── Bar Ingestion ──────────────────────────────────────────────────────────────

def fetch_polygon_grouped_daily(date_str, max_retries=2):
    """
    Primary: Fetch entire US market in 1 API call via Polygon Grouped Daily.
    Tags bars with source='polygon'.
    """
    if not POLYGON_API_KEY:
        return []
    url = (
        f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
        f"?adjusted=true&apiKey={POLYGON_API_KEY}"
    )
    for attempt in range(max_retries):
        try:
            res = requests.get(url, timeout=20)
            if res.status_code == 200:
                results = res.json().get("results", [])
                records = []
                for r in results:
                    records.append((
                        r["T"], date_str, r["o"], r["h"], r["l"], r["c"],
                        r["v"], r.get("vw", r["c"]), "polygon"
                    ))
                return records
            elif res.status_code == 429:
                logger.warning("Polygon rate limit 429 hit on %s (attempt %d).", date_str, attempt + 1)
                if attempt < max_retries - 1:
                    time.sleep(12)
            else:
                logger.error(
                    "Polygon API error on %s: HTTP %d %s", date_str, res.status_code, res.text[:100]
                )
                break
        except Exception as e:
            logger.error("Polygon request error on %s: %s", date_str, e)
            time.sleep(3)
    return []


def fetch_alpaca_batch_daily(symbols, date_str, chunk_size=100):
    """
    Failover 1: Fetch batch daily bars for universe symbols via Alpaca Data API v2.
    Tests feed=sip first; falls back to feed=iex on rejection.
    Returns: (records, feed_used) where feed_used is 'alpaca_sip' or 'alpaca_iex'
    """
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        logger.warning("Alpaca credentials not configured for failover.")
        return [], None

    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_API_SECRET
    }

    start_iso = f"{date_str}T00:00:00Z"
    end_iso   = f"{date_str}T23:59:59Z"

    # Detect if SIP feed is allowed
    feed_to_use = "sip"
    test_chunk = symbols[:min(5, len(symbols))]
    test_sym_str = ",".join(test_chunk)
    sip_test_url = (
        f"https://data.alpaca.markets/v2/stocks/bars"
        f"?symbols={test_sym_str}&timeframe=1Day&start={start_iso}&end={end_iso}&feed=sip&limit=10"
    )
    try:
        test_res = requests.get(sip_test_url, headers=headers, timeout=10)
        if test_res.status_code == 200:
            feed_to_use = "sip"
            logger.info("Alpaca SIP feed detected and authorized. Using feed=sip for consolidated volume.")
        else:
            feed_to_use = "iex"
            logger.warning(
                "Alpaca SIP feed rejected (HTTP %d). Falling back to feed=iex (IEX-only).",
                test_res.status_code
            )
    except Exception as e:
        feed_to_use = "iex"
        logger.warning("Failed to test Alpaca SIP feed (%s). Falling back to feed=iex.", e)

    records = []
    source_tag = "alpaca_sip" if feed_to_use == "sip" else "alpaca_iex"

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        sym_str = ",".join(chunk)
        url = (
            f"https://data.alpaca.markets/v2/stocks/bars"
            f"?symbols={sym_str}&timeframe=1Day&start={start_iso}&end={end_iso}"
            f"&feed={feed_to_use}&limit=10000"
        )
        try:
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200:
                data = res.json().get("bars", {})
                for sym, bars in data.items():
                    if bars:
                        b = bars[-1]
                        records.append((
                            sym, date_str,
                            float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]),
                            int(b["v"]), float(b.get("vw", b["c"])), source_tag
                        ))
            else:
                logger.warning(
                    "Alpaca batch query returned HTTP %d for chunk %d (feed=%s)",
                    res.status_code, i // chunk_size, feed_to_use
                )
        except Exception as e:
            logger.error("Alpaca request error for chunk: %s", e)
        time.sleep(0.3)

    return records, source_tag


def fetch_yfinance_bulk_daily(symbols, date_str, chunk_size=80):
    """
    Failover 2: Fetch bulk daily bars using yfinance download.
    Tags bars with source='yfinance'.
    """
    records = []
    try:
        import yfinance as yf
        curr_d = datetime.date.fromisoformat(date_str)
        next_d = (curr_d + datetime.timedelta(days=1)).isoformat()

        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i + chunk_size]
            sym_str = " ".join(chunk)
            df = yf.download(
                sym_str, start=date_str, end=next_d,
                interval="1d", group_by="ticker", progress=False, threads=True
            )
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
                            records.append((sym, date_str, o, h, l, c, v, c, "yfinance"))
                except Exception:
                    continue
    except Exception as e:
        logger.error("yfinance bulk download error: %s", e)
    return records


def ingest_daily_bars(date_str, force_failover=False):
    """
    Step 3: Save bars and prune historical records older than 500 calendar days.
    Sequentially tests:
      1. Polygon Grouped Daily (1 API call)  -> source='polygon'
      2. Alpaca batch daily bars (Failover 1) -> source='alpaca_sip' or 'alpaca_iex'
         If alpaca_iex: yfinance is called as volume failover.
      3. yfinance bulk download (Failover 2) -> source='yfinance'
    """
    logger.info("Ingesting daily bars for date: %s (force_failover=%s)", date_str, force_failover)
    bars = []
    source_used = "Polygon Grouped Daily"

    if not force_failover:
        bars = fetch_polygon_grouped_daily(date_str)

    if not bars:
        logger.warning(
            "Primary Polygon ingestion unavailable or forced failover. "
            "Initiating Failover Sequence 1 (Alpaca)..."
        )
        conn = get_connection()
        universe_symbols = pd.read_sql("SELECT symbol FROM tickers WHERE is_active = 1", conn)["symbol"].tolist()
        conn.close()

        alpaca_bars, source_tag = fetch_alpaca_batch_daily(universe_symbols, date_str)
        if alpaca_bars:
            if source_tag == "alpaca_iex":
                logger.warning(
                    "Alpaca feed is IEX-only (source=alpaca_iex). Volume is not consolidated. "
                    "Fetching yfinance consolidated volume failover for Alpaca price bars..."
                )
                alpaca_symbols = [b[0] for b in alpaca_bars]
                yf_bars = fetch_yfinance_bulk_daily(alpaca_symbols, date_str)
                yf_vol_map = {b[0]: b[6] for b in yf_bars}

                merged_bars = []
                yf_merged_count = 0
                for b in alpaca_bars:
                    sym = b[0]
                    if sym in yf_vol_map and yf_vol_map[sym] > 0:
                        merged_bars.append((b[0], b[1], b[2], b[3], b[4], b[5], yf_vol_map[sym], b[7], "yfinance"))
                        yf_merged_count += 1
                    else:
                        merged_bars.append(b)
                bars = merged_bars
                source_used = f"Alpaca (IEX price) + yfinance consolidated volume ({yf_merged_count} merged)"
                logger.info(
                    "Consolidated volume failover merged: %d/%d bars now have yfinance consolidated volume.",
                    yf_merged_count, len(bars)
                )
            else:
                bars = alpaca_bars
                source_used = "Alpaca Batch API (SIP consolidated)"

    if not bars:
        logger.warning("Alpaca failover produced 0 bars. Initiating Failover Sequence 2 (yfinance)...")
        conn = get_connection()
        universe_symbols = pd.read_sql("SELECT symbol FROM tickers WHERE is_active = 1", conn)["symbol"].tolist()
        conn.close()
        bars = fetch_yfinance_bulk_daily(universe_symbols, date_str)
        source_used = "yfinance Bulk Download"

    if not bars:
        logger.error(
            "All ingestion pipelines (Polygon, Alpaca, yfinance) failed to retrieve bars for %s.", date_str
        )
        return 0

    conn = get_connection()
    cur = conn.cursor()
    cur.executemany("""
        INSERT OR REPLACE INTO daily_bars (symbol, timestamp, open, high, low, close, volume, vwap, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, bars)

    # Prune data older than 500 calendar days from target execution date
    cur.execute("DELETE FROM daily_bars WHERE timestamp < date(?, '-500 days')", (date_str,))
    conn.commit()
    conn.close()
    logger.info("Successfully ingested %d bars using [%s] for %s.", len(bars), source_used, date_str)
    return len(bars)


def ingest_historical_bars_for_ticker(symbol, start_date, end_date):
    """
    Ingest historical bars for an individual ticker from Polygon daily aggregates.
    Tags bars with source='polygon'.
    """
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start_date}/{end_date}"
        f"?adjusted=true&sort=asc&apiKey={POLYGON_API_KEY}"
    )
    for _ in range(3):
        try:
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                results = res.json().get("results", [])
                records = []
                for r in results:
                    d_str = datetime.datetime.fromtimestamp(
                        r["t"] / 1000.0, datetime.timezone.utc
                    ).strftime("%Y-%m-%d")
                    records.append((
                        symbol, d_str, r["o"], r["h"], r["l"], r["c"],
                        r["v"], r.get("vw", r["c"]), "polygon"
                    ))
                if records:
                    conn = get_connection()
                    cur = conn.cursor()
                    cur.executemany("""
                        INSERT OR REPLACE INTO daily_bars (symbol, timestamp, open, high, low, close, volume, vwap, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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


# ── Market Regime ──────────────────────────────────────────────────────────────

def check_market_regime(date_str, conn=None):
    """
    Phase 7: Strategy Quality Filter - Market Regime Check.
    Returns 'Bullish', 'Caution', or 'Unknown'.
    Also returns additional regime detail fields used for regime.json export.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        query = """
            SELECT timestamp, close
            FROM daily_bars
            WHERE symbol = 'SPY' AND timestamp <= ?
            ORDER BY timestamp ASC
        """
        spy_df = pd.read_sql(query, conn, params=(date_str,))
        if len(spy_df) < 50:
            logger.warning(
                "Market regime check: insufficient SPY bars (%d < 50) on or before %s",
                len(spy_df), date_str
            )
            return "Unknown"

        spy_df["sma_50"]  = ta.sma(spy_df["close"], length=50)
        spy_df["sma_200"] = ta.sma(spy_df["close"], length=200)

        latest = spy_df.iloc[-1]
        close  = float(latest["close"])
        sma_50  = float(latest["sma_50"])  if pd.notnull(latest["sma_50"])  else None
        sma_200 = float(latest["sma_200"]) if pd.notnull(latest["sma_200"]) else None

        if sma_50 is None:
            return "Unknown"

        if sma_200 is not None:
            regime = "Bullish" if (close >= sma_50 and close >= sma_200) else "Caution"
        else:
            regime = "Bullish" if close >= sma_50 else "Caution"

        logger.info(
            "Market Regime on %s: %s (SPY Close=%.2f, SMA50=%.2f%s)",
            date_str, regime, close, sma_50,
            f", SMA200={sma_200:.2f}" if sma_200 is not None else ""
        )
        return regime
    except Exception as e:
        logger.error("Error computing market regime for %s: %s", date_str, e)
        return "Unknown"
    finally:
        if close_conn:
            conn.close()


def compute_regime_detail(date_str, grouped, conn=None):
    """
    Compute rich market regime metrics for regime.json:
      - spy_close, spy_sma_200, spy_pct_vs_sma200
      - regime label
      - pct_universe_above_sma50: % of active universe tickers with close > 50-SMA
    Returns a dict.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        # SPY metrics
        spy_query = """
            SELECT timestamp, close
            FROM daily_bars
            WHERE symbol = 'SPY' AND timestamp <= ?
            ORDER BY timestamp ASC
        """
        spy_df = pd.read_sql(spy_query, conn, params=(date_str,))

        spy_close      = None
        spy_sma_200    = None
        spy_pct_vs_200 = None
        regime         = "Unknown"

        if len(spy_df) >= 50:
            spy_df["sma_50"]  = ta.sma(spy_df["close"], length=50)
            spy_df["sma_200"] = ta.sma(spy_df["close"], length=200)
            latest    = spy_df.iloc[-1]
            spy_close = round(float(latest["close"]), 2)
            sma_50    = float(latest["sma_50"])  if pd.notnull(latest["sma_50"])  else None
            sma_200   = float(latest["sma_200"]) if pd.notnull(latest["sma_200"]) else None
            spy_sma_200 = round(sma_200, 2) if sma_200 is not None else None
            if sma_200 and sma_200 > 0:
                spy_pct_vs_200 = round(((spy_close - sma_200) / sma_200) * 100.0, 2)
            if sma_50 is not None:
                if sma_200 is not None:
                    regime = "Bullish" if (spy_close >= sma_50 and spy_close >= sma_200) else "Caution"
                else:
                    regime = "Bullish" if spy_close >= sma_50 else "Caution"

        # Universe breadth: % of tickers with close > 50-SMA
        above_50 = 0
        total    = 0
        for sym, df_sym in grouped.items():
            if sym.upper() == "SPY":
                continue
            if len(df_sym) < 50:
                continue
            try:
                sma50_series = ta.sma(df_sym["close"], length=50)
                last_close   = float(df_sym["close"].iloc[-1])
                last_sma50   = float(sma50_series.iloc[-1]) if pd.notnull(sma50_series.iloc[-1]) else None
                if last_sma50 is not None:
                    total += 1
                    if last_close > last_sma50:
                        above_50 += 1
            except Exception:
                continue

        pct_above_50 = round((above_50 / total) * 100.0, 1) if total > 0 else None

        return {
            "date":                   date_str,
            "regime":                 regime,
            "spy_close":              spy_close,
            "spy_sma_200":            spy_sma_200,
            "spy_pct_vs_sma200":      spy_pct_vs_200,
            "pct_universe_above_sma50": pct_above_50,
            "universe_breadth_n":     total,
            "generated_at":           datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error("Error computing regime detail for %s: %s", date_str, e)
        return {
            "date": date_str, "regime": "Unknown",
            "spy_close": None, "spy_sma_200": None,
            "spy_pct_vs_sma200": None,
            "pct_universe_above_sma50": None, "universe_breadth_n": 0,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
    finally:
        if close_conn:
            conn.close()


# ── Earnings Calendar ──────────────────────────────────────────────────────────

def fetch_finnhub_earnings_calendar(from_date, to_date, cache_dir=".cache"):
    """
    Fetch earnings calendar from Finnhub within [from_date, to_date].
    Caches results locally to handle rate limits and avoid repeated network calls.
    Returns: dict mapping symbol -> {'date': YYYY-MM-DD, 'hour': bmo/amc/'', ...}
    """
    if not FINNHUB_API_KEY:
        logger.warning("Finnhub API key not configured; skipping earnings calendar check.")
        return {}

    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"earnings_{from_date}_{to_date}.json")

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info("Loaded %d earnings calendar records from cache (%s).", len(data), cache_file)
                return data
        except Exception as e:
            logger.warning("Failed to read earnings cache %s: %s", cache_file, e)

    url = (
        f"https://finnhub.io/api/v1/calendar/earnings"
        f"?from={from_date}&to={to_date}&token={FINNHUB_API_KEY}"
    )
    for attempt in range(3):
        try:
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                calendar_items = res.json().get("earningsCalendar", [])
                earnings_map = {}
                for item in calendar_items:
                    sym = item.get("symbol")
                    if sym:
                        earnings_map[sym.upper()] = {
                            "date":    item.get("date"),
                            "hour":    item.get("hour", ""),
                            "quarter": item.get("quarter"),
                            "year":    item.get("year")
                        }
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(earnings_map, f, indent=2)
                except Exception as e:
                    logger.warning("Failed to write earnings cache: %s", e)
                logger.info(
                    "Fetched and cached %d earnings events from Finnhub (%s to %s).",
                    len(earnings_map), from_date, to_date
                )
                return earnings_map
            elif res.status_code == 429:
                logger.warning("Finnhub 429 rate limit (attempt %d). Waiting 3s...", attempt + 1)
                time.sleep(3)
            else:
                logger.warning(
                    "Finnhub earnings calendar returned HTTP %d: %s",
                    res.status_code, res.text[:100]
                )
                break
        except Exception as e:
            logger.warning("Finnhub earnings request error (attempt %d): %s", attempt + 1, e)
            time.sleep(2)

    logger.warning("Gracefully proceeding without Finnhub earnings calendar data.")
    return {}


def _is_near_earnings(symbol, date_str, earnings_map, trading_days_series, proximity_days=EARNINGS_PROXIMITY_DAYS):
    """
    Return (near_earnings: bool, earnings_date: str|None) for a given symbol.
    'Near' = earnings report is within `proximity_days` trading sessions of date_str.
    Uses the trading_days_series (sorted list of known trading-day strings) for proximity.
    """
    if not earnings_map or symbol not in earnings_map:
        return False, None

    earnings_date = earnings_map[symbol].get("date")
    if not earnings_date:
        return False, None

    try:
        # Build index lookup for fast proximity test
        td_list = sorted(trading_days_series)
        if date_str not in td_list or earnings_date not in td_list:
            # Fall back to calendar-day proximity
            signal_dt   = datetime.date.fromisoformat(date_str)
            earnings_dt = datetime.date.fromisoformat(earnings_date)
            diff = abs((earnings_dt - signal_dt).days)
            near = diff <= proximity_days * 2  # rough calendar-day approximation
            return near, earnings_date

        idx_signal   = td_list.index(date_str)
        idx_earnings = td_list.index(earnings_date)
        near = abs(idx_earnings - idx_signal) <= proximity_days
        return near, earnings_date
    except Exception:
        return False, None


# ── RS Scores & Signal Streak ──────────────────────────────────────────────────

def calculate_universe_rs_scores(grouped, date_str):
    """
    Phase 7: Relative Strength (RS) Score Calculation.
    Computes 63-day percentage price performance for each active ticker vs the universe.
    Ranks using pd.Series.rank(pct=True) * 99 -> 0-99 percentile.
    SPY is excluded from ranking.
    Returns: dict mapping symbol -> rs_score (float, 0-99)
    """
    perf_dict = {}
    for sym, df_sym in grouped.items():
        if sym.upper() == "SPY":
            continue
        if len(df_sym) < 50:
            continue
        try:
            latest_close = float(df_sym["close"].iloc[-1])
            base_close   = float(df_sym["close"].iloc[-63]) if len(df_sym) >= 63 else float(df_sym["close"].iloc[0])
            if base_close > 0:
                pct_change = ((latest_close - base_close) / base_close) * 100.0
                perf_dict[sym] = pct_change
        except Exception:
            continue

    if not perf_dict:
        return {}
    if len(perf_dict) == 1:
        only_sym = next(iter(perf_dict))
        return {only_sym: 99.0}

    s = pd.Series(perf_dict)
    rs_ranks = (s.rank(pct=True) * 99.0).round(1)
    rs_scores = rs_ranks.to_dict()
    logger.info("Computed RS scores for %d universe tickers on %s.", len(rs_scores), date_str)
    return rs_scores


def calculate_signal_streak(symbol, current_date, conn=None):
    """
    Computes consecutive trading days up to and including current_date that symbol
    triggered a buy signal.  Returns 1 for a brand-new signal.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT timestamp
            FROM buy_signals
            WHERE symbol = ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 50
        """, (symbol, current_date))
        signal_dates = [r[0] for r in cur.fetchall()]
        if not signal_dates:
            return 1

        if current_date not in signal_dates:
            signal_dates.insert(0, current_date)

        cur.execute("""
            SELECT DISTINCT timestamp
            FROM daily_bars
            WHERE timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 50
        """, (current_date,))
        trading_days = [r[0] for r in cur.fetchall()]

        streak = 0
        sig_date_set = set(signal_dates)
        for t_day in trading_days:
            if t_day in sig_date_set:
                streak += 1
            else:
                break
        return max(1, streak)
    except Exception as e:
        logger.warning("Error calculating signal streak for %s on %s: %s", symbol, current_date, e)
        return 1
    finally:
        if close_conn:
            conn.close()


# ── Composite Score ────────────────────────────────────────────────────────────

def compute_composite_score(rvol, rs_score, proximity_pct, trend_strength_pct):
    """
    Compute a 0-100 composite signal quality score.

    Components (each normalised to 0-100 before weighting):
      - RVOL component:            RVOL clipped to [1.0, 5.0], scaled to 0-100
      - RS vs SPY component:       rs_score already 0-99, pass through as 0-100
      - Breakout Proximity:        0=far, 100=at 20-day high (proximity_pct already 0-100)
      - Trend Strength:            % of MAs aligned below price (0, 33, 67, 100)

    Returns (composite_score: float, components: dict)
    """
    # RVOL: clip to [1, 5], map linearly to 0-100
    rvol_clamped  = max(1.0, min(float(rvol or 1.0), 5.0))
    rvol_score    = ((rvol_clamped - 1.0) / 4.0) * 100.0

    # RS score: already 0-99, treat as 0-100
    rs_clamped    = max(0.0, min(float(rs_score or 0.0), 100.0))

    # Breakout proximity: 0-100 passed directly
    prox_clamped  = max(0.0, min(float(proximity_pct or 0.0), 100.0))

    # Trend strength: 0-100 passed directly
    trend_clamped = max(0.0, min(float(trend_strength_pct or 0.0), 100.0))

    composite = (
        WEIGHT_RVOL              * rvol_score  +
        WEIGHT_RS                * rs_clamped  +
        WEIGHT_BREAKOUT_PROXIMITY * prox_clamped +
        WEIGHT_TREND_STRENGTH    * trend_clamped
    )
    composite = round(composite, 1)

    components = {
        "rvol_score":             round(rvol_score, 1),
        "rs_score_component":     round(rs_clamped, 1),
        "breakout_proximity_score": round(prox_clamped, 1),
        "trend_strength_score":   round(trend_clamped, 1),
        "weights": {
            "rvol":              WEIGHT_RVOL,
            "rs":                WEIGHT_RS,
            "breakout_proximity": WEIGHT_BREAKOUT_PROXIMITY,
            "trend_strength":    WEIGHT_TREND_STRENGTH
        }
    }
    return composite, components


# ── Core TA Engine ─────────────────────────────────────────────────────────────

def process_single_ticker_screener(
    symbol, df_symbol, date_str,
    rs_score=None, market_regime="Unknown",
    spy_return_63d=None, earnings_map=None, trading_days=None
):
    """
    Compute technical indicators for a single symbol and evaluate Momentum Breakout rules.

    New computed fields added to details JSON:
      - pct_change_1d:         today's % change vs prior close
      - atr_pct:               ATR(14) as % of close price
      - avg_dollar_vol_20d:    20-day average dollar volume
      - rs_vs_spy:             63-day return minus SPY's 63-day return (percentage points)
      - dist_to_52w_high_pct:  % distance below 52-week high (negative = below, 0 = at high)
      - near_earnings:         bool — report within EARNINGS_PROXIMITY_DAYS trading days
      - earnings_date:         YYYY-MM-DD of upcoming/recent report (or null)
      - composite_score:       0-100 signal quality score
      - score_components:      breakdown of composite score inputs
    """
    if len(df_symbol) < 50:
        return None

    try:
        df = df_symbol.copy()
        latest = df.iloc[-1]
        close_price = float(latest["close"])

        # Reject unconsolidated IEX volume bars
        bar_source = str(latest.get("source") or "").lower()
        if bar_source == "alpaca_iex":
            logger.info("Ticker %s skipped: latest bar source is alpaca_iex (unconsolidated volume).", symbol)
            return None

        # Liquidity Filter 1: close >= $5
        if close_price < MIN_LAST_CLOSE:
            return None

        # Liquidity Filter 2: 20-day average dollar volume >= $5M
        dollar_vol = df["close"] * df["volume"]
        avg_dollar_vol_20 = float(dollar_vol.tail(20).mean())
        if avg_dollar_vol_20 < MIN_AVG_DOLLAR_VOLUME:
            return None

        # ── Technical Indicators ──────────────────────────────────────────────
        df["sma_200"] = ta.sma(df["close"], length=200)
        df["sma_50"]  = ta.sma(df["close"], length=50)
        df["ema_20"]  = ta.ema(df["close"], length=20)
        df["rsi"]     = ta.rsi(df["close"], length=14)
        df["atr"]     = ta.atr(df["high"], df["low"], df["close"], length=14)

        # Lookahead-free RVOL: compare today's volume against prior 20-day SMA
        vol_sma_20_prior = ta.sma(df["volume"], length=20).shift(1)
        df["rvol"] = df["volume"] / vol_sma_20_prior

        latest_calc = df.iloc[-1]
        rvol    = float(latest_calc["rvol"]) if pd.notnull(latest_calc["rvol"]) else 0.0
        rsi     = float(latest_calc["rsi"])  if pd.notnull(latest_calc["rsi"])  else 0.0
        sma_200 = float(latest_calc["sma_200"]) if pd.notnull(latest_calc["sma_200"]) else None
        sma_50  = float(latest_calc["sma_50"])  if pd.notnull(latest_calc["sma_50"])  else None
        ema_20  = float(latest_calc["ema_20"])  if pd.notnull(latest_calc["ema_20"])  else None
        atr_val = float(latest_calc["atr"])     if pd.notnull(latest_calc["atr"])     else None

        # ── Signal Rules ──────────────────────────────────────────────────────
        is_above_smas = (close_price > sma_50) if sma_50 is not None else False
        is_rvol_high  = rvol >= 1.5
        is_rsi_valid  = 50 <= rsi <= 75

        # Lookahead-free 20-day high: exclude today's bar
        prior_20_bars = df.iloc[:-1].tail(20)
        if len(prior_20_bars) < 20:
            return None
        prior_20d_high    = float(prior_20_bars["close"].max())
        is_new_20d_high   = close_price >= (prior_20d_high * 0.99)

        if not (is_above_smas and is_rvol_high and is_rsi_valid and is_new_20d_high):
            return None

        # Phase 7 Quality Filter: RS Score >= 70
        if rs_score is not None and rs_score < 70.0:
            logger.info(
                "Ticker %s passed technical breakout but filtered out by RS score: %.1f < 70",
                symbol, rs_score
            )
            return None

        # ── New Computed Fields ───────────────────────────────────────────────

        # 1. % change today vs prior close
        pct_change_1d = None
        if len(df) >= 2:
            prior_close = float(df["close"].iloc[-2])
            if prior_close > 0:
                pct_change_1d = round(((close_price - prior_close) / prior_close) * 100.0, 2)

        # 2. ATR(14) as % of close
        atr_pct = None
        if atr_val is not None and close_price > 0:
            atr_pct = round((atr_val / close_price) * 100.0, 2)

        # 3. 20-day avg dollar volume (already computed above)
        avg_dollar_vol_20d = round(avg_dollar_vol_20, 0)

        # 4. RS vs SPY: 63-day return minus SPY's 63-day return
        rs_vs_spy = None
        if spy_return_63d is not None and len(df) >= 63:
            try:
                ticker_return_63d = ((float(df["close"].iloc[-1]) - float(df["close"].iloc[-63])) /
                                     float(df["close"].iloc[-63])) * 100.0
                rs_vs_spy = round(ticker_return_63d - spy_return_63d, 2)
            except Exception:
                pass
        elif spy_return_63d is not None and len(df) >= 2:
            try:
                ticker_return_63d = ((float(df["close"].iloc[-1]) - float(df["close"].iloc[0])) /
                                     float(df["close"].iloc[0])) * 100.0
                rs_vs_spy = round(ticker_return_63d - spy_return_63d, 2)
            except Exception:
                pass

        # 5. Distance to 52-week high (as % — negative means below high)
        dist_to_52w_high_pct = None
        bars_52w = df.tail(252)  # ~252 trading days in a year
        if len(bars_52w) > 0:
            high_52w = float(bars_52w["high"].max())
            if high_52w > 0:
                dist_to_52w_high_pct = round(((close_price - high_52w) / high_52w) * 100.0, 2)

        # 6. Earnings proximity flag
        near_earnings  = False
        earnings_date  = None
        if earnings_map and trading_days is not None:
            near_earnings, earnings_date = _is_near_earnings(
                symbol, date_str, earnings_map, trading_days
            )

        # 7. MA alignment
        ma_alignment = {
            "above_ema20":  (close_price > ema_20)  if ema_20  is not None else None,
            "above_sma50":  (close_price > sma_50)  if sma_50  is not None else None,
            "above_sma200": (close_price > sma_200) if sma_200 is not None else None,
            "ema20":   round(ema_20,  2) if ema_20  is not None else None,
            "sma50":   round(sma_50,  2) if sma_50  is not None else None,
            "sma200":  round(sma_200, 2) if sma_200 is not None else None
        }

        # 8. Composite score components
        # Breakout proximity: how close close_price is to the 20-day high (0-100)
        if prior_20d_high > 0:
            breakout_proximity_pct = max(0.0, min(100.0, (close_price / prior_20d_high) * 100.0 - 99.0))
        else:
            breakout_proximity_pct = 0.0

        # Trend strength: % of the 3 MAs that are below the close price (0, 33, 67, 100)
        ma_vals   = [ema_20, sma_50, sma_200]
        ma_active = [m for m in ma_vals if m is not None]
        trend_strength_pct = (sum(1 for m in ma_active if close_price > m) / len(ma_active)) * 100.0 if ma_active else 0.0

        composite_score, score_components = compute_composite_score(
            rvol, rs_score, breakout_proximity_pct, trend_strength_pct
        )

        details_json = json.dumps({
            "ma_alignment":         ma_alignment,
            "market_regime":        market_regime,
            "rs_score":             round(rs_score, 1) if rs_score is not None else None,
            # New fields
            "pct_change_1d":        pct_change_1d,
            "atr_pct":              atr_pct,
            "avg_dollar_vol_20d":   avg_dollar_vol_20d,
            "rs_vs_spy":            rs_vs_spy,
            "dist_to_52w_high_pct": dist_to_52w_high_pct,
            "near_earnings":        near_earnings,
            "earnings_date":        earnings_date,
            "composite_score":      composite_score,
            "score_components":     score_components
        })
        return (
            date_str,
            symbol,
            "Momentum Breakout",
            round(close_price, 2),
            round(rvol, 2),
            round(rsi, 2),
            details_json
        )
    except Exception as e:
        logger.debug("Error computing TA for %s: %s", symbol, e)
    return None


# ── Screener Engine ────────────────────────────────────────────────────────────

def run_screener_engine(date_str, max_workers=4):
    """
    Step 4 & 5: Load local history into Pandas, compute TA via Pandas_TA,
    apply quality filters, generate buy signals, and calculate signal_streak.
    """
    logger.info("Running technical analysis engine for date %s...", date_str)
    conn = get_connection()

    query = """
        SELECT symbol, timestamp, open, high, low, close, volume, source
        FROM daily_bars
        WHERE timestamp <= ?
        ORDER BY symbol, timestamp ASC
    """
    df_all = pd.read_sql(query, conn, params=(date_str,))
    active_tickers = pd.read_sql("SELECT symbol FROM tickers WHERE is_active = 1", conn)["symbol"].tolist()

    if df_all.empty:
        conn.close()
        logger.warning("No bars found in daily_bars on or before %s", date_str)
        return []

    active_set = set(active_tickers)
    grouped    = {sym: group for sym, group in df_all.groupby("symbol") if sym in active_set}
    logger.info("Loaded historical bars for %d active universe tickers.", len(grouped))

    # Phase 7: Compute Market Regime & Universe RS Scores
    market_regime = check_market_regime(date_str, conn=conn)
    rs_scores     = calculate_universe_rs_scores(grouped, date_str)
    conn.close()

    # Compute SPY 63-day return for RS-vs-SPY field
    spy_return_63d = None
    if "SPY" in grouped:
        spy_df = grouped["SPY"]
        if len(spy_df) >= 2:
            try:
                n = min(63, len(spy_df))
                spy_return_63d = (
                    (float(spy_df["close"].iloc[-1]) - float(spy_df["close"].iloc[-n])) /
                    float(spy_df["close"].iloc[-n])
                ) * 100.0
            except Exception:
                pass

    # Known trading days for earnings proximity calculation
    all_trading_days = sorted(df_all["timestamp"].unique().tolist())

    # Fetch earnings calendar for a ±7 calendar-day window around date_str
    try:
        target_dt   = datetime.date.fromisoformat(date_str)
        earn_from   = (target_dt - datetime.timedelta(days=7)).isoformat()
        earn_to     = (target_dt + datetime.timedelta(days=7)).isoformat()
        earnings_map = fetch_finnhub_earnings_calendar(earn_from, earn_to)
    except Exception as e:
        logger.warning("Could not fetch earnings calendar: %s", e)
        earnings_map = {}

    signals = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_single_ticker_screener,
                sym,
                df_sym,
                date_str,
                rs_scores.get(sym),
                market_regime,
                spy_return_63d,
                earnings_map,
                all_trading_days
            ): sym
            for sym, df_sym in grouped.items()
        }
        for future in as_completed(futures):
            res = future.result()
            if res:
                signals.append(res)

    logger.info("Generated %d buy signals passing quality filters for %s.", len(signals), date_str)

    if signals:
        conn = get_connection()
        cur  = conn.cursor()
        cur.executemany("""
            INSERT INTO buy_signals (timestamp, symbol, setup_name, close_price, rvol, rsi, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, signals)
        conn.commit()

        # Attach signal_streak to each signal's details in-place after DB insert
        # (streak requires the row to exist first so the current day counts)
        cur.execute("""
            SELECT id, symbol, timestamp, details FROM buy_signals WHERE timestamp = ?
        """, (date_str,))
        inserted_rows = cur.fetchall()
        for sig_id, sym, ts, det in inserted_rows:
            streak = calculate_signal_streak(sym, ts, conn=conn)
            try:
                det_dict = json.loads(det) if det else {}
            except Exception:
                det_dict = {}
            det_dict["signal_streak"] = streak
            cur.execute(
                "UPDATE buy_signals SET details = ? WHERE id = ?",
                (json.dumps(det_dict), sig_id)
            )
        conn.commit()
        conn.close()

    return signals


# ── Post-Breakout Performance ──────────────────────────────────────────────────

def update_post_breakout_performance(conn=None):
    """
    Phase 8: Post-Breakout Performance Tracking Engine.
    Scans buy_signals for records missing price_5d, price_10d, or price_20d
    and fills them from subsequent bars in daily_bars.
    Returns: dict summary of tracked signals and win rates.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, symbol, timestamp, close_price, price_5d, price_10d, price_20d,
                   return_5d_pct, return_10d_pct, return_20d_pct
            FROM buy_signals
            WHERE price_5d IS NULL OR price_10d IS NULL OR price_20d IS NULL
        """)
        pending_signals = cur.fetchall()
        logger.info("Checking post-breakout performance updates for %d pending signals...", len(pending_signals))

        updated_count = 0
        for row in pending_signals:
            sig_id, sym, sig_ts, sig_close, p5, p10, p20, ret5, ret10, ret20 = row
            if sig_close is None or sig_close <= 0:
                continue

            cur.execute("""
                SELECT timestamp, close
                FROM daily_bars
                WHERE symbol = ? AND timestamp > ?
                ORDER BY timestamp ASC
            """, (sym, sig_ts))
            subsequent_bars = cur.fetchall()
            num_subsequent  = len(subsequent_bars)

            new_p5, new_ret5    = p5,  ret5
            new_p10, new_ret10  = p10, ret10
            new_p20, new_ret20  = p20, ret20
            has_new_data = False

            if new_p5 is None and num_subsequent >= 5:
                bar_5    = subsequent_bars[4]
                new_p5   = round(float(bar_5[1]), 2)
                new_ret5 = round(((new_p5 - sig_close) / sig_close) * 100.0, 2)
                has_new_data = True

            if new_p10 is None and num_subsequent >= 10:
                bar_10    = subsequent_bars[9]
                new_p10   = round(float(bar_10[1]), 2)
                new_ret10 = round(((new_p10 - sig_close) / sig_close) * 100.0, 2)
                has_new_data = True

            if new_p20 is None and num_subsequent >= 20:
                bar_20    = subsequent_bars[19]
                new_p20   = round(float(bar_20[1]), 2)
                new_ret20 = round(((new_p20 - sig_close) / sig_close) * 100.0, 2)
                has_new_data = True

            if has_new_data:
                cur.execute("""
                    UPDATE buy_signals
                    SET price_5d=?, return_5d_pct=?, price_10d=?, return_10d_pct=?,
                        price_20d=?, return_20d_pct=?
                    WHERE id=?
                """, (new_p5, new_ret5, new_p10, new_ret10, new_p20, new_ret20, sig_id))
                updated_count += 1

        if updated_count > 0:
            conn.commit()
            logger.info("Successfully updated post-breakout metrics for %d signals.", updated_count)
        else:
            logger.info("No pending signals reached new post-breakout maturity thresholds.")

        summary = get_performance_summary(conn=conn)
        return summary
    finally:
        if close_conn:
            conn.close()


def get_performance_summary(conn=None):
    """
    Phase 8: Aggregate win rates, average returns, and sample counts for 5d, 10d, and 20d horizons.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        df = pd.read_sql("SELECT return_5d_pct, return_10d_pct, return_20d_pct FROM buy_signals", conn)

        def calc_horizon_stats(series):
            valid = series.dropna()
            count = len(valid)
            if count == 0:
                return {"count": 0, "win_rate": None, "avg_return": None, "max_return": None, "min_return": None}
            wins     = int((valid > 0).sum())
            win_rate = round((wins / count) * 100.0, 1)
            return {
                "count":      int(count),
                "win_rate":   win_rate,
                "avg_return": round(float(valid.mean()), 2),
                "max_return": round(float(valid.max()),  2),
                "min_return": round(float(valid.min()),  2)
            }

        return {
            "total_signals": len(df),
            "horizon_5d":    calc_horizon_stats(df["return_5d_pct"]),
            "horizon_10d":   calc_horizon_stats(df["return_10d_pct"]),
            "horizon_20d":   calc_horizon_stats(df["return_20d_pct"]),
            "generated_at":  datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
    finally:
        if close_conn:
            conn.close()


# ── Web Export ─────────────────────────────────────────────────────────────────

def export_web_data(output_dir="public/data", bars_limit=250):
    """
    Phase 5+: Export static JSON payloads for web visualization.
      1. latest_signals.json  — all signals with all new fields from details JSON
      2. signal_bars.json     — historical OHLCV bars for signal tickers
      3. performance_summary.json — aggregate win-rate stats
      4. regime.json          — market regime + SPY vs SMA200 + % universe above SMA50
    """
    logger.info("Exporting web visualization payloads to %s...", output_dir)
    os.makedirs(output_dir, exist_ok=True)

    conn = get_connection()

    # 1. latest_signals.json — full signal list
    query_signals = """
        SELECT
            b.id, b.timestamp, b.symbol, b.setup_name, b.close_price,
            b.rvol, b.rsi, b.details, b.created_at,
            t.name, t.market_cap, t.sector,
            b.price_5d, b.price_10d, b.price_20d,
            b.return_5d_pct, b.return_10d_pct, b.return_20d_pct
        FROM buy_signals b
        LEFT JOIN tickers t ON b.symbol = t.symbol
        ORDER BY b.timestamp DESC, b.rvol DESC
    """
    cur = conn.cursor()
    cur.execute(query_signals)
    raw_signals = cur.fetchall()

    signals_list  = []
    signal_symbols = set()

    for row in raw_signals:
        (
            sig_id, timestamp, symbol, setup_name, close_price,
            rvol, rsi, details, created_at,
            ticker_name, market_cap, sector,
            p5, p10, p20, ret5, ret10, ret20
        ) = row
        signal_symbols.add(symbol)

        parsed_details = {}
        if details:
            try:
                parsed_details = json.loads(details) if isinstance(details, str) else details
            except Exception:
                parsed_details = {}

        ma_alignment = parsed_details.get("ma_alignment") or {
            "above_ema20": None, "above_sma50": True, "above_sma200": None,
            "ema20": None, "sma50": None, "sma200": None
        }

        signal_obj = {
            "id":           sig_id,
            "timestamp":    timestamp,
            "symbol":       symbol,
            "name":         ticker_name or symbol,
            "sector":       sector or "Unknown",
            "market_cap":   market_cap,
            "setup_name":   setup_name,
            "close_price":  float(close_price) if close_price is not None else None,
            "rvol":         float(rvol) if rvol is not None else None,
            "rsi":          float(rsi)  if rsi  is not None else None,
            "market_regime":   parsed_details.get("market_regime", "Unknown"),
            "rs_score":        float(parsed_details["rs_score"]) if parsed_details.get("rs_score") is not None else None,
            "ma_alignment":    ma_alignment,
            # ── Performance ──────────────────────────────────────────────────
            "price_5d":        float(p5)    if p5    is not None else None,
            "price_10d":       float(p10)   if p10   is not None else None,
            "price_20d":       float(p20)   if p20   is not None else None,
            "return_5d_pct":   float(ret5)  if ret5  is not None else None,
            "return_10d_pct":  float(ret10) if ret10 is not None else None,
            "return_20d_pct":  float(ret20) if ret20 is not None else None,
            # ── New computed fields ───────────────────────────────────────────
            "pct_change_1d":        parsed_details.get("pct_change_1d"),
            "atr_pct":              parsed_details.get("atr_pct"),
            "avg_dollar_vol_20d":   parsed_details.get("avg_dollar_vol_20d"),
            "rs_vs_spy":            parsed_details.get("rs_vs_spy"),
            "dist_to_52w_high_pct": parsed_details.get("dist_to_52w_high_pct"),
            "near_earnings":        parsed_details.get("near_earnings", False),
            "earnings_date":        parsed_details.get("earnings_date"),
            "composite_score":      parsed_details.get("composite_score"),
            "score_components":     parsed_details.get("score_components"),
            "signal_streak":        parsed_details.get("signal_streak", 1),
            "created_at":           created_at
        }
        signals_list.append(signal_obj)

    latest_signals_file = os.path.join(output_dir, "latest_signals.json")
    with open(latest_signals_file, "w", encoding="utf-8") as f:
        json.dump(signals_list, f, indent=2)
    logger.info("Saved %d signals to %s", len(signals_list), latest_signals_file)

    # 2. signal_bars.json
    signal_bars_data = {}
    if signal_symbols:
        placeholders = ",".join(["?"] * len(signal_symbols))
        query_bars = f"""
            SELECT symbol, timestamp, open, high, low, close, volume, vwap
            FROM daily_bars
            WHERE symbol IN ({placeholders})
            ORDER BY symbol, timestamp ASC
        """
        cur.execute(query_bars, list(signal_symbols))
        bar_rows = cur.fetchall()

        bars_by_symbol = {}
        for b_row in bar_rows:
            sym, ts, o, h, l, c, v, vw = b_row
            bars_by_symbol.setdefault(sym, []).append({
                "timestamp": ts,
                "open":   round(o,  2) if o  is not None else None,
                "high":   round(h,  2) if h  is not None else None,
                "low":    round(l,  2) if l  is not None else None,
                "close":  round(c,  2) if c  is not None else None,
                "volume": int(v)       if v  is not None else 0,
                "vwap":   round(vw, 2) if vw is not None else None
            })

        for sym, bars in bars_by_symbol.items():
            signal_bars_data[sym] = bars[-bars_limit:]

    signal_bars_file = os.path.join(output_dir, "signal_bars.json")
    with open(signal_bars_file, "w", encoding="utf-8") as f:
        json.dump(signal_bars_data, f, indent=2)
    logger.info("Saved historical bars for %d tickers to %s", len(signal_bars_data), signal_bars_file)

    # 3. performance_summary.json
    perf_summary = get_performance_summary(conn=conn)
    perf_summary_file = os.path.join(output_dir, "performance_summary.json")
    with open(perf_summary_file, "w", encoding="utf-8") as f:
        json.dump(perf_summary, f, indent=2)
    logger.info("Saved performance summary to %s", perf_summary_file)

    # 4. regime.json — load the most-recent bar date's grouped data for breadth calc
    try:
        query_latest_date = "SELECT MAX(timestamp) FROM daily_bars"
        latest_bar_date = conn.execute(query_latest_date).fetchone()[0]
        if latest_bar_date:
            regime_df = pd.read_sql("""
                SELECT symbol, timestamp, close, high, low
                FROM daily_bars
                WHERE timestamp <= ?
                ORDER BY symbol, timestamp ASC
            """, conn, params=(latest_bar_date,))
            active_tickers_list = pd.read_sql(
                "SELECT symbol FROM tickers WHERE is_active = 1", conn
            )["symbol"].tolist()
            active_set_regime = set(active_tickers_list)
            grouped_regime = {
                sym: grp for sym, grp in regime_df.groupby("symbol")
                if sym in active_set_regime
            }
            regime_detail = compute_regime_detail(latest_bar_date, grouped_regime, conn=None)
        else:
            regime_detail = {
                "date": None, "regime": "Unknown",
                "spy_close": None, "spy_sma_200": None, "spy_pct_vs_sma200": None,
                "pct_universe_above_sma50": None, "universe_breadth_n": 0,
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
    except Exception as e:
        logger.error("Error computing regime detail for export: %s", e)
        regime_detail = {
            "date": None, "regime": "Unknown",
            "spy_close": None, "spy_sma_200": None, "spy_pct_vs_sma200": None,
            "pct_universe_above_sma50": None, "universe_breadth_n": 0,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

    regime_file = os.path.join(output_dir, "regime.json")
    with open(regime_file, "w", encoding="utf-8") as f:
        json.dump(regime_detail, f, indent=2)
    logger.info("Saved market regime detail to %s", regime_file)

    conn.close()

    return {
        "latest_signals_path":      latest_signals_file,
        "signal_bars_path":         signal_bars_file,
        "performance_summary_path": perf_summary_file,
        "regime_path":              regime_file,
        "signals_count":            len(signals_list),
        "tickers_count":            len(signal_bars_data),
        "performance_summary":      perf_summary,
        "regime":                   regime_detail
    }


if __name__ == "__main__":
    init_db()
    refresh_ticker_universe()
