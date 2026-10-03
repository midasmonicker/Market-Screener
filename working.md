# Market Screener â€” Complete Project Working Context

> **Purpose**: This file preserves full working context across agent, profile, and environment switches. Read this first when resuming work on this project.
> **Maintenance rule**: Update this file after every project update so it remains the current handoff context.

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
| Phase 18 | Lightweight Charts sizing: positioned the chart host to fill its chart region and added ResizeObserver-based initial sizing and resize handling. | ✅ Complete |
| Phase 19 | Corrected “20-day High Proximity” detail display: persist and export trigger-date `dist_to_20d_high_pct`; keep 52-week distance under its own label. Verified with a clean production build and `next start`; ROG displays +9.7%. | ✅ Complete |
| Phase 20 | Frontend Bug Fixes & Dashboard Data Accuracy: symbol deduplication on fetch, performance metrics sourced from `performance_summary.json`, exchange-aware watchlist copy, ResizeObserver chart resize, stable container ref, `absolute inset-0` chart host, 20d-high-proximity detail fix. | ✅ Complete |
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

### Finnhub Historical Earnings Depth
- The `/stock/earnings` response used for AMD returned four quarterly periods: 2025-09-30, 2025-12-31, 2026-03-31, and 2026-06-30.
- Requests with a wider `from`/`to` range and with `limit=8` or `limit=20` returned the same four records; AMD's 2025-06-30 quarter was absent.
- The observed response does not establish whether a higher Finnhub plan exposes older periods. When no report-date field is returned, earnings eligibility uses the approximate `EARNINGS_REPORT_LAG_DAYS_DEFAULT` buffer; a quarter missing from the response cannot be selected and may remain `null`.

### Earnings Enrichment Runtime
- Per-symbol earnings responses are cached in `backend/.cache/earnings_{SYMBOL}.json` for 168 hours. A fresh cache hit returns records without a Finnhub request; expired cache records remain usable if refresh is deferred.
- `run_screener_engine()` now runs technical screening first and enriches only symbols that produced signals, rather than walking the full historical universe.
- Each run allows at most 25 cache-miss/stale-cache API lookups and gives the step a 45-second budget. Requests time out after 5 seconds with at most one retry; any remaining signal symbols proceed without refreshed earnings fields.
- Logs report earnings cache hits, stale entries, misses, API lookups, deferred symbols, and elapsed time. The pipeline separately reports universe refresh, bar ingestion, technical screening, export, and total runtime.
- GitHub Actions restores and saves `backend/.cache` with `actions/cache@v4`; its runner is otherwise ephemeral, so a local cache alone does not warm scheduled runs.
- Cache inventory before the 2026-09-29 full run: 153 files, 143 fresh within seven days, 10 unreadable from an interrupted prior run. The 153-file JSON scan took 1.93s locally; enrichment now avoids scanning the universe and checks only signal symbols.
- Final uninterrupted `python run_daily.py --date 2026-09-29 --dry-run` completed successfully in 45.74s: universe refresh 40.52s (including two Polygon reference pagination 429 waits; 5,311 tickers), Polygon ingestion 3.51s (12,605 bars), technical screening 1.10s, earnings enrichment 0.00s (0 signals to enrich), and JSON export 0.42s.
- `frontend/public/data/latest_signals.json` was rewritten and parsed successfully (9 historical signals; no new signal was generated for 2026-09-29). The target date had 12,605 stored bars.

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

### Historical Seed Incident and Production Database (2026-09-30)
- Windows System events show Kernel-Power entering Modern Standby at 08:20:30 (idle timeout) and exiting at 08:22:45 (keyboard input), matching the reported 08:22 write cutoff. WSL `dmesg` includes DNS-resolution failures but no explicit suspend marker. The interruption is the strongest explanation for the blocked run; no code-level infinite loop or final network notification was found. Treat the Actions timeout and explicit logging shutdown as resilience, not proof of a script-level root-cause fix.
- `seed_history.py` refreshes the active ticker universe once before iterating sessions. The grouped daily provider supplies bars for a broader symbol set; the screener filters to `tickers.is_active = 1`. Seed verification: SQLite integrity `ok`, 2,430,795 bars, 14,395 distinct bar symbols, 4,982 active tickers, 200 sessions from 2025-12-11 through 2026-09-29, 200 sessions each for SPY and AAPL, and no duplicate symbol/date groups. Historical-only symbols and share/listing variants are retained as source history; they are not eligible for screening unless active in the ticker table. No cleanup is needed before screening.
- Promoted the seed to `backend/stock_screener.db` after backing up the prior 4.5 MiB DB to ignored `backend/.cache/stock_screener_before_seed_20260930.db`. Production now passes SQLite integrity checks and has the same 2,430,795 bars / 200-session date range.
- Production indicator check for AAPL on 2026-09-29: RVOL20 0.9032, RSI14 50.8781, SMA200 288.3256; SPY regime `Bullish` (SPY close 764.20, SMA200 719.25).
- The 332,972,032-byte database compresses with gzip level 1 to 145,291,393 bytes. A test of the workflow's 90 MiB split/concatenate/gzip-restore path produced two parts (largest 90 MiB) and a byte-identical database (SHA-256 `918432d78eebb0ef18e15089ec726b5b8a38a81691616f48c2f1a3cdfff33eb4`). This fits GitHub's 100 MiB per-file limit and supports retaining the compressed orphan `data` branch; restore is now staged atomically and verified with SQLite before use. Actions has a 240-minute job timeout.


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

## 11. Exchange-Aware Export and Statistics Boundaries (2026-10-01)
- The watchlist export formats each currently filtered ticker using its `primary_exchange` MIC, mapping known MICs to TradingView exchange prefixes.
- `latest_signals.json` is limited to signals on the newest 10 distinct dates in `daily_bars`.
- `performance_summary.json` queries the full `buy_signals` table; its activity average uses the 30 calendar days ending on the latest signal date. `setup_stats.json` uses database outcomes across signal history, independent of the latest-signals window.
- Verified with a clean Next.js production build; backend syntax checks passed for `screener.py` and `outcomes.py`.

## 12. Dashboard Null-Value Handling (2026-10-01)
- `frontend/components/TrackRecord.tsx` treats `hit_rate_5d` and `hit_rate_20d` as nullable and renders `—` instead of calling `.toFixed()` on null. `ScorePill` retains its intentional `Pending` fallback.
- Verified against the live production JSON: `setup_stats.json` has null 5d/20d hit rates with zero completed outcomes. `latest_signals.json` had 29 records total, 18 dated 2026-09-30; both source return fields and the page’s direct numeric-format fields were audited. The page’s other `.toFixed()` calls were guarded or operated on derived values with early exits.
- Production `npm run build` passed. Browser verification with the live JSON showed signal rows and the Track Record card with `—` hit rates, without an Application error or `.toFixed()` TypeError.

## 13. Live Watchlist Verification (2026-10-01)
- A trusted browser click on the rebuilt production app at `http://localhost:3001` copied 27 unique tickers, matching the default `Showing 27 of 27 setups` table. ARX resolved to `NYSE:ARX`; ROG and UTZ mapped from XNYS to NYSE, and AMPL mapped from XNAS to NASDAQ.
- With Min RVOL set to 3.0x, the table showed 2 of 27 and the copy action produced `NYSE:ROG, NYSE:CAAP` (2 tickers).
- Direct fetch of `latest_signals.json` returned 29 records across two dates, with `primary_exchange` present on every record. The table now keeps the newest signal per symbol, and the dashboard tolerates older `performance_summary.json` files without `signal_activity`.

## 14. Disclaimer and Company News (2026-10-01)
- Added an always-visible, non-dismissible financial disclaimer inside the sticky dashboard header.
- Added Finnhub `/company-news` enrichment for signal-producing symbols only: seven-day lookback, three headlines maximum, per-symbol four-hour cache, heuristic catalyst categories, 40-lookup cap, and 45-second budget. Earnings and news now share one paced request helper with a 1.1-second minimum interval and the existing 429 retry/backoff.
- Exported headline, source, URL, date, and category on each signal. Added the Recent News disclosure beside Why It Triggered, including linked category-tagged items and a no-news empty state.
- A real 27-symbol refresh returned 58 headlines for 22 symbols; five had no news. Browser verification on the clean production build showed three real AMPL articles with `Other`, `Earnings/Guidance`, and `Analyst Rating` categories; ROG rendered the empty state in an earlier no-news check. The disclaimer was visible on the dashboard.
- Resolved 43 committed conflict blocks in `latest_signals.json` by keeping the current HEAD side; validated 29 records, 27 symbols, and all exchange/high-proximity fields before enriching the snapshot with news.
- Deleted `.next` before the final production build. Build passed compilation, lint/type checks, static generation, and build-trace collection.

## 15. Generated Web Data Policy (2026-10-01)
- `frontend/public/data/*.json` files are generated outputs from the backend export and are committed by the scheduled Actions workflow for static frontend rendering. They are not source files; do not hand-edit them for changes that will be committed.
- For one-off local tests, prefer fixtures or an in-memory Playwright response override. If a local payload must be changed, keep it out of commits and restore it before pushing.
- `frontend/scripts/validate-data-json.mjs` recursively parses every JSON payload and rejects literal Git conflict markers. It runs before the daily bot commit and in frontend push/PR CI. The repository has no existing pre-commit hook or contributor guide.
- The repository has no Vercel configuration or deployment workflow. Replacing committed static payloads with deploy-time artifacts would require adding an authenticated deployment/upload path and coordinating it with frontend builds; defer that larger change until deployment ownership and target are established.

## 16. SEC Insider Activity (2026-10-01)
- SEC source paths: `https://www.sec.gov/files/company_tickers.json`, `https://data.sec.gov/submissions/CIK##########.json`, and the raw filing submission at `/Archives/edgar/data/{CIK}/{accession-without-dashes}/{accession}.txt`. The raw `.txt` contains the parseable `<ownershipDocument>`; the corresponding primary `.xml` path may be served as rendered HTML.
- All SEC requests use `User-Agent: Market Screener michaelonyeweke@yahoo.com` and a 0.35-second minimum inter-request delay. CIK associations persist in `backend/.cache`; per-symbol insider results cache for four hours.
- Only screened signals are queried. The default lookback is 21 calendar days; only non-derivative transaction code `P` with acquired/disposed code `A` is included. The export carries `insider_activity` and `insider_cluster`; SEC failures leave empty activity without blocking signal persistence.
- Real SEC PGEN Form 4 check: Director AGEE NANCY H bought 3,411 shares on 2026-08-21 at $7.23 ($24,661.53), filed 2026-08-25. This predates the production lookback; it was injected only through an in-memory browser response to verify the populated panel. PGEN is empty in the live 21-day lookup; ROG’s real browser modal showed the empty state.
- After deleting `frontend/.next`, the clean Next.js production build passed compilation, lint/type checks, static generation, and trace collection. Browser verification used `http://localhost:3001`; no changes were made to the existing Finnhub news or earnings helpers.

## 17. FINRA Consolidated Short Interest (2026-10-01)
- FINRA Regulation Short Interest file (bi-monthly settlement, EoD) is parsed and stored. The consolidated file (~22,595 securities per settlement date) is ingested into the `short_interest` SQLite table (`symbol`, `settlement_date`, `current_short_position`, `previous_short_position`, `avg_daily_volume`, `days_to_cover`, `change_pct`, `market_class`). Signal enrichment performs an instant local SQLite indexed query (`idx_short_interest_sym_date`).
- **Conditional Refresh**: `refresh_short_interest_data(as_of_date)` compares `MAX(settlement_date)` in SQLite against the latest available published FINRA date (e.g. 2026-09-15). Re-downloads only when a new settlement period is published; skips download when already up to date.
- **Frontend Panel**: Added `Short Interest` disclosure panel in `frontend/components/StockChart.tsx` displaying Days to Cover (DTC), Shares Short (with % change vs prior period), % Float Short, and prominently displays the settlement date with an explicit staleness disclaimer: `As of YYYY-MM-DD (data lags ~1–2 weeks)`. Displays an alert pill when DTC >= 5.0d indicating elevated squeeze risk. Renders a clean empty state (`No short interest data available for {symbol}`) if no record exists.
- **Production Build & Browser Verification**: Next.js production build succeeded with 0 errors after deleting `.next`. Automated Chrome CDP verification at `http://localhost:3001` confirmed both populated rendering (ROG: DTC 4.05d, 629,013 shares short, +9.19%, as of 2026-09-15) and clean empty state rendering.

## 18. Frontend Bug Fixes & Dashboard Data Accuracy (2026-10-02)
Resolved a suite of frontend rendering bugs in `frontend/app/page.tsx` and `frontend/components/StockChart.tsx`:

### `page.tsx` Fixes
- **Deduplication on load**: Signals are now deduplicated by symbol client-side on fetch — only the most recent signal per ticker (by `timestamp`) is kept in state. Previously all historical signals were rendered, inflating the "Showing N of N setups" count.
- **Performance metrics sourced from backend**: `todayCount`, `avg30Count`, and `avgBreakoutGain` now come from `performance_summary.json` (`signal_activity.today_count`, `signal_activity.avg_per_signal_day_30d`, `avg_breakout_gain_pct`) rather than being re-derived client-side from the limited `latest_signals.json` window. The `performance_summary.json` fetch was added to the parallel load block.
- **Watchlist copy deduplication**: `handleCopyTradingViewWatchlist` now filters duplicates from `displaySignals` using a `Set` before formatting, preventing repeated tickers in the clipboard output.
- **Exchange-aware watchlist**: `formatTradingViewSymbol` is now called with `signal.primary_exchange` (the Polygon MIC code) rather than `signal.sector`, fixing incorrect exchange prefix mapping.
- **Null-safe metric cards**: `todayCount` and `avg30Count` are guarded for `null`/undefined before rendering; the signal summary line now reads from `performanceSummary.total_signals` with a fallback message.

### `StockChart.tsx` Fixes
- **Chart resize via ResizeObserver**: Replaced `window.addEventListener('resize', ...)` with a `ResizeObserver` on the chart container `div`, giving accurate resize events without relying on the window-level event. The observer is disconnected on cleanup.
- **Stable container ref in closure**: The `chartContainerRef.current` value is captured into a local `chartContainer` variable at the top of the `useEffect` to prevent stale-ref issues in the cleanup function.
- **Chart host uses `absolute inset-0`**: Changed `className="w-full h-full"` to `className="absolute inset-0"` on the chart container `div`, ensuring the chart fills its absolutely-positioned host region correctly.
- **20-day High Proximity fix**: `dist_to_20d_high_pct` is now used for the "20-day High Proximity" trigger detail (was incorrectly using `dist_to_52w_high_pct`). Sign-formatted as `+X.X%` / `-X.X%`; `passed` reflects whether the value is ≥ -1 (within 1% of 20d high), or defaults `true` when null.

## 19. Mirrored Momentum Breakdown Strategy (2026-10-02)
- `process_single_ticker_screener()` now emits either `Momentum Breakout` or `Momentum Breakdown` from the same daily scan. Breakdown gates are RVOL >= 1.5, RSI 25-50, close below the 50-SMA, within 1% of the prior 20-close minimum (today excluded), `rs_vs_spy <= -70pp`, and below the 20-EMA / 50-SMA / 200-SMA. Both branches keep point-in-time calculations and the existing earnings gate.
- Composite scores retain the existing 0-100 weighted component structure, but proximity, RS magnitude, and trend alignment are interpreted directionally. Signal streaks and exports key on setup name so same-symbol setups do not collapse together.
- SEC Form 4 parsing now keeps open-market P/A purchases and S/D sales separately. The existing CIK lookup, request pacing, 21-day lookback, cache TTL, and budget are reused; cache schema version 2 prevents old purchase-only results being treated as complete. Sales are supplemental context only. Finnhub headlines receive bullish/bearish/neutral tags, including for cached headlines. Breakdown signals attach FINRA DTC >= 5 as a squeeze-risk caution.
- `get_performance_summary()` reports per-setup activity and directional horizon stats; breakdown losses in underlying price count as short wins. `export_setup_stats()` reports setup-separated hit rates, inverse-SPY excess returns, and short-side adverse upside excursion. The dashboard's Bullish/Bearish tabs scope cards, filters, table, portfolio sizing, and Track Record to one setup; switching modes clears selection. Breakdown chart stops use entry + 2xATR, with the short-loss asymmetry cue.
- Backend regression tests: `python -m unittest test_momentum_breakdown.py` passes 5 tests for both setup rules, SEC S/D parsing, direction-aware summary stats, and setup-separated Track Record output.
- Clean Next.js production build after deleting `.next` passed compilation, lint/type checking, static page generation, and trace collection (exit code 0). Real browser with current static data showed 51 bullish results; bearish mode correctly showed 0 results and its empty state; switching back restored bullish rows. A browser-only synthetic fixture (not written to JSON/SQLite) verified the bearish checklist, $100 entry / $4 ATR -> $108 stop, short asymmetry note, SEC sale rendering, DTC 6.2 squeeze caution, directional news tags, portfolio mode isolation, and breakdown-only Track Record.
- Local production DB coverage is stale at 2026-09-29 (4,973 active symbols with bars). The most recent stored session and the four preceding stored sessions each produced 0 breakdown candidates under the requested -70pp rule. The exported static snapshot has newer Oct. 1 bullish signals, but no qualifying real breakdown candidate. Do not report the synthetic fixture as a real signal; a real breakdown detail panel remains unverified until the universe produces one.

## 20. Date-Grouped Signal Table (2026-10-03)
- Group the currently filtered active-view signal list by `timestamp`; date groups stay newest-first, and the selected sortable column sorts rows within each date. The Date column is informational/non-sortable because group order is fixed.
- The newest date group defaults expanded; older groups default collapsed. Group counts use the filtered rows, and clicking a header toggles that date inline. Existing signal rows, mobile drawers, filters, and the Bullish/Bearish view scope are preserved.
- The result counter and helper text explicitly say counts and TradingView export cover the full filtered set across all dates; collapsing groups does not alter the filter or export input.
- Mobile browser verification at 375px showed the same date headers and working expansion/drawers. The sticky header's redundant `As of` badge is hidden below `sm` (the Data Freshness card retains the date) to prevent page-level horizontal overflow.
- Clean production build after deleting `.next` passed. Real browser dataset: Oct. 1 expanded by default (27 signals), Sep. 30 collapsed (15), Sep. 29 collapsed (9), 51 total. Expanding Sep. 30 changed visible table rows 31 -> 46; collapsing returned to 31. At mobile width the same expansion worked and body width had no overflow. Sorting RVOL within Oct. 1 changed its first row without changing date-group order. Copy TradingView while Sep. 29 remained collapsed copied 51 tickers, matching the full filtered set.

## 21. Intraday Checkpoint Screening (2026-10-03)
- Added intraday screening at approximately 11:00am, 1:00pm, and 3:30pm ET using the existing daily technical history plus current Alpaca IEX snapshot bars. Checkpoint runs reuse the persisted active ticker universe; they do not refresh ticker references, ingest daily bars, send alerts, or perform Finnhub/SEC/API ticker enrichment. NYSE calendar and open-session checks skip holidays, weekends, and closed/early-closed sessions.
- GitHub Actions includes paired UTC schedules for EST/EDT: 15:00/16:00 UTC (11am), 17:00/18:00 UTC (1pm), and 19:30/20:30 UTC (3:30pm). The non-seasonal companion schedule exits before restoring the database. Checkpoints starting more than five minutes after their scheduled ET target skip rather than compare partial volume at a different time; the regular EOD run remains at 22:30 UTC.
- Alpaca Basic is free, provides real-time IEX coverage and historical data since 2016; its historical bars endpoint supports minute bars from `1Min` through `59Min` (including `30Min`), up to 10,000 data points per page, and 200 historical requests/minute. Basic historical queries restrict the most recent 15 minutes. Alpaca describes IEX as approximately 2.5% of market volume. Checkpoint RVOL therefore requests the prior 20 sessions of `30Min` IEX bars and compares current cumulative IEX volume with the average IEX volume through the same ET checkpoint time, clamping historical sessions to their actual close. Its basis is `partial_day_iex_vs_prior_20d_same_time_iex`; it is explicitly labeled `IEX time-of-day RVOL (not comparable to EOD)` because EOD RVOL can use consolidated volume. Missing baselines are not silently replaced with a mismatched full-day denominator.
- Checkpoint rows are keyed separately from EOD rows. Repeated checkpoint hits update one same-day symbol/setup row, retain first/last-seen checkpoint metadata and observations, and show whether the setup remained active. Current intraday prices feed RSI, moving-average alignment, and relative-strength calculations; the 20-day dollar-volume liquidity baseline excludes the partial current session.
- `latest_signals.json` includes checkpoint-only dates and distinguishes an EOD signal from its same-day checkpoint. The existing date-grouped table renders the amber `Intraday · {time}` badge and `IEX · time-adjusted` RVOL label in the same group; checkpoint rows are kept separate from EOD rows when loading.
- Post-signal performance, `performance_summary.json`, `signal_outcomes`, `setup_stats.json`, backfill cleanup, and alert queries are restricted to `checkpoint_id='eod'`; Track Record/backtesting remains full-day only.
- Verification: 8 backend regression tests passed, covering same-day deduplication, EST/EDT checkpoint selection, EOD-only outcomes/statistics, and existing Momentum Breakdown behavior. GitHub Actions YAML parsed; Python diagnostics were clean. A clean production Next.js build passed after deleting `frontend/.next`. Production-browser fixture verification showed two same-day TEST rows (EOD and 11am checkpoint), the checkpoint badge and “Still active through 1:00pm” note, partial-IEX RVOL labeling, newest date expanded with the older group collapsed, and RVOL sorting within that date. The Track Record fixture showed one EOD signal; no generated JSON or database fixture was written.
