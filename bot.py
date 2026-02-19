import osimport os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
PICKS = [
{"player": "Bukayo Saka", "team": "ARS", "stat": "Shots on Target", "line": 1.5, "proj": 2.4, "prob": 0.851, "edge": 0.325, "grade": "A", "pick": "OVER", "source": "PrizePicks", "sport": "EPL"},
{"player": "Nathan MacKinnon", "team": "COL", "stat": "Points", "line": 0.5, "proj": 1.2, "prob": 0.836, "edge": 0.310, "grade": "A", "pick": "OVER", "source": "PrizePicks", "sport": "NHL"},
{"player": "Connor McDavid", "team": "EDM", "stat": "Points", "line": 0.5, "proj": 1.1, "prob": 0.814, "edge": 0.288, "grade": "A", "pick": "OVER", "source": "Kalshi", "sport": "NHL"},
{"player": "Trae Young", "team": "ATL", "stat": "Assists", "line": 10.5, "proj": 11.7, "prob": 0.761, "edge": 0.235, "grade": "A", "pick": "OVER", "source": "Kalshi", "sport": "NBA"},
{"player": "Alperen Sengun", "team": "HOU", "stat": "Points", "line": 20.5, "proj": 22.2, "prob": 0.732, "edge": 0.206, "grade": "B", "pick": "OVER", "source": "PrizePicks", "sport": "NBA"},
]
def fmt(picks, label):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    msg = "PROPNINJA - " + label + "\n" + ts + "\n\n"
    for i, p in enumerate(picks, 1):
        msg += str(i) + ". " + p["grade"] + " " + p["player"] + " (" + p["team"] + ")\n"
        msg += "   " + p["stat"] + " | Line: " + str(p["line"]) + " Proj: " + str(p["proj"]) + "\n"
        msg += "   " + p["pick"] + " | Conf: " + str(round(p["prob"]*100, 1)) + "% | Edge: +" + str(round(p["edge"]*100, 1)) + "% | " + p["source"] + "\n\n"
    msg += "For entertainment only. Gamble responsibly."
    return msgdef menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Get All Picks", callback_data="all")],
        [InlineKeyboardButton("PrizePicks", callback_data="src_PrizePicks"),
         InlineKeyboardButton("Kalshi", callback_data="src_Kalshi")],
        [InlineKeyboardButton("EPL Picks", callback_data="sport_EPL"),
         InlineKeyboardButton("NHL Picks", callback_data="sport_NHL")],
        [InlineKeyboardButton("NBA Picks", callback_data="sport_NBA")],
    ])

def nav(cb):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Refresh", callback_data=cb)],
        [InlineKeyboardButton("Main Menu", callback_data="menu")],
    ])async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "PropNinja Bot is LIVE\nTap below to get picks:",
        reply_markup=menu()
    )

async def picks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(fmt(PICKS[:5], "ALL PLATFORMS")[:4096])
async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    if d == "menu":
        await q.edit_message_text("PropNinja - Choose:", reply_markup=menu())
        return
    if d == "all":
        await q.edit_message_text(fmt(PICKS[:5], "ALL")[:4096], reply_markup=nav("all"))
        return
    if d.startswith("src_"):
        src = d.split("_", 1)[1]
        picks = [p for p in PICKS if p["source"] == src]
        await q.edit_message_text(fmt(picks, src)[:4096] if picks else "No picks now.", reply_markup=nav(d))
        return
    if d.startswith("sport_"):
        sport = d.split("_", 1)[1]
        picks = [p for p in PICKS if p["sport"] == sport]
        await q.edit_message_text(fmt(picks, sport)[:4096] if picks else "No picks now.", reply_markup=nav(d))
        return
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing!")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("picks", picks_cmd))
    app.add_handler(CallbackQueryHandler(button))
    logger.info("PropNinja Bot is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
