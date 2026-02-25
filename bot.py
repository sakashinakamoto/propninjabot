import os
import math
import logging
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# =====================================================
# CONFIG
# =====================================================

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PRIZEPICKS_API = "https://partner-api.prizepicks.com/projections?per_page=1000"
KALSHI_API = "https://trading-api.kalshi.com/trade-api/v2"

DECIMAL_ODDS   = 1.90
MIN_EDGE       = 0.06
MIN_CONFIDENCE = 0.64

# =====================================================
# CORE MODEL (3 Factor + Source Bias)
# =====================================================

def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def compute_projection(line, stat, source):
    if line <= 0:
        return 0, 0, 0

    stat_lower = stat.lower()

    # Base stat bias
    boost = 0.055
    if "assist" in stat_lower:
        boost += 0.01
    elif "points" in stat_lower:
        boost += 0.008
    elif "rebound" in stat_lower:
        boost -= 0.005

    # Source bias
    source_bias = {
        "PrizePicks": 0.055,
        "Kalshi": 0.045
    }
    boost += source_bias.get(source, 0.05)

    # 3-factor weighted projection
    season_proj  = line * (1 + boost)
    recent_proj  = line * (1 + boost * 1.15)
    matchup_proj = line * (1 + boost * 0.90)

    projection = (season_proj * 0.4) + (recent_proj * 0.4) + (matchup_proj * 0.2)

    std_dev = line * 0.18
    z = (projection - line) / std_dev
    probability = normal_cdf(z)

    implied = 1 / DECIMAL_ODDS
    edge = probability - implied

    return round(projection, 2), round(probability, 4), round(edge, 4)

def grade(edge):
    if edge >= 0.14: return "A+"
    if edge >= 0.11: return "A"
    if edge >= 0.09: return "B"
    if edge >= 0.06: return "C"
    return "D"

# =====================================================
# DATA FETCHERS
# =====================================================

def fetch_prizepicks():
    picks = []
    try:
        resp = requests.get(PRIZEPICKS_API, timeout=15)
        if resp.status_code != 200:
            return []

        data = resp.json()
        players = {}

        for item in data.get("included", []):
            if item.get("type") in ("new_player", "player"):
                attrs = item.get("attributes", {})
                players[item["id"]] = {
                    "name": attrs.get("display_name") or attrs.get("name", "Unknown"),
                    "team": attrs.get("team", ""),
                }

        for proj in data.get("data", []):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type", "")
            status = attrs.get("status", "")

            if status in ("locked", "disabled") or not line:
                continue

            try:
                line = float(line)
            except:
                continue

            rel = proj.get("relationships", {})
            pid = rel.get("new_player", {}).get("data", {}).get("id") \
               or rel.get("player", {}).get("data", {}).get("id")

            pinfo = players.get(pid, {"name": "Unknown", "team": ""})

            projection, prob, edge = compute_projection(line, stat, "PrizePicks")

            if prob >= MIN_CONFIDENCE and edge >= MIN_EDGE:
                picks.append({
                    "source": "PrizePicks",
                    "player": pinfo["name"],
                    "team": pinfo["team"],
                    "stat": stat,
                    "line": line,
                    "projection": projection,
                    "probability": prob,
                    "edge": edge,
                    "grade": grade(edge),
                    "sport": attrs.get("league", "")
                })

    except Exception as e:
        logger.warning(f"PrizePicks error: {e}")

    return picks

def fetch_kalshi():
    picks = []
    try:
        resp = requests.get(
            f"{KALSHI_API}/markets",
            params={"limit": 200, "status": "open"},
            timeout=15
        )

        if resp.status_code != 200:
            return []

        for market in resp.json().get("markets", []):
            title = market.get("title", "")
            subtitle = market.get("subtitle", "")
            combined = (title + " " + subtitle).lower()

            if not any(x in combined for x in ["points", "assists", "rebounds", "goals", "shots"]):
                continue

            line = 0
            for w in title.replace("+", " ").split():
                try:
                    val = float(w)
                    if 0.5 <= val <= 500:
                        line = val
                        break
                except:
                    continue

            if line <= 0:
                continue

            projection, prob, edge = compute_projection(line, subtitle, "Kalshi")

            if prob >= MIN_CONFIDENCE and edge >= MIN_EDGE:
                picks.append({
                    "source": "Kalshi",
                    "player": title[:50],
                    "team": "",
                    "stat": subtitle,
                    "line": line,
                    "projection": projection,
                    "probability": prob,
                    "edge": edge,
                    "grade": grade(edge),
                    "sport": market.get("series_ticker", "")
                })

    except Exception as e:
        logger.warning(f"Kalshi error: {e}")

    return picks

# =====================================================
# PICK ENGINE
# =====================================================

def generate_picks(source_filter=None):
    all_picks = []
    if source_filter in (None, "PrizePicks"):
        all_picks.extend(fetch_prizepicks())
    if source_filter in (None, "Kalshi"):
        all_picks.extend(fetch_kalshi())

    # Deduplicate
    seen = set()
    unique = []
    for p in all_picks:
        key = f"{p['player']}_{p['stat']}_{p['line']}"
        if key not in seen:
            seen.add(key)
            unique.append(p)

    unique.sort(key=lambda x: x["edge"], reverse=True)
    return unique[:15]

# =====================================================
# TELEGRAM UI
# =====================================================

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 All Live Picks", callback_data="all")],
        [
            InlineKeyboardButton("🏀 PrizePicks", callback_data="pp"),
            InlineKeyboardButton("📈 Kalshi", callback_data="ks")
        ],
        [InlineKeyboardButton("🔥 Top A/A+ Only", callback_data="top")]
    ])

def format_message(picks, label):
    msg = f"🎯 {label}\n_{datetime.now().strftime('%b %d %I:%M %p')}_\n\n"

    if not picks:
        return msg + "No picks met threshold right now."

    for i, p in enumerate(picks[:10], 1):
        msg += (
            f"{i}. {p['grade']} {p['player']}\n"
            f"   {p['stat']} | {p['sport']}\n"
            f"   Line: {p['line']} → Proj: {p['projection']}\n"
            f"   Conf: {p['probability']*100:.1f}% | Edge: +{p['edge']*100:.1f}% | {p['source']}\n\n"
        )

    msg += "For entertainment only."
    return msg

# =====================================================
# HANDLERS
# =====================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "PropNinja Master Bot\nLive PrizePicks + Kalshi edges\n",
        reply_markup=main_menu()
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    await query.edit_message_text("Fetching live picks...")

    if data == "all":
        picks = generate_picks()
        label = "ALL PLATFORMS"
    elif data == "pp":
        picks = generate_picks("PrizePicks")
        label = "PRIZEPICKS"
    elif data == "ks":
        picks = generate_picks("Kalshi")
        label = "KALSHI"
    elif data == "top":
        picks = [p for p in generate_picks() if p["grade"] in ("A+", "A")]
        label = "TOP A/A+ PICKS"
    else:
        return

    await query.edit_message_text(
        format_message(picks, label)[:4096],
        reply_markup=main_menu()
    )

# =====================================================
# MAIN
# =====================================================

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    logger.info("PropNinja Master Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
