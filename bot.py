import os
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
app = Flask(__name__)

# Telegram bot setup
bot_app = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is active 🚀")

bot_app.add_handler(CommandHandler("start", start))

@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook_handler():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return "OK"

if __name__ == "__main__":
    bot_app.bot.set_webhook(url=f"{os.environ.get('RENDER_EXTERNAL_URL')}/{TOKEN}")
    app.run(host="0.0.0.0", port=10000)
