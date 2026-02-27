import os
import math
import logging
import requests
import numpy as np
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from sklearn.linear_model import LogisticRegression

# -------------------------------
# CONFIGURATION CONSTANTS
# -------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
MIN_PROB = 0.57
MIN_EDGE_FLOOR = 0.01  # minimal edge to always include picks
PRIZEPICKS_API = "https://api.prizepicks.com/projections"
KALSHI_API = "https://trading-api.kalshi.com/trade-api/v2/markets"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("propninja")

# -------------------------------
# DATA FETCHING FUNCTIONS
# -------------------------------
def fetch_prizepicks():
    picks = []
    try:
        resp = requests.get(PRIZEPICKS_API, params={"per_page":250, "single_stat":True}, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"PrizePicks status {resp.status_code}")
            return []
        data = resp.json()
        players = {}
        for item in data.get("included", []):
            if item.get("type") == "new_player":
                attr = item.get("attributes", {})
                players[item["id"]] = {"name": attr.get("display_name","Unknown"), "team": attr.get("team","")}
        for proj in data.get("data", []):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type","")
            sport = attrs.get("league","")
            if not line or not stat: continue
            try: line = float(line)
            except: continue
            pid = proj.get("relationships",{}).get("new_player",{}).get("data",{}).get("id","")
            pinfo = players.get(pid,{"name": attrs.get("description","Unknown"), "team": ""})
            picks.append({
                "player": pinfo["name"],
                "team": pinfo["team"],
                "stat": stat,
                "line": line,
                "source": "PrizePicks",
                "sport": sport.upper()
            })
    except Exception as e:
        logger.warning(f"PrizePicks error: {e}")
    return picks

def fetch_kalshi():
    picks = []
    keywords = ["points", "assists", "rebounds", "goals", "shots", "strikeouts", "hits", "yards", "touchdowns"]
    try:
        resp = requests.get(KALSHI_API, params={"limit":1000,"status":"open"}, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Kalshi status {resp.status_code}")
            return []
        for market in resp.json().get("markets", []):
            title = market.get("title","")
            category = market.get("category","").upper()
            if not any(kw in title.lower() for kw in keywords): continue
            line = 0
            for w in title.replace("+"," ").replace(",","").split():
                try:
                    val = float(w)
                    if 0.5 <= val <= 500:
                        line = val
                        break
                except: continue
            if line <= 0: continue
            picks.append({
                "player": title[:40],
                "team": "",
                "stat": title[:30],
                "line": line,
                "source": "Kalshi",
                "sport": category if category else "KALSHI",
                "liquidity": market.get("volume",0) + market.get("open_interest",0)
            })
    except Exception as e:
        logger.warning(f"Kalshi error: {e}")
    return picks

# -------------------------------
# NORMALIZATION & FEATURE ENGINEERING
# -------------------------------
def normalize_markets(raw_markets):
    norm = []
    for m in raw_markets:
        implied = None
        if m["source"] == "PrizePicks":
            implied = 1 / 1.9
        else:
            implied = float(m.get("price", 0.5))
        norm.append({
            "player": m["player"],
            "team": m.get("team",""),
            "stat": m["stat"],
            "line": m["line"],
            "source": m["source"],
            "sport": m["sport"],
            "liquidity": m.get("liquidity",1),
            "implied_prob": implied
        })
    return norm

def build_features(markets):
    X = []
    for m in markets:
        X.append([m["line"], m.get("liquidity",1), len(m["stat"])])
    return np.array(X)

# -------------------------------
# MODEL EXECUTION
# -------------------------------
def run_edge_model(markets):
    if not markets: return []
    X = build_features(markets)
    model = LogisticRegression()
    y = np.array([0 if x[0]<5 else 1 for x in X])
    model.fit(X,y)
    probs = model.predict_proba(X)[:,1]
    signals = []
    for m, p in zip(markets, probs):
        edge = p - m["implied_prob"]
        tier = ""
        if edge > 0.06: tier = "A"
        elif edge > 0.04: tier = "B"
        elif edge >= MIN_EDGE_FLOOR: tier = "C"
        else: tier = "C"  # always include low edge
        signals.append({
            "player": m["player"],
            "market": m["stat"],
            "source": m["source"],
            "market_prob": round(m["implied_prob"],3),
            "model_prob": round(p,3),
            "edge": round(edge*100,1),
            "tier": tier,
            "confidence": round(p*100,1)
        })
    signals.sort(key=lambda x: x["edge"], reverse=True)
    return signals[:40]

def format_signals(signals):
    if not signals:
        return "No qualified edges found. Market likely efficient right now."
    msg = "🔥 PROP NINJA SIGNALS 🔥\n\n"
    for s in signals:
        msg += f"{s['player']} | {s['market']} | {s['source']} | "
        msg += f"Market Prob: {s['market_prob']} | Model Prob: {s['model_prob']} | "
        msg += f"Edge: +{s['edge']}% | Tier: {s['tier']} | Conf: {s['confidence']}%\n\n"
    return msg

# -------------------------------
# TELEGRAM HANDLER
# -------------------------------
async def runmodel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Running Prop Ninja edge model...")
    raw_markets = fetch_prizepicks() + fetch_kalshi()
    logger.info(f"Fetched {len(raw_markets)} raw markets")
    norm_markets = normalize_markets(raw_markets)
    signals = run_edge_model(norm_markets)
    msg = format_signals(signals)
    await update.message.reply_text(msg)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "PropNinja Bot\nLive edge model for PrizePicks & Kalshi.\nUse /runmodel to fetch live signals."
    )

# -------------------------------
# BOT ENTRY POINT
# -------------------------------
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing!")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("runmodel", runmodel_command))
    logger.info("PropNinja edge model bot running...")
    app.run_polling(allowed_updates=None)

if __name__ == "__main__":
    main()