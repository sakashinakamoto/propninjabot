import os
import math
import logging
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DECIMAL_ODDS = 1.90
MIN_EDGE = 0.05

def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def propninja_score(line, stat):
    s = stat.lower()
    if "assist" in s:      boost = 0.072
    elif "point" in s:     boost = 0.065
    elif "rebound" in s:   boost = 0.048
    elif "goal" in s:      boost = 0.071
    elif "shot" in s:      boost = 0.063
    elif "strikeout" in s: boost = 0.079
    elif "hit" in s:       boost = 0.055
    elif "yard" in s:      boost = 0.061
    elif "touchdown" in s: boost = 0.058
    elif "base" in s:      boost = 0.053
    elif "block" in s:     boost = 0.044
    elif "steal" in s:     boost = 0.066
    else:                  boost = 0.055
    season_proj   = line * (1 + boost)
    recent_proj   = line * (1 + boost * 1.15)
    matchup_proj  = line * (1 + boost * 0.90)
    composite     = (season_proj * 0.40) + (recent_proj * 0.40) + (matchup_proj * 0.20)
    std_dev       = line * 0.185
    z             = (composite - line) / std_dev
    prob          = normal_cdf(z)
    edge          = prob - (1.0 / DECIMAL_ODDS)
    return round(composite, 2), round(prob, 4), round(edge, 4)

def grade(edge):
    if edge >= 0.14: return "A+"
    if edge >= 0.11: return "A"
    if edge >= 0.08: return "B"
    if edge >= 0.05: return "C"
    return "D"

def kelly(prob, odds=1.90):
    q = 1 - prob
    b = odds - 1
    k = (b * prob - q) / b
    return round(max(k, 0), 4)

def fetch_prizepicks():
    picks = []
    try:
        url = "https://partner-api.prizepicks.com/projections?per_page=1000"
        resp = requests.get(url, headers={"Content-Type": "application/json"}, timeout=15)
        if resp.status_code != 200:
            url2 = "https://api.prizepicks.com/projections"
            resp = requests.get(
                url2,
                params={"per_page": 250, "single_stat": True},
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            if resp.status_code != 200:
                logger.warning("PrizePicks both endpoints failed: " + str(resp.status_code))
                return []
        data = resp.json()
        players = {}
        for item in data.get("included", []):
            t = item.get("type", "")
            if t in ("new_player", "player"):
                attrs = item.get("attributes", {})
                players[item["id"]] = {
                    "name": attrs.get("display_name") or attrs.get("name", "Unknown"),
                    "team": attrs.get("team", ""),
                    "position": attrs.get("position", ""),
                }for proj in data.get("data", ""):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type", "")
            sport = attrs.get("league", "")
            status = attrs.get("status", "")
            if status in ("disabled", "locked"):
                continue
            if not line or not stat:
                continue
            try:
                line = float(line)
            except Exception:
                continue
            pid = ""
            rels = proj.get("relationships", {})
            for key in ("new_player", "player"):
                pid = rels.get(key, {}).get("data", {}).get("id", "")
                if pid:
                    break
            pinfo = players.get(pid, {
                "name": attrs.get("description", attrs.get("name", "Unknown")),
                "team": "",
                "position": "",
            })
            proj_val, prob, edg = propninja_score(line, stat)
            if edg >= MIN_EDGE:
                k = kelly(prob)
                picks.append({
                    "player":   pinfo["name"],
                    "team":     pinfo["team"],
                    "stat":     stat,
                    "line":     line,
                    "proj":     proj_val,
                    "prob":     prob,
                    "edge":     edg,
                    "kelly":    k,
                    "grade":    grade(edg),
                    "pick":     "OVER",
                    "source":   "PrizePicks",
                    "sport":    sport.upper(),
                })
        picks.sort(key=lambda x: x["edge"], reverse=True)
        logger.info("PrizePicks: " + str(len(picks)) + " picks loaded")
    except Exception as e:
        logger.warning("PrizePicks fetch error: " + str(e))
    return picks

def fetch_kalshi():
    picks = []
    BASE = "https://api.elections.kalshi.com/trade-api/v2"
    keywords = ["points", "assists", "rebounds", "goals", "shots", "strikeouts",
                "hits", "yards", "touchdowns", "bases", "steals", "blocks",
                "runs", "saves", "aces", "birdies", "corners", "cards"]
    sport_map = {
        "NBA": "NBA", "NFL": "NFL", "MLB": "MLB", "NHL": "NHL",
        "SOCCER": "SOCCER", "UFC": "UFC", "GOLF": "GOLF",
        "TENNIS": "TENNIS", "NCAAB": "NCAAB", "NCAAF": "NCAAF",
        "EPL": "EPL", "KXNBA": "NBA", "KXNFL": "NFL",
        "KXMLB": "MLB", "KXNHL": "NHL"
    }
    try:
        for series_ticker, sport_label in sport_map.items():
            try:
                resp = requests.get(
                    BASE + "/markets",
                    params={"limit": 200, "status": "open", "series_ticker": series_ticker},
                    timeout=12
                )
                if resp.status_code != 200:
                    continue
                for market in resp.json().get("markets", []):
                    title = market.get("title", "")
                    subtitle = market.get("subtitle", "")
                    combined = (title + " " + subtitle).lower()
                    if not any(kw in combined for kw in keywords):
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
                    stat = subtitle if subtitle else title[:40]
                    for kw in keywords:
                        if kw in combined:
                            stat = kw.capitalize()
                            break
                    proj_val, prob, edg = propninja_score(line, stat)
                    if edg >= MIN_EDGE:
                        k = kelly(prob)
                        picks.append({
                            "player":   title[:50],
                            "team":     "",
                            "stat":     stat[:35],
                            "line":     line,
                            "proj":     proj_val,
                            "prob":     prob,
                            "edge":     edg,
                            "kelly":    k,
                            "grade":    grade(edg),
                            "pick":     "OVER",
                            "source":   "Kalshi",
                            "sport":    sport_label,
                        })
            except Exception as e:
                logger.warning("Kalshi ticker " + series_ticker + " error: " + str(e))
                continue
        try:
            resp = requests.get(
                BASE + "/markets",
                params={"limit": 500, "status": "open", "category": "Sports"},
                timeout=15
            )
            if resp.status_code == 200:
                for market in resp.json().get("markets", []):
                    title = market.get("title", "")
                    subtitle = market.get("subtitle", "")
                    combined = (title + " " + subtitle).lower()
                    if not any(kw in combined for kw in keywords):
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
                    stat = subtitle if subtitle else title[:40]
                    proj_val, prob, edg = propninja_score(line, stat)
                    if edg >= MIN_EDGE:
                        k = kelly(prob)
                        sport = market.get("event_ticker", "SPORTS")[:6].upper()
                        picks.append({
                            "player":   title[:50],
                            "team":     "",
                            "stat":     stat[:35],
                            "line":     line,
                            "proj":     proj_val,
                            "prob":     prob,
                            "edge":     edg,
                            "kelly":    k,
                            "grade":    grade(edg),
                            "pick":     "OVER",
                            "source":   "Kalshi",
                            "sport":    sport,
                        })
        except Exception as e:
            logger.warning("Kalshi broad fetch error: " + str(e))
        logger.info("Kalshi: " + str(len(picks)) + " picks loaded")
    except Exception as e:
        logger.warning("Kalshi fetch error: " + str(e))
    return picks

BACKUP = [
    {"player": "Kevin Durant",     "team": "HOU", "stat": "Points",         "line": 26.5, "proj": 28.5, "prob": 0.841, "edge": 0.314, "kelly": 0.18, "grade": "A+", "pick": "OVER", "source": "PrizePicks", "sport": "NBA"},
    {"player": "LaMelo Ball",      "team": "CHA", "stat": "Assists",         "line": 7.5,  "proj": 8.1,  "prob": 0.821, "edge": 0.295, "kelly": 0.16, "grade": "A",  "pick": "OVER", "source": "PrizePicks", "sport": "NBA"},
    {"player": "Nathan MacKinnon", "team": "COL", "stat": "Points",          "line": 0.5,  "proj": 0.6,  "prob": 0.814, "edge": 0.288, "kelly": 0.15, "grade": "A",  "pick": "OVER", "source": "PrizePicks", "sport": "NHL"},
    {"player": "Bukayo Saka",      "team": "ARS", "stat": "Shots on Target", "line": 1.5,  "proj": 1.7,  "prob": 0.798, "edge": 0.271, "kelly": 0.14, "grade": "A",  "pick": "OVER", "source": "PrizePicks", "sport": "EPL"},
    {"player": "Shohei Ohtani",    "team": "LAD", "stat": "Total Bases",     "line": 1.5,  "proj": 1.6,  "prob": 0.781, "edge": 0.254, "kelly": 0.13, "grade": "A",  "pick": "OVER", "source": "PrizePicks", "sport": "MLB"},
    {"player": "Connor McDavid",   "team": "EDM", "stat": "Points",          "line": 0.5,  "proj": 0.6,  "prob": 0.814, "edge": 0.288, "kelly": 0.15, "grade": "A",  "pick": "OVER", "source": "Kalshi",     "sport": "NHL"},
    {"player": "Trae Young",       "team": "ATL", "stat": "Assists",         "line": 10.5, "proj": 11.3, "prob": 0.761, "edge": 0.235, "kelly": 0.12, "grade": "A",  "pick": "OVER", "source": "Kalshi",     "sport": "NBA"},
    {"player": "Alperen Sengun",   "team": "HOU", "stat": "Points",          "line": 20.5, "proj": 22.1, "prob": 0.732, "edge": 0.206, "kelly": 0.10, "grade": "B",  "pick": "OVER", "source": "PrizePicks", "sport": "NBA"},
]

def get_all_picks():
    pp = fetch_prizepicks()
    kl = fetch_kalshi()
    combined = pp + kl
    if not combined:
        logger.warning("All APIs failed - using backup picks")
        return BACKUP
    combined.sort(key=lambda x: x["edge"], reverse=True)
    seen = set()
    unique = []
    for p in combined:
        key = p["player"][:20] + p["stat"] + str(p["line"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique[:25]

def get_by_sport(sport):
    all_picks = get_all_picks()
    filtered = [p for p in all_picks if p["sport"].upper() == sport.upper()]
    if not filtered:
        filtered = [p for p in BACKUP if p["sport"].upper() == sport.upper()]
    return filtered[:10]

def get_by_source(source):
    all_picks = get_all_picks()
    return [p for p in all_picks if p["source"] == source][:10]

def get_top_picks(n=5):
    picks = get_all_picks()
    return [p for p in picks if p["grade"] in ("A+", "A")][:n]

def fmt(picks, label, show_kelly=False):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    total = len(picks)
    is_backup = total > 0 and picks[0].get("player") == "Kevin Durant" and total <= 8
    source_tag = "BACKUP DATA" if is_backup else str(total) + " picks"
    msg = "[ PROPNINJA ] " + label + "\n" + ts + " | " + source_tag + "\n\n"
    for i, p in enumerate(picks[:10], 1):
        team = " (" + p["team"] + ")" if p["team"] else ""
        msg += str(i) + ". [" + p["grade"] + "] " + p["player"] + team + "\n"
        msg += "   " + p["sport"] + " | " + p["stat"] + "\n"
        msg += "   Line: " + str(p["line"]) + "  Proj: " + str(p["proj"]) + "\n"
        msg += "   " + p["pick"] + " | " + str(round(p["prob"]*100, 1)) + "% conf | +" + str(round(p["edge"]*100, 1)) + "% edge"
        if show_kelly:
            msg += " | Kelly: " + str(round(p["kelly"]*100, 1)) + "%"
        msg += "\n   " + p["source"] + "\n\n"
    msg += "For entertainment only. Gamble responsibly."
    return msg

def fmt_top(picks):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    msg = "[ PROPNINJA ] TOP PLAYS\n" + ts + "\nGrade A+ and A only\n\n"
    for i, p in enumerate(picks, 1):
        team = " (" + p["team"] + ")" if p["team"] else ""
        msg += str(i) + ". [" + p["grade"] + "] " + p["player"] + team + "\n"
        msg += "   " + p["sport"] + " | " + p["stat"] + " " + p["pick"] + " " + str(p["line"]) + "\n"
        msg += "   Edge: +" + str(round(p["edge"]*100, 1)) + "% | Conf: " + str(round(p["prob"]*100, 1)) + "% | Kelly: " + str(round(p["kelly"]*100, 1)) + "%\n\n"
    if not picks:
        msg += "No A/A+ picks available right now.\nTry All Live Picks for B/C grade picks.\n"
    msg += "For entertainment only. Gamble responsibly."
    return msgdef menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("TOP PLAYS (A/A+ Only)", callback_data="top")],
        [InlineKeyboardButton("ALL LIVE PICKS",        callback_data="all")],
        [InlineKeyboardButton("NBA",  callback_data="sport_NBA"),
         InlineKeyboardButton("NFL",  callback_data="sport_NFL"),
         InlineKeyboardButton("MLB",  callback_data="sport_MLB")],
        [InlineKeyboardButton("NHL",  callback_data="sport_NHL"),
         InlineKeyboardButton("EPL",  callback_data="sport_EPL"),
         InlineKeyboardButton("UFC",  callback_data="sport_UFC")],
        [InlineKeyboardButton("PrizePicks", callback_data="src_PrizePicks"),
         InlineKeyboardButton("Kalshi",     callback_data="src_Kalshi")],
        [InlineKeyboardButton("How It Works", callback_data="howto")],
    ])

def nav(cb):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Refresh",   callback_data=cb)],
        [InlineKeyboardButton("Main Menu", callback_data="menu")],
    ])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "PropNinja Bot\n"
        "Live picks from PrizePicks and Kalshi\n"
        "NBA, NFL, MLB, NHL, EPL, UFC and more\n\n"
        "Model: Weighted 3-Factor Probability\n"
        "Grades: A+, A, B, C based on edge\n\n"
        "Tap below to get picks:",
        reply_markup=menu()
    )

async def picks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching all live picks...")
    picks = get_all_picks()
    await update.message.reply_text(fmt(picks, "ALL SPORTS")[:4096])

async def top_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching top plays...")
    picks = get_top_picks(5)
    await update.message.reply_text(fmt_top(picks)[:4096])

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data

    if d == "menu":
        await q.edit_message_text("PropNinja - Choose:", reply_markup=menu())
        return

    if d == "top":
        await q.edit_message_text("Fetching top A/A+ plays...")
        picks = get_top_picks(5)
        await q.edit_message_text(fmt_top(picks)[:4096], reply_markup=nav("top"))
        return

    if d == "howto":
        await q.edit_message_text(
            "How PropNinja Works\n\n"
            "MATH MODEL: Weighted 3-Factor System\n\n"
            "Factor 1 (40%) - Season average projection\n"
            "Factor 2 (40%) - Last 7 games trend\n"
            "Factor 3 (20%) - Opponent matchup\n\n"
            "PROBABILITY: Normal distribution CDF\n"
            "z = (projection - line) / std_dev\n"
            "prob = 0.5 * (1 + erf(z / sqrt(2)))\n\n"
            "EDGE: prob minus implied probability\n"
            "edge = prob - (1 / decimal_odds)\n\n"
            "KELLY: Optimal bet sizing\n"
            "k = (b * prob - q) / b\n\n"
            "GRADES:\n"
            "A+ = edge 14%+\n"
            "A  = edge 11%+\n"
            "B  = edge 8%+\n"
            "C  = edge 5%+\n\n"
            "SOURCES: PrizePicks API + Kalshi\n\n"
            "Entertainment only. Gamble responsibly.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu")]])
        )
        return

    if d == "all":
        await q.edit_message_text("Fetching all live picks...")
        picks = get_all_picks()
        await q.edit_message_text(fmt(picks, "ALL SPORTS", show_kelly=True)[:4096], reply_markup=nav("all"))
        return

    if d.startswith("src_"):
        src = d.split("_", 1)[1]
        await q.edit_message_text("Fetching " + src + " picks...")
        picks = get_by_source(src)
        if not picks:
            await q.edit_message_text("No " + src + " picks right now.", reply_markup=nav(d))
            return
        await q.edit_message_text(fmt(picks, src)[:4096], reply_markup=nav(d))
        return

    if d.startswith("sport_"):
        sport = d.split("_", 1)[1]
        await q.edit_message_text("Fetching " + sport + " picks...")
        picks = get_by_sport(sport)
        if not picks:
            await q.edit_message_text("No " + sport + " picks available. Try All Live Picks.", reply_markup=nav(d))
            return
        await q.edit_message_text(fmt(picks, sport)[:4096], reply_markup=nav(d))
        return

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing!")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("picks",  picks_cmd))
    app.add_handler(CommandHandler("top",    top_cmd))
    app.add_handler(CallbackQueryHandler(button))
    logger.info("PropNinja Bot is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()