# Market Screener — Complete Project Working Context

> **Purpose**: This file preserves full working context across agent, profile, and environment switches. Read this first when resuming work on this project.

---

## 1. Project Overview & Current State

### What It Does
A Python-based **daily momentum stock screener** that:
- Tracks the full US equity universe (~1,000–12,000 tickers depending on data source)
- Calculates technical indicators locally using `pandas-ta` on historical daily OHLCV bars stored in SQLite
- Emits **Momentum Breakout** buy signals when all four conditions are met:
  - `RVOL >= 1.5` (relative volume vs 20-day average)
  - `RSI (14) between 50 and 75`
  - `Close price > 50-day SMA`
  - `Close price >= 20-day rolling high * 0.99`
- Dispatches **Discord webhook alerts** with rich embed messages listing the day's triggered setups
- Runs automatically via **GitHub Actions** after market close every weekday

### Implementation Status
| Phase | Description | Status |
|---|---|---|
| Phase 1 | Python environment, SQLite schema, database initialization | ✅ Complete |
| Phase 2 | Polygon ingestion, Pandas_TA engine, signal generation | ✅ Complete |
| Phase 3 | Multi-tier failover (Polygon → Alpaca → yfinance) | ✅ Complete |
| Phase 4 | Discord webhook alerts, `run_daily.py` orchestrator | ✅ Complete |
| Deployment | GitHub Actions CI/CD (`.github/workflows/daily_screener.yml`) | ✅ Deployed |

---

## 2. File System & Component Map

```
Market Screener/
├── .env                          # Local API keys (never committed)
├── requirements.txt              # Python dependencies (pandas-ta excluded — see gotchas)
├── schema.sql                    # SQLite DDL: creates tickers, daily_bars, buy_signals tables
├── stock_screener.db             # Persistent SQLite database (committed via GitHub Actions bot)
├── screener.py                   # Core data pipeline: universe seeding, bar ingestion, failover, TA engine
├── alerts.py                     # Discord webhook formatter and dispatcher
├── run_daily.py                  # Automated entrypoint: orchestrates all 6 pipeline steps
├── test_run.py                   # Manual verification script (historical backtest simulation)
└── .github/
    └── workflows/
        └── daily_screener.yml    # GitHub Actions: scheduled + manual trigger, secrets injection, DB push
```

### Role of Each File

| File | Role |
|---|---|
| `screener.py` | Universe seeding (FMP → Polygon fallback), Polygon Grouped Daily ingestion, Alpaca failover, yfinance failover, individual ticker historical ingestion, Pandas_TA indicator engine, multi-threaded signal evaluation, SQLite persistence |
| `alerts.py` | Reads `buy_signals` table, builds Discord embed JSON payload, dispatches via `DISCORD_WEBHOOK_URL`; supports `dry_run=True` for testing |
| `run_daily.py` | Sequential 6-step orchestrator: `init_db → refresh_ticker_universe → ingest_daily_bars → run_screener_engine → send_discord_alerts`; accepts `--date`, `--force-failover`, `--dry-run` CLI args |
| `schema.sql` | DDL for three tables and one composite index; safe to re-run (`IF NOT EXISTS`) |
| `requirements.txt` | Core pip deps: `pandas`, `requests`, `python-dotenv`, `yfinance` — **`pandas-ta` is NOT listed here** (installed separately; see Critical Fixes) |
| `daily_screener.yml` | GitHub Actions workflow: schedules at `0 21 * * 1-5`, maps repo secrets to env vars, commits updated `stock_screener.db` back to `main` after each run |

---

## 3. Database Architecture (SQLite)

### Schema

```sql
-- Ticker Registry
CREATE TABLE IF NOT EXISTS tickers (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    market_cap REAL,
    sector TEXT,
    is_active BOOLEAN DEFAULT 1,
    last_updated DATE
);

-- Rolling 200-Day Daily Bars
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT,
    timestamp DATE,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    vwap REAL,
    PRIMARY KEY (symbol, timestamp),
    FOREIGN KEY (symbol) REFERENCES tickers(symbol)
);

-- Generated Buy Signals
CREATE TABLE IF NOT EXISTS buy_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATE,
    symbol TEXT,
    setup_name TEXT,
    close_price REAL,
    rvol REAL,
    rsi REAL,
    details JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bars_symbol_time ON daily_bars(symbol, timestamp DESC);
```

### Rolling Window Manager
- `ingest_daily_bars()` automatically prunes `daily_bars` after every write:
  ```sql
  DELETE FROM daily_bars WHERE timestamp < date('now', '-360 days')
  ```
- This keeps approximately **250 trading days** (one year) of history for all TA calculations.
- Ensures `stock_screener.db` file size stays bounded and queries stay fast at scale.

---

## 4. Data Ingestion & Multi-Tier Failover Engine

### Universe Seeding (`refresh_ticker_universe`)

```
Priority 1: FMP Stock Screener API (marketCap > $300M, price > $5, active, not ETF)
            └─► API: https://financialmodelingprep.com/api/v3/stock-screener?...
            └─► Status: Responds 403 "Legacy Endpoint" on current FMP tier
                        (code handles gracefully, falls through)

Priority 2: Polygon Reference Tickers (active common stocks, type=CS, limit=1000)
            └─► API: https://api.polygon.io/v3/reference/tickers?market=stocks&type=CS&active=true
            └─► Status: ✅ Works — seeds ~993 tickers per call

Hardcoded Core: 8 large-cap stocks always added as guaranteed minimum universe
                (AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AMD)
```

### Daily Bar Ingestion (`ingest_daily_bars`)

```
Priority 1 — Polygon Grouped Daily (PRIMARY)
  API: /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true
  Pros: Entire US market in ONE API call (~12,000 tickers)
  Rate limit: ~5 req/min on free tier → 429 triggers failover
  On 429: waits 12s × attempt, up to 2 retries

Priority 2 — Alpaca Data API v2 (FAILOVER 1)
  API: https://data.alpaca.markets/v2/stocks/bars?symbols={chunk}&timeframe=1Day
  Headers: APCA-API-KEY-ID, APCA-API-SECRET-KEY
  Batches 100 symbols per request; ~0.3s delay between chunks
  Tested: ✅ Successfully ingested 946 bars via forced failover

Priority 3 — yfinance Bulk Download (FAILOVER 2)
  Uses yf.download() with group_by='ticker', threads=True
  Batches 80 symbols per download call
  No auth required; slower but always available
```

### Individual Historical Ingestion (`ingest_historical_bars_for_ticker`)
- Used for seeding historical window for specific tickers
- API: `https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}`
- Converts Polygon millisecond timestamps to `YYYY-MM-DD`
- Retry on 429 with 12s sleep

### Technical Indicator Engine (`process_single_ticker_screener`)
- Requires **minimum 50 bars** per symbol to calculate
- Computes per-symbol using `pandas_ta`:
  - `SMA(200)` — long-term trend filter
  - `SMA(50)` — medium-term trend filter
  - `EMA(20)` — short-term momentum
  - `RSI(14)` — momentum oscillator
  - `RVOL` = `volume / SMA(volume, 20)` — relative volume
- Executes across a `ThreadPoolExecutor(max_workers=4)` pool for parallelism

---

## 5. Critical Fixes & System Gotchas

### ⚠️ `pandas-ta` PyPI Installation Issue (CRITICAL)

**Problem**: `pandas-ta==0.4.71b0` on PyPI declares a hard dependency on `pandas<2.0`. Installing it normally (`pip install pandas-ta`) will downgrade pandas or fail with a dependency conflict on Python 3.11 + pandas 3.x.

**Fix**: Install `pandas-ta` with `--pre --no-deps` to pull the latest prerelease and skip dependency resolution entirely:
```bash
pip install pandas-ta --pre --no-deps
```

This is reflected in the workflow (which also runs Python **3.12** to match the local environment):
```yaml
- name: Set up Python 3.12
  uses: actions/setup-python@v5
  with:
    python-version: '3.12'
    cache: 'pip'

- name: Install dependencies
  run: |
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    pip install pandas-ta --pre --no-deps
```

**`requirements.txt` deliberately omits `pandas-ta`** — it is always installed explicitly in the workflow step above.

### FMP API Legacy Endpoint Errors
- All `api/v3/*` FMP endpoints return HTTP 403 `"Legacy Endpoint"` on current subscription tier.
- All `/stable/*` screener endpoints return HTTP 402 `"Restricted Endpoint"`.
- **Resolution**: Universe seeding always falls through to Polygon reference API (handled silently as a `WARNING` in logs, not an error).

### Polygon Rate Limits
- Free tier allows ~5 requests/minute for grouped daily aggregates.
- The pipeline sees HTTP 429 after ~5 sequential calls.
- The system backs off 12 seconds per retry (2 retries max) before triggering Alpaca failover.

### SQLite Concurrency
- `stock_screener.db` uses `sqlite3.connect()` per function call (connection-per-operation pattern).
- Not intended for concurrent multi-process writes — single-process pipeline execution is assumed.

### GitHub Actions DB Commit Pattern
- After each run, the Actions bot commits `stock_screener.db` with:
  ```
  chore: automated daily database update [skip ci]
  ```
- `[skip ci]` prevents this commit from re-triggering the workflow.
- `git diff --staged --quiet` check prevents empty commits on days with no data change.

### Alpaca API Header Names
- `APCA-API-KEY-ID` = value of `ALPACA_API_KEY` env var
- `APCA-API-SECRET-KEY` = value of `ALPACA_API_SECRET` **or** `ALPACA_SECRET_KEY`
  - `screener.py` checks both: `os.getenv("ALPACA_API_SECRET") or os.getenv("ALPACA_SECRET_KEY", "")`
  - GitHub Actions secret is named `ALPACA_SECRET_KEY`; local `.env` uses `ALPACA_API_SECRET`

---

## 6. Execution Commands & Usage

### Standard Daily Run (after market close)
```bash
python run_daily.py
```

### Backtest / Historical Date Scan
```bash
python run_daily.py --date 2025-09-19
```

### Dry Run (no webhook sent, prints payload)
```bash
python run_daily.py --date 2025-09-19 --dry-run
```

### Force Failover Test (bypass Polygon, use Alpaca)
```bash
python run_daily.py --force-failover --dry-run
```

### Seed Historical Data for Specific Tickers
```python
from screener import ingest_historical_bars_for_ticker
ingest_historical_bars_for_ticker("NVDA", "2025-01-01", "2026-03-20")
```

### Inspect Database
```bash
# Check table row counts
.\venv\Scripts\python.exe -c "import sqlite3; conn = sqlite3.connect('stock_screener.db'); print('tickers:', conn.execute('SELECT count(*) FROM tickers').fetchone()[0]); print('bars:', conn.execute('SELECT count(*) FROM daily_bars').fetchone()[0]); print('signals:', conn.execute('SELECT count(*) FROM buy_signals').fetchone()[0])"
```

---

## 7. GitHub Actions Deployment Status

### Workflow File
[`.github/workflows/daily_screener.yml`](.github/workflows/daily_screener.yml)

### Schedule
| Setting | Value |
|---|---|
| Cron | `0 21 * * 1-5` |
| Time | 21:00 UTC = 4:00 PM EST / 5:00 PM EDT |
| Days | Monday–Friday only |

### Required Repository Secrets
All set under `Settings → Secrets and variables → Actions`:

| Secret Name | Maps To | Used By |
|---|---|---|
| `POLYGON_API_KEY` | `POLYGON_API_KEY` | `screener.py` — primary bar ingestion |
| `ALPACA_API_KEY` | `ALPACA_API_KEY` | `screener.py` — failover 1 header |
| `ALPACA_SECRET_KEY` | `ALPACA_SECRET_KEY` | `screener.py` — failover 1 header |
| `FMP_API_KEY` | `FMP_API_KEY` | `screener.py` — universe seeding (graceful fail) |
| `DISCORD_WEBHOOK_URL` | `DISCORD_WEBHOOK_URL` | `alerts.py` — webhook dispatch |

### Commit History (Key Milestones)
```
50b69ba  fix: install pandas-ta via direct zip archive to bypass git authentication
b0f1612  fix: install pandas-ta directly from GitHub repo to resolve build dependency
79be333  Initial commit - Stock Screener Pipeline
```

---

## 8. Environment Setup (Local)

```bash
# Create virtual environment (Python 3.12 locally, 3.11 in Actions)
py -m venv venv
.\venv\Scripts\pip install -r requirements.txt
.\venv\Scripts\pip install pandas-ta --no-deps

# Verify
.\venv\Scripts\python.exe -c "import pandas, pandas_ta, requests, dotenv, sqlite3, yfinance; print('All OK')"
```

### `.env` File Structure
```
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
EODHD_API_KEY=...
FINNHUB_API_KEY=...
FMP_API_KEY=...
POLYGON_API_KEY=...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## 9. Verified Test Results (Phase 2 Execution)

Historical scan across 4 dates using 8-symbol watchlist (223 bars per symbol):

| Date | Signals Found | Notes |
|---|---|---|
| 2025-09-19 | **3** (AAPL, MSFT, META) | AMZN RVOL 2.51x just below threshold |
| 2025-10-31 | **2** (AAPL, AMZN) | AMZN RVOL 3.11x highest of all tests |
| 2026-02-04 | **1** (AAPL) | AAPL only ticker above SMA50 with elevated RVOL |
| 2026-03-20 | **0** | Market correction phase — all below SMA50 |

Sample signals table verified in `stock_screener.db`:
```
id  timestamp   symbol  setup_name         close_price  rvol   rsi
 6  2026-02-04  AAPL    Momentum Breakout  276.49       1.60   68.14
 5  2025-10-31  AAPL    Momentum Breakout  270.37       1.80   69.04
 4  2025-10-31  AMZN    Momentum Breakout  244.22       3.11   67.05
 3  2025-09-19  AAPL    Momentum Breakout  245.50       2.94   67.75
 2  2025-09-19  META    Momentum Breakout  778.38       2.29   60.80
 1  2025-09-19  MSFT    Momentum Breakout  517.93       2.42   59.05
```
