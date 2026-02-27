import os, math, logging, requests, threading
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIG & LOGGING ---
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DECIMAL_ODDS = 1.90
MIN_PROB = 0.60
MIN_EDGE = 0.05

# --- YOUR MATH ENGINE ---
def compute_edge(line, stat):
    if line <= 0: return 0, 0, 0
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
    std_dev = line * 0.18
    z = (projection - line) / std_dev
    prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    edge = prob - (1 / DECIMAL_ODDS)
    return round(projection, 2), round(prob, 4), round(edge, 4)

def grade_pick(edge):
    if edge >= 0.12: return "A"
    if edge >= 0.09: return "B"
    return "C"

# --- LIVE SCRAPERS ---
def fetch_all_data():
    picks = []
    # Simplified PrizePicks Scraper
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        resp = requests.get("https://api.prizepicks.com/projections", params={"per_page": 100}, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            for proj in data.get("data", []):
                attr = proj.get("attributes", {})
                line = float(attr.get("line_score", 0))
                stat = attr.get("stat_type", "")
                proj_val, prob, edg = compute_edge(line, stat)
                if prob >= MIN_PROB:
                    picks.append({
                        "grade": grade_pick(edg), "player": attr.get("description"),
                        "stat": stat, "line": line, "proj": proj_val,
                        "prob": prob, "edge": edg, "source": "PrizePicks"
                    })
    except: pass
    return sorted(picks, key=lambda x: x['edge'], reverse=True)

# --- TELEGRAM HANDLERS ---
def fmt_msg(picks):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    msg = f"🛡️ *PROPNINJA LIVE*\n{ts} | {len(picks)} picks\n\n"
    for i, p in enumerate(picks[:5], 1):
        msg += f"{i}. *[{p['grade']}] {p['player']}*\n   {p['stat']} | Line: {p['line']} Proj: {p['proj']}\n   Conf: {round(p['prob']*100,1)}% | Edge: +{round(p['edge']*100,1)}%\n\n"
    return msg

async def start(update, context):
    await update.message.reply_text("🥷 *PropNinja Bot Active*", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("GET PICKS", callback_data="all")]]), parse_mode="Markdown")

async def button(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "all":
        await query.edit_message_text(fmt_msg(fetch_all_data()), parse_mode="Markdown")

# --- RENDER WEB SERVER ---
@app.route('/')
def health(): return "Bot is Running", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot = Application.builder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CallbackQueryHandler(button))
    bot.run_polling()