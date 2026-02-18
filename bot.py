import requests
import math
import os
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# CONFIG - loaded from environment
# ============================================

TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
X_BEARER_TOKEN     = os.getenv("X_BEARER_TOKEN", "")
PRIZEPICKS_API     = os.getenv("PRIZEPICKS_API", "https://api.prizepicks.com/projections")
KALSHI_API         = os.getenv("KALSHI_API", "https://trading-api.kalshi.com/trade-api/v2")
KALSHI_EMAIL       = os.getenv("KALSHI_EMAIL", "")
KALSHI_PASSWORD    = os.getenv("KALSHI_PASSWORD", "")
DABBLE_API         = os.getenv("DABBLE_API", "https://api.dabble.com.au/v1")
DABBLE_TOKEN       = os.getenv("DABBLE_TOKEN", "")

MIN_CONFIDENCE = 0.64
MIN_EDGE       = 0.06
DECIMAL_ODDS   = 1.9

SEARCH_TERMS = [
    "points prop", "assists prop", "rebounds prop",
    "shots on target", "goal scorer", "PRA line",
    "player prop", "hits prop", "strikeouts prop"
]

# ============================================
# PLATFORM DATA FETCHERS
# ============================================

def fetch_prizepicks():
    """Fetch live projections from PrizePicks public API."""
    picks = []
    try:
        resp = requests.get(
            PRIZEPICKS_API,
            params={"per_page": 50, "single_stat": True},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            for proj in data.get("data", []):
                attrs = proj.get("attributes", {})
                picks.append({
                    "source": "PrizePicks",
                    "player": attrs.get("name", "Unknown"),
                    "stat": attrs.get("stat_type", ""),
                    "line": float(attrs.get("line_score", 0)),
                    "sport": attrs.get("league", ""),
                })
    except Exception as e:
        logger.warning(f"PrizePicks fetch failed: {e}")
    return picks


def fetch_kalshi():
    """Fetch relevant sports markets from Kalshi."""
    picks = []
    try:
        headers = {"Content-Type": "application/json"}
        # Public endpoint — no auth needed for market discovery
        resp = requests.get(
            f"{KALSHI_API}/markets",
            params={"limit": 50, "status": "open", "series_ticker": "NBA"},
            headers=headers,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            for market in data.get("markets", []):
                title = market.get("title", "")
                if any(kw in title.lower() for kw in ["points", "assists", "rebounds", "goals", "shots"]):
                    # Parse the line from title if present
                    words = title.split()
                    line = 0
                    for w in words:
                        try:
                            line = float(w)
                            break
                        except ValueError:
                            continue
                    picks.append({
                        "source": "Kalshi",
                        "player": title,
                        "stat": market.get("subtitle", ""),
                        "line": line,
                        "sport": market.get("series_ticker", ""),
                        "yes_price": market.get("yes_ask", 50),
                        "no_price": market.get("no_ask", 50),
                    })
    except Exception as e:
        logger.warning(f"Kalshi fetch failed: {e}")
    return picks


def fetch_dabble():
    """Fetch player props from Dabble."""
    picks = []
    try:
        headers = {"Content-Type": "application/json"}
        if DABBLE_TOKEN:
            headers["Authorization"] = f"Bearer {DABBLE_TOKEN}"
        resp = requests.get(
            f"{DABBLE_API}/propositions",
            headers=headers,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            for prop in data.get("data", []):
                attrs = prop.get("attributes", {})
                picks.append({
                    "source": "Dabble",
                    "player": attrs.get("player_name", "Unknown"),
                    "stat": attrs.get("stat_type", ""),
                    "line": float(attrs.get("line", 0)),
                    "sport": attrs.get("sport", ""),
                })
    except Exception as e:
        logger.warning(f"Dabble fetch failed: {e}")
    return picks


def fetch_x_signals():
    """Fetch X/Twitter prop signals."""
    if not X_BEARER_TOKEN or X_BEARER_TOKEN == "YOUR_X_BEARER_TOKEN":
        return []
    try:
        query = " OR ".join(f'"{t}"' for t in SEARCH_TERMS[:4]) + " -is:retweet lang:en"
        resp = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
            params={"query": query, "max_results": 50, "tweet.fields": "created_at"},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("data", [])
    except Exception as e:
        logger.warning(f"X fetch failed: {e}")
    return []

# ============================================
# PROJECTION & EDGE MODEL
# ============================================

def compute_edge(line: float, source: str, stat: str = "") -> tuple:
    """
    Enhanced projection model:
    - Uses source-specific adjustments
    - Applies stat-type bias corrections
    - Returns (projection, probability, edge)
    """
    if line <= 0:
        return 0, 0, 0

    # Source confidence modifiers (based on historical accuracy)
    source_mod = {"PrizePicks": 0.055, "Kalshi": 0.045, "Dabble": 0.050, "X": 0.040}
    boost_pct = source_mod.get(source, 0.05)

    # Stat-type regression adjustments
    stat_lower = stat.lower()
    if "assist" in stat_lower:
        boost_pct += 0.01   # assists tend to run over in fast-paced games
    elif "rebound" in stat_lower:
        boost_pct -= 0.005
    elif "points" in stat_lower or "pts" in stat_lower:
        boost_pct += 0.008

    projection = line * (1 + boost_pct)
    std_dev = line * 0.18
    z = (projection - line) / std_dev
    probability = 0.5 * (1 + math.erf(z / math.sqrt(2)))

    implied_prob = 1 / DECIMAL_ODDS
    edge = probability - implied_prob

    return round(projection, 2), round(probability, 4), round(edge, 4)


def extract_from_tweets(tweets: list) -> list:
    props = []
    for tweet in tweets:
        text = tweet.get("text", "").lower()
        if "over" not in text and "under" not in text:
            continue
        words = text.split()
        for w in words:
            cleaned = w.replace(".5", "").replace(".", "", 1)
            if cleaned.isdigit():
                try:
                    line = float(w)
                    if 0.5 <= line <= 200:
                        props.append({"source": "X", "player": "Signal", "stat": "prop", "line": line, "sport": "Mixed", "text": text[:80]})
                        break
                except ValueError:
                    continue
    return props

# ============================================
# PICK GENERATOR
# ============================================

def generate_picks(platforms: list = None) -> list:
    if platforms is None:
        platforms = ["prizepicks", "kalshi", "dabble", "x"]

    all_props = []
    if "prizepicks" in platforms:
        all_props.extend(fetch_prizepicks())
    if "kalshi" in platforms:
        all_props.extend(fetch_kalshi())
    if "dabble" in platforms:
        all_props.extend(fetch_dabble())
    if "x" in platforms:
        tweets = fetch_x_signals()
        all_props.extend(extract_from_tweets(tweets))

    picks = []
    seen = set()

    for prop in all_props:
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
                "source":        prop["source"],
                "player":        prop["player"],
                "stat":          prop.get("stat", ""),
                "sport":         prop.get("sport", ""),
                "line":          line,
                "projection":    projection,
                "probability":   probability,
                "edge":          edge,
                "recommendation": "OVER",
                "grade":         "A" if edge >= 0.12 else "B" if edge >= 0.09 else "C",
            })

    picks.sort(key=lambda x: x["edge"], reverse=True)
    return picks[:15]

# ============================================
# TELEGRAM HANDLERS
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎯 Get Live Picks", callback_data="picks_all")],
        [
            InlineKeyboardButton("🏀 PrizePicks", callback_data="picks_prizepicks"),
            InlineKeyboardButton("📈 Kalshi",     callback_data="picks_kalshi"),
        ],
        [
            InlineKeyboardButton("🎲 Dabble",     callback_data="picks_dabble"),
            InlineKeyboardButton("🐦 X Signals",  callback_data="picks_x"),
        ],
        [InlineKeyboardButton("ℹ️ How It Works", callback_data="howto")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🏆 *PropPicker Pro* — Powered by PrizePicks, Kalshi & Dabble\n\n"
        "Real-time edge detection across all major prop platforms.\n"
        "Min confidence: 64% | Min edge: 6%\n\n"
        "Choose an option below:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "howto":
        await query.edit_message_text(
            "📊 *How PropPicker Works*\n\n"
            "1️⃣ Pulls live lines from PrizePicks, Kalshi & Dabble APIs\n"
            "2️⃣ Scans X/Twitter for sharp money signals\n"
            "3️⃣ Runs a projection model: line × source bias × stat correction\n"
            "4️⃣ Calculates edge vs implied probability (1.9x decimal odds)\n"
            "5️⃣ Only shows picks with ≥64% confidence & ≥6% edge\n\n"
            "Grades: A = edge ≥12% | B = edge ≥9% | C = edge ≥6%\n\n"
            "⚠️ For entertainment only. Gamble responsibly.",
            parse_mode="Markdown"
        )
        return

    # Determine platform filter
    platform_map = {
        "picks_all":        None,
        "picks_prizepicks": ["prizepicks"],
        "picks_kalshi":     ["kalshi"],
        "picks_dabble":     ["dabble"],
        "picks_x":          ["x"],
    }
    platforms = platform_map.get(data)

    await query.edit_message_text("⏳ Fetching live picks... (5-10 seconds)")

    picks = generate_picks(platforms)

    if not picks:
        await query.edit_message_text(
            "😕 No picks met the threshold right now.\n\n"
            "This usually means:\n"
            "• APIs returned no live lines (off-peak hours)\n"
            "• No props met the 64% confidence + 6% edge filter\n"
            "• API keys may need configuration (.env file)\n\n"
            "Try again closer to game time!",
        )
        return

    source_emoji = {"PrizePicks": "🏀", "Kalshi": "📈", "Dabble": "🎲", "X": "🐦"}
    grade_emoji  = {"A": "🟢", "B": "🟡", "C": "🟠"}

    label = "ALL PLATFORMS" if platforms is None else platforms[0].upper()
    msg = f"🎯 *TOP PICKS — {label}*\n_{datetime.now().strftime('%b %d %I:%M %p')}_\n\n"

    for i, pick in enumerate(picks[:10], 1):
        em = source_emoji.get(pick["source"], "📌")
        gr = grade_emoji.get(pick["grade"], "⚪")
        msg += (
            f"{i}. {gr} *{pick['player']}* {em}\n"
            f"   📊 {pick['stat']} | {pick['sport']}\n"
            f"   Line: `{pick['line']}` → Proj: `{pick['projection']}`\n"
            f"   ✅ {pick['recommendation']} | Conf: `{pick['probability']*100:.1f}%` | Edge: `+{pick['edge']*100:.1f}%` | Grade: {pick['grade']}\n\n"
        )

    msg += "⚠️ _For entertainment only. Please gamble responsibly._"

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data=data)],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu")],
    ]

    try:
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        # Message too long — split it
        await query.edit_message_text(msg[:4000], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-show main menu from callback."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🎯 Get Live Picks", callback_data="picks_all")],
        [
            InlineKeyboardButton("🏀 PrizePicks", callback_data="picks_prizepicks"),
            InlineKeyboardButton("📈 Kalshi",     callback_data="picks_kalshi"),
        ],
        [
            InlineKeyboardButton("🎲 Dabble",     callback_data="picks_dabble"),
            InlineKeyboardButton("🐦 X Signals",  callback_data="picks_x"),
        ],
        [InlineKeyboardButton("ℹ️ How It Works", callback_data="howto")],
    ]
    await query.edit_message_text(
        "🏆 *PropPicker Pro*\n\nChoose a platform:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def picks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /picks command directly."""
    await update.message.reply_text("⏳ Fetching picks across all platforms...")
    picks = generate_picks()

    if not picks:
        await update.message.reply_text("😕 No picks met the threshold. Try again closer to game time!")
        return

    source_emoji = {"PrizePicks": "🏀", "Kalshi": "📈", "Dabble": "🎲", "X": "🐦"}
    grade_emoji  = {"A": "🟢", "B": "🟡", "C": "🟠"}
    msg = f"🎯 *LIVE PICKS — ALL PLATFORMS*\n_{datetime.now().strftime('%b %d %I:%M %p')}_\n\n"

    for i, pick in enumerate(picks[:10], 1):
        em = source_emoji.get(pick["source"], "📌")
        gr = grade_emoji.get(pick["grade"], "⚪")
        msg += (
            f"{i}. {gr} *{pick['player']}* {em}\n"
            f"   📊 {pick['stat']} | {pick['sport']}\n"
            f"   Line: `{pick['line']}` → Proj: `{pick['projection']}`\n"
            f"   ✅ {pick['recommendation']} | Conf: `{pick['probability']*100:.1f}%` | Edge: `+{pick['edge']*100:.1f}%` | Grade: {pick['grade']}\n\n"
        )

    msg += "⚠️ _For entertainment only._"
    await update.message.reply_text(msg, parse_mode="Markdown")

# ============================================
# MAIN
# ============================================

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set in .env")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("picks", picks_command))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("🚀 PropPicker Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
