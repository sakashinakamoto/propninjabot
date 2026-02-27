import os
import logging
import requests
import math
from datetime import datetime
from typing import List, Dict, Any

import numpy as np
from sklearn.linear_model import LogisticRegression

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# CONFIGURATION CONSTANTS
# =========================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

DECIMAL_ODDS_PRIZEPICKS = 1.90
MIN_PROB = 0.57
MIN_EDGE = 0.03
LIQUIDITY_THRESHOLD = 0  # set >0 if you want liquidity filtering

# =========================
# LOGGING SETUP
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("propninja")

# =========================
# DATA FETCHING FUNCTIONS
# =========================

def fetch_prizepicks() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            "https://api.prizepicks.com/projections",
            params={"per_page": 250, "single_stat": True},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"PrizePicks API status: {resp.status_code}")
            return []

        data = resp.json()
        players = {}

        for item in data.get("included", []):
            if item.get("type") == "new_player":
                attrs = item.get("attributes", {})
                players[item["id"]] = {
                    "name": attrs.get("display_name", "Unknown"),
                    "team": attrs.get("team", ""),
                }

        markets = []
        for proj in data.get("data", []):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type")
            league = attrs.get("league")

            if not line or not stat:
                continue

            try:
                line = float(line)
            except Exception:
                continue

            pid = (
                proj.get("relationships", {})
                .get("new_player", {})
                .get("data", {})
                .get("id")
            )

            player_info = players.get(pid, {"name": "Unknown", "team": ""})

            markets.append(
                {
                    "player": player_info["name"],
                    "team": player_info["team"],
                    "market": stat,
                    "line": line,
                    "source": "PrizePicks",
                    "league": league,
                    "price": None,  # fixed payout model
                    "liquidity": None,
                }
            )

        logger.info(f"Fetched {len(markets)} PrizePicks markets")
        return markets

    except Exception as e:
        logger.exception(f"PrizePicks fetch error: {e}")
        return []


def fetch_kalshi() -> List[Dict[str, Any]]:
    try:
        resp = requests.get(
            "https://trading-api.kalshi.com/trade-api/v2/markets",
            params={"limit": 1000, "status": "open"},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"Kalshi API status: {resp.status_code}")
            return []

        markets = []
        for m in resp.json().get("markets", []):
            title = m.get("title", "")
            subtitle = m.get("subtitle", "")
            category = m.get("category", "")
            price = m.get("yes_ask") or m.get("yes_bid")
            volume = m.get("volume", 0)
            open_interest = m.get("open_interest", 0)

            if not price:
                continue

            markets.append(
                {
                    "player": title[:50],
                    "team": "",
                    "market": subtitle or title[:30],
                    "line": None,
                    "source": "Kalshi",
                    "league": category,
                    "price": float(price) / 100 if float(price) > 1 else float(price),
                    "liquidity": volume + open_interest,
                }
            )

        logger.info(f"Fetched {len(markets)} Kalshi markets")
        return markets

    except Exception as e:
        logger.exception(f"Kalshi fetch error: {e}")
        return []

# =========================
# NORMALIZATION
# =========================

def normalize_markets(raw_markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []

    for m in raw_markets:
        if m["source"] == "Kalshi":
            implied_prob = float(m["price"])
        else:
            implied_prob = 1 / DECIMAL_ODDS_PRIZEPICKS

        normalized.append(
            {
                "player": m["player"],
                "team": m["team"],
                "market": m["market"],
                "source": m["source"],
                "league": m["league"],
                "implied_probability": implied_prob,
                "liquidity": m["liquidity"] or 0,
            }
        )

    return normalized

# =========================
# FEATURE ENGINEERING
# =========================

def build_feature_vector(market: Dict[str, Any]) -> np.ndarray:
    return np.array(
        [
            market["implied_probability"],
            market["liquidity"],
        ]
    )

# =========================
# PROBABILITY MODEL
# =========================

def run_edge_model(markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not markets:
        return []

    X = np.array([build_feature_vector(m) for m in markets])

    # Deterministic synthetic training baseline
    y = np.array([1 if m["implied_probability"] < 0.5 else 0 for m in markets])

    model = LogisticRegression()
    model.fit(X, y)

    probs = model.predict_proba(X)[:, 1]

    results = []
    for i, m in enumerate(markets):
        model_prob = float(probs[i])
        edge = model_prob - m["implied_probability"]

        results.append(
            {
                **m,
                "model_probability": model_prob,
                "edge": edge,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

    return results

# =========================
# SIGNAL FILTERING
# =========================

def filter_signals(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    signals = []

    for r in results:
        if r["model_probability"] >= MIN_PROB and r["edge"] >= MIN_EDGE:
            if r["liquidity"] >= LIQUIDITY_THRESHOLD:
                if r["edge"] > 0.06:
                    tier = "Tier A"
                elif r["edge"] > 0.04:
                    tier = "Tier B"
                else:
                    tier = "Tier C"

                signals.append({**r, "tier": tier})

    return signals

# =========================
# TELEGRAM HANDLER
# =========================

async def runmodel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Running Prop Ninja edge model...")

    raw_markets = fetch_prizepicks() + fetch_kalshi()
    normalized = normalize_markets(raw_markets)
    model_results = run_edge_model(normalized)
    signals = filter_signals(model_results)

    if not signals:
        await update.message.reply_text(
            "No qualified edges found. Market likely efficient right now."
        )
        return

    signals.sort(key=lambda x: x["edge"], reverse=True)

    messages = []
    for s in signals[:25]:
        msg = (
            f"🔥 PROP NINJA SIGNAL\n"
            f"{s['player']} | {s['market']}\n"
            f"Source: {s['source']}\n"
            f"Market Probability: {round(s['implied_probability']*100,2)}%\n"
            f"Model Probability: {round(s['model_probability']*100,2)}%\n"
            f"Edge: {round(s['edge']*100,2)}%\n"
            f"{s['tier']} | Confidence: {round(s['model_probability']*100,1)}%\n"
        )
        messages.append(msg)

    await update.message.reply_text("\n\n".join(messages)[:4096])

# =========================
# MAIN
# =========================

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("runmodel", runmodel_command))

    logger.info("PropNinja edge model bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()