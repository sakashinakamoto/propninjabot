# propninja_master_bot.py

import os
import math
import time
import asyncio
import logging
import random
from typing import Dict, List, Tuple
from datetime import datetime

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

DECIMAL_ODDS = 1.90
MIN_PROB = 0.60
MIN_EDGE = 0.05
NUM_SIMS = 10000
CACHE_TTL = 120


# -----------------------------
# SIMPLE TTL CACHE
# -----------------------------

class TTLCache:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self.store: Dict[str, Tuple[float, object]] = {}

    def get(self, key: str):
        if key not in self.store:
            return None
        ts, val = self.store[key]
        if time.time() - ts > self.ttl:
            del self.store[key]
            return None
        return val

    def set(self, key: str, value):
        self.store[key] = (time.time(), value)


cache = TTLCache(CACHE_TTL)


# -----------------------------
# CORE ENGINE
# -----------------------------

def quantum_boost(prob: float) -> float:
    amplitude = math.sqrt(max(prob, 0.01))
    interference = 0.04 * math.sin(math.pi * amplitude)
    boosted = min((amplitude + interference) ** 2, 0.99)
    return round(boosted, 4)


def compute_edge(line: float, stat: str):
    if line <= 0:
        return 0.0, 0.0, 0.0

    boost = 0.055
    s = stat.lower()

    keywords = {
        "assist": 0.010,
        "point": 0.008,
        "rebound": -0.005,
        "goal": 0.007,
        "shot": 0.006,
        "strikeout": 0.009,
        "hit": 0.005,
        "yard": 0.006,
        "touchdown": 0.007,
        "base": 0.004,
        "steal": 0.008,
        "block": 0.003,
        "save": 0.006,
        "corner": 0.005,
        "run": 0.005,
        "ace": 0.006,
    }

    for k, v in keywords.items():
        if k in s:
            boost += v
            break

    projection = line * (1 + boost)
    std_dev = line * 0.18

    z = (projection - line) / std_dev
    prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    prob = quantum_boost(prob)

    edge = prob - (1 / DECIMAL_ODDS)
    return round(projection, 2), round(prob, 4), round(edge, 4)


def grade(edge: float) -> str:
    if edge >= 0.14:
        return "A+"
    if edge >= 0.11:
        return "A"
    if edge >= 0.08:
        return "B"
    if edge >= 0.05:
        return "C"
    return "D"


def kelly(prob: float, odds: float = 1.90) -> float:
    b = odds - 1
    if b <= 0:
        return 0.0
    k = (prob * b - (1 - prob)) / b
    return round(max(k, 0) * 0.25, 4)


# -----------------------------
# ASYNC FETCHERS
# -----------------------------

async def fetch_json(session: aiohttp.ClientSession, url: str, params=None):
    try:
        async with session.get(url, params=params, timeout=15) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception:
        return None


async def fetch_prizepicks():
    cached = cache.get("prizepicks")
    if cached:
        return cached

    picks = []

    async with aiohttp.ClientSession() as session:
        data = await fetch_json(
            session,
            "https://api.prizepicks.com/projections",
            {"per_page": 250, "single_stat": True},
        )

        if not data:
            return []

        players = {}
        for item in data.get("included", []):
            if item.get("type") == "new_player":
                attrs = item.get("attributes", {})
                players[item["id"]] = {
                    "name": attrs.get("display_name", "Unknown"),
                    "team": attrs.get("team", ""),
                }

        for proj in data.get("data", []):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type", "")
            sport = attrs.get("league", "")

            if not line or not stat:
                continue

            try:
                line = float(line)
            except Exception:
                continue

            pid = proj.get("relationships", {}).get("new_player", {}).get("data", {}).get("id", "")
            pinfo = players.get(pid, {"name": attrs.get("description", "Unknown"), "team": ""})

            projection, prob, edg = compute_edge(line, stat)

            if prob >= MIN_PROB and edg >= MIN_EDGE:
                picks.append({
                    "player": pinfo["name"],
                    "team": pinfo["team"],
                    "stat": stat,
                    "line": line,
                    "proj": projection,
                    "prob": prob,
                    "edge": edg,
                    "kelly": kelly(prob),
                    "grade": grade(edg),
                    "pick": "OVER",
                    "source": "PrizePicks",
                    "sport": sport.upper(),
                })

    picks.sort(key=lambda x: x["edge"], reverse=True)
    result = picks[:20]
    cache.set("prizepicks", result)
    return result


async def fetch_kalshi():
    cached = cache.get("kalshi")
    if cached:
        return cached

    picks = []
    keywords = [
        "points", "assists", "rebounds", "goals", "shots",
        "strikeouts", "hits", "yards", "touchdowns", "bases",
        "steals", "blocks", "runs", "saves", "aces", "corners"
    ]

    endpoints = [
        "https://api.elections.kalshi.com/trade-api/v2",
        "https://trading-api.kalshi.com/trade-api/v2",
    ]

    tickers = ["NBA", "NFL", "MLB", "NHL", "SOCCER", "UFC", "GOLF", "TEN", "EPL"]

    async with aiohttp.ClientSession() as session:
        for base in endpoints:
            for ticker in tickers:
                data = await fetch_json(
                    session,
                    base + "/markets",
                    {"limit": 200, "status": "open", "series_ticker": ticker},
                )

                if not data:
                    continue

                for market in data.get("markets", []):
                    title = market.get("title", "")
                    subtitle = market.get("subtitle", "")
                    combined = (title + " " + subtitle).lower()

                    if not any(k in combined for k in keywords):
                        continue

                    line = 0.0
                    for w in title.replace("+", " ").replace(",", "").split():
                        try:
                            val = float(w)
                            if 0.5 <= val <= 500:
                                line = val
                                break
                        except Exception:
                            continue

                    if line <= 0:
                        continue

                    stat = subtitle if subtitle else title[:30]
                    projection, prob, edg = compute_edge(line, stat)

                    if prob >= MIN_PROB and edg >= MIN_EDGE:
                        picks.append({
                            "player": title[:45],
                            "team": "",
                            "stat": stat[:30],
                            "line": line,
                            "proj": projection,
                            "prob": prob,
                            "edge": edg,
                            "kelly": kelly(prob),
                            "grade": grade(edg),
                            "pick": "OVER",
                            "source": "Kalshi",
                            "sport": ticker,
                        })

    picks.sort(key=lambda x: x["edge"], reverse=True)
    result = picks[:20]
    cache.set("kalshi", result)
    return result


# -----------------------------
# MONTE CARLO
# -----------------------------

def gauss(mu, sigma):
    u1 = max(random.random(), 1e-10)
    u2 = random.random()
    z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
    return mu + sigma * z


def simulate_matchup(team_a, team_b, sport):
    scale = {
        "NBA": 112.0,
        "NFL": 23.0,
        "NHL": 3.0,
        "MLB": 4.5,
        "EPL": 1.4,
    }.get(sport, 50.0)

    sigma = {
        "NBA": 11.0,
        "NFL": 9.5,
        "NHL": 1.4,
        "MLB": 2.8,
        "EPL": 1.2,
    }.get(sport, 8.0)

    wins_a = 0

    for _ in range(NUM_SIMS):
        sa = gauss(scale * 1.03, sigma)
        sb = gauss(scale * 1.00, sigma)
        if sa > sb:
            wins_a += 1

    win_a = wins_a / NUM_SIMS
    win_b = 1 - win_a

    return {
        "team_a": team_a,
        "team_b": team_b,
        "sport": sport,
        "win_a": round(win_a, 4),
        "win_b": round(win_b, 4),
    }


# -----------------------------
# AGGREGATION
# -----------------------------

async def get_all_picks():
    pp, kl = await asyncio.gather(fetch_prizepicks(), fetch_kalshi())
    combined = pp + kl
    combined.sort(key=lambda x: x["edge"], reverse=True)

    seen = set()
    unique = []

    for p in combined:
        key = p["player"][:20] + p["stat"] + str(p["line"])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique[:25]


# -----------------------------
# FORMATTERS
# -----------------------------

def fmt(picks, label):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    msg = "[ PROPNINJA ] " + label + "\n"
    msg += ts + "\n\n"

    for i, p in enumerate(picks[:10], 1):
        team = " (" + p["team"] + ")" if p["team"] else ""
        msg += f"{i}. [{p['grade']}] {p['player']}{team}\n"
        msg += f"   {p['sport']} | {p['stat']}\n"
        msg += f"   Line: {p['line']}  Proj: {p['proj']}\n"
        msg += f"   {p['pick']} | {round(p['prob']*100,1)}% | +{round(p['edge']*100,1)}% edge\n\n"

    msg += "Entertainment only."
    return msg


# -----------------------------
# TELEGRAM
# -----------------------------

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ALL PICKS", callback_data="all")],
        [InlineKeyboardButton("Sim NBA", callback_data="sim_NBA")],
        [InlineKeyboardButton("How It Works", callback_data="howto")],
    ])


def nav(cb):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Refresh", callback_data=cb),
         InlineKeyboardButton("Menu", callback_data="menu")]
    ])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "[ PROPNINJA MASTER ]\nSelect an option:",
        reply_markup=menu()
    )


async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data

    if d == "menu":
        await q.edit_message_text("Select:", reply_markup=menu())
        return

    if d == "all":
        await q.edit_message_text("Fetching...")
        picks = await get_all_picks()
        await q.edit_message_text(fmt(picks, "ALL PICKS")[:4096], reply_markup=nav("all"))
        return

    if d.startswith("sim_"):
        sport = d.split("_")[1]
        r = simulate_matchup("Team A", "Team B", sport)
        msg = f"{sport} Simulation\n\nTeam A: {r['win_a']*100}%\nTeam B: {r['win_b']*100}%"
        await q.edit_message_text(msg, reply_markup=nav(d))
        return

    if d == "howto":
        msg = (
            "Quantum Boosted Probability\n"
            "Monte Carlo 10,000 sims\n"
            "Kelly 0.25x fractional\n"
            "Edge = prob - (1/odds)"
        )
        await q.edit_message_text(msg, reply_markup=nav("menu"))


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    logger.info("PropNinja Master running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()