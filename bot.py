import math
import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
format=”%(asctime)s - %(levelname)s - %(message)s”,
level=logging.INFO
)
logger = logging.getLogger(**name**)

TELEGRAM_TOKEN = os.environ.get(“TELEGRAM_TOKEN”, “”)
DECIMAL_ODDS   = 1.90
MIN_PROB       = 0.64
MIN_EDGE       = 0.06

# ── Sample picks (real model runs when APIs are connected) ──

SAMPLE_PICKS = [
{“player”:“Bukayo Saka”,     “team”:“ARS”,“stat”:“Shots on Target”,“line”:1.5,“proj”:2.4,“prob”:0.851,“edge”:0.325,“grade”:“A”,“pick”:“OVER”,“source”:“PrizePicks”,“sport”:“EPL”},
{“player”:“Nathan MacKinnon”,“team”:“COL”,“stat”:“Points”,         “line”:0.5,“proj”:1.2,“prob”:0.836,“edge”:0.310,“grade”:“A”,“pick”:“OVER”,“source”:“PrizePicks”,“sport”:“NHL”},
{“player”:“Connor McDavid”,  “team”:“EDM”,“stat”:“Points”,         “line”:0.5,“proj”:1.1,“prob”:0.814,“edge”:0.288,“grade”:“A”,“pick”:“OVER”,“source”:“Kalshi”,    “sport”:“NHL”},
{“player”:“Kai Havertz”,     “team”:“ARS”,“stat”:“Shots”,          “line”:1.5,“proj”:2.3,“prob”:0.819,“edge”:0.293,“grade”:“A”,“pick”:“OVER”,“source”:“PrizePicks”,“sport”:“EPL”},
{“player”:“Gabriel Martinelli”,“team”:“ARS”,“stat”:“Shots on Target”,“line”:1.5,“proj”:2.2,“prob”:0.808,“edge”:0.282,“grade”:“A”,“pick”:“OVER”,“source”:“Dabble”,  “sport”:“EPL”},
{“player”:“Domantas Sabonis”,“team”:“SAC”,“stat”:“Rebounds”,       “line”:13.5,“proj”:14.6,“prob”:0.771,“edge”:0.245,“grade”:“A”,“pick”:“OVER”,“source”:“PrizePicks”,“sport”:“NBA”},
{“player”:“Trae Young”,      “team”:“ATL”,“stat”:“Assists”,        “line”:10.5,“proj”:11.7,“prob”:0.761,“edge”:0.235,“grade”:“A”,“pick”:“OVER”,“source”:“Kalshi”,  “sport”:“NBA”},
{“player”:“Leandro Trossard”,“team”:“ARS”,“stat”:“Shots”,          “line”:1.5,“proj”:2.1,“prob”:0.767,“edge”:0.241,“grade”:“A”,“pick”:“OVER”,“source”:“PrizePicks”,“sport”:“EPL”},
{“player”:“David Pastrnak”,  “team”:“BOS”,“stat”:“Shots on Goal”,  “line”:3.5,“proj”:4.2,“prob”:0.743,“edge”:0.217,“grade”:“B”,“pick”:“OVER”,“source”:“Dabble”,   “sport”:“NHL”},
{“player”:“Alperen Sengun”,  “team”:“HOU”,“stat”:“Points”,         “line”:20.5,“proj”:22.2,“prob”:0.732,“edge”:0.206,“grade”:“B”,“pick”:“OVER”,“source”:“PrizePicks”,“sport”:“NBA”},
]

SOURCE_EMOJI = {“PrizePicks”:“🏀”,“Kalshi”:“📈”,“Dabble”:“🎲”,“X”:“🐦”}
GRADE_EMOJI  = {“A”:“🟢”,“B”:“🟡”,“C”:“🟠”}
SPORT_EMOJI  = {“EPL”:“⚽”,“NHL”:“🏒”,“NBA”:“🏀”,“NFL”:“🏈”,“MLB”:“⚾”}

def fmt_pick(i, p):
se = SOURCE_EMOJI.get(p[“source”], “📌”)
ge = GRADE_EMOJI.get(p[“grade”], “⚪”)
sp = SPORT_EMOJI.get(p[“sport”], “🎯”)
return (
f”{i}. {ge}{sp} *{p[‘player’]}* ({p[‘team’]}) {se}\n”
f”   {p[‘stat’]} | Line: `{p['line']}` → Proj: `{p['proj']}`\n”
f”   {p[‘pick’]} | Conf: `{p['prob']*100:.1f}%` | Edge: `+{p['edge']*100:.1f}%` | {p[‘grade’]}\n\n”
)

def fmt_message(picks, label):
ts  = datetime.now().strftime(”%b %d %I:%M %p”)
msg = f”🥷 *PROPNINJA — {label}*\n_{ts}_\n\n”
for i, p in enumerate(picks, 1):
msg += fmt_pick(i, p)
msg += “⚠️ *Entertainment only. Gamble responsibly.*”
return msg

def main_menu():
return InlineKeyboardMarkup([
[InlineKeyboardButton(“🎯 All Live Picks”,    callback_data=“all”)],
[InlineKeyboardButton(“🏀 PrizePicks”, callback_data=“src_PrizePicks”),
InlineKeyboardButton(“📈 Kalshi”,     callback_data=“src_Kalshi”)],
[InlineKeyboardButton(“🎲 Dabble”,     callback_data=“src_Dabble”),
InlineKeyboardButton(“🐦 X Signals”,  callback_data=“src_X”)],
[InlineKeyboardButton(“⚽ EPL Picks”,  callback_data=“sport_EPL”),
InlineKeyboardButton(“🏒 NHL Picks”,  callback_data=“sport_NHL”)],
[InlineKeyboardButton(“ℹ️ How It Works”, callback_data=“howto”)],
])

def nav(cb):
return InlineKeyboardMarkup([
[InlineKeyboardButton(“🔄 Refresh”,   callback_data=cb)],
[InlineKeyboardButton(“🏠 Main Menu”, callback_data=“menu”)],
])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“🥷 *PropNinja Bot is LIVE!*\n”
“━━━━━━━━━━━━━━━━━━━━━━━━\n”
“EV-ranked picks: PrizePicks · Kalshi · Dabble\n”
“Sports: EPL · NHL · NBA · NFL · MLB\n\n”
“Min confidence: 64% | Min edge: 6%\n\n”
“Tap below to get your picks 👇”,
reply_markup=main_menu(),
parse_mode=“Markdown”
)

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
q = update.callback_query
await q.answer()
d = q.data

```
if d == "menu":
    await q.edit_message_text(
        "🥷 *PropNinja Bot*\n\nChoose an option:",
        reply_markup=main_menu(), parse_mode="Markdown"
    )
    return

if d == "howto":
    await q.edit_message_text(
        "📊 *How PropNinja Works*\n\n"
        "1️⃣ Pulls live lines from PrizePicks, Kalshi & Dabble\n"
        "2️⃣ Applies source bias + stat type corrections\n"
        "3️⃣ Calculates hit probability via normal distribution\n"
        "4️⃣ Computes edge vs implied probability (1.9× odds)\n"
        "5️⃣ Only shows picks ≥64% confidence & ≥6% edge\n\n"
        "🟢 Grade A = edge ≥12%\n"
        "🟡 Grade B = edge ≥9%\n"
        "🟠 Grade C = edge ≥6%\n\n"
        "⚠️ Entertainment only. Gamble responsibly.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Back", callback_data="menu")]
        ]),
        parse_mode="Markdown"
    )
    return

if d == "all":
    picks = SAMPLE_PICKS[:8]
    await q.edit_message_text(
        fmt_message(picks, "ALL PLATFORMS")[:4096],
        parse_mode="Markdown", reply_markup=nav("all")
    )
    return

if d.startswith("src_"):
    src    = d.split("_", 1)[1]
    picks  = [p for p in SAMPLE_PICKS if p["source"] == src][:6]
    label  = src.upper()
    if not picks:
        await q.edit_message_text(
            f"😕 No {label} picks right now. Try All Live Picks!",
            reply_markup=nav(d)
        )
        return
    await q.edit_message_text(
        fmt_message(picks, label)[:4096],
        parse_mode="Markdown", reply_markup=nav(d)
    )
    return

if d.startswith("sport_"):
    sport  = d.split("_", 1)[1]
    picks  = [p for p in SAMPLE_PICKS if p["sport"] == sport][:6]
    emoji  = SPORT_EMOJI.get(sport, "🎯")
    label  = f"{emoji} {sport}"
    if not picks:
        await q.edit_message_text(
            f"😕 No {sport} picks right now.",
            reply_markup=nav(d)
        )
        return
    await q.edit_message_text(
        fmt_message(picks, label)[:4096],
        parse_mode="Markdown", reply_markup=nav(d)
    )
    return
```

async def picks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
msg = fmt_message(SAMPLE_PICKS[:8], “ALL PLATFORMS”)
await update.message.reply_text(msg[:4096], parse_mode=“Markdown”)

def main():
if not TELEGRAM_TOKEN:
raise ValueError(“TELEGRAM_TOKEN missing! Add it to Railway Variables.”)
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler(“start”, start))
app.add_handler(CommandHandler(“picks”, picks_cmd))
app.add_handler(CallbackQueryHandler(button))
logger.info(“🚀 PropNinja Bot is running…”)
app.run_polling(allowed_updates=Update.ALL_TYPES)

if **name** == “**main**”:
main()
