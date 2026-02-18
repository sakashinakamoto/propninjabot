import os
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

app = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# ---- Commands ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is live 🚀")

application.add_handler(CommandHandler("start", start))

# ---- Webhook Route ----
@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "ok"

# ---- Main ----
if __name__ == "__main__":
    application.bot.set_webhook(url=f"{RENDER_EXTERNAL_URL}/{TOKEN}")
    app.run(host="0.0.0.0", port=10000)
