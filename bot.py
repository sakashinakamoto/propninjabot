import os
import math
import logging
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DECIMAL_ODDS = 1.90


def compute_model(line, stat, liquidity=1.0):
    if line <= 0:
        return None

    boost = 0.055
    s = stat.lower()

    if "assist" in s: boost += 0.010
    elif "point" in s: boost += 0.008
    elif "rebound" in s: boost -= 0.005
    elif "goal" in s: boost += 0.007
    elif "shot" in s: boost += 0.006
    elif "strikeout" in s: boost += 0.009
    elif "hit" in s: boost += 0.005

    projection = line * (1 + boost)
    std_dev = max(line * 0.18, 0.1)

    z = (projection - line) / std_dev
    prob_over = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    prob_under = 1 - prob_over

    implied = 1 / DECIMAL_ODDS
    edge_over = prob_over - implied
    edge_under = prob_under - implied

    liquidity_weight = 1 + min(math.log10(liquidity + 1), 2)

    score_over = edge_over * liquidity_weight
    score_under = edge_under * liquidity_weight

    return {
        "projection": round(projection, 2),
        "prob_over": round(prob_over, 4),
        "prob_under": round(prob_under, 4),
        "edge_over": round(edge_over, 4),
        "edge_under": round(edge_under, 4),
        "score_over": round(score_over, 4),
        "score_under": round(score_under, 4),
    }


def grade(edge):
    edge = abs(edge)
    if edge >= 0.12: return "A"
    if edge >= 0.09: return "B"
    return "C"


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
            return []

        data = resp.json()
        players = {}

        for item in data.get("included", []):
            if item.get("type") == "new_player":
                attrs = item.get("attributes", {})
                players[item["id"]] = {
                    "name": attrs.get("display_name", "Unknown"),
                    "team": attrs.get("team", ""),
                }

        for proj in data.get("data", []):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type", "")
            sport = attrs.get("league", "")

            if not line or not stat:
                continue

            try:
                line = float(line)
            except Exception:
                continue

            pid = proj.get("relationships", {}).get("new_player", {}).get("data", {}).get("id", "")
            pinfo = players.get(pid, {"name": attrs.get("description", "Unknown"), "team": ""})

            model = compute_model(line, stat, liquidity=1000)
            if not model:
                continue

            for side in ["OVER", "UNDER"]:
                picks.append({
                    "player": pinfo["name"],
                    "team": pinfo["team"],
                    "stat": stat,
                    "line": line,
                    "proj": model["projection"],
                    "prob": model["prob_over"] if side == "OVER" else model["prob_under"],
                    "edge": model["edge_over"] if side == "OVER" else model["edge_under"],
                    "score": model["score_over"] if side == "OVER" else model["score_under"],
                    "grade": grade(model["edge_over"] if side == "OVER" else model["edge_under"]),
                    "pick": side,
                    "source": "PrizePicks",
                    "sport": sport.upper(),
                })

    except Exception:
        return []

    return picks


def fetch_kalshi():
    picks = []
    keywords = ["points", "assists", "rebounds", "goals", "shots", "strikeouts", "hits", "yards", "touchdowns"]

    try:
        resp = requests.get(
            "https://trading-api.kalshi.com/trade-api/v2/markets",
            params={"limit": 1000, "status": "open"},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        if resp.status_code != 200:
            return []

        for market in resp.json().get("markets", []):
            title = market.get("title", "")
            category = market.get("category", "").upper()

            if not any(kw in title.lower() for kw in keywords):
                continue

            line = 0.0
            for w in title.replace("+", " ").replace(",", "").split():
                try:
                    val = float(w)
                    if 0.5 <= val <= 500:
                        line = val
                        break
                except ValueError:
                    continue

            if line <= 0:
                continue

            stat = market.get("subtitle", title[:30])
            sport = category if category else "KALSHI"

            volume = market.get("volume", 0)
            open_interest = market.get("open_interest", 0)
            liquidity = volume + open_interest

            model = compute_model(line, stat, liquidity=liquidity)
            if not model:
                continue

            for side in ["OVER", "UNDER"]:
                picks.append({
                    "player": title[:40],
                    "team": "",
                    "stat": stat[:30],
                    "line": line,
                    "proj": model["projection"],
                    "prob": model["prob_over"] if side == "OVER" else model["prob_under"],
                    "edge": model["edge_over"] if side == "OVER" else model["edge_under"],
                    "score": model["score_over"] if side == "OVER" else model["score_under"],
                    "grade": grade(model["edge_over"] if side == "OVER" else model["edge_under"]),
                    "pick": side,
                    "source": "Kalshi",
                    "sport": sport,
                })

    except Exception:
        return []

    return picks


def get_all_picks():
    pp = fetch_prizepicks()
    kl = fetch_kalshi()
    all_picks = pp + kl

    if not all_picks:
        return []

    all_picks.sort(key=lambda x: x["score"], reverse=True)

    seen = set()
    unique = []

    for p in all_picks:
        key = p["player"] + p["stat"] + str(p["line"]) + p["pick"]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique[:40]


def get_by_sport(sport):
    all_picks = get_all_picks()
    return [p for p in all_picks if p["sport"].upper() == sport.upper()]


def fmt(picks, label):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    total = len(picks)
    msg = "PROPNINJA - " + label + "\n" + ts + " | " + str(total) + " picks found\n\n"

    for i, p in enumerate(picks[:40], 1):
        msg += f"{i}. {p['grade']} {p['player']}"
        if p["team"]:
            msg += f" ({p['team']})"
        msg += f" [{p['source']}]\n"
        msg += f"   {p['stat']} | Line: {p['line']} Proj: {p['proj']}\n"
        msg += f"   {p['pick']} | Conf: {round(p['prob']*100,1)}% | Edge: {round(p['edge']*100,1)}% | Score: {round(p['score']*100,2)} | {p['sport']}\n\n"

    msg += "For entertainment only. Gamble responsibly."
    return msg


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ALL LIVE PICKS", callback_data="all")],
        [InlineKeyboardButton("NBA", callback_data="sport_NBA"),
         InlineKeyboardButton("NFL", callback_data="sport_NFL"),
         InlineKeyboardButton("MLB", callback_data="sport_MLB")],
        [InlineKeyboardButton("NHL", callback_data="sport_NHL"),
         InlineKeyboardButton("EPL", callback_data="sport_EPL"),
         InlineKeyboardButton("UFC", callback_data="sport_UFC")],
    ])


def nav(cb):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Refresh", callback_data=cb)],
        [InlineKeyboardButton("Main Menu", callback_data="menu")],
    ])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "PropNinja Bot\nLive ranked picks from PrizePicks & Kalshi\n\nTap below:",
        reply_markup=menu()
    )


async def picks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching live ranked slate...")
    picks = get_all_picks()
    await update.message.reply_text(fmt(picks, "ALL SPORTS")[:4096])


async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data

    if d == "menu":
        await q.edit_message_text("Choose:", reply_markup=menu())
        return

    if d == "all":
        picks = get_all_picks()
        await q.edit_message_text(fmt(picks, "ALL SPORTS")[:4096], reply_markup=nav("all"))
        return

    if d.startswith("sport_"):
        sport = d.split("_", 1)[1]
        picks = get_by_sport(sport)
        await q.edit_message_text(fmt(picks, sport)[:4096], reply_markup=nav(d))
        return


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("picks", picks_cmd))
    app.add_handler(CallbackQueryHandler(button))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()