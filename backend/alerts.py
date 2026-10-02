import os
import sys
import sqlite3
import datetime
import json
import logging
import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

load_dotenv()
DB_PATH             = "stock_screener.db"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")


def get_recent_signals(date_str=None, limit=20):
    """Fetch signals from buy_signals table for a specific date or latest records."""
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    if date_str:
        cur.execute("""
            SELECT id, timestamp, symbol, setup_name, close_price, rvol, rsi, details, created_at,
                   return_5d_pct, return_10d_pct, return_20d_pct
            FROM buy_signals
            WHERE timestamp = ?
            ORDER BY rvol DESC
            LIMIT ?
        """, (date_str, limit))
    else:
        cur.execute("""
            SELECT id, timestamp, symbol, setup_name, close_price, rvol, rsi, details, created_at,
                   return_5d_pct, return_10d_pct, return_20d_pct
            FROM buy_signals
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))

    rows = cur.fetchall()
    conn.close()

    signals = []
    for r in rows:
        details_val    = r[7]
        parsed_details = {}
        if details_val:
            try:
                parsed_details = json.loads(details_val) if isinstance(details_val, str) else details_val
            except Exception:
                parsed_details = {}

        signals.append({
            "id":              r[0],
            "timestamp":       r[1],
            "symbol":          r[2],
            "setup_name":      r[3],
            "close_price":     float(r[4]),
            "rvol":            float(r[5]),
            "rsi":             float(r[6]),
            "details":         parsed_details,
            "market_regime":   parsed_details.get("market_regime", "Unknown"),
            "rs_score":        parsed_details.get("rs_score"),
            "created_at":      r[8],
            "return_5d_pct":   r[9],
            "return_10d_pct":  r[10],
            "return_20d_pct":  r[11],
            # New fields surfaced from details JSON
            "composite_score":      parsed_details.get("composite_score"),
            "signal_streak":        parsed_details.get("signal_streak", 1),
            "near_earnings":        parsed_details.get("near_earnings", False),
            "earnings_date":        parsed_details.get("earnings_date"),
            "pct_change_1d":        parsed_details.get("pct_change_1d"),
            "atr_pct":              parsed_details.get("atr_pct"),
            "rs_vs_spy":            parsed_details.get("rs_vs_spy"),
            "rs_vs_sector":         parsed_details.get("rs_vs_sector"),
            "dist_to_52w_high_pct": parsed_details.get("dist_to_52w_high_pct"),
        })
    return signals


def build_discord_payload(signals, date_str=None):
    """
    Construct rich embed JSON payload for Discord Webhook.

    De-duplication rule: only signals with signal_streak == 1 (first breakout day)
    are alerted.  Continuing streaks are mentioned in the embed description so the
    user knows they exist but are not spammed with a repeat card each day.

    Each new-breakout field shows:
      - Composite score (0-100)
      - Earnings warning badge (if near_earnings is True)
      - Price + today's % change, RVOL, RSI
      - RS Score + RS vs SPY differential, Market Regime
    """
    target_date = date_str or (signals[0]["timestamp"] if signals else datetime.date.today().isoformat())

    # Filter: Discord alerts only for brand-new breakout days (streak == 1)
    new_breakouts = [s for s in signals if (s.get("signal_streak") or 1) == 1]

    if not new_breakouts:
        no_signal_msg = "No new momentum breakout setups today."
        if signals:
            continuing = [s["symbol"] for s in signals if (s.get("signal_streak") or 1) > 1]
            if continuing:
                no_signal_msg += (
                    "\n_(Continuing streaks: "
                    + ", ".join(continuing[:10])
                    + (" ..." if len(continuing) > 10 else "")
                    + " - shown in dashboard only)_"
                )
        return {
            "username":   "Market Screener Bot",
            "avatar_url": "https://img.icons8.com/color/96/bullish.png",
            "embeds": [{
                "title":       "\U0001f4ca Daily Stock Screener: " + target_date,
                "description": no_signal_msg,
                "color":       0x808080,
                "footer":      {"text": "Automated SQLite Screener Engine"},
                "timestamp":   datetime.datetime.now(datetime.timezone.utc).isoformat()
            }]
        }

    fields = []
    for sig in new_breakouts[:10]:
        rs_val    = sig.get("rs_score")
        rs_str    = f"{rs_val:.1f}" if rs_val is not None else "N/A"
        regime    = sig.get("market_regime", "Unknown")
        score     = sig.get("composite_score")
        score_str = f"**Score:** {score:.0f}/100\n" if score is not None else ""

        # Earnings warning badge
        near_earn = sig.get("near_earnings", False)
        earn_date = sig.get("earnings_date")
        if near_earn and earn_date:
            earn_warning = "\u26a0\ufe0f **EARNINGS " + earn_date + "**\n"
        elif near_earn:
            earn_warning = "\u26a0\ufe0f **NEAR EARNINGS**\n"
        else:
            earn_warning = ""

        rs_vs_spy  = sig.get("rs_vs_spy")
        rs_spy_str = f" | **RS vs SPY:** {rs_vs_spy:+.1f}pp" if rs_vs_spy is not None else ""

        pct_chg = sig.get("pct_change_1d")
        chg_str = f" | **Chg:** {pct_chg:+.2f}%" if pct_chg is not None else ""

        field_value = (
            earn_warning
            + score_str
            + f"**Price:** ${sig['close_price']:.2f}" + chg_str + "\n"
            + f"**RVOL:** {sig['rvol']:.2f}x | **RSI:** {sig['rsi']:.1f}\n"
            + f"**RS Score:** {rs_str}" + rs_spy_str + f" | **Regime:** {regime}"
        )
        fields.append({
            "name":   "\U0001f680 " + sig["symbol"] + " - " + sig["setup_name"],
            "value":  field_value,
            "inline": True
        })

    new_count  = len(new_breakouts)
    continuing = len(signals) - new_count
    desc = f"**{new_count}** new breakout candidate{'s' if new_count != 1 else ''}"
    if continuing > 0:
        desc += f" _(+{continuing} continuing streak{'s' if continuing != 1 else ''} - see dashboard)_"
    desc += " passing volume, momentum, and quality filters."

    embed = {
        "title":       "\U0001f3af Momentum Breakout Signals: " + target_date,
        "description": desc,
        "color":       0x2ECC71,
        "fields":      fields,
        "footer":      {"text": f"Scanned Universe | {new_count} New Setup{'s' if new_count != 1 else ''}"},
        "timestamp":   datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    return {
        "username":   "Market Screener Bot",
        "avatar_url": "https://img.icons8.com/color/96/bullish.png",
        "embeds":     [embed]
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


def send_discord_failure_alert(step_name, error_message, date_str=None, dry_run=False):
    """
    Constructs and dispatches a high-priority failure alert to Discord Webhook.
    """
    target_date = date_str or datetime.date.today().isoformat()
    payload = {
        "username":   "Market Screener Bot",
        "avatar_url": "https://img.icons8.com/color/96/error.png",
        "embeds": [{
            "title":       "\U0001f6a8 PIPELINE FAILURE: " + step_name,
            "description": f"The automated stock screener failed during **{step_name}** on `{target_date}`.",
            "color":       0xE74C3C,
            "fields": [{
                "name":   "Error Details",
                "value":  f"```{str(error_message)[:1000]}```",
                "inline": False
            }],
            "footer":    {"text": "Antigravity Screener Automation \u2022 Immediate attention required"},
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }]
    }

    if dry_run:
        logger.info("[DRY RUN] Generated Discord Failure payload for %s: %s", step_name, error_message)
        return payload

    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL is not set in environment.")
        return payload

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code in (200, 204):
            logger.info("Successfully dispatched Discord failure alert for %s.", step_name)
        else:
            logger.error("Failed to send Discord failure alert: HTTP %d %s", res.status_code, res.text)
    except Exception as e:
        logger.error("Exception sending Discord failure alert: %s", e)

    return payload


def send_discord_stale_alert(expected_date, actual_date, fresh_count, universe_count, dry_run=False):
    """
    Constructs and dispatches a DATA STALE alert to Discord Webhook.
    """
    payload = {
        "username":   "Market Screener Bot",
        "avatar_url": "https://img.icons8.com/color/96/warning.png",
        "embeds": [{
            "title": "\u26a0\ufe0f DATA STALE ALERT",
            "description": (
                f"The daily bar ingestion failed freshness validation for expected trading day `{expected_date}`.\n\n"
                f"**Fresh Bars Count:** {fresh_count} / {universe_count} active tickers\n"
                f"**Most Common Date in DB:** `{actual_date}`"
            ),
            "color":     0xF39C12,
            "footer":    {"text": "Data Pipeline Freshness Gatekeeper"},
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }]
    }

    if dry_run:
        logger.info("[DRY RUN] Generated Discord DATA STALE payload for %s (actual: %s).", expected_date, actual_date)
        return payload

    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL is not set in environment.")
        return payload

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code in (200, 204):
            logger.info("Successfully dispatched Discord DATA STALE alert.")
        else:
            logger.error("Failed to send Discord DATA STALE alert: HTTP %d %s", res.status_code, res.text)
    except Exception as e:
        logger.error("Exception sending Discord DATA STALE alert: %s", e)

    return payload


def _normalize_signal(sig):
    """Normalizes dict or tuple representations of buy signals."""
    if isinstance(sig, dict):
        parsed_details = sig.get("details")
        if isinstance(parsed_details, str):
            try:
                parsed_details = json.loads(parsed_details)
            except Exception:
                parsed_details = {}
        elif not isinstance(parsed_details, dict):
            parsed_details = {}

        return {
            "timestamp":       sig.get("timestamp"),
            "symbol":          sig.get("symbol"),
            "setup_name":      sig.get("setup_name", "Momentum Breakout"),
            "close_price":     float(sig.get("close_price") or 0.0),
            "rvol":            float(sig.get("rvol") or 0.0),
            "rsi":             float(sig.get("rsi") or 0.0),
            "market_regime":   sig.get("market_regime") or parsed_details.get("market_regime", "Unknown"),
            "rs_score":        sig.get("rs_score") if sig.get("rs_score") is not None else parsed_details.get("rs_score"),
            "composite_score": sig.get("composite_score") if sig.get("composite_score") is not None else parsed_details.get("composite_score"),
            "near_earnings":   sig.get("near_earnings", False) or parsed_details.get("near_earnings", False),
            "earnings_date":   sig.get("earnings_date") or parsed_details.get("earnings_date"),
            "rs_vs_spy":       sig.get("rs_vs_spy") or parsed_details.get("rs_vs_spy"),
        }
    elif isinstance(sig, (list, tuple)):
        # (date_str, symbol, setup_name, close_price, rvol, rsi, details_json, ...)
        details_val    = sig[6] if len(sig) > 6 else None
        parsed_details = {}
        if details_val:
            try:
                parsed_details = json.loads(details_val) if isinstance(details_val, str) else details_val
            except Exception:
                parsed_details = {}

        return {
            "timestamp":       sig[0] if len(sig) > 0 else None,
            "symbol":          sig[1] if len(sig) > 1 else "UNKNOWN",
            "setup_name":      sig[2] if len(sig) > 2 else "Momentum Breakout",
            "close_price":     float(sig[3]) if len(sig) > 3 and sig[3] is not None else 0.0,
            "rvol":            float(sig[4]) if len(sig) > 4 and sig[4] is not None else 0.0,
            "rsi":             float(sig[5]) if len(sig) > 5 and sig[5] is not None else 0.0,
            "market_regime":   parsed_details.get("market_regime", "Unknown"),
            "rs_score":        parsed_details.get("rs_score"),
            "composite_score": parsed_details.get("composite_score"),
            "near_earnings":   parsed_details.get("near_earnings", False),
            "earnings_date":   parsed_details.get("earnings_date"),
            "rs_vs_spy":       parsed_details.get("rs_vs_spy"),
        }
    return None


def format_telegram_message(signals, date_str=None):
    """
    Format a clean Markdown message displaying date, ticker count,
    ticker symbols, breakout prices, RVOL, RSI, RS score, composite score,
    and earnings warning.
    """
    norm_signals = []
    for s in (signals or []):
        ns = _normalize_signal(s)
        if ns:
            norm_signals.append(ns)

    target_date = date_str or (norm_signals[0]["timestamp"] if norm_signals else datetime.date.today().isoformat())

    if not norm_signals:
        return (
            f"\U0001f4ca *Daily Stock Screener: {target_date}*\n\n"
            f"No momentum breakout setups met the screening criteria today."
        )

    lines = [
        f"\U0001f3af *Momentum Breakout Signals: {target_date}*",
        f"Found *{len(norm_signals)}* breakout candidate{'s' if len(norm_signals) != 1 else ''}:\n"
    ]

    for sig in norm_signals[:25]:
        sym   = str(sig["symbol"]).replace("_", "\\_")
        setup = str(sig.get("setup_name", "Momentum Breakout")).replace("_", "\\_")
        price = f"${sig['close_price']:.2f}"
        rvol  = f"{sig['rvol']:.2f}x"
        rsi   = f"{sig['rsi']:.1f}"

        rs_val    = sig.get("rs_score")
        rs_str    = f" | RS: `{rs_val:.1f}`" if rs_val is not None else ""

        score     = sig.get("composite_score")
        score_str = f" | Score: `{score:.0f}`" if score is not None else ""

        near_earn = sig.get("near_earnings", False)
        earn_date = sig.get("earnings_date")
        earn_str  = (
            f"\n  \u26a0\ufe0f *Earnings: {earn_date}*" if near_earn and earn_date
            else ("\n  \u26a0\ufe0f *Near Earnings*" if near_earn else "")
        )

        lines.append(
            f"\u2022 *{sym}* ({setup})\n"
            f"  Price: `{price}` | RVOL: `{rvol}` | RSI: `{rsi}`{rs_str}{score_str}{earn_str}"
        )

    if len(norm_signals) > 25:
        lines.append(f"\n_...and {len(norm_signals) - 25} more setups._")

    return "\n".join(lines)


def send_telegram_alert(signals, bot_token=None, chat_id=None, dry_run=False, target_date=None):
    """
    Format a clean Markdown message and dispatch HTTP POST request to Telegram sendMessage API.
    Handles errors gracefully without throwing exceptions.
    """
    try:
        token = bot_token or TELEGRAM_BOT_TOKEN
        chat  = chat_id  or TELEGRAM_CHAT_ID

        if signals is None:
            signals = get_recent_signals(target_date)

        message_text = format_telegram_message(signals, date_str=target_date)

        if dry_run:
            logger.info("[DRY RUN] Generated Telegram alert message:\n%s", message_text)
            return {"ok": True, "dry_run": True, "message": message_text}

        if not token or not chat:
            logger.warning("Telegram alert skipped: bot_token or chat_id is missing.")
            return None

        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id":                chat,
            "text":                   message_text,
            "parse_mode":             "Markdown",
            "disable_web_page_preview": True
        }

        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            logger.info("Successfully dispatched Telegram alert (%d signals).", len(signals) if signals else 0)
            return res.json()
        else:
            logger.error("Failed to send Telegram alert: HTTP %d %s", res.status_code, res.text)
            return None
    except Exception as e:
        logger.error("Exception sending Telegram alert: %s", e)
        return None


if __name__ == "__main__":
    test_signals = get_recent_signals()
    print(f"Loaded {len(test_signals)} signals from DB.")
    payload = build_discord_payload(test_signals)
    print(json.dumps(payload, indent=2))
    print("\n--- Telegram Message Preview ---")
    tg_msg = format_telegram_message(test_signals)
    print(tg_msg)
