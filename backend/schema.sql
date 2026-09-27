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
    source TEXT,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    price_5d REAL,
    price_10d REAL,
    price_20d REAL,
    return_5d_pct REAL,
    return_10d_pct REAL,
    return_20d_pct REAL
);

CREATE INDEX IF NOT EXISTS idx_bars_symbol_time ON daily_bars(symbol, timestamp DESC);

-- Signal Outcomes & Backtest Tracking
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

CREATE INDEX IF NOT EXISTS idx_outcomes_date ON signal_outcomes(signal_date);
