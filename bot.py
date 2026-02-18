import requests
import math
import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
format=”%(asctime)s - %(name)s - %(levelname)s - %(message)s”,
level=logging.INFO
)
logger = logging.getLogger(**name**)

# ============================================

# CONFIG

# ============================================

TELEGRAM_TOKEN  = os.getenv(“TELEGRAM_TOKEN”, “”)
X_BEARER_TOKEN  = os.getenv(“X_BEARER_TOKEN”, “”)
PRIZEPICKS_API  = os.getenv(“PRIZEPICKS_API”, “https://api.prizepicks.com/projections”)
KALSHI_API      = os.getenv(“KALSHI_API”, “https://trading-api.kalshi.com/trade-api/v2”)
DABBLE_API      = os.getenv(“DABBLE_API”, “https://api.dabble.com.au/v1”)
DABBLE_TOKEN    = os.getenv(“DABBLE_TOKEN”, “”)

MIN_CONFIDENCE  = 0.64
MIN_EDGE        = 0.06
DECIMAL_ODDS    = 1.9

SEARCH_TERMS = [
“points prop”, “assists prop”, “rebounds prop”,
“shots on target”, “goal scorer”, “PRA line”,
“player prop”, “hits prop”, “strikeouts prop”
]

# ============================================

# PLATFORM FETCHERS

# ============================================

def fetch_prizepicks():
picks = []
try:
resp = requests.get(
PRIZEPICKS_API,
params={“per_page”: 50, “single_stat”: True},
headers={“Content-Type”: “application/json”},
timeout=10
)
if resp.status_code == 200:
for proj in resp.json().get(“data”, []):
attrs = proj.get(“attributes”, {})
line = attrs.get(“line_score”, 0)
if not line:
continue
picks.append({
“source”: “PrizePicks”,
“player”: attrs.get(“name”, “Unknown”),
“stat”:   attrs.get(“stat_type”, “”),
“line”:   float(line),
“sport”:  attrs.get(“league”, “”),
})
except Exception as e:
logger.warning(f”PrizePicks fetch failed: {e}”)
return picks

def fetch_kalshi():
picks = []
try:
for ticker in [“NBA”, “NFL”, “MLB”, “NHL”, “SOC”]:
resp = requests.get(
f”{KALSHI_API}/markets”,
params={“limit”: 50, “status”: “open”, “series_ticker”: ticker},
headers={“Content-Type”: “application/json”},
timeout=10
)
if resp.status_code != 200:
continue
for market in resp.json().get(“markets”, []):
title = market.get(“title”, “”)
if not any(kw in title.lower() for kw in
[“points”, “assists”, “rebounds”, “goals”, “shots”]):
continue
line = 0.0
for w in title.split():
try:
line = float(w)
break
except ValueError:
continue
picks.append({
“source”: “Kalshi”,
“player”: title,
“stat”:   market.get(“subtitle”, “”),
“line”:   line,
“sport”:  ticker.lower(),
})
except Exception as e:
logger.warning(f”Kalshi fetch failed: {e}”)
return picks

def fetch_dabble():
picks = []
try:
headers = {“Content-Type”: “application/json”}
if DABBLE_TOKEN:
headers[“Authorization”] = f”Bearer {DABBLE_TOKEN}”
resp = requests.get(f”{DABBLE_API}/propositions”, headers=headers, timeout=10)
if resp.status_code == 200:
for prop in resp.json().get(“data”, []):
attrs = prop.get(“attributes”, {})
line = attrs.get(“line”)
if not line:
continue
picks.append({
“source”: “Dabble”,
“player”: attrs.get(“player_name”, “Unknown”),
“stat”:   attrs.get(“stat_type”, “”),
“line”:   float(line),
“sport”:  attrs.get(“sport”, “”),
})
except Exception as e:
logger.warning(f”Dabble fetch failed: {e}”)
return picks

def fetch_x_signals():
if not X_BEARER_TOKEN:
return []
try:
query = “ OR “.join(f’”{t}”’ for t in SEARCH_TERMS[:4]) + “ -is:retweet lang:en”
resp = requests.get(
“https://api.twitter.com/2/tweets/search/recent”,
headers={“Authorization”: f”Bearer {X_BEARER_TOKEN}”},
params={“query”: query, “max_results”: 50, “tweet.fields”: “created_at”},
timeout=10
)
if resp.status_code == 200:
return resp.json().get(“data”, [])
except Exception as e:
logger.warning(f”X fetch failed: {e}”)
return []

def extract_from_tweets(tweets):
props = []
for tweet in tweets:
text = tweet.get(“text”, “”).lower()
if “over” not in text and “under” not in text:
continue
for w in text.split():
cleaned = w.replace(”.5”, “”).replace(”.”, “”, 1)
if cleaned.isdigit():
try:
line = float(w)
if 0.5 <= line <= 200:
props.append({
“source”: “X”,
“player”: “Signal”,
“stat”:   “prop”,
“line”:   line,
“sport”:  “Mixed”,
})
break
except ValueError:
continue
return props

# ============================================

# PROJECTION & EDGE MODEL

# ============================================

def compute_edge(line, source, stat=””):
if line <= 0:
return 0, 0, 0
boosts = {“PrizePicks”: 0.055, “Kalshi”: 0.045, “Dabble”: 0.050, “X”: 0.040}
boost = boosts.get(source, 0.05)
s = stat.lower()
if “assist” in s:    boost += 0.010
elif “point” in s:   boost += 0.008
elif “rebound” in s: boost -= 0.005
projection = line * (1 + boost)
std_dev    = line * 0.18
z          = (projection - line) / std_dev
probability = 0.5 * (1 + math.erf(z / math.sqrt(2)))
edge        = probability - (1 / DECIMAL_ODDS)
return round(projection, 2), round(probability, 4), round(edge, 4)

# ============================================

# PICK GENERATOR

# ============================================

def generate_picks(platforms=None):
if platforms is None:
platforms = [“prizepicks”, “kalshi”, “dabble”, “x”]
raw = []
if “prizepicks” in platforms: raw.extend(fetch_prizepicks())
if “kalshi”     in platforms: raw.extend(fetch_kalshi())
if “dabble”     in platforms: raw.extend(fetch_dabble())
if “x”          in platforms:
raw.extend(extract_from_tweets(fetch_x_signals()))

```
picks, seen = [], set()
for prop in raw:
    line = prop.get("line", 0)
    if line <= 0:
        continue
    projection, probability, edge = compute_edge(line, prop["source"], prop.get("stat", ""))
    if probability >= MIN_CONFIDENCE and edge >= MIN_EDGE:
        key = f"{prop['player']}_{prop['stat']}_{line}"
        if key in seen:
            continue
        seen.add(key)
        picks.append({
            "source":         prop["source"],
            "player":         prop["player"],
            "stat":           prop.get("stat", ""),
            "sport":          prop.get("sport", ""),
            "line":           line,
            "projection":     projection,
            "probability":    probability,
            "edge":           edge,
            "recommendation": "OVER",
            "grade":          "A" if edge >= 0.12 else "B" if edge >= 0.09 else "C",
        })
picks.sort(key=lambda x: x["edge"], reverse=True)
return picks[:15]
```

# ============================================

# FORMATTING

# ============================================

SOURCE_EMOJI = {“PrizePicks”: “🏀”, “Kalshi”: “📈”, “Dabble”: “🎲”, “X”: “🐦”}
GRADE_EMOJI  = {“A”: “🟢”, “B”: “🟡”, “C”: “🟠”}

def format_picks(picks, label):
ts  = datetime.now().strftime(”%b %d %I:%M %p”)
msg = f”🥷 *PROPNINJA — {label}*\n_{ts}_\n\n”
for i, p in enumerate(picks[:10], 1):
em = SOURCE_EMOJI.get(p[“source”], “📌”)
gr = GRADE_EMOJI.get(p[“grade”], “⚪”)
msg += (
f”{i}. {gr} *{p[‘player’]}* {em}\n”
f”   {p[‘stat’]} | {p[‘sport’]}\n”
f”   Line: `{p['line']}` → Proj: `{p['projection']}`\n”
f”   {p[‘recommendation’]} | Conf: `{p['probability']*100:.1f}%` | “
f”Edge: `+{p['edge']*100:.1f}%` | Grade: {p[‘grade’]}\n\n”
)
msg += “⚠️ *For entertainment only. Gamble responsibly.*”
return msg

def main_keyboard():
return InlineKeyboardMarkup([
[InlineKeyboardButton(“🎯 All Live Picks”, callback_data=“picks_all”)],
[
InlineKeyboardButton(“🏀 PrizePicks”, callback_data=“picks_prizepicks”),
InlineKeyboardButton(“📈 Kalshi”,     callback_data=“picks_kalshi”),
],
[
InlineKeyboardButton(“🎲 Dabble”,     callback_data=“picks_dabble”),
InlineKeyboardButton(“🐦 X Signals”,  callback_data=“picks_x”),
],
[InlineKeyboardButton(“ℹ️ How It Works”, callback_data=“howto”)],
])

# ============================================

# TELEGRAM HANDLERS

# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“🥷 *PropNinja Bot*\n”
“━━━━━━━━━━━━━━━━━━━━\n”
“Real-time EV picks: PrizePicks · Kalshi · Dabble · X\n\n”
“Min confidence: 64% | Min edge: 6%\n\n”
“Choose an option:”,
reply_markup=main_keyboard(),
parse_mode=“Markdown”
)

async def picks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(“⏳ Fetching picks across all platforms…”)
picks = generate_picks()
if not picks:
await update.message.reply_text(
“😕 No picks met the threshold right now.\nTry again closer to game time!”
)
return
msg = format_picks(picks, “ALL PLATFORMS”)
await update.message.reply_text(msg, parse_mode=“Markdown”)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()
data  = query.data

```
nav = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔄 Refresh",   callback_data=data)],
    [InlineKeyboardButton("🏠 Main Menu", callback_data="menu")],
])

if data == "menu":
    await query.edit_message_text(
        "🥷 *PropNinja Bot*\n\nChoose a platform:",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )
    return

if data == "howto":
    await query.edit_message_text(
        "📊 *How PropNinja Works*\n\n"
        "1️⃣ Pulls live lines from PrizePicks, Kalshi & Dabble\n"
        "2️⃣ Scans X/Twitter for sharp money signals\n"
        "3️⃣ Applies source bias + stat corrections\n"
        "4️⃣ Calculates edge vs implied probability\n"
        "5️⃣ Only shows picks ≥64% confidence & ≥6% edge\n\n"
        "🟢 A = edge ≥12% | 🟡 B = edge ≥9% | 🟠 C = edge ≥6%\n\n"
        "⚠️ Entertainment only. Gamble responsibly.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu")]
        ]),
        parse_mode="Markdown"
    )
    return

platform_map = {
    "picks_all":        None,
    "picks_prizepicks": ["prizepicks"],
    "picks_kalshi":     ["kalshi"],
    "picks_dabble":     ["dabble"],
    "picks_x":          ["x"],
}

if data in platform_map:
    await query.edit_message_text("⏳ Fetching picks... (5-10 seconds)")
    picks = generate_picks(platforms=platform_map[data])
    label = "ALL PLATFORMS" if platform_map[data] is None else platform_map[data][0].upper()
    if not picks:
        await query.edit_message_text(
            "😕 No picks met the threshold right now.\n\n"
            "• APIs may have no live lines (off-peak hours)\n"
            "• Try again closer to game time!",
            reply_markup=nav
        )
        return
    msg = format_picks(picks, label)
    try:
        await query.edit_message_text(msg[:4096], parse_mode="Markdown", reply_markup=nav)
    except Exception:
        await query.edit_message_text(msg[:4000], reply_markup=nav)
```

# ============================================

# MAIN

# ============================================

def main():
if not TELEGRAM_TOKEN:
raise ValueError(“TELEGRAM_TOKEN is not set! Add it to Railway Variables.”)
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler(“start”, start))
app.add_handler(CommandHandler(“picks”, picks_command))
app.add_handler(CallbackQueryHandler(button_handler))
logger.info(“🚀 PropNinja Bot is running…”)
app.run_polling(allowed_updates=Update.ALL_TYPES)

if **name** == “**main**”:
main()
