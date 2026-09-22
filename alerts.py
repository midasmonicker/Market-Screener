import os
import sys
import sqlite3
import datetime
import logging
import requests
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

load_dotenv()
DB_PATH = "stock_screener.db"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def get_recent_signals(date_str=None, limit=20):
    """Fetch signals from buy_signals table for a specific date or latest records."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if date_str:
        cur.execute("""
            SELECT id, timestamp, symbol, setup_name, close_price, rvol, rsi, created_at
            FROM buy_signals
            WHERE timestamp = ?
            ORDER BY rvol DESC
            LIMIT ?
        """, (date_str, limit))
    else:
        cur.execute("""
            SELECT id, timestamp, symbol, setup_name, close_price, rvol, rsi, created_at
            FROM buy_signals
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
    
    rows = cur.fetchall()
    conn.close()
    
    signals = []
    for r in rows:
        signals.append({
            "id": r[0],
            "timestamp": r[1],
            "symbol": r[2],
            "setup_name": r[3],
            "close_price": float(r[4]),
            "rvol": float(r[5]),
            "rsi": float(r[6]),
            "created_at": r[7]
        })
    return signals

def build_discord_payload(signals, date_str=None):
    """
    Construct rich embed JSON payload for Discord Webhook.
    """
    target_date = date_str or (signals[0]["timestamp"] if signals else datetime.date.today().isoformat())
    
    if not signals:
        return {
            "username": "Market Screener Bot",
            "avatar_url": "https://img.icons8.com/color/96/bullish.png",
            "embeds": [
                {
                    "title": f"📊 Daily Stock Screener: {target_date}",
                    "description": "No momentum breakout setups met the screening criteria today.",
                    "color": 0x808080, # Gray
                    "footer": {
                        "text": "Automated SQLite Screener Engine"
                    },
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
            ]
        }

    fields = []
    for sig in signals[:10]: # Top 10 signals
        field_value = (
            f"**Price:** ${sig['close_price']:.2f}\n"
            f"**RVOL:** {sig['rvol']:.2f}x\n"
            f"**RSI (14):** {sig['rsi']:.1f}"
        )
        fields.append({
            "name": f"🚀 {sig['symbol']} - {sig['setup_name']}",
            "value": field_value,
            "inline": True
        })

    embed = {
        "title": f"🎯 Momentum Breakout Signals: {target_date}",
        "description": f"Identified **{len(signals)}** high-probability breakout candidates meeting volume, momentum, and MA conditions.",
        "color": 0x2ECC71, # Emerald Green
        "fields": fields,
        "footer": {
            "text": f"Scanned Universe | {len(signals)} Active Setups"
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    return {
        "username": "Market Screener Bot",
        "avatar_url": "https://img.icons8.com/color/96/bullish.png",
        "embeds": [embed]
    }

def send_discord_alerts(date_str=None, dry_run=False):
    """
    Reads signals and dispatches payload to DISCORD_WEBHOOK_URL.
    If dry_run is True, returns payload without making external HTTP POST.
    """
    signals = get_recent_signals(date_str)
    payload = build_discord_payload(signals, date_str)

    if dry_run:
        logger.info("[DRY RUN] Generated Discord Webhook payload for %d signals.", len(signals))
        return payload

    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL is not set in environment.")
        return payload

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code in (200, 204):
            logger.info("Successfully dispatched Discord alert with %d signals.", len(signals))
        else:
            logger.error("Failed to send Discord alert: HTTP %d %s", res.status_code, res.text)
    except Exception as e:
        logger.error("Exception sending Discord alert: %s", e)

    return payload

if __name__ == "__main__":
    test_signals = get_recent_signals()
    print(f"Loaded {len(test_signals)} signals from DB.")
    payload = build_discord_payload(test_signals)
    import json
    print(json.dumps(payload, indent=2))
