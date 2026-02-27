HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://app.prizepicks.com/",
    "Origin": "https://app.prizepicks.com"
}

import os, math, logging, requests, threading
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIG ---
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# --- MATH ENGINE ---
def compute_edge(line, stat):
    if line <= 0: return 0, 0, 0
    boost = 0.055
    s = stat.lower()
    if "assist" in s: boost += 0.010
    elif "point" in s: boost += 0.008
    elif "rebound" in s: boost -= 0.005
    elif "goal" in s: boost += 0.007
    projection = line * (1 + boost)
    std_dev = line * 0.18
    z = (projection - line) / std_dev
    prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    edge = prob - (1 / 1.90)
    return round(projection, 2), round(prob, 4), round(edge, 4)

# --- SCRAPER (Fixes the Name/Team issue) ---
def get_all_picks():
    picks = []
    try:
        url = "https://api.prizepicks.com/projections"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, params={"per_page": 250, "single_stat": True}, headers=headers, timeout=15).json()
        
        # KEY FIX: Map IDs to Names & Teams
        player_map = {}
        for item in resp.get("included", []):
            if item.get("type") == "new_player":
                player_map[item["id"]] = {
                    "name": item["attributes"].get("display_name"),
                    "team": item["attributes"].get("team")
                }
        
        # Link Projections to the Map
        for proj in resp.get("data", []):
            attr = proj["attributes"]
            pid = proj["relationships"]["new_player"]["data"]["id"]
            player_info = player_map.get(pid, {"name": "Unknown", "team": "N/A"})
            
            line = float(attr.get("line_score", 0))
            stat = attr.get("stat_type", "")
            projection, prob, edge = compute_edge(line, stat)
            
            if prob >= 0.60:
                picks.append({
                    "player": player_info["name"],
                    "team": player_info["team"],
                    "stat": stat, "line": line, "proj": projection,
                    "prob": prob, "edge": edge, "source": "PrizePicks",
                    "sport": attr.get("league", "OTHER").upper()
                })
    except Exception as e: print(f"Scrape Error: {e}")
    return sorted(picks, key=lambda x: x['edge'], reverse=True)

# --- TELEGRAM FORMATTING ---
def fmt(picks, label):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    msg = f"🛡️ *PROPNINJA - {label}*\n{ts} | {len(picks)} picks found\n\n"
    for i, p in enumerate(picks[:25], 1): # Restoration of 25-player limit
        grade = "A+" if p['edge'] > 0.12 else "A"
        msg += f"{i}. *[{grade}] {p['player']}* ({p['team']}) [{p['source']}]\n"
        msg += f"   {p['stat']} | Line: {p['line']} Proj: {p['proj']}\n"
        msg += f"   *OVER* | Conf: {round(p['prob']*100,1)}% | Edge: +{round(p['edge']*100,1)}% | {p['sport']}\n\n"
    return msg

# --- MENU & BUTTONS ---
def get_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ALL LIVE PICKS", callback_data="all")],
        [InlineKeyboardButton("NBA", callback_data="sport_NBA"), InlineKeyboardButton("NFL", callback_data="sport_NFL")],
        [InlineKeyboardButton("MLB", callback_data="sport_MLB"), InlineKeyboardButton("NHL", callback_data="sport_NHL")],
        [InlineKeyboardButton("PrizePicks Only", callback_data="src_PrizePicks"), InlineKeyboardButton("Kalshi Only", callback_data="src_Kalshi")]
    ])

async def start(update, context):
    await update.message.reply_text("🥷 *PropNinja Dashboard*", reply_markup=get_menu(), parse_mode="Markdown")

async def button(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    all_picks = get_all_picks()
    
    if data == "all":
        text = fmt(all_picks, "ALL SPORTS")
    elif data.startswith("sport_"):
        sport = data.split("_")[1]
        text = fmt([p for p in all_picks if p['sport'] == sport], sport)
    elif data.startswith("src_"):
        src = data.split("_")[1]
        text = fmt([p for p in all_picks if p['source'] == src], src)
        
    await query.edit_message_text(text[:4096], reply_markup=get_menu(), parse_mode="Markdown")

# --- SERVER ---
@app.route('/')
def health(): return "Active"

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
    bot = Application.builder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CallbackQueryHandler(button))
    bot.run_polling()