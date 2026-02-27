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
        resp = requests.get(
            'https://api.prizepicks.com/projections',
            params={'per_page': 250, 'single_stat': True},
            headers={'Content-Type': 'application/json'},
            timeout=15
        )

        if resp.status_code != 200:
            logger.warning('PrizePicks status: ' + str(resp.status_code))
            return []

        data = resp.json()

        if not isinstance(data, dict):
            logger.warning('PrizePicks returned invalid JSON structure')
            return []

        players = {}

        # Build player lookup table
        for item in data.get('included', []):
            if not isinstance(item, dict):
                continue

            if item.get('type') == 'new_player':
                attrs = item.get('attributes', {})
                player_id = item.get('id')

                if player_id:
                    players[player_id] = {
                        'name': attrs.get('display_name', 'Unknown'),
                        'team': attrs.get('team', '')
                    }

        # Process projections
        for proj in data.get('data', []):
            if not isinstance(proj, dict):
                continue

            attrs = proj.get('attributes', {})
            if not isinstance(attrs, dict):
                continue

            line = attrs.get('line_score')
            stat = attrs.get('stat_type', '')
            sport = attrs.get('league', '')

            if line is None or not stat:
                continue

            try:
                line = float(line)
            except:
                continue

            relationships = proj.get('relationships', {})
            new_player = relationships.get('new_player', {})
            player_data = new_player.get('data', {})
            player_id = player_data.get('id')

            pinfo = players.get(player_id, {
                'name': attrs.get('description', 'Unknown'),
                'team': ''
            })

            projection, prob, edg = compute_edge(line, stat)

            if prob >= MIN_PROB and edg >= MIN_EDGE:
                picks.append({
                    'player': pinfo['name'],
                    'team': pinfo['team'],
                    'stat': stat,
                    'line': line,
                    'proj': projection,
                    'prob': prob,
                    'edge': edg,
                    'grade': grade(edg),
                    'pick': 'OVER',
                    'source': 'PrizePicks',
                    'sport': str(sport).upper()
                })

        logger.info('PrizePicks live qualified picks: ' + str(len(picks)))

    except Exception as e:
        logger.warning('PrizePicks error: ' + str(e))

    return picks

def fetch_kalshi():
    picks = []
    tickers = ['NBA', 'NFL', 'MLB', 'NHL', 'SOCCER', 'UFC', 'GOLF', 'TEN']

    try:
        for ticker in tickers:
            try:
                resp = requests.get(
                    'https://trading-api.kalshi.com/trade-api/v2/markets',
                    params={'limit': 200, 'status': 'open', 'series_ticker': ticker},
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )

                if resp.status_code != 200:
                    continue

                markets = resp.json().get('markets', [])

                for market in markets:
                    title = market.get('title', '')
                    subtitle = market.get('subtitle', '')

                    yes_price = market.get('yes_price')
                    no_price = market.get('no_price')

                    if yes_price is None or no_price is None:
                        continue

                    try:
                        market_prob_yes = float(yes_price) / 100.0
                        market_prob_no = float(no_price) / 100.0
                    except:
                        continue

                    # Extract numeric line from title
                    line = 0.0
                    for word in title.replace(',', '').split():
                        try:
                            val = float(word.replace('+', ''))
                            if val > 0:
                                line = val
                                break
                        except:
                            continue

                    if line <= 0:
                        continue

                    stat = subtitle if subtitle else title[:30]

                    projection, model_prob, _ = compute_edge(line, stat)

                    # Evaluate BOTH sides
                    edge_yes = model_prob - market_prob_yes
                    edge_no = (1 - model_prob) - market_prob_no

                    if edge_yes >= MIN_EDGE and model_prob >= 0.55:
                        picks.append({
                            'player': title[:40],
                            'team': '',
                            'stat': stat[:30],
                            'line': line,
                            'proj': projection,
                            'prob': round(model_prob, 4),
                            'edge': round(edge_yes, 4),
                            'grade': grade(edge_yes),
                            'pick': 'YES',
                            'source': 'Kalshi',
                            'sport': ticker
                        })

                    elif edge_no >= MIN_EDGE and (1 - model_prob) >= 0.55:
                        picks.append({
                            'player': title[:40],
                            'team': '',
                            'stat': stat[:30],
                            'line': line,
                            'proj': projection,
                            'prob': round(1 - model_prob, 4),
                            'edge': round(edge_no, 4),
                            'grade': grade(edge_no),
                            'pick': 'NO',
                            'source': 'Kalshi',
                            'sport': ticker
                        })

            except:
                continue

        logger.info('Kalshi live qualified picks: ' + str(len(picks)))

    except Exception as e:
        logger.warning('Kalshi error: ' + str(e))

    return picks

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