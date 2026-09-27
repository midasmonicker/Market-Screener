# Market Screener â€” Complete Project Working Context

> **Purpose**: This file preserves full working context across agent, profile, and environment switches. Read this first when resuming work on this project.

---

## 1. Project Overview & Current State

### What It Does
A Python-based **daily momentum stock screener** that:
- Tracks the full US equity universe (~1,000â€“12,000 tickers depending on data source)
- Calculates technical indicators locally using `pandas-ta` on historical daily OHLCV bars stored in SQLite
- Emits **Momentum Breakout** buy signals when all four conditions are met:
  - `RVOL >= 1.5` (relative volume vs 20-day average)
  - `RSI (14) between 50 and 75`
  - `Close price > 50-day SMA`
  - `Close price >= 20-day rolling high * 0.99`
- Dispatches **Discord webhook** and **Telegram Bot alerts** with rich formatting listing the day's triggered setups
- Runs automatically via **GitHub Actions** after market close every weekday

### Implementation Status
| Phase | Description | Status |
|---|---|---|
| Phase 1 | Python environment, SQLite schema, database initialization | âœ… Complete |
| Phase 2 | Polygon ingestion, Pandas_TA engine, signal generation | âœ… Complete |
| Phase 3 | Multi-tier failover (Polygon â†’ Alpaca â†’ yfinance) | âœ… Complete |
| Phase 4 | Discord webhook alerts, `run_daily.py` orchestrator | âœ… Complete |
| Phase 5.1 | Frontend data bridge: export static JSON payloads for web visualization | âœ… Complete |
| Phase 5.2 | Next.js Dashboard: Dark trading UI, metric cards, Lightweight Charts modal | âœ… Complete |
| Phase 6 | Telegram Bot alert notifications alongside Discord | âœ… Complete |
| Phase 7 | Strategy Quality Filters: Market Regime (SPY SMA) + RS Score (63-day percentile >= 70) | âœ… Complete |
| Phase 8 | Post-Breakout Performance Tracking: 5/10/20-day return columns + win rate summary | âœ… Complete |
| Phase 9 | Frontend Upgrade: TradingView Export Tool, Strategy Performance Stats Header, VWAP & ATR Stop overlays | âœ… Complete |
| Phase 10 | Pipeline Hardening: Source Tagging, IEX Volume Failover, Lookahead Fixes, Polygon Pagination & Liquidity Filter | âœ… Complete |
| Phase 11 | Robustness & DB Persistence: Holiday skip, data freshness check, exception alerting, idempotency, orphan `data` branch | âœ… Complete |
| Phase 11b | Backtest & Outcome Tracking: `outcomes.py`, `signal_outcomes` table, `--backfill` replay, `setup_stats.json` export | âœ… Complete |
| Phase 12 | DB Gitignore: `stock_screener.db` removed from `main` tracking; `.gitignore` updated; restored/persisted exclusively via `data` orphan branch | ✅ Complete |
| Phase 13 | TA Engine Extension: computed fields (% change, ATR%, 20d dollar vol, RS vs SPY, 52-wk high distance), Finnhub earnings-proximity flagging, composite 0-100 score (RVOL/RS/proximity/trend), signal_streak dedup (Discord alerts on streak==1 only), `regime.json` (SPY vs SMA200 + % universe above SMA50) | ✅ Complete |
| Phase 14 | Dashboard UI Overhaul: Loading/error/empty states (failed URL display), As-of badge, Market Regime/30d-avg/Data Freshness cards, 14 sortable columns, multi-criteria filters with localStorage presets, chart modal "Why it triggered" & ATR risk calculator box, and Strategy Track Record section (`TrackRecord.tsx`) | ✅ Complete |
| Phase 15 | Monorepo Structure: Separation into `frontend/` (Next.js App Router, Tailwind, TypeScript) and `backend/` (Python TA pipeline, SQLite, venv). Restored `globals.css` inside `frontend/app/`, scoped `.gitignore` files, fixed export paths to `../frontend/public/data`, and aligned CI/CD `daily_screener.yml`. | ✅ Complete |
| Phase 16 | Sector Taxonomy & Backfill: Resolved root cause of exchange MIC codes (`XNAS`, `XNYS`) polluting sector column. Added SIC-to-GICS sector lookup (`_sic_to_sector`), `backfill_ticker_sectors()` using Polygon reference ticker endpoint (`v3/reference/tickers/{sym}`), disk caching (`.cache/ticker_sectors.json`), `ON CONFLICT` sector update fix, and auto-resolution in `export_web_data()`. | ✅ Complete |
| Phase 17 | Responsive Column Prioritization: Optimized signals table for viewport widths under 768px (`md:`). Collapses to 4 essential columns (Symbol, Price, RVOL, Score) + chevron expand toggle. Expanding a row reveals the full breakdown (Date, 1d Chg, RSI, ATR%, 20d $ Vol, RS vs SPY, Streak, Earnings, 52w High Dist, MA Alignment) in a stacked drawer with direct chart trigger, preventing horizontal scroll on mobile. | ✅ Complete |
| Deployment | GitHub Actions CI/CD (`.github/workflows/daily_screener.yml`) — cron `30 22 * * 1-5` (22:30 UTC Mon–Fri) | ✅ Deployed |

---

## 2. File System & Component Map

```
Market Screener/
├── .env                          # Local API keys (never committed)
├── .gitignore                    # Root gitignore (monorepo scope: node_modules, .next, venv, .env, *.db)
├── working.md                    # Project context & architecture history
├── .github/
│   └── workflows/
│       └── daily_screener.yml    # GitHub Actions: 22:30 UTC Mon-Fri; runs backend & commits frontend data
├── frontend/
│   ├── .gitignore                # Frontend-scoped gitignore (.next, node_modules, out, dist, etc.)
│   ├── package.json              # Next.js, React, Tailwind, lightweight-charts
│   ├── tsconfig.json             # TypeScript compiler config
│   ├── tailwind.config.js        # Tailwind CSS dark theme configuration
│   ├── postcss.config.js         # PostCSS plugins
│   ├── app/
│   │   ├── layout.tsx            # Next.js App Router root layout (imports ./globals.css)
│   │   ├── globals.css           # Tailwind directives & custom scrollbars
│   │   └── page.tsx              # Main dashboard: filters, presets, sortable table, metric cards
│   ├── components/
│   │   ├── StockChart.tsx        # TradingView chart modal, why-it-triggered panel, ATR risk box
│   │   └── TrackRecord.tsx       # Strategy track record section from setup_stats.json
│   └── public/
│       └── data/
│           ├── latest_signals.json
│           ├── signal_bars.json
│           ├── performance_summary.json
│           ├── setup_stats.json
│           └── regime.json
└── backend/
    ├── .gitignore                # Backend-scoped gitignore (venv, __pycache__, stock_screener.db, .cache)
    ├── requirements.txt          # Python dependencies (pandas-ta excluded — installed --no-deps)
    ├── schema.sql                # SQLite DDL: tickers, daily_bars, buy_signals, signal_outcomes
    ├── stock_screener.db         # SQLite database (persisted via orphan data branch)
    ├── screener.py               # Core TA engine & export_web_data (exports to ../frontend/public/data)
    ├── alerts.py                 # Discord & Telegram notification functions
    ├── run_daily.py              # Pipeline orchestrator
    ├── outcomes.py               # Backtesting & outcome statistics generator
    ├── test_run.py               # Historical test verification script
    └── venv/                     # Local Python 3.12 virtual environment
```



### Role of Each File

| File | Role |
|---|---|
| `app/page.tsx` | Main Next.js dashboard: Strategy Performance Stats Header (10d win rate, avg breakout gain, market regime badge), "Copy TradingView Watchlist" export button, RVOL leader, RS/regime badges, signal table with MA alignment and 5d/10d/20d return pills. Consumes `latest_signals.json`, `signal_bars.json`, and `performance_summary.json` |
| `components/StockChart.tsx` | Interactive TradingView Lightweight Chart modal: candlestick price series, 20-EMA (blue), 50-SMA (yellow), 200-SMA (purple), **VWAP line** (orange, #f97316), **2Ã—ATR trailing stop line** (rose dashed, #f43f5e, toggleable), toggle toolbar, and volume histogram sub-chart |
| `screener.py` | Universe seeding (incl. SPY for regime/RS), bar ingestion, failover, TA engine, SQLite persistence, and `export_web_data()`. Source-tagged bars (`polygon`, `alpaca_sip`, `alpaca_iex`, `yfinance`). Liquidity filter constants at top. |
| `alerts.py` | Dispatches **Discord** + **Telegram** alerts. Phase 11 adds `send_discord_failure_alert()` (exception hook) and `send_discord_stale_alert()` (data freshness failure). |
| `run_daily.py` | 8-step orchestrator in try/except with failure alerting. Phase 11: holiday skip, freshness check, idempotency (`clear_existing_signals_for_date`). Phase 11b: calls `update_signal_outcomes()` (step 6) and `export_setup_stats()` (step 8). |
| `outcomes.py` | **Phase 11b** â€” Backtest & outcome tracking. `update_signal_outcomes()` upserts forward returns with strict no-lookahead. `export_setup_stats()` writes `setup_stats.json`. `run_backfill()` replays screener over stored history. CLI: `--backfill`, `--export-only`, default. |
| `public/data/` | Static JSON payloads (`latest_signals.json`, `signal_bars.json`, `performance_summary.json`, `setup_stats.json`) committed back to repository for instant client-side rendering |
| `schema.sql` | DDL for four tables (`tickers`, `daily_bars` with `source TEXT`, `buy_signals`, `signal_outcomes`) and two indexes; safe to re-run (`IF NOT EXISTS`) |
| `requirements.txt` | Core pip deps: `pandas`, `requests`, `python-dotenv`, `yfinance`, `numba`, `tqdm`, `scipy`, `pandas_market_calendars` â€” **`pandas-ta` is NOT listed here** (installed separately; see Critical Fixes) |
| `daily_screener.yml` | GitHub Actions: `30 22 * * 1-5` (22:30 UTC Monâ€“Fri). Restores `stock_screener.db` from orphan `data` branch at start; commits `public/data/` to `main`; force-pushes DB as single commit to `data` branch after each run. |


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

-- Rolling Daily Bars (Phase 10: added source column)
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT,
    timestamp DATE,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    vwap REAL,
    source TEXT,              -- 'polygon', 'alpaca_sip', 'alpaca_iex', 'yfinance'
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
    details JSON,                 -- includes ma_alignment, market_regime, rs_score (Phase 7)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    -- Phase 8 additions (auto-migrated via ALTER TABLE in init_db):
    -- price_5d REAL, price_10d REAL, price_20d REAL
    -- return_5d_pct REAL, return_10d_pct REAL, return_20d_pct REAL
);

-- Signal Outcomes (Phase 11b)
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
);

CREATE INDEX IF NOT EXISTS idx_bars_symbol_time ON daily_bars(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_outcomes_date ON signal_outcomes(signal_date);
```

### Rolling Window Manager
- `ingest_daily_bars()` automatically prunes `daily_bars` after every write:
  ```sql
  DELETE FROM daily_bars WHERE timestamp < date(?, '-500 days')
  ```
  The window is **relative to `date_str`** (not system date), so historical backfills always retain the bars they need.
- This keeps approximately **350 trading days** of history for all TA calculations.
- Ensures `stock_screener.db` file size stays bounded and queries stay fast at scale.



---

## 4. Data Ingestion & Multi-Tier Failover Engine

### Universe Seeding (`refresh_ticker_universe`)

```
Priority 1: FMP Stock Screener API (marketCap > $300M, price > $5, active, not ETF)
            â””â”€â–º API: https://financialmodelingprep.com/api/v3/stock-screener?...
            â””â”€â–º Status: Responds 403 "Legacy Endpoint" on current FMP tier
                        (code handles gracefully, falls through)

Priority 2: Polygon Reference Tickers (active common stocks, type=CS, limit=1000)
            â””â”€â–º API: https://api.polygon.io/v3/reference/tickers?market=stocks&type=CS&active=true
            â””â”€â–º Status: âœ… Works â€” seeds ~993 tickers per call

Hardcoded Core: 9 stocks always added as guaranteed minimum universe
                (AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AMD)
                + SPY (Index ETF â€” required for Market Regime check and RS Score baseline)
```

### Daily Bar Ingestion (`ingest_daily_bars`)

```
Priority 1 â€” Polygon Grouped Daily (PRIMARY)
  API: /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true
  Pros: Entire US market in ONE API call (~12,000 tickers)
  Rate limit: ~5 req/min on free tier â†’ 429 triggers failover
  On 429: waits 12s Ã— attempt, up to 2 retries

Priority 2 â€” Alpaca Data API v2 (FAILOVER 1)
  API: https://data.alpaca.markets/v2/stocks/bars?symbols={chunk}&timeframe=1Day
  Headers: APCA-API-KEY-ID, APCA-API-SECRET-KEY
  Batches 100 symbols per request; ~0.3s delay between chunks
  Tested: âœ… Successfully ingested 946 bars via forced failover

Priority 3 â€” yfinance Bulk Download (FAILOVER 2)
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
  - `SMA(200)` â€” long-term trend filter
  - `SMA(50)` â€” medium-term trend filter
  - `EMA(20)` â€” short-term momentum
  - `RSI(14)` â€” momentum oscillator
  - `RVOL` = `volume / SMA(volume, 20)` â€” relative volume
- Executes across a `ThreadPoolExecutor(max_workers=4)` pool for parallelism

### Strategy Quality Filters (Phase 7 â€” Complete)
Pre-screened in the main thread before worker execution (not inside the ThreadPoolExecutor):

**Market Regime Check** (`check_market_regime(date_str, conn)`)
- Queries SPY bars from `daily_bars`; computes rolling 50-day and 200-day SMA
- Returns `"Bullish"` if SPY close â‰¥ both SMAs, `"Caution"` if below either, `"Unknown"` if insufficient bars (<50)
- Regime is embedded in every signal's `details` JSON column and exposed in alerts and web UI

**RS Score** (`calculate_universe_rs_scores(grouped, date_str)`)
- Computes 63-day (3-month) percentage price performance for every ticker vs the universe
- Ranks using `pd.Series.rank(pct=True) * 99` â†’ 0â€“99 percentile score
- SPY itself is excluded from the ranking
- **Signals with RS score < 70 are filtered out** before DB insertion
- RS score is embedded in every signal's `details` JSON column

**JSON fields added to `buy_signals.details`:**
```json
{"ma_alignment": {...}, "market_regime": "Bullish|Caution|Unknown", "rs_score": 82.4}
```

### Post-Breakout Performance Tracking Engine (Phase 8 â€” Complete)
Automatically measures forward trading performance across 5-day, 10-day, and 20-day horizons:

- **Schema columns added to `buy_signals`** (auto-migrated via `init_db()`):
  `price_5d REAL, return_5d_pct REAL, price_10d REAL, return_10d_pct REAL, price_20d REAL, return_20d_pct REAL`
- **Forward Horizon Resolution** (`update_post_breakout_performance()`):
  - Identifies signals with unfinalized forward metrics
  - Queries subsequent chronological daily bars for the ticker (`timestamp > signal.timestamp`)
  - Calculates percentage return vs entry `close_price`: `((forward_price - entry_price) / entry_price) * 100`
- **Aggregate Performance Summary** (`get_performance_summary()`):
  - Calculates win rates (% positive returns), average returns, and sample counts across 5d, 10d, and 20d horizons
  - Exported to `public/data/performance_summary.json`
  - Integrated into Next.js dashboard metric cards and table badges

---

## 5. Critical Fixes & System Gotchas

### âš ï¸ `pandas-ta` PyPI Installation Issue (CRITICAL)

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

**`requirements.txt` deliberately omits `pandas-ta`** â€” it is always installed explicitly in the workflow step above.

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
- Not intended for concurrent multi-process writes â€” single-process pipeline execution is assumed.

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

### Outcome Tracking & Backfill (Phase 11b)
```bash
# Replay screener over stored history and compute all forward outcomes
.\venv\Scripts\python.exe outcomes.py --backfill

# Update pending outcomes for existing signals + re-export setup_stats.json (default)
.\venv\Scripts\python.exe outcomes.py

# Export setup_stats.json only (no outcome updates)
.\venv\Scripts\python.exe outcomes.py --export-only
```

---

## 7. GitHub Actions Deployment Status

### Workflow Files
- [`.github/workflows/daily_screener.yml`](.github/workflows/daily_screener.yml) — Scheduled production pipeline (cron `30 22 * * 1-5` + manual `workflow_dispatch`). Runs screener, commits web data to `frontend/public/data/`, and snapshots SQLite DB to orphan `data` branch.
- [`.github/workflows/frontend.yml`](.github/workflows/frontend.yml) — Frontend CI (path filtered to `frontend/**`). Runs `npm ci` and `npm run build` inside `frontend/`.
- [`.github/workflows/backend.yml`](.github/workflows/backend.yml) — Backend CI (path filtered to `backend/**`). Installs dependencies, sets up `pandas-ta --no-deps`, verifies imports, and runs `--dry-run`.

### Required Repository Secrets
All set under `Settings â†’ Secrets and variables â†’ Actions`:

| Secret Name | Maps To | Used By |
|---|---|---|
| `POLYGON_API_KEY` | `POLYGON_API_KEY` | `screener.py` â€” primary bar ingestion |
| `ALPACA_API_KEY` | `ALPACA_API_KEY` | `screener.py` â€” failover 1 header |
| `ALPACA_SECRET_KEY` | `ALPACA_SECRET_KEY` | `screener.py` â€” failover 1 header |
| `FMP_API_KEY` | `FMP_API_KEY` | `screener.py` â€” universe seeding (graceful fail) |
| `DISCORD_WEBHOOK_URL` | `DISCORD_WEBHOOK_URL` | `alerts.py` â€” webhook dispatch |
| `TELEGRAM_BOT_TOKEN` | `TELEGRAM_BOT_TOKEN` | `alerts.py` â€” Telegram bot dispatch |
| `TELEGRAM_CHAT_ID` | `TELEGRAM_CHAT_ID` | `alerts.py` â€” Telegram bot target chat |

### Commit History (Key Milestones)
```
373fe84  fix: stop tracking stock_screener.db; add to .gitignore
c963461  merge: resolve conflict keeping local stock_screener.db
c6d2b10  feat: complete Next.js dashboard with TradingView charts
b9195c9  fix: correct Alpaca secret env var name mismatch in daily_screener.yml
ace6cc9  fix: pre-install all pandas-ta sub-deps explicitly; install pandas-ta --no-deps
6d9b043  docs: update working.md to reflect Python 3.12 and --pre --no-deps fix
38d05a9  fix: upgrade to Python 3.12 and use pandas-ta --pre --no-deps
50b69ba  fix: install pandas-ta via direct zip archive to bypass git authentication
b0f1612  fix: install pandas-ta directly from GitHub repo to resolve build dependency
79be333  Initial commit - Stock Screener Pipeline
```

---

## 8. Environment Setup (Local)

```bash
# Create virtual environment (Python 3.12 locally and in Actions)
py -m venv venv
.\\venv\\Scripts\\pip install -r requirements.txt  # includes numba, tqdm, scipy, pandas_market_calendars
.\\venv\\Scripts\\pip install pandas-ta --no-deps

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
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## 9. Verified Test Results (Phase 2 Execution)

Historical scan across 4 dates using 8-symbol watchlist (223 bars per symbol):

| Date | Signals Found | Notes |
|---|---|---|
| 2025-09-19 | **3** (AAPL, MSFT, META) | AMZN RVOL 2.51x just below threshold |
| 2025-10-31 | **2** (AAPL, AMZN) | AMZN RVOL 3.11x highest of all tests |
| 2026-02-04 | **1** (AAPL) | AAPL only ticker above SMA50 with elevated RVOL |
| 2026-03-20 | **0** | Market correction phase â€” all below SMA50 |

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

### Phase 6 â€” Telegram Alert Verification
Dry-run `python run_daily.py --date 2025-09-19 --dry-run` produced correct Telegram Markdown message:
- 3 signals (AAPL, MSFT, META) listed with breakout price, RVOL, RSI
- UTF-8 emoji output confirmed on Windows terminal after `sys.stdout.reconfigure(encoding="utf-8")`
- Invalid-token test (HTTP 404) returned `None` cleanly â€” no exception raised

### Phase 7 â€” Market Regime & RS Score Filter Verification
- **SPY Market Regime Values**:
  - `2025-09-19`: SPY Close = 663.70, SMA50 = 640.61 $\rightarrow$ **Bullish**
  - `2026-03-20`: SPY Close = 648.57, SMA50 = 683.89, SMA200 = 660.36 $\rightarrow$ **Caution** (below both MAs)
  - `2026-09-22`: SPY Close = 773.38, SMA50 = 760.59, SMA200 = 717.19 $\rightarrow$ **Bullish**
- **RS Filter Validation (2026-09-23)**:
  `[INFO] Ticker META passed technical breakout but filtered out by RS score: 61.9 < 70`
  Confirmed low-percentile momentum candidates are filtered out prior to signal emission.

### Phase 8 â€” Post-Breakout Return Verification
Post-breakout forward returns verified across 5d, 10d, and 20d horizons:
- **5-Day Horizon**: Win rate 28.6% (n=7)
- **10-Day Horizon**: Win rate 14.3% (n=7)
- **20-Day Horizon**: Win rate 42.9% (n=7, max return +7.05% on AAPL)
- **Web Export**: Payloads committed to `public/data/latest_signals.json`, `signal_bars.json`, and `performance_summary.json`
- **Dashboard UI**: Next.js App Router displays RS badges, Market Regime status, and interactive 5D/10D/20D performance pills alongside TradingView Lightweight Charts.

### Phase 9 â€” Frontend Upgrade Verification
Next.js production build (`npm run build`) passed with exit code 0. Output confirmed:
```
âœ“ Compiled successfully
âœ“ Generating static pages (4/4)
Route /   8.76 kB   First Load JS 96 kB
```

**Features delivered and confirmed:**

#### 1. "Copy TradingView Watchlist" Export Button (`app/page.tsx`)
- Button renders above the signals table header with a `Copy` icon (lucide-react).
- `formatTradingViewSymbol(sym, sector)` maps sector heuristic â†’ exchange prefix:
  - `Technology / Communication` â†’ `NASDAQ:`
  - Known NYSE large-caps â†’ `NYSE:`
  - Default â†’ `NASDAQ:`
- Tickers formatted as `NASDAQ:AAPL, NYSE:META, ...` copied to clipboard via `navigator.clipboard.writeText()` with `document.execCommand('copy')` fallback.
- Floating toast notification (`toastMessage` state) confirms copy success or failure.

#### 2. Strategy Performance Stats Header (`app/page.tsx`)
Four summary metric cards rendered above the signals table:
| Card | Data Source | Detail |
|---|---|---|
| Market Regime Badge | `perfSummary.market_regime` from `performance_summary.json` | Pulsing green "Bullish" / orange "Caution"; shows SPY RS â‰¥ 70 badge |
| 10-Day Win Rate % | `perfSummary.horizon_10d.win_rate` | e.g. `14.3%` (n shown in sub-label) |
| Avg Breakout Gain % | Mean of positive `return_10d_pct` values from `latest_signals.json` | e.g. `+4.3%` â€” winners only |
| Total Breakout Setups / RVOL Leader | Count from `latest_signals.json`, highest RVOL ticker | e.g. `7 setups Â· AAPL 2.94Ã—` |

#### 3. VWAP Overlay (`components/StockChart.tsx`)
- `calculateVWAP(data: BarData[])`: uses `bar.vwap` if present, otherwise cumulative `(typical_price Ã— volume) / cumulative_volume`.
- Rendered as `ISeriesApi<'Line'>` in orange (`#f97316`), toggled by the `VWAP` button in the chart toolbar.
- Default state: **visible** (`showIndicators.vwap = true`).

#### 4. 2Ã—ATR Trailing Stop Overlay (`components/StockChart.tsx`)
- `calculateATRStop(data: BarData[], period = 14, multiplier = 2)`: Wilder's RMA smoothing for True Range â†’ ATR; ratcheting trailing stop (never re-enters downward once price rises).
- Rendered as `ISeriesApi<'Line'>` in rose (`#f43f5e`) with `LineStyle.Dashed` from `lightweight-charts`.
- Stop level = `close - 2 Ã— ATR` at each bar.
- Toggled by "2Ã— ATR Stop" button in the chart toolbar.
- Default state: **visible** (`showIndicators.atrStop = true`).

### Phase 10 â€” Pipeline Hardening & Quality Filters Verification
Executed `python run_daily.py --date 2025-09-19 --dry-run` and verified:
- **`source` Column Migration**: Added nullable `source TEXT` to `daily_bars` schema and auto-migrated cleanly in `init_db()`.
- **Alpaca IEX Volume Failover**: Alpaca `feed=sip` probed dynamically; on rejection (HTTP 403 free tier), falls back to `feed=iex` and merges `yfinance` consolidated volume into price bars, tagging them `'yfinance'`. All bars retaining `'alpaca_iex'` are strictly disqualified from generating buy signals.
- **Lookahead Resolution**:
  - `RVOL` uses prior 20 trading days' average volume: `ta.sma(df["volume"], length=20).shift(1)`.
  - 20-day high excludes today's bar: `df.iloc[:-1].tail(20)["close"].max() * 0.99`.
- **Polygon Reference Ticker Pagination**: Traverses `next_url` until exhausted with 429 rate limit backoff (12s sleep). Seeded universe expanded from ~1,000 to **5,310 active common stocks** (common stocks `type=CS` only).
- **Liquidity Filter**: Configured at top of `screener.py`:
  - `MIN_LAST_CLOSE = 5.0` ($5.00 minimum close)
- **Liquidity Filter**: Configured at top of `screener.py`:
  - `MIN_LAST_CLOSE = 5.0` (\$5.00 minimum close)
  - `MIN_AVG_DOLLAR_VOLUME = 5000000.0` (\$5M 20-day average dollar volume)
  - Applied prior to technical indicator calculations and quality filters.

### Phase 11 â€” Robustness & DB Persistence Verification
Executed `python run_daily.py --date 2025-09-19 --dry-run` and verified:
- **Holiday Skip**: `check_us_market_holiday_and_schedule()` using `pandas_market_calendars` NYSE calendar. Exits cleanly (code 0) on US market holidays if no explicit `--date` flag given.
- **Data Freshness Check**: `verify_data_freshness()` confirms â‰¥50% of active universe has bars for the expected trading day; if not, sends `send_discord_stale_alert()` and raises `RuntimeError`.
- **Exception Alerting**: Full pipeline wrapped in try/except; any unhandled exception calls `send_discord_failure_alert(step_name, error)` and re-raises.
- **Idempotency**: `clear_existing_signals_for_date(date_str)` deletes existing signals for the target date before re-screening. Re-running for the same date produces no duplicate rows.
- **DB Branch Strategy**: `stock_screener.db` removed from `main` commits. Workflow restores from `origin/data` orphan branch at start; force-pushes as single commit to `data` branch after each run. Tradeoff: no per-run DB history on `data` branch (acceptable â€” runs once daily).
- **Cron**: Changed to `30 22 * * 1-5` (22:30 UTC Monâ€“Fri).
- **`pandas_market_calendars`** added to `requirements.txt`.
- YAML validated (`yaml.safe_load` exit 0).

### Phase 11b â€” Backtest & Outcome Tracking Verification
Executed `python outcomes.py --backfill` (exit code 0):
- **Replay**: Screener replayed across all distinct historical dates with â‰¥50 prior trading sessions.
- **Signals generated**: 9 signals across historical dates (SPY, AMD, GOOGL Ã—4, META, AAPL).
- **Outcomes upserted**: 9 rows in `signal_outcomes` with `ret_1d/5d/10d/20d`, `max_drawdown_20d`, `max_runup_20d`, `spy_ret_5d/20d`, `excess_5d/20d`.
- **No lookahead**: All horizons use bars strictly `WHERE timestamp > signal_date LIMIT N`. `ret_20d = NULL` when fewer than 20 subsequent bars exist.
- **`setup_stats.json` exported**:
  ```json
  {
    "Momentum Breakout": {
      "signal_count": 9, "hit_rate_5d": 66.7, "hit_rate_20d": 55.6,
      "median_excess_5d": 0.0, "avg_excess_5d": 0.85,
      "median_excess_20d": -0.53, "avg_excess_20d": -2.46,
      "median_max_drawdown": -2.02
    }
  }
  ```
- **Full integrated pipeline** (`python run_daily.py --date 2025-09-19 --dry-run`) verified:
  - Step 6 calls `update_signal_outcomes()` â†’ "All outcomes up-to-date"
  - Step 8 exports `setup_stats.json` â†’ `Saved setup performance statistics to public/data/setup_stats.json`
  - Exit code 0.

### Phase 12 - DB Gitignore Verification
- stock_screener.db added to .gitignore (commit 373fe84) - no longer committed to main.
- Workflow restores stock_screener.db from origin/data orphan branch at start of each run.
- Workflow force-pushes DB back as single commit to data branch after each run.
- Cron updated from 0 21 * * 1-5 to 30 22 * * 1-5 (22:30 UTC Mon-Fri).
- requirements.txt expanded: added numba, tqdm, scipy alongside existing deps.


---

## 10. Node.js Environment Gotcha (Windows)

The system-level `node` / `npm` in `C:\Program Files\nodejs` is **broken** on this machine:
- PowerShell policy blocks execution of `npm.ps1`
- EPERM errors looking for profile path `C:\Users\DELL`

**Working Node.js runtime (v20.18.0):**
```powershell
$env:PATH = "C:\Users\MidasMonicker\AppData\Local\Programs\nodejs;" + $env:PATH
& "C:\Users\MidasMonicker\AppData\Local\Programs\nodejs\npm.cmd" run build
```

Always prefix Next.js/npm commands with the PATH override above, or call `npm.cmd` explicitly.

