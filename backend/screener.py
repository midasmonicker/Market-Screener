import os
import sys
import time
import datetime
import sqlite3
import json
import logging
import re
import xml.etree.ElementTree as ET
import csv
import io
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
EARNINGS_ENRICHMENT_MAX_LOOKUPS = 25
EARNINGS_ENRICHMENT_BUDGET_SECONDS = 45.0
NEWS_CACHE_TTL_HOURS = 4
NEWS_ENRICHMENT_MAX_LOOKUPS = 40
NEWS_ENRICHMENT_BUDGET_SECONDS = 45.0
FINNHUB_MIN_REQUEST_INTERVAL_SECONDS = 1.1
_FINNHUB_LAST_REQUEST_AT = None
SEC_USER_AGENT = "Market Screener michaelonyeweke@yahoo.com"
SEC_MIN_REQUEST_INTERVAL_SECONDS = 0.35
SEC_CIK_CACHE_TTL_DAYS = 30
SEC_INSIDER_CACHE_TTL_HOURS = 4
SEC_INSIDER_LOOKBACK_CALENDAR_DAYS = 21
SEC_INSIDER_MAX_FILINGS_PER_SYMBOL = 8
SEC_INSIDER_ENRICHMENT_BUDGET_SECONDS = 45.0
_SEC_LAST_REQUEST_AT = None
# Finnhub omits report dates in this response, so this lag is an approximation.
EARNINGS_REPORT_LAG_DAYS_DEFAULT = 35

# ── Sector Relative Strength Mapping ──────────────────────────────────────────
# Standard GICS sector ETF mapping plus SIC/Polygon fallback aliases
SECTOR_TO_ETF = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Cyclical": "XLY",       # SIC/Polygon taxonomy compatibility
    "Consumer Staples": "XLP",
    "Consumer Defensive": "XLP",      # SIC/Polygon taxonomy compatibility
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}
SECTOR_ETFS = ("XLK", "XLV", "XLF", "XLE", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC")

# ── Short Interest Ingestion Constants (FINRA bi-weekly bulk file) ─────────────
FINRA_SHORT_INTEREST_URL_BASE = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date_str}.csv"
FINRA_SHORT_INTEREST_FILES_CATALOG_URL = "https://www.finra.org/finra-data/browse-catalog/equity-short-interest/files"
FINRA_HTTP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Max Polygon ticker-details API calls per pipeline run for sector backfill
# (respects free-tier rate limit; priority symbols are always processed first)
MAX_SECTOR_FETCHES_PER_RUN = 200
SIGNAL_WINDOW_TRADING_DAYS = 10

# Exchange MIC codes that Polygon's bulk tickers endpoint puts in the `primary_exchange`
# field — these are NOT sector names and must be replaced via backfill.
_MIC_CODES = {"XNAS", "XNYS", "XASE", "BATS", "ARCX", "XCIS", "US", "Unknown"}


def _sic_to_sector(sic_code) -> str:
    """
    Map a Polygon SIC code (string or int) to a GICS-style sector label.
    Returns "Unknown" if the code is missing or unrecognised.

    SIC reference: https://www.osha.gov/data/sic-manual
    """
    if not sic_code:
        return "Unknown"
    try:
        code = int(sic_code)
    except (ValueError, TypeError):
        return "Unknown"

    # Specific overrides for high-volume tech/healthcare SIC codes
    _OVERRIDES = {
        3559: "Industrials",     # Special Industry Machinery
        3571: "Technology",     # Electronic Computers (Apple)
        3572: "Technology",     # Computer Storage Devices
        3575: "Technology",     # Computer Terminals
        3577: "Technology",     # Computer Peripheral Equipment
        3578: "Technology",     # Calculating Machines
        3579: "Technology",     # Office Machines NEC
        3661: "Communication Services",  # Telephone & Telegraph Apparatus
        3663: "Communication Services",  # Radio/TV Broadcasting Apparatus
        3669: "Communication Services",  # Communications Equipment NEC
        3672: "Technology",     # Printed Circuit Boards
        3674: "Technology",     # Semiconductors & Related Devices
        3675: "Technology",     # Electronic Capacitors
        3678: "Technology",     # Electronic Connectors
        3679: "Technology",     # Electronic Components NEC
        3812: "Industrials",    # Defense Electronics
        3825: "Technology",     # Instruments for Measuring
        3841: "Healthcare",     # Surgical & Medical Instruments
        3842: "Healthcare",     # Orthopedic, Prosthetic Appliances
        3845: "Healthcare",     # Electromedical Equipment
        4813: "Communication Services",  # Telephone Communications
        4833: "Communication Services",  # Television Broadcasting
        4841: "Communication Services",  # Cable & Other Pay Television
        4899: "Communication Services",  # Communications Services NEC
        5912: "Consumer Defensive",  # Drug Stores
        6020: "Financials",     # State commercial banks
        6022: "Financials",     # State commercial banks (member)
        6036: "Financials",     # Savings institutions, not federally chartered
        6141: "Financials",     # Personal credit institutions
        6159: "Financials",     # Federal-sponsored credit agencies
        6282: "Financials",     # Investment advice
        6311: "Financials",     # Life insurance
        6321: "Financials",     # Accident and health insurance
        6331: "Financials",     # Fire, marine & casualty insurance
        6411: "Financials",     # Insurance agents, brokers
        7370: "Technology",     # Computer Programming, Data Processing
        7371: "Technology",     # Computer Programming Services
        7372: "Technology",     # Prepackaged Software
        7373: "Technology",     # Computer Integrated Systems Design
        7374: "Technology",     # Computer Processing and Data Preparation
        7375: "Technology",     # Computer Rental & Leasing
        7377: "Technology",     # Computer Rental & Leasing NEC
        7379: "Technology",     # Computer Related Services NEC
        8000: "Healthcare",     # Health services
        8011: "Healthcare",     # Offices & clinics of medical doctors
        8049: "Healthcare",     # Offices & clinics of other health practitioners
        8051: "Healthcare",     # Skilled nursing care facilities
        8062: "Healthcare",     # General medical & surgical hospitals
        8099: "Healthcare",     # Health services NEC
    }
    if code in _OVERRIDES:
        return _OVERRIDES[code]

    # Broad range fallback
    if 100   <= code <= 999:   return "Basic Materials"      # Agriculture / Fishing
    if 1000  <= code <= 1399:  return "Energy"               # Mining, Oil & Gas
    if 1400  <= code <= 1499:  return "Basic Materials"      # Non-metallic minerals
    if 1500  <= code <= 1799:  return "Industrials"          # Construction
    if 2000  <= code <= 2099:  return "Consumer Defensive"   # Food & kindred products
    if 2100  <= code <= 2199:  return "Consumer Defensive"   # Tobacco
    if 2200  <= code <= 2399:  return "Consumer Cyclical"    # Textiles, Apparel
    if 2400  <= code <= 2499:  return "Basic Materials"      # Lumber & Wood
    if 2500  <= code <= 2599:  return "Consumer Cyclical"    # Furniture & Fixtures
    if 2600  <= code <= 2699:  return "Basic Materials"      # Paper
    if 2700  <= code <= 2799:  return "Communication Services"  # Publishing
    if 2800  <= code <= 2899:  return "Basic Materials"      # Chemicals
    if 2900  <= code <= 2999:  return "Energy"               # Petroleum refining
    if 3000  <= code <= 3199:  return "Basic Materials"      # Rubber, Plastics, Leather
    if 3200  <= code <= 3299:  return "Basic Materials"      # Stone, Clay, Glass
    if 3300  <= code <= 3399:  return "Basic Materials"      # Primary Metals
    if 3400  <= code <= 3499:  return "Industrials"          # Fabricated Metals
    if 3500  <= code <= 3599:  return "Industrials"          # Industrial Machinery
    if 3600  <= code <= 3699:  return "Technology"           # Electronic Equipment
    if 3700  <= code <= 3799:  return "Consumer Cyclical"    # Transportation Equipment (Autos)
    if 3800  <= code <= 3899:  return "Healthcare"           # Scientific Instruments
    if 3900  <= code <= 3999:  return "Consumer Cyclical"    # Misc Manufacturing
    if 4000  <= code <= 4599:  return "Industrials"          # Transportation
    if 4600  <= code <= 4699:  return "Energy"               # Pipelines
    if 4700  <= code <= 4799:  return "Industrials"          # Transport Services
    if 4800  <= code <= 4899:  return "Communication Services"  # Communications
    if 4900  <= code <= 4999:  return "Utilities"            # Electric, Gas, Water
    if 5000  <= code <= 5199:  return "Consumer Cyclical"    # Wholesale - Durable
    if 5200  <= code <= 5399:  return "Consumer Cyclical"    # Retail - General
    if 5400  <= code <= 5499:  return "Consumer Defensive"   # Retail - Food
    if 5500  <= code <= 5999:  return "Consumer Cyclical"    # Retail - Misc
    if 6000  <= code <= 6499:  return "Financials"           # Finance & Banking
    if 6500  <= code <= 6599:  return "Real Estate"          # Real Estate
    if 6600  <= code <= 6799:  return "Financials"           # Holding Companies
    if 7000  <= code <= 7099:  return "Consumer Cyclical"    # Hotels / Lodging
    if 7200  <= code <= 7299:  return "Consumer Cyclical"    # Personal Services
    if 7300  <= code <= 7399:  return "Industrials"          # Business Services
    if 7500  <= code <= 7999:  return "Consumer Cyclical"    # Entertainment / Recreation
    if 8000  <= code <= 8099:  return "Healthcare"           # Health Services
    if 8100  <= code <= 8999:  return "Industrials"          # Professional Services
    return "Unknown"



def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db(schema_path="schema.sql"):
    """Initialize database tables and indexes from schema file, and perform column migrations."""
    logger.info("Initializing database from %s...", schema_path)
    conn = get_connection()
    with open(schema_path, "r") as f:
        conn.executescript(f.read())

    cur = conn.cursor()

    cur.execute("PRAGMA table_info(tickers)")
    ticker_cols = {row[1] for row in cur.fetchall()}
    if "primary_exchange" not in ticker_cols:
        logger.info("Migrating tickers table: Adding missing column primary_exchange (TEXT)")
        cur.execute("ALTER TABLE tickers ADD COLUMN primary_exchange TEXT")

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
                                None,
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
                                        None,  # sector filled by backfill_ticker_sectors()
                                        r.get("primary_exchange"),
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
        ("AAPL", "Apple Inc.", 3000000000000.0, "Technology", None, 1),
        ("MSFT", "Microsoft Corp.", 3000000000000.0, "Technology", None, 1),
        ("NVDA", "NVIDIA Corp.", 2500000000000.0, "Technology", None, 1),
        ("AMZN", "Amazon.com Inc.", 2000000000000.0, "Consumer Cyclical", None, 1),
        ("GOOGL", "Alphabet Inc.", 2000000000000.0, "Technology", None, 1),
        ("META", "Meta Platforms Inc.", 1500000000000.0, "Technology", None, 1),
        ("TSLA", "Tesla Inc.", 800000000000.0, "Consumer Cyclical", None, 1),
        ("AMD", "Advanced Micro Devices Inc.", 300000000000.0, "Technology", None, 1),
        ("SPY", "SPDR S&P 500 ETF Trust", 550000000000.0, "Index ETF", None, 1),
        # Sector ETFs for Sector Relative Strength computation
        ("XLK", "Technology Select Sector SPDR Fund", 70000000000.0, "Sector ETF", None, 1),
        ("XLV", "Health Care Select Sector SPDR Fund", 40000000000.0, "Sector ETF", None, 1),
        ("XLF", "Financial Select Sector SPDR Fund", 45000000000.0, "Sector ETF", None, 1),
        ("XLE", "Energy Select Sector SPDR Fund", 35000000000.0, "Sector ETF", None, 1),
        ("XLY", "Consumer Discretionary Select Sector SPDR Fund", 22000000000.0, "Sector ETF", None, 1),
        ("XLP", "Consumer Staples Select Sector SPDR Fund", 18000000000.0, "Sector ETF", None, 1),
        ("XLI", "Industrial Select Sector SPDR Fund", 20000000000.0, "Sector ETF", None, 1),
        ("XLB", "Materials Select Sector SPDR Fund", 6000000000.0, "Sector ETF", None, 1),
        ("XLU", "Utilities Select Sector SPDR Fund", 17000000000.0, "Sector ETF", None, 1),
        ("XLRE", "Real Estate Select Sector SPDR Fund", 6000000000.0, "Sector ETF", None, 1),
        ("XLC", "Communication Services Select Sector SPDR Fund", 19000000000.0, "Sector ETF", None, 1),
    ]
    tickers.extend(core_stocks)

    conn = get_connection()
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO tickers (symbol, name, market_cap, sector, primary_exchange, is_active, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, date('now'))
        ON CONFLICT(symbol) DO UPDATE SET
            market_cap=CASE WHEN excluded.market_cap > 0 THEN excluded.market_cap ELSE tickers.market_cap END,
            name=excluded.name,
            sector=CASE
                WHEN excluded.sector IS NOT NULL
                     AND excluded.sector NOT IN ('XNAS','XNYS','XASE','BATS','ARCX','XCIS','US','Unknown')
                THEN excluded.sector
                ELSE tickers.sector
            END,
            primary_exchange=COALESCE(excluded.primary_exchange, tickers.primary_exchange),
            is_active=1,
            last_updated=date('now')
    """, tickers)
    conn.commit()
    conn.close()
    logger.info("Total active tickers seeded in database: %d", len(tickers))
    return len(tickers)



# ── Sector Backfill ─────────────────────────────────────────────────────────────

def backfill_ticker_sectors(
    priority_symbols=None,
    max_fetches=MAX_SECTOR_FETCHES_PER_RUN,
    target_symbols=None,
):
    """
    Phase 16: Backfill sector and listing exchange metadata.

    For every ticker whose `sector` is NULL or is an exchange MIC code (XNAS, XNYS
    etc.), fetches the ticker details from Polygon v3/reference/tickers/{sym},
    maps `sic_code` → GICS-style sector via `_sic_to_sector()`, stores Polygon's
    `primary_exchange` MIC, and persists both values to the tickers table.

    Sectors and exchanges have separate caches. `target_symbols` restricts a call
    to a requested subset, such as the current exported signal window.

    Args:
        priority_symbols: iterable of symbols to process first (e.g. buy-signal symbols)
        max_fetches:      cap on total Polygon API calls this run (default MAX_SECTOR_FETCHES_PER_RUN)

    Returns:
        int — number of tickers updated in the DB this run
    """
    if not POLYGON_API_KEY:
        logger.warning("POLYGON_API_KEY not set; cannot backfill ticker sectors.")
        return 0

    cache_dir  = ".cache"
    os.makedirs(cache_dir, exist_ok=True)
    sector_cache_file = os.path.join(cache_dir, "ticker_sectors.json")
    exchange_cache_file = os.path.join(cache_dir, "ticker_exchanges.json")

    # ── Load persistent sector cache ──────────────────────────────────────────
    sector_cache: dict = {}
    if os.path.exists(sector_cache_file):
        try:
            with open(sector_cache_file, "r", encoding="utf-8") as fh:
                sector_cache = json.load(fh)
        except Exception as e:
            logger.warning("Failed to load sector cache: %s", e)

    exchange_cache: dict = {}
    if os.path.exists(exchange_cache_file):
        try:
            with open(exchange_cache_file, "r", encoding="utf-8") as fh:
                exchange_cache = json.load(fh)
        except Exception as e:
            logger.warning("Failed to load exchange cache: %s", e)

    conn = get_connection()
    cur  = conn.cursor()

    # ── Apply any already-cached values immediately ────────────────────────────
    if target_symbols is None:
        cur.execute("SELECT symbol, sector, primary_exchange FROM tickers WHERE is_active = 1")
    else:
        target_symbols = sorted(set(target_symbols))
        if target_symbols:
            placeholders = ",".join(["?"] * len(target_symbols))
            cur.execute(
                f"SELECT symbol, sector, primary_exchange FROM tickers WHERE symbol IN ({placeholders})",
                target_symbols,
            )
        else:
            cur.execute("SELECT symbol, sector, primary_exchange FROM tickers WHERE 1 = 0")
    all_rows = cur.fetchall()

    needs_fetch = []
    for sym, sec, primary_exchange in all_rows:
        sector_missing = sec is None or sec in _MIC_CODES
        exchange_missing = not primary_exchange or not primary_exchange.strip()
        cached_sector = sector_cache.get(sym)
        cached_exchange = exchange_cache.get(sym)

        if sector_missing and isinstance(cached_sector, str):
            cur.execute(
                "UPDATE tickers SET sector = ? WHERE symbol = ?",
                (cached_sector, sym)
            )
            sector_missing = False
        if exchange_missing and cached_exchange:
            cur.execute(
                "UPDATE tickers SET primary_exchange = ? WHERE symbol = ?",
                (cached_exchange, sym)
            )
            exchange_missing = False
        if sector_missing or exchange_missing:
            needs_fetch.append(sym)
    conn.commit()

    if needs_fetch:
        logger.info("Ticker metadata backfill: %d tickers need Polygon lookup.", len(needs_fetch))

    # ── Prioritise buy-signal symbols ─────────────────────────────────────────
    if priority_symbols:
        pset = set(priority_symbols)
        needs_fetch = (
            [s for s in needs_fetch if s in pset] +
            [s for s in needs_fetch if s not in pset]
        )

    updated = 0
    failed  = 0
    fetch_count = 0

    for sym in needs_fetch:
        if fetch_count >= max_fetches:
            logger.info(
                "Sector backfill: reached max_fetches=%d cap. %d tickers deferred to next run.",
                max_fetches, len(needs_fetch) - fetch_count
            )
            break

        # Rate-limit: Polygon free tier allows ~5 req/min (sleep between calls)
        if fetch_count > 0:
            time.sleep(13)

        url = f"https://api.polygon.io/v3/reference/tickers/{sym}?apiKey={POLYGON_API_KEY}"
        success = False
        for attempt in range(3):
            try:
                res = requests.get(url, timeout=10)
                if res.status_code == 200:
                    results    = res.json().get("results", {})
                    sic_code   = results.get("sic_code")
                    ticker_type = results.get("type", "CS")
                    primary_exchange = results.get("primary_exchange")

                    if ticker_type in ("ETF", "ETP"):
                        sector = "Index ETF"
                    else:
                        sector = _sic_to_sector(sic_code)

                    sector_cache[sym] = sector
                    if primary_exchange:
                        exchange_cache[sym] = primary_exchange
                    mic_placeholders = ",".join(["?"] * len(_MIC_CODES))
                    cur.execute(
                        f"""UPDATE tickers
                            SET sector = CASE
                                    WHEN sector IS NULL OR sector IN ({mic_placeholders}) THEN ?
                                    ELSE sector
                                END,
                                primary_exchange = CASE
                                    WHEN primary_exchange IS NULL OR TRIM(primary_exchange) = '' THEN ?
                                    ELSE primary_exchange
                                END
                            WHERE symbol = ?""",
                        (*sorted(_MIC_CODES), sector, primary_exchange, sym)
                    )
                    conn.commit()
                    # Incremental cache write
                    try:
                        with open(sector_cache_file, "w", encoding="utf-8") as fh:
                            json.dump(sector_cache, fh, indent=2)
                        with open(exchange_cache_file, "w", encoding="utf-8") as fh:
                            json.dump(exchange_cache, fh, indent=2)
                    except Exception:
                        pass
                    updated += 1
                    fetch_count += 1
                    logger.info(
                        "Ticker metadata backfill: %s -> %s, %s (SIC %s)",
                        sym, sector, primary_exchange or "exchange unknown", sic_code or "N/A"
                    )
                    success = True
                    break
                elif res.status_code == 429:
                    wait = 15 * (attempt + 1)
                    logger.warning(
                        "Polygon rate-limit 429 for %s (attempt %d). Waiting %ds...",
                        sym, attempt + 1, wait
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Polygon ticker details HTTP %d for %s: %s",
                        res.status_code, sym, res.text[:120]
                    )
                    failed += 1
                    fetch_count += 1
                    break
            except Exception as e:
                logger.warning("Sector fetch error for %s (attempt %d): %s", sym, attempt + 1, e)
                if attempt == 2:
                    failed += 1
                    fetch_count += 1
                else:
                    time.sleep(5)

    # ── Persist updated caches ────────────────────────────────────────────────
    try:
        with open(sector_cache_file, "w", encoding="utf-8") as fh:
            json.dump(sector_cache, fh, indent=2)
        with open(exchange_cache_file, "w", encoding="utf-8") as fh:
            json.dump(exchange_cache, fh, indent=2)
    except Exception as e:
        logger.warning("Failed to save ticker metadata caches: %s", e)

    conn.close()
    logger.info(
        "Ticker metadata backfill complete: %d updated, %d failed, %d already cached.",
        updated, failed, len(all_rows) - len(needs_fetch)
    )
    return updated


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
                    "Polygon API error on %s: HTTP %d %s", date_str, res.status_code, res.text[:1000]
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


def fetch_yfinance_bulk_daily(
    symbols, date_str, chunk_size=80, batch_delay=1.0, max_retries=4,
    initial_backoff=5.0, download_threads=20
):
    """
    Failover 2: Fetch batched daily bars with backoff for Yahoo rate limits.
    Tags bars with source='yfinance'.
    """
    records = []
    try:
        import yfinance as yf
        from yfinance.exceptions import YFRateLimitError

        class RateLimitLogHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.detected = False

            def emit(self, record):
                message = record.getMessage().lower()
                if any(term in message for term in ("yfratelimiterror", "too many requests", "rate limited")):
                    self.detected = True

        curr_d = datetime.date.fromisoformat(date_str)
        next_d = (curr_d + datetime.timedelta(days=1)).isoformat()

        for batch_index, i in enumerate(range(0, len(symbols), chunk_size)):
            chunk = symbols[i:i + chunk_size]
            sym_str = " ".join(chunk)
            df = None
            for attempt in range(max_retries):
                rate_limit_handler = RateLimitLogHandler()
                yf_logger = logging.getLogger("yfinance")
                yf_logger.addHandler(rate_limit_handler)
                try:
                    df = yf.download(
                        sym_str, start=date_str, end=next_d,
                        interval="1d", group_by="ticker", progress=False, threads=download_threads,
                        timeout=30
                    )
                except YFRateLimitError as e:
                    rate_limit_handler.detected = True
                    logger.debug("Yahoo rate limit raised for batch %d: %s", batch_index + 1, e)
                except Exception as e:
                    logger.error("yfinance batch %d failed: %s", batch_index + 1, e)
                    break
                finally:
                    yf_logger.removeHandler(rate_limit_handler)

                if rate_limit_handler.detected:
                    if attempt == max_retries - 1:
                        logger.error(
                            "Yahoo rate limit persisted for batch %d/%d after %d attempts; keeping any partial data.",
                            batch_index + 1, (len(symbols) + chunk_size - 1) // chunk_size,
                            max_retries
                        )
                        break
                    delay = initial_backoff * (2 ** attempt)
                    logger.warning(
                        "Yahoo rate limit on batch %d/%d (attempt %d/%d); retrying in %.1fs.",
                        batch_index + 1, (len(symbols) + chunk_size - 1) // chunk_size,
                        attempt + 1, max_retries, delay
                    )
                    time.sleep(delay)
                    continue
                break

            batch_records_before = len(records)
            no_data = []
            if df is not None and not df.empty:
                for sym in chunk:
                    try:
                        if len(chunk) == 1:
                            sym_df = df
                        elif sym in df.columns.levels[0]:
                            sym_df = df[sym]
                        else:
                            no_data.append(sym)
                            continue

                        if sym_df.empty:
                            no_data.append(sym)
                            continue
                        row = sym_df.iloc[-1]
                        o = float(row.get("Open", 0))
                        h = float(row.get("High", 0))
                        l = float(row.get("Low", 0))
                        c = float(row.get("Close", 0))
                        v = int(row.get("Volume", 0))
                        if c > 0:
                            records.append((sym, date_str, o, h, l, c, v, c, "yfinance"))
                        else:
                            no_data.append(sym)
                    except Exception as e:
                        logger.warning("Could not parse yfinance data for %s: %s", sym, e)
                        no_data.append(sym)
            else:
                no_data.extend(chunk)

            logger.info(
                "yfinance batch %d: %d bars retrieved; %d symbols returned no data.",
                batch_index + 1, len(records) - batch_records_before, len(no_data)
            )
            if batch_index < (len(symbols) - 1) // chunk_size:
                time.sleep(batch_delay)
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


# ── Earnings Calendar / Company Earnings ───────────────────────────────────────


def _finnhub_get_json(url, symbol, endpoint):
    """Make a paced Finnhub request with the retry/backoff used by enrichment."""
    global _FINNHUB_LAST_REQUEST_AT

    for attempt in range(2):
        if _FINNHUB_LAST_REQUEST_AT is not None:
            elapsed = time.monotonic() - _FINNHUB_LAST_REQUEST_AT
            remaining = FINNHUB_MIN_REQUEST_INTERVAL_SECONDS - elapsed
            if remaining > 0:
                time.sleep(remaining)

        try:
            response = requests.get(url, timeout=5)
            _FINNHUB_LAST_REQUEST_AT = time.monotonic()
            raw_body = response.text[:2000] if hasattr(response, "text") else str(response)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429:
                logger.warning(
                    "Finnhub 429 rate limit on %s request for %s (attempt %d).",
                    endpoint, symbol, attempt + 1
                )
                if attempt == 0:
                    time.sleep(2)
                    continue
            else:
                logger.warning(
                    "Finnhub %s returned HTTP %d for %s: %s",
                    endpoint, response.status_code, symbol, raw_body
                )
            break
        except Exception as e:
            logger.warning(
                "Finnhub %s request failed for %s (attempt %d): %s",
                endpoint, symbol, attempt + 1, e
            )
            if attempt == 0:
                time.sleep(1)

    return None


def fetch_finnhub_symbol_earnings(
    symbol, cache_dir=".cache", ttl_hours=168, stats=None, max_api_lookups=None
):
    """
    Fetch a symbol's historical quarterly earnings dataset from Finnhub's
    /stock/earnings endpoint and cache the result keyed by symbol.
    Returns {period_date: {"period": ..., "actual": ..., "estimate": ..., "surprise": ...}}
    """
    if not FINNHUB_API_KEY:
        logger.warning("Finnhub API key not configured; skipping stock earnings lookup for %s.", symbol)
        return {}

    sym = str(symbol).upper()
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"earnings_{sym}.json")
    now_ts = datetime.datetime.now(datetime.timezone.utc)
    cached_records = {}

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            fetched_at = cached.get("fetched_at")
            if fetched_at:
                fetched_dt = datetime.datetime.fromisoformat(fetched_at)
                age_hours = (now_ts - fetched_dt).total_seconds() / 3600.0
                cached_records = cached.get("records", {})
                if age_hours < ttl_hours:
                    if stats is not None:
                        stats["cache_hits"] += 1
                    return cached_records
                if stats is not None:
                    stats["stale_cache"] += 1
        except Exception as e:
            logger.warning("Failed to read earnings cache %s: %s", cache_file, e)
            if stats is not None:
                stats["cache_misses"] += 1
    elif stats is not None:
        stats["cache_misses"] += 1

    if max_api_lookups is not None and stats is not None:
        if stats["api_lookups"] >= max_api_lookups:
            stats["deferred"] += 1
            return cached_records
        stats["api_lookups"] += 1

    url = f"https://finnhub.io/api/v1/stock/earnings?symbol={sym}&token={FINNHUB_API_KEY}"
    payload = _finnhub_get_json(url, sym, "stock/earnings")
    if isinstance(payload, list):
        records = {}
        for item in payload:
            period = item.get("period")
            if not period:
                continue
            try:
                parsed_date = datetime.date.fromisoformat(period)
            except Exception:
                continue
            records[parsed_date.isoformat()] = {
                "symbol": item.get("symbol", sym),
                "period": parsed_date.isoformat(),
                "estimate": item.get("estimate"),
                "actual": item.get("actual"),
                "surprise": item.get("surprise"),
                "surprisePercent": item.get("surprisePercent"),
                "year": item.get("year"),
                "quarter": item.get("quarter"),
            }
        cache_payload = {"fetched_at": now_ts.isoformat(), "records": records}
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_payload, f, indent=2)
        except Exception as e:
            logger.warning("Failed to write symbol earnings cache for %s: %s", sym, e)
        logger.info("Fetched and cached %d earnings periods for %s from Finnhub.", len(records), sym)
        return records

    logger.warning("Proceeding without fresh earnings data for %s.", sym)
    return cached_records


def classify_news_catalyst(headline):
    """Assign a rough catalyst label from common headline keywords."""
    text = str(headline or "").lower()
    if any(word in text for word in (
        "merger", "acquisition", "acquire", "acquired", "buyout", "takeover", "to combine"
    )):
        return "M&A"
    if any(word in text for word in (
        "analyst", "price target", "upgrade", "downgrade", "initiates coverage",
        "overweight", "underweight", "buy rating", "sell rating"
    )):
        return "Analyst Rating"
    if any(word in text for word in (
        "fda", "regulatory", "regulator", "approval", "cleared", "clearance",
        "clinical trial", "sec charges", "doj", "ftc", "antitrust", "recall", "probe"
    )):
        return "Regulatory/FDA"
    if any(word in text for word in (
        "earnings", "quarterly results", "financial results", "guidance", "outlook",
        "revenue", "eps", "profit", "sales forecast"
    )):
        return "Earnings/Guidance"
    return "Other"


def fetch_finnhub_company_news(
    symbol,
    to_date=None,
    cache_dir=".cache",
    ttl_hours=NEWS_CACHE_TTL_HOURS,
    stats=None,
    max_api_lookups=None,
    lookback_days=7,
):
    """Fetch and cache up to three recent company-news headlines for a symbol."""
    sym = str(symbol).upper()
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"news_{sym}.json")
    now = datetime.datetime.now(datetime.timezone.utc)
    cached_articles = []

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            fetched_at = cached.get("fetched_at")
            cached_articles = cached.get("articles", [])
            age_hours = (now - datetime.datetime.fromisoformat(fetched_at)).total_seconds() / 3600.0
            if age_hours < ttl_hours:
                if stats is not None:
                    stats["cache_hits"] = stats.get("cache_hits", 0) + 1
                return cached_articles[:3] if isinstance(cached_articles, list) else []
            if stats is not None:
                stats["stale_cache"] = stats.get("stale_cache", 0) + 1
        except Exception as e:
            logger.warning("Failed to read company-news cache %s: %s", cache_file, e)
            if stats is not None:
                stats["cache_misses"] = stats.get("cache_misses", 0) + 1
    elif stats is not None:
        stats["cache_misses"] = stats.get("cache_misses", 0) + 1

    if not FINNHUB_API_KEY:
        logger.warning("Finnhub API key not configured; skipping company-news lookup for %s.", sym)
        return cached_articles[:3] if isinstance(cached_articles, list) else []

    if max_api_lookups is not None and stats is not None:
        if stats.get("api_lookups", 0) >= max_api_lookups:
            stats["deferred"] = stats.get("deferred", 0) + 1
            return cached_articles[:3] if isinstance(cached_articles, list) else []
        stats["api_lookups"] = stats.get("api_lookups", 0) + 1

    try:
        end_date = datetime.date.fromisoformat(to_date) if to_date else now.date()
    except ValueError:
        end_date = now.date()
    start_date = end_date - datetime.timedelta(days=max(lookback_days - 1, 0))
    url = (
        "https://finnhub.io/api/v1/company-news"
        f"?symbol={sym}&from={start_date.isoformat()}&to={end_date.isoformat()}&token={FINNHUB_API_KEY}"
    )
    payload = _finnhub_get_json(url, sym, "company-news")
    if not isinstance(payload, list):
        logger.warning("Proceeding without fresh company news for %s.", sym)
        return cached_articles[:3] if isinstance(cached_articles, list) else []

    dated_articles = []
    for item in payload:
        headline = str(item.get("headline") or "").strip()
        if not headline:
            continue
        try:
            published_at = datetime.datetime.fromtimestamp(
                float(item.get("datetime")), tz=datetime.timezone.utc
            )
        except (TypeError, ValueError, OSError):
            try:
                published_at = datetime.datetime.fromisoformat(
                    str(item.get("publishedAt")).replace("Z", "+00:00")
                )
            except Exception:
                continue
        article = {
            "headline": headline,
            "source": str(item.get("source") or ""),
            "url": str(item.get("url") or ""),
            "date": published_at.date().isoformat(),
            "category": classify_news_catalyst(headline),
        }
        dated_articles.append((published_at, article))

    dated_articles.sort(key=lambda value: value[0], reverse=True)
    articles = [article for _, article in dated_articles[:3]]
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": now.isoformat(), "articles": articles}, f, indent=2)
    except Exception as e:
        logger.warning("Failed to write company-news cache for %s: %s", sym, e)
    logger.info("Fetched and cached %d recent news headlines for %s from Finnhub.", len(articles), sym)
    return articles


def _sec_get(url, symbol, endpoint):
    """Fetch one SEC resource with a descriptive User-Agent and conservative pacing."""
    global _SEC_LAST_REQUEST_AT

    headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    }
    for attempt in range(2):
        if _SEC_LAST_REQUEST_AT is not None:
            elapsed = time.monotonic() - _SEC_LAST_REQUEST_AT
            remaining = SEC_MIN_REQUEST_INTERVAL_SECONDS - elapsed
            if remaining > 0:
                time.sleep(remaining)

        try:
            response = requests.get(url, headers=headers, timeout=10)
            _SEC_LAST_REQUEST_AT = time.monotonic()
        except Exception as e:
            _SEC_LAST_REQUEST_AT = time.monotonic()
            logger.warning("SEC %s request failed for %s (attempt %d): %s", endpoint, symbol, attempt + 1, e)
            if attempt == 0:
                time.sleep(1)
                continue
            return None

        if response.status_code == 200:
            return response
        if response.status_code == 429 and attempt == 0:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(max(float(retry_after), 1.0), 10.0) if retry_after else 2.0
            except (TypeError, ValueError):
                delay = 2.0
            logger.warning("SEC 429 rate limit on %s for %s; retrying after %.1fs.", endpoint, symbol, delay)
            time.sleep(delay)
            continue

        logger.warning("SEC %s returned HTTP %d for %s.", endpoint, response.status_code, symbol)
        return None

    return None


def _load_sec_company_tickers(cache_dir=".cache"):
    """Load the SEC ticker/CIK directory, refreshing its persistent 30-day cache."""
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, "sec_company_tickers.json")
    now = datetime.datetime.now(datetime.timezone.utc)
    cached_companies = None

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached_companies = cached.get("companies")
            fetched_at = cached.get("fetched_at")
            if fetched_at and isinstance(cached_companies, dict):
                age_days = (now - datetime.datetime.fromisoformat(fetched_at)).total_seconds() / 86400.0
                if age_days < SEC_CIK_CACHE_TTL_DAYS:
                    return cached_companies
        except Exception as e:
            logger.warning("Failed to read SEC company ticker cache %s: %s", cache_file, e)

    response = _sec_get(
        "https://www.sec.gov/files/company_tickers.json",
        "company directory",
        "company_tickers.json",
    )
    if response is None:
        return cached_companies if isinstance(cached_companies, dict) else {}

    try:
        companies = response.json()
        if not isinstance(companies, dict):
            raise ValueError("SEC company ticker file was not a JSON object")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": now.isoformat(), "companies": companies}, f)
        return companies
    except Exception as e:
        logger.warning("Failed to parse or cache SEC company ticker file: %s", e)
        return cached_companies if isinstance(cached_companies, dict) else {}


def _company_name_tokens(value):
    ignored = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "THE", "LTD", "LIMITED"}
    return {token for token in re.findall(r"[A-Z0-9]+", str(value or "").upper()) if token not in ignored}


def _sec_cik_for_symbol(symbol, company_name=None, cache_dir=".cache"):
    """Resolve and persist the SEC CIK for one ticker without scanning the universe."""
    sym = str(symbol).upper()
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"sec_cik_{sym}.json")
    now = datetime.datetime.now(datetime.timezone.utc)

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            fetched_at = cached.get("fetched_at")
            if fetched_at:
                age_days = (now - datetime.datetime.fromisoformat(fetched_at)).total_seconds() / 86400.0
                if age_days < SEC_CIK_CACHE_TTL_DAYS:
                    return str(cached.get("cik") or "") or None
        except Exception as e:
            logger.warning("Failed to read SEC CIK cache %s: %s", cache_file, e)

    rows = _load_sec_company_tickers(cache_dir).values()
    candidates = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("ticker", "")).upper() == sym and row.get("cik_str")
    ]
    if not candidates:
        return None

    company_tokens = _company_name_tokens(company_name)
    if company_tokens and len(candidates) > 1:
        candidates.sort(
            key=lambda row: len(company_tokens & _company_name_tokens(row.get("title"))),
            reverse=True,
        )
    chosen = candidates[0]
    cik = str(chosen["cik_str"]).zfill(10)
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({
                "ticker": sym,
                "cik": cik,
                "company_name": chosen.get("title"),
                "fetched_at": now.isoformat(),
            }, f)
    except Exception as e:
        logger.warning("Failed to cache SEC CIK for %s: %s", sym, e)
    return cik


def _parse_sec_form4(raw_submission, symbol, as_of_date, start_date, filing_date, filing_url):
    """Extract only acquired, non-derivative open-market P transactions from a filing."""
    purchases = []
    ownership_documents = re.findall(
        r"<ownershipDocument\b[^>]*>.*?</ownershipDocument\s*>",
        raw_submission,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for xml_document in ownership_documents:
        try:
            root = ET.fromstring(xml_document)
        except ET.ParseError as e:
            logger.warning("Could not parse SEC Form 4 XML for %s: %s", symbol, e)
            continue

        owners = root.findall("./reportingOwner") or [None]
        for transaction in root.findall("./nonDerivativeTable/nonDerivativeTransaction"):
            if transaction.findtext("./transactionCoding/transactionCode") != "P":
                continue
            if transaction.findtext("./transactionAmounts/transactionAcquiredDisposedCode/value") != "A":
                continue

            transaction_date = transaction.findtext("./transactionDate/value")
            try:
                parsed_date = datetime.date.fromisoformat(transaction_date)
            except (TypeError, ValueError):
                continue
            if parsed_date < start_date or parsed_date > as_of_date:
                continue

            shares_text = transaction.findtext("./transactionAmounts/transactionShares/value")
            price_text = transaction.findtext("./transactionAmounts/transactionPricePerShare/value")
            try:
                shares = float(shares_text)
            except (TypeError, ValueError):
                continue
            try:
                price_per_share = float(price_text)
            except (TypeError, ValueError):
                price_per_share = None

            security_title = transaction.findtext("./securityTitle/value")
            for owner in owners:
                owner_id = owner.find("./reportingOwnerId") if owner is not None else None
                relationship = owner.find("./reportingOwnerRelationship") if owner is not None else None
                relationships = []
                if relationship is not None:
                    if relationship.findtext("isOfficer") == "1":
                        relationships.append("Officer")
                    if relationship.findtext("isDirector") == "1":
                        relationships.append("Director")
                    if relationship.findtext("isTenPercentOwner") == "1":
                        relationships.append("10% owner")
                purchases.append({
                    "name": owner_id.findtext("rptOwnerName") if owner_id is not None else None,
                    "title": relationship.findtext("officerTitle") if relationship is not None else None,
                    "relationships": relationships,
                    "transaction_date": parsed_date.isoformat(),
                    "shares": shares,
                    "price_per_share": price_per_share,
                    "total_value": round(shares * price_per_share, 2) if price_per_share is not None else None,
                    "security": security_title,
                    "filing_date": filing_date,
                    "filing_url": filing_url,
                })

    return purchases


def fetch_sec_insider_activity(
    symbol,
    as_of_date,
    company_name=None,
    cache_dir=".cache",
    lookback_days=SEC_INSIDER_LOOKBACK_CALENDAR_DAYS,
    max_filings=SEC_INSIDER_MAX_FILINGS_PER_SYMBOL,
    stats=None,
):
    """Fetch recent Form 4 open-market purchases for one screened signal symbol."""
    sym = str(symbol).upper()
    empty_result = {"purchases": [], "insider_cluster": False}
    try:
        signal_date = datetime.date.fromisoformat(as_of_date)
    except (TypeError, ValueError):
        return empty_result

    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"sec_insider_{sym}.json")
    now = datetime.datetime.now(datetime.timezone.utc)
    cached_result = None
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached_result = {
                "purchases": cached.get("purchases", []),
                "insider_cluster": bool(cached.get("insider_cluster", False)),
            }
            fetched_at = cached.get("fetched_at")
            age_hours = (now - datetime.datetime.fromisoformat(fetched_at)).total_seconds() / 3600.0
            if (
                cached.get("as_of_date") == signal_date.isoformat()
                and cached.get("lookback_days") == lookback_days
                and age_hours < SEC_INSIDER_CACHE_TTL_HOURS
            ):
                if stats is not None:
                    stats["cache_hits"] += 1
                return cached_result
            if stats is not None:
                stats["stale_cache"] += 1
        except Exception as e:
            logger.warning("Failed to read SEC insider cache %s: %s", cache_file, e)
            if stats is not None:
                stats["cache_misses"] += 1
    elif stats is not None:
        stats["cache_misses"] += 1

    try:
        cik = _sec_cik_for_symbol(sym, company_name=company_name, cache_dir=cache_dir)
        if not cik:
            logger.info("No SEC CIK mapping found for signal symbol %s.", sym)
            return cached_result or empty_result

        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        submissions_response = _sec_get(submissions_url, sym, "submissions")
        if submissions_response is None:
            return cached_result or empty_result
        submissions = submissions_response.json()
        recent = submissions.get("filings", {}).get("recent", {})
        start_date = signal_date - datetime.timedelta(days=lookback_days - 1)
        purchases = []
        filings_checked = 0

        for index, form in enumerate(recent.get("form", [])):
            if form != "4":
                continue
            filing_date_text = recent.get("filingDate", [])[index]
            try:
                filing_date = datetime.date.fromisoformat(filing_date_text)
            except (TypeError, ValueError):
                continue
            if filing_date < start_date:
                break
            if filing_date > signal_date:
                continue

            accession = recent.get("accessionNumber", [])[index]
            if not accession:
                continue
            accession_path = accession.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_path}/{accession}.txt"
            )
            filing_response = _sec_get(filing_url, sym, "Form 4 filing")
            filings_checked += 1
            if filing_response is None:
                continue
            purchases.extend(_parse_sec_form4(
                filing_response.text,
                sym,
                signal_date,
                start_date,
                filing_date.isoformat(),
                filing_url,
            ))
            if filings_checked >= max_filings:
                break

        deduplicated = {}
        for purchase in purchases:
            dedupe_key = (
                purchase.get("name"), purchase["transaction_date"],
                purchase["shares"], purchase.get("price_per_share"), purchase.get("security"),
            )
            deduplicated.setdefault(dedupe_key, purchase)
        purchases = sorted(
            deduplicated.values(),
            key=lambda purchase: (purchase["transaction_date"], purchase["filing_date"]),
            reverse=True,
        )
        buyer_names = {str(purchase.get("name") or "").strip().casefold() for purchase in purchases}
        buyer_names.discard("")
        result = {"purchases": purchases, "insider_cluster": len(buyer_names) > 1}
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({
                "fetched_at": now.isoformat(),
                "as_of_date": signal_date.isoformat(),
                "lookback_days": lookback_days,
                **result,
            }, f, indent=2)
        if stats is not None:
            stats["symbols_fetched"] = stats.get("symbols_fetched", 0) + 1
        logger.info(
            "SEC insider lookup for %s completed: %d P-code purchases across %d Form 4 filings.",
            sym, len(purchases), filings_checked
        )
        return result
    except Exception as e:
        logger.warning("SEC insider lookup failed for %s; continuing without insider data: %s", sym, e)
        return cached_result or empty_result


# ── FINRA Short Interest Bulk Ingestion & Lookup ────────────────────────────────

def get_latest_stored_short_interest_settlement_date(conn=None) -> str | None:
    """Return the newest settlement_date present in short_interest table, or None."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(settlement_date) FROM short_interest")
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception as e:
        logger.warning("Failed to query latest short interest settlement date: %s", e)
        return None
    finally:
        if close_conn:
            conn.close()


def discover_available_finra_settlement_date(as_of_date: str | None = None) -> str | None:
    """
    Find the newest available FINRA short interest settlement date on or before as_of_date.
    First checks the official catalog files page at FINRA_SHORT_INTEREST_FILES_CATALOG_URL.
    If that fails or catalog cannot be reached, probes recent candidate settlement dates
    (15th / last calendar day and nearby weekdays) going back 45 days.
    Returns: 'YYYY-MM-DD' or None.
    """
    headers = {"User-Agent": FINRA_HTTP_USER_AGENT}
    ref_date = datetime.date.today()
    if as_of_date:
        try:
            ref_date = datetime.date.fromisoformat(as_of_date)
        except Exception:
            pass

    # 1. Try parsing the catalog files page
    try:
        resp = requests.get(FINRA_SHORT_INTEREST_FILES_CATALOG_URL, headers=headers, timeout=10)
        if resp.status_code == 200:
            # Look for shrtYYYYMMDD.csv in the page HTML
            dates_found = re.findall(r"shrt(\d{8})\.csv", resp.text)
            if dates_found:
                parsed_dates = []
                for d_str in dates_found:
                    try:
                        d = datetime.date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8]))
                        if d <= ref_date:
                            parsed_dates.append(d)
                    except Exception:
                        continue
                if parsed_dates:
                    newest = max(parsed_dates)
                    logger.info("Discovered newest FINRA settlement date from files catalog: %s", newest.isoformat())
                    return newest.isoformat()
    except Exception as e:
        logger.warning("Error fetching FINRA files catalog: %s; falling back to candidate date probes.", e)

    # 2. Candidate date probe fallback (check 15th, 14th, 13th, and end-of-month dates backwards)
    for i in range(45):
        cand = ref_date - datetime.timedelta(days=i)
        if cand.day in (13, 14, 15, 16, 28, 29, 30, 31):
            cand_str = cand.strftime("%Y%m%d")
            url = FINRA_SHORT_INTEREST_URL_BASE.format(date_str=cand_str)
            try:
                head_resp = requests.head(url, headers=headers, timeout=5)
                if head_resp.status_code == 200:
                    logger.info("Discovered active FINRA short interest file via probe: %s (%s)", cand.isoformat(), url)
                    return cand.isoformat()
            except Exception:
                continue

    return None


def ingest_finra_short_interest_bulk(settlement_date_str: str, conn=None) -> int:
    """
    Downloads the bulk pipe-delimited FINRA CSV for settlement_date_str and inserts or
    replaces records into the short_interest table.
    Returns the count of records ingested.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        clean_date_str = settlement_date_str.replace("-", "")
        url = FINRA_SHORT_INTEREST_URL_BASE.format(date_str=clean_date_str)
        headers = {"User-Agent": FINRA_HTTP_USER_AGENT}
        logger.info("Fetching bulk FINRA short interest file from %s...", url)
        
        t0 = time.perf_counter()
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.error("Failed to download FINRA short interest file (HTTP %d) from %s", resp.status_code, url)
            return 0

        text = resp.text
        reader = csv.DictReader(io.StringIO(text), delimiter="|")
        
        rows_to_insert = []
        for row in reader:
            sym = row.get("symbolCode", "").strip().upper()
            if not sym:
                continue
            
            raw_settlement = row.get("settlementDate", "").strip() or settlement_date_str
            # normalize settlement date to YYYY-MM-DD
            if len(raw_settlement) == 8 and raw_settlement.isdigit():
                norm_settlement = f"{raw_settlement[:4]}-{raw_settlement[4:6]}-{raw_settlement[6:]}"
            else:
                norm_settlement = raw_settlement

            try:
                curr_pos = int(row.get("currentShortPositionQuantity") or 0)
            except Exception:
                curr_pos = None

            try:
                prev_pos = int(row.get("previousShortPositionQuantity") or 0)
            except Exception:
                prev_pos = None

            try:
                avg_vol = int(row.get("averageDailyVolumeQuantity") or 0)
            except Exception:
                avg_vol = None

            try:
                dtc = float(row.get("daysToCoverQuantity") or 0.0)
            except Exception:
                dtc = None

            try:
                chg_pct = float(row.get("changePercent") or 0.0)
            except Exception:
                chg_pct = None

            mkt_class = row.get("marketClassCode", "").strip() or None

            rows_to_insert.append((
                sym, norm_settlement, curr_pos, prev_pos, avg_vol, dtc, chg_pct, mkt_class
            ))

        if not rows_to_insert:
            logger.warning("FINRA short interest file contained 0 valid rows.")
            return 0

        cur = conn.cursor()
        cur.executemany("""
            INSERT INTO short_interest (
                symbol, settlement_date, current_short_position, previous_short_position,
                avg_daily_volume, days_to_cover, change_pct, market_class
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, settlement_date) DO UPDATE SET
                current_short_position = excluded.current_short_position,
                previous_short_position = excluded.previous_short_position,
                avg_daily_volume = excluded.avg_daily_volume,
                days_to_cover = excluded.days_to_cover,
                change_pct = excluded.change_pct,
                market_class = excluded.market_class,
                updated_at = CURRENT_TIMESTAMP
        """, rows_to_insert)
        conn.commit()

        elapsed = time.perf_counter() - t0
        logger.info(
            "Ingested %d short interest rows for settlement date %s in %.2fs.",
            len(rows_to_insert), settlement_date_str, elapsed
        )
        return len(rows_to_insert)

    except Exception as e:
        logger.exception("Unexpected error during FINRA short interest ingestion: %s", e)
        return 0
    finally:
        if close_conn:
            conn.close()


def refresh_short_interest_data(as_of_date: str | None = None, conn=None) -> dict:
    """
    Conditional refresh: checks if the bulk short-interest file needs updating.
    Discovers the latest available publication/settlement date from FINRA. If a newer
    settlement date exists than the latest stored one in SQLite, downloads and stores it.
    Returns: dict with status ('up_to_date', 'downloaded', or 'failed'), settlement_date, and row_count.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        latest_stored = get_latest_stored_short_interest_settlement_date(conn=conn)
        available_date = discover_available_finra_settlement_date(as_of_date=as_of_date)

        logger.info(
            "[Short Interest] Latest stored settlement date: %s | Newest available: %s",
            latest_stored, available_date
        )

        if not available_date:
            logger.warning("[Short Interest] Could not discover any available FINRA settlement date.")
            return {"status": "skipped", "settlement_date": latest_stored, "row_count": 0}

        if latest_stored and available_date <= latest_stored:
            logger.info(
                "[Short Interest] Data is up-to-date (stored %s >= available %s). Skipping download.",
                latest_stored, available_date
            )
            return {"status": "up_to_date", "settlement_date": latest_stored, "row_count": 0}

        # Need to download new settlement date file
        logger.info(
            "[Short Interest] New settlement date available (%s > %s). Initiating bulk download...",
            available_date, latest_stored
        )
        count = ingest_finra_short_interest_bulk(available_date, conn=conn)
        return {"status": "downloaded", "settlement_date": available_date, "row_count": count}

    except Exception as e:
        logger.exception("Error during short interest refresh: %s", e)
        return {"status": "failed", "error": str(e), "settlement_date": None, "row_count": 0}
    finally:
        if close_conn:
            conn.close()


def get_symbol_short_interest(symbol: str, conn=None) -> dict | None:
    """
    Fast local DB lookup for a symbol's most recent short interest data.
    Does NOT make any network calls.
    Returns: {
        'shares_short': int,
        'days_to_cover': float,
        'short_percent_of_float': float | None,
        'settlement_date': str,
        'change_pct': float | None,
        'market_class': str | None
    } or None if not found.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.settlement_date, s.current_short_position, s.days_to_cover,
                   s.change_pct, s.market_class, t.market_cap
            FROM short_interest s
            LEFT JOIN tickers t ON s.symbol = t.symbol
            WHERE s.symbol = ?
            ORDER BY s.settlement_date DESC
            LIMIT 1
        """, (symbol.upper(),))
        row = cur.fetchone()
        if not row:
            return None

        settlement_date, shares_short, dtc, chg_pct, mkt_class, mkt_cap = row

        return {
            "shares_short": shares_short,
            "days_to_cover": round(float(dtc), 2) if dtc is not None else None,
            "short_percent_of_float": None,
            "settlement_date": settlement_date,
            "change_pct": round(float(chg_pct), 2) if chg_pct is not None else None,
            "market_class": mkt_class
        }
    except Exception as e:
        logger.warning("Error fetching short interest for %s: %s", symbol, e)
        return None
    finally:
        if close_conn:
            conn.close()


def fetch_finnhub_earnings_calendar(from_date, to_date, cache_dir=".cache"):
    """Backward-compatible wrapper retained for older callers; prefer symbol-scoped fetches."""
    logger.warning("Deprecated broad calendar/earnings path invoked for %s to %s. Prefer per-symbol stock/earnings lookups.", from_date, to_date)
    return {}


def _is_near_earnings(symbol, date_str, earnings_map, trading_days_series, proximity_days=EARNINGS_PROXIMITY_DAYS):
    """
    Return (near_earnings: bool, earnings_date: str|None) for a given symbol.
    Only consider earnings periods that were already known as of the signal date.
    If the API includes a report date, filter to report_date <= signal_date.
    If only quarter-end period is available, require a conservative buffer so future
    periods are not treated as already known.
    """
    if not earnings_map:
        return False, None

    candidate_map = earnings_map.get(symbol) if isinstance(earnings_map, dict) and symbol in earnings_map and isinstance(earnings_map[symbol], dict) else earnings_map
    if not candidate_map:
        return False, None

    signal_dt = datetime.date.fromisoformat(date_str)
    eligibility_buffer_days = EARNINGS_REPORT_LAG_DAYS_DEFAULT
    eligible_dates = []

    for key, value in candidate_map.items():
        info = value if isinstance(value, dict) else {}
        period_value = info.get("period") or info.get("date") or key
        report_date_value = info.get("reportDate") or info.get("report_date") or info.get("actualDate") or info.get("actual_date")

        try:
            period_dt = datetime.date.fromisoformat(str(period_value))
        except Exception:
            continue

        if report_date_value:
            try:
                report_dt = datetime.date.fromisoformat(str(report_date_value))
            except Exception:
                report_dt = None
            if report_dt is not None and report_dt <= signal_dt:
                eligible_dates.append(period_dt)
            continue

        # Conservative fallback: only count quarter-end periods that were likely already reported,
        # not future periods that had not closed yet.
        if period_dt <= signal_dt - datetime.timedelta(days=eligibility_buffer_days):
            eligible_dates.append(period_dt)

    if not eligible_dates:
        return False, None

    nearest_date = max(eligible_dates)
    earnings_date = nearest_date.isoformat()
    try:
        td_list = sorted(trading_days_series)
        if date_str not in td_list or earnings_date not in td_list:
            diff = abs((nearest_date - signal_dt).days)
            near = diff <= proximity_days * 2
            return near, earnings_date

        idx_signal = td_list.index(date_str)
        idx_earnings = td_list.index(earnings_date)
        near = abs(idx_earnings - idx_signal) <= proximity_days
        return near, earnings_date
    except Exception:
        return False, earnings_date


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
    spy_return_63d=None, earnings_map=None, trading_days=None,
    ticker_sector=None, sector_returns_63d=None
):
    """
    Compute technical indicators for a single symbol and evaluate Momentum Breakout rules.

    New computed fields added to details JSON:
      - pct_change_1d:         today's % change vs prior close
      - atr_pct:               ATR(14) as % of close price
      - avg_dollar_vol_20d:    20-day average dollar volume
      - rs_vs_spy:             63-day return minus SPY's 63-day return (percentage points)
      - rs_vs_sector:          63-day return minus sector ETF's 63-day return (percentage points)
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
        dist_to_20d_high_pct = round(((close_price / prior_20d_high) - 1) * 100, 2)
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
        ticker_return_63d = None
        if len(df) >= 63:
            try:
                base_close_63 = float(df["close"].iloc[-63])
                if base_close_63 > 0:
                    ticker_return_63d = ((float(df["close"].iloc[-1]) - base_close_63) / base_close_63) * 100.0
            except Exception:
                pass
        elif len(df) >= 2:
            try:
                base_close_0 = float(df["close"].iloc[0])
                if base_close_0 > 0:
                    ticker_return_63d = ((float(df["close"].iloc[-1]) - base_close_0) / base_close_0) * 100.0
            except Exception:
                pass

        if spy_return_63d is not None and ticker_return_63d is not None:
            rs_vs_spy = round(ticker_return_63d - spy_return_63d, 2)

        # 4b. RS vs Sector: 63-day return minus sector ETF's 63-day return (point-in-time)
        rs_vs_sector = None
        if ticker_sector and sector_returns_63d and ticker_return_63d is not None:
            sector_etf = SECTOR_TO_ETF.get(ticker_sector)
            if sector_etf and sector_etf in sector_returns_63d and sector_returns_63d[sector_etf] is not None:
                rs_vs_sector = round(ticker_return_63d - sector_returns_63d[sector_etf], 2)

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
            "rs_vs_sector":         rs_vs_sector,
            "dist_to_20d_high_pct": dist_to_20d_high_pct,
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
    screening_started = time.perf_counter()
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

    # Load ticker sector mapping from tickers table
    ticker_sectors = dict(pd.read_sql("SELECT symbol, sector FROM tickers WHERE is_active = 1", conn).itertuples(index=False, name=None))

    # Pre-compute point-in-time 63-day returns for sector ETFs as of date_str
    sector_returns_63d = {}
    for etf_sym in SECTOR_ETFS:
        if etf_sym in grouped:
            etf_df = grouped[etf_sym]
            if len(etf_df) >= 2:
                try:
                    n = min(63, len(etf_df))
                    base_close = float(etf_df["close"].iloc[-n])
                    if base_close > 0:
                        ret = ((float(etf_df["close"].iloc[-1]) - base_close) / base_close) * 100.0
                        sector_returns_63d[etf_sym] = ret
                except Exception:
                    pass

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
                {},
                all_trading_days,
                ticker_sectors.get(sym),
                sector_returns_63d
            ): sym
            for sym, df_sym in grouped.items()
        }
        for future in as_completed(futures):
            res = future.result()
            if res:
                signals.append(res)

    screening_seconds = time.perf_counter() - screening_started
    logger.info(
        "Technical screening (excluding earnings enrichment) completed in %.2fs; %d signals passed for %s.",
        screening_seconds, len(signals), date_str
    )

    earnings_started = time.perf_counter()
    earnings_stats = {"cache_hits": 0, "stale_cache": 0, "cache_misses": 0, "api_lookups": 0, "deferred": 0}
    earnings_deferred = 0
    for index, signal in enumerate(signals):
        if time.perf_counter() - earnings_started >= EARNINGS_ENRICHMENT_BUDGET_SECONDS:
            earnings_deferred += len(signals) - index
            break

        symbol = signal[1]
        try:
            earnings_map = fetch_finnhub_symbol_earnings(
                symbol,
                stats=earnings_stats,
                max_api_lookups=EARNINGS_ENRICHMENT_MAX_LOOKUPS
            )
        except Exception as e:
            logger.warning("Earnings enrichment failed for %s; continuing without refreshed data: %s", symbol, e)
            earnings_map = {}
        try:
            near_earnings, earnings_date = _is_near_earnings(
                symbol, date_str, earnings_map, all_trading_days
            ) if earnings_map else (False, None)
            details = json.loads(signal[6]) if signal[6] else {}
            details["near_earnings"] = near_earnings
            details["earnings_date"] = earnings_date
            signals[index] = (*signal[:6], json.dumps(details))
        except Exception as e:
            logger.warning("Could not enrich earnings fields for %s: %s", symbol, e)

    earnings_seconds = time.perf_counter() - earnings_started
    earnings_deferred += earnings_stats["deferred"]
    if earnings_deferred:
        logger.warning(
            "Earnings enrichment stopped/deferred for %d signal(s); the remaining pipeline will continue.",
            earnings_deferred
        )
    logger.info(
        "Earnings enrichment completed in %.2fs for %d signal(s): %d cache hits, %d stale cache, "
        "%d cache misses, %d API lookups, %d deferred.",
        earnings_seconds, len(signals), earnings_stats["cache_hits"], earnings_stats["stale_cache"],
        earnings_stats["cache_misses"], earnings_stats["api_lookups"], earnings_deferred
    )

    news_started = time.perf_counter()
    news_stats = {"cache_hits": 0, "stale_cache": 0, "cache_misses": 0, "api_lookups": 0, "deferred": 0}
    news_deferred = 0
    for index, signal in enumerate(signals):
        if time.perf_counter() - news_started >= NEWS_ENRICHMENT_BUDGET_SECONDS:
            news_deferred += len(signals) - index
            break

        symbol = signal[1]
        try:
            news = fetch_finnhub_company_news(
                symbol,
                to_date=date_str,
                stats=news_stats,
                max_api_lookups=NEWS_ENRICHMENT_MAX_LOOKUPS,
            )
        except Exception as e:
            logger.warning("Company-news enrichment failed for %s; continuing without news: %s", symbol, e)
            news = []

        try:
            details = json.loads(signal[6]) if signal[6] else {}
            details["news"] = news[:3]
            signals[index] = (*signal[:6], json.dumps(details))
        except Exception as e:
            logger.warning("Could not enrich company-news fields for %s: %s", symbol, e)

    news_seconds = time.perf_counter() - news_started
    news_deferred += news_stats["deferred"]
    if news_deferred:
        logger.warning(
            "Company-news enrichment stopped/deferred for %d signal(s); the remaining pipeline will continue.",
            news_deferred
        )
    logger.info(
        "Company-news enrichment completed in %.2fs for %d signal(s): %d cache hits, %d stale cache, "
        "%d cache misses, %d API lookups, %d deferred.",
        news_seconds, len(signals), news_stats["cache_hits"], news_stats["stale_cache"],
        news_stats["cache_misses"], news_stats["api_lookups"], news_deferred
    )

    # Short interest enrichment (fast local SQLite lookup, no per-symbol network calls)
    si_started = time.perf_counter()
    si_found = 0
    si_conn = get_connection()
    try:
        for index, signal in enumerate(signals):
            symbol = signal[1]
            try:
                si_data = get_symbol_short_interest(symbol, conn=si_conn)
                if si_data:
                    si_found += 1
                details = json.loads(signal[6]) if signal[6] else {}
                details["short_interest"] = si_data
                signals[index] = (*signal[:6], json.dumps(details))
            except Exception as e:
                logger.warning("Could not attach short interest for %s: %s", symbol, e)
    finally:
        si_conn.close()

    logger.info(
        "Short interest enrichment completed in %.4fs for %d signal(s) (%d matched in DB).",
        time.perf_counter() - si_started, len(signals), si_found
    )

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
        df = pd.read_sql(
            "SELECT timestamp, return_5d_pct, return_10d_pct, return_20d_pct FROM buy_signals",
            conn,
        )

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

        breakout_returns = []
        for returns in df[["return_20d_pct", "return_10d_pct", "return_5d_pct"]].itertuples(
            index=False, name=None
        ):
            value = next((float(item) for item in returns if pd.notna(item)), None)
            if value is not None:
                breakout_returns.append(value)

        positive_breakout_returns = [value for value in breakout_returns if value > 0]
        average_breakout_gain = None
        if breakout_returns:
            gain_sample = positive_breakout_returns or breakout_returns
            average_breakout_gain = round(sum(gain_sample) / len(gain_sample), 2)

        signal_activity = {
            "as_of": None,
            "today_count": 0,
            "avg_per_signal_day_30d": None,
        }
        latest_signal_date = conn.execute("SELECT MAX(timestamp) FROM buy_signals").fetchone()[0]
        if latest_signal_date:
            window_start = conn.execute(
                "SELECT date(?, '-30 days')", (latest_signal_date,)
            ).fetchone()[0]
            total_30d, signal_days_30d = conn.execute(
                """SELECT COUNT(*), COUNT(DISTINCT timestamp)
                   FROM buy_signals
                   WHERE timestamp >= ? AND timestamp <= ?""",
                (window_start, latest_signal_date),
            ).fetchone()
            today_count = conn.execute(
                "SELECT COUNT(*) FROM buy_signals WHERE timestamp = ?",
                (latest_signal_date,),
            ).fetchone()[0]
            signal_activity = {
                "as_of": latest_signal_date,
                "today_count": int(today_count),
                "avg_per_signal_day_30d": round(total_30d / signal_days_30d, 1)
                if signal_days_30d
                else None,
            }

        return {
            "total_signals": len(df),
            "horizon_5d":    calc_horizon_stats(df["return_5d_pct"]),
            "horizon_10d":   calc_horizon_stats(df["return_10d_pct"]),
            "horizon_20d":   calc_horizon_stats(df["return_20d_pct"]),
            "signal_activity": signal_activity,
            "avg_breakout_gain_pct": average_breakout_gain,
            "generated_at":  datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
    finally:
        if close_conn:
            conn.close()


# ── Web Export ─────────────────────────────────────────────────────────────────

def export_web_data(output_dir="../frontend/public/data", bars_limit=250):
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
    cur = conn.cursor()

    cur.execute(
        "SELECT DISTINCT timestamp FROM daily_bars ORDER BY timestamp DESC LIMIT ?",
        (SIGNAL_WINDOW_TRADING_DAYS,),
    )
    signal_window_dates = [row[0] for row in cur.fetchall()]

    # Resolve metadata only for symbols in the export window, avoiding unrelated universe lookups.
    unresolved_symbols = []
    if signal_window_dates:
        date_placeholders = ",".join(["?"] * len(signal_window_dates))
        cur.execute(f"""
            SELECT DISTINCT b.symbol
            FROM buy_signals b
            LEFT JOIN tickers t ON b.symbol = t.symbol
            WHERE b.timestamp IN ({date_placeholders})
              AND (
                    t.sector IS NULL
                    OR t.sector IN ('XNAS','XNYS','XASE','BATS','ARCX','XCIS','US','Unknown')
                    OR t.primary_exchange IS NULL
                    OR TRIM(t.primary_exchange) = ''
              )
        """, signal_window_dates)
        unresolved_symbols = [row[0] for row in cur.fetchall()]
    if unresolved_symbols:
        logger.info(
            "Resolving sector/exchange metadata for %d export-window symbols: %s",
            len(unresolved_symbols), unresolved_symbols
        )
        try:
            backfill_ticker_sectors(
                priority_symbols=unresolved_symbols,
                target_symbols=unresolved_symbols,
                max_fetches=min(len(unresolved_symbols), MAX_SECTOR_FETCHES_PER_RUN),
            )
        except Exception as e:
            logger.warning("Ticker metadata backfill during web export failed: %s", e)

    # 1. latest_signals.json — signals from the most recent trading sessions only
    query_signals = f"""
        SELECT
            b.id, b.timestamp, b.symbol, b.setup_name, b.close_price,
            b.rvol, b.rsi, b.details, b.created_at,
            t.name, t.market_cap, t.sector, t.primary_exchange,
            b.price_5d, b.price_10d, b.price_20d,
            b.return_5d_pct, b.return_10d_pct, b.return_20d_pct
        FROM buy_signals b
        LEFT JOIN tickers t ON b.symbol = t.symbol
        WHERE b.timestamp IN ({','.join(['?'] * len(signal_window_dates))})
        ORDER BY b.timestamp DESC, b.rvol DESC
    """
    raw_signals = []
    if signal_window_dates:
        cur.execute(query_signals, signal_window_dates)
        raw_signals = cur.fetchall()

    # Load bars for SPY and all sector ETFs to compute point-in-time returns for signals
    benchmark_symbols = list(SECTOR_ETFS) + ["SPY"]
    bench_placeholders = ",".join(["?"] * len(benchmark_symbols))
    cur.execute(f"""
        SELECT symbol, timestamp, close
        FROM daily_bars
        WHERE symbol IN ({bench_placeholders})
        ORDER BY symbol, timestamp ASC
    """, benchmark_symbols)
    bench_rows = cur.fetchall()
    bench_bars_by_symbol = {}
    for b_sym, b_ts, b_close in bench_rows:
        if b_sym not in bench_bars_by_symbol:
            bench_bars_by_symbol[b_sym] = []
        bench_bars_by_symbol[b_sym].append((b_ts, float(b_close)))

    def _calc_pit_return_63d(bars_list, as_of_date):
        # Filter bars up to as_of_date
        sub = [c for (t, c) in bars_list if t <= as_of_date]
        if len(sub) < 2:
            return None
        n = min(63, len(sub))
        base = sub[-n]
        if base <= 0:
            return None
        return ((sub[-1] - base) / base) * 100.0

    signals_list  = []
    signal_symbols = set()
    seen_signal_keys = set()

    for row in raw_signals:
        (
            sig_id, timestamp, symbol, setup_name, close_price,
            rvol, rsi, details, created_at,
            ticker_name, market_cap, sector, primary_exchange,
            p5, p10, p20, ret5, ret10, ret20
        ) = row
        signal_key = (timestamp, symbol)
        if signal_key in seen_signal_keys:
            continue
        seen_signal_keys.add(signal_key)
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

        # Resolve point-in-time rs_vs_sector
        rs_vs_sector = parsed_details.get("rs_vs_sector")
        if rs_vs_sector is None and sector and sector in SECTOR_TO_ETF:
            sec_etf = SECTOR_TO_ETF[sector]
            if sec_etf in bench_bars_by_symbol and "SPY" in bench_bars_by_symbol:
                etf_ret = _calc_pit_return_63d(bench_bars_by_symbol[sec_etf], timestamp)
                spy_ret = _calc_pit_return_63d(bench_bars_by_symbol["SPY"], timestamp)
                rs_vs_spy_val = parsed_details.get("rs_vs_spy")
                if etf_ret is not None and spy_ret is not None and rs_vs_spy_val is not None:
                    # ticker_ret_63d = rs_vs_spy + spy_ret_63d
                    ticker_ret = rs_vs_spy_val + spy_ret
                    rs_vs_sector = round(ticker_ret - etf_ret, 2)

        signal_obj = {
            "id":           sig_id,
            "timestamp":    timestamp,
            "symbol":       symbol,
            "name":         ticker_name or symbol,
            "sector":       sector or "Unknown",
            "primary_exchange": primary_exchange,
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
            "rs_vs_sector":         rs_vs_sector,
            "dist_to_20d_high_pct": parsed_details.get("dist_to_20d_high_pct"),
            "dist_to_52w_high_pct": parsed_details.get("dist_to_52w_high_pct"),
            "near_earnings":        parsed_details.get("near_earnings", False),
            "earnings_date":        parsed_details.get("earnings_date"),
            "news":                 parsed_details.get("news", [])[:3],
            "insider_activity":     parsed_details.get("insider_activity", []),
            "insider_cluster":      bool(parsed_details.get("insider_cluster", False)),
            "short_interest":       parsed_details.get("short_interest") or get_symbol_short_interest(symbol, conn=conn),
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

    # 3. performance_summary.json uses the full buy_signals table, not the export window.
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
