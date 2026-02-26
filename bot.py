import os
import math
import logging
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DECIMAL_ODDS = 1.90
MIN_PROB = 0.60
MIN_EDGE = 0.05


# =========================
# CORE MATH
# =========================

def compute_edge(line, stat):
    if line <= 0:
        return 0, 0, 0

    boost = 0.055
    s = stat.lower()

    if "assist" in s:
        boost += 0.010
    elif "point" in s:
        boost += 0.008
    elif "rebound" in s:
        boost -= 0.005
    elif "goal" in s:
        boost += 0.007
    elif "shot" in s:
        boost += 0.006
    elif "strikeout" in s:
        boost += 0.009
    elif "hit" in s:
        boost += 0.005

    projection = line * (1 + boost)
    std_dev = line * 0.18
    z = (projection - line) / std_dev
    prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    edge = prob - (1 / DECIMAL_ODDS)

    return round(projection, 2), round(prob, 4), round(edge, 4)


def grade(edge):
    if edge >= 0.12:
        return "A"
    if edge >= 0.09:
        return "B"
    return "C"


# =========================
# DATA FETCHERS
# =========================

def fetch_prizepicks():
    picks = []
    try:
        resp = requests.get(
            "https://api.prizepicks.com/projections",
            params={"per_page": 250, "single_stat": True},
            headers={"Content-Type": "application/json"},
            timeout=15
        )

        if resp.status_code != 200:
            logger.warning(f"PrizePicks status: {resp.status_code}")
            return []

        data = resp.json()
        players = {}

        for item in data.get("included", []):
            if item.get("type") == "new_player":
                attrs = item.get("attributes", {})
                players[item["id"]] = {
                    "name": attrs.get("display_name", "Unknown"),
                    "team": attrs.get("team", "")
                }

        for proj in data.get("data", []):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type", "")
            sport = attrs.get("league", "")

            if not line or not stat:
                continue

            line = float(line)
            pid = proj.get("relationships", {}).get("new_player", {}).get("data", {}).get("id", "")
            pinfo = players.get(pid, {"name": attrs.get("description", "Unknown"), "team": ""})

            projection, prob, edg = compute_edge(line, stat)

            if prob >= MIN_PROB and edg >= MIN_EDGE:
                picks.append({
                    "player": pinfo["name"],
                    "team": pinfo["team"],
                    "stat": stat,
                    "line": line,
                    "proj": projection,
                    "prob": prob,
                    "edge": edg,
                    "grade": grade(edg),
                    "pick": "OVER",
                    "source": "PrizePicks",
                    "sport": sport.upper()
                })

        logger.info(f"PrizePicks: {len(picks)} picks")

    except Exception as e:
        logger.warning(f"PrizePicks error: {e}")

    return picks


def fetch_kalshi():
    picks = []
    try:
        tickers = ["NBA", "NFL", "MLB", "NHL", "SOCCER", "UFC"]

        for ticker in tickers:
            try:
                resp = requests.get(
                    "https://trading-api.kalshi.com/trade-api/v2/markets",
                    params={"limit": 100, "status": "open", "series_ticker": ticker},
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )

                if resp.status_code != 200:
                    continue

                for market in resp.json().get("markets", []):
                    title = market.get("title", "")

                    if not any(kw in title.lower() for kw in
                               ["points", "assists", "rebounds", "goals", "shots", "strikeouts", "hits"]):
                        continue

                    line = 0.0
                    for word in title.split():
                        try:
                            line = float(word.replace("+", ""))
                            if line > 0:
                                break
                        except ValueError:
                            continue

                    if line <= 0:
                        continue

                    stat = market.get("subtitle", title[:30])
                    projection, prob, edg = compute_edge(line, stat)

                    if prob >= MIN_PROB and edg >= MIN_EDGE:
                        picks.append({
                            "player": title[:40],
                            "team": "",
                            "stat": stat[:30],
                            "line": line,
                            "proj": projection,
                            "prob": prob,
                            "edge": edg,
                            "grade": grade(edg),
                            "pick": "OVER",
                            "source": "Kalshi",
                            "sport": ticker
                        })

            except Exception:
                continue

        logger.info(f"Kalshi: {len(picks)} picks")

    except Exception as e:
        logger.warning(f"Kalshi error: {e}")

    return picks


# =========================
# BACKUP PICKS
# =========================

BACKUP = [
    {"player": "Kevin Durant", "team": "HOU", "stat": "Points", "line": 26.5,
     "proj": 28.3, "prob": 0.841, "edge": 0.314, "grade": "A",
     "pick": "OVER", "source": "PrizePicks", "sport": "NBA"},
]


# =========================
# PICK LOGIC
# =========================

def get_all_picks():
    pp = fetch_prizepicks()
    kl = fetch_kalshi()

    all_picks = pp + kl
    if not all_picks:
        logger.warning("No live picks found, using backup")
        return BACKUP

    all_picks.sort(key=lambda x: x["edge"], reverse=True)
    return all_picks[:20]


def get_by_sport(sport):
    picks = get_all_picks()
    return [p for p in picks if p["sport"] == sport]


# =========================
# FORMATTER
# =========================

def fmt(picks, label):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    msg = f"PROPNINJA - {label}\n{ts}\n\n"

    for i, p in enumerate(picks[:10], 1):
        msg += (
            f"{i}. {p['grade']} {p['player']} ({p['team']})\n"
            f"   {p['stat']} | Line: {p['line']} Proj: {p['proj']}\n"
            f"   {p['pick']} | Conf: {round(p['prob']*100,1)}% | "
            f"Edge: +{round(p['edge']*100,1)}%\n\n"
        )

    msg += "For entertainment only."
    return msg


# =========================
# TELEGRAM UI
# =========================

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ALL LIVE PICKS", callback_data="all")],
        [InlineKeyboardButton("NBA", callback_data="sport_NBA"),
         InlineKeyboardButton("NFL", callback_data="sport_NFL")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "PropNinja Bot\nTap below to get picks:",
        reply_markup=menu()
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "all":
        picks = get_all_picks()
        await query.edit_message_text(fmt(picks, "ALL SPORTS"))

    elif query.data.startswith("sport_"):
        sport = query.data.split("_")[1]
        picks = get_by_sport(sport)
        await query.edit_message_text(fmt(picks, sport))


# =========================
# MAIN
# =========================

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    logger.info("PropNinja Bot Running")
    app.run_polling()


if __name__ == "__main__":
    main()
