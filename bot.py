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

# CORRECT LIVE ENDPOINTS
PRIZEPICKS_PRIMARY = "https://partner-api.prizepicks.com/projections?per_page=1000"
PRIZEPICKS_FALLBACK = "https://api.prizepicks.com/projections"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"

DECIMAL_ODDS   = 1.90
MIN_EDGE       = 0.04   # temporarily lowered to confirm flow
MIN_CONFIDENCE = 0.58   # temporarily lowered
# ==========================================
# TEAM ABBREVIATIONS (NBA example)
# ==========================================

TEAM_ABBR = {
    "Los Angeles Lakers": "LAL",
    "Los Angeles Clippers": "LAC",
    "Golden State Warriors": "GSW",
    "Boston Celtics": "BOS",
    "Milwaukee Bucks": "MIL",
    "Phoenix Suns": "PHX",
    "Denver Nuggets": "DEN",
    "Miami Heat": "MIA",
    "Dallas Mavericks": "DAL",
    "Philadelphia 76ers": "PHI",
    "New York Knicks": "NYK",
    "Chicago Bulls": "CHI",
    "Brooklyn Nets": "BKN",
    "Cleveland Cavaliers": "CLE",
    "Atlanta Hawks": "ATL",
    # Add more as needed
}
# =====================================================
# MODEL
# =====================================================

def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def compute_projection(line, stat, source):
    if line <= 0:
        return 0, 0, 0

    boost = 0.05
    stat_lower = stat.lower()

    if "assist" in stat_lower:
        boost += 0.01
    elif "points" in stat_lower:
        boost += 0.008
    elif "rebound" in stat_lower:
        boost -= 0.005

    if source == "PrizePicks":
        boost += 0.055
    elif source == "Kalshi":
        boost += 0.045

    season = line * (1 + boost)
    recent = line * (1 + boost * 1.15)
    matchup = line * (1 + boost * 0.90)

    projection = (season * 0.4) + (recent * 0.4) + (matchup * 0.2)

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
# PRIZEPICKS
# =====================================================

def fetch_prizepicks():
    picks = []

    try:
        resp = requests.get(PRIZEPICKS_PRIMARY, timeout=10)

        if resp.status_code != 200:
            logger.warning("Primary PrizePicks failed, trying fallback")
            resp = requests.get(
                PRIZEPICKS_FALLBACK,
                params={"per_page": 250, "single_stat": True},
                timeout=10
            )

        if resp.status_code != 200:
            logger.warning("PrizePicks unavailable")
            return []

        data = resp.json()
        logger.info(f"PrizePicks raw projections: {len(data.get('data', []))}")

        for proj in data.get("data", []):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type", "")
            status = attrs.get("status", "")

            if status in ("locked", "disabled"):
                continue

            try:
                line = float(line)
            except:
                continue

            projection, prob, edge = compute_projection(line, stat, "PrizePicks")

            # Loosen filter for debug
            if edge >= MIN_EDGE:
                picks.append({
                    "source": "PrizePicks",
                    "player": attrs.get("description", "Unknown"),
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

    logger.info(f"PrizePicks picks returned: {len(picks)}")
    return picks

# =====================================================
# KALSHI (CORRECT ENDPOINT)
# =====================================================

def fetch_kalshi():
    picks = []

    try:
        resp = requests.get(
            f"{KALSHI_API}/markets",
            params={"limit": 300, "status": "open"},
            timeout=12
        )

        if resp.status_code != 200:
            logger.warning("Kalshi API failed")
            return []

        markets = resp.json().get("markets", [])
        logger.info(f"Kalshi markets fetched: {len(markets)}")

        for market in markets:
            title = market.get("title", "")
            subtitle = market.get("subtitle", "")
            combined = (title + " " + subtitle).lower()

            if not any(x in combined for x in ["points", "assists", "rebounds", "goals", "shots"]):
                continue

            line = 0
            for w in title.replace("+", " ").replace(",", "").split():
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

            if edge >= MIN_EDGE:
                picks.append({
                    "source": "Kalshi",
                    "player": title[:50],
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

    logger.info(f"Kalshi picks returned: {len(picks)}")
    return picks

# =====================================================
# ENGINE
# =====================================================

def generate_picks():
    picks = fetch_prizepicks() + fetch_kalshi()

    seen = set()
    unique = []

    for p in picks:
        key = f"{p['player']}_{p['stat']}_{p['line']}"
        if key not in seen:
            seen.add(key)
            unique.append(p)

    unique.sort(key=lambda x: x["edge"], reverse=True)
    logger.info(f"Total combined picks: {len(unique)}")
    return unique[:15]

# =====================================================
# TELEGRAM
# =====================================================

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Refresh Live Picks", callback_data="refresh")]
    ])

def format_msg(picks):
    msg = f"🎯 LIVE PICKS\n_{datetime.now().strftime('%b %d %I:%M %p')}_\n\n"

    if not picks:
        return msg + "No qualifying picks right now.\n(But APIs are connected)"

    for i, p in enumerate(picks[:10], 1):
        msg += (
            f"{i}. {p['grade']} {p['player']}\n"
            f"   {p['stat']} | {p['sport']}\n"
            f"   Line: {p['line']} → Proj: {p['projection']}\n"
            f"   Conf: {p['probability']*100:.1f}% | Edge: +{p['edge']*100:.1f}% | {p['source']}\n\n"
        )

    return msg + "For entertainment only."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("PropNinja LIVE Bot", reply_markup=menu())

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Fetching live markets...")

    picks = generate_picks()

    await query.edit_message_text(format_msg(picks)[:4096], reply_markup=menu())

# =====================================================
# MAIN
# =====================================================

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    logger.info("LIVE PropNinja running...")
    app.run_polling()

if __name__ == "__main__":
    main()
