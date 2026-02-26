# bot.py

import os
import math
import logging
import requests
import random
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DECIMAL_ODDS = 1.90
MIN_EDGE = 0.05
NUM_SIMULATIONS = 10000


# ═══════════════════════════════════════════════════════
# SPORT WEIGHTS
# ═══════════════════════════════════════════════════════

SPORT_WEIGHTS = {
    "NBA": {
        "off_rating": 0.35, "def_rating": 0.35, "pace": 0.05,
        "rest": 0.05, "travel": 0.03, "home": 0.04,
        "injury": 0.08, "historical": 0.05,
    },
    "NFL": {
        "dvoa": 0.30, "epa": 0.25, "success": 0.15,
        "rest": 0.08, "travel": 0.04, "home": 0.05,
        "injury": 0.08, "historical": 0.05,
    },
    "NHL": {
        "off_rating": 0.32, "def_rating": 0.32, "pace": 0.06,
        "rest": 0.07, "travel": 0.05, "home": 0.05,
        "injury": 0.08, "historical": 0.05,
    },
    "MLB": {
        "off_rating": 0.28, "def_rating": 0.28, "pace": 0.04,
        "rest": 0.10, "travel": 0.06, "home": 0.06,
        "injury": 0.10, "historical": 0.08,
    },
    "DEFAULT": {
        "off_rating": 0.33, "def_rating": 0.33, "pace": 0.05,
        "rest": 0.06, "travel": 0.04, "home": 0.05,
        "injury": 0.08, "historical": 0.06,
    },
}

SPORT_STD = {
    "NBA": 11.0,
    "NFL": 9.5,
    "NHL": 1.4,
    "MLB": 2.8,
    "EPL": 1.2,
    "DEFAULT": 8.0,
}

SPORT_SCALE = {
    "NBA": 112.0,
    "NFL": 23.0,
    "NHL": 3.0,
    "MLB": 4.5,
    "EPL": 1.4,
    "DEFAULT": 50.0,
}


# ═══════════════════════════════════════════════════════
# MATCHUP ENGINE
# ═══════════════════════════════════════════════════════

class Matchup:
    def __init__(self, team_a, team_b, sport, league):
        self.team_a = team_a
        self.team_b = team_b
        self.sport = sport.upper()
        self.league = league
        self.metrics_a = {}
        self.metrics_b = {}
        self.situational_a = {}
        self.situational_b = {}
        self.injury_a = {}
        self.injury_b = {}
        self.historical = {}
        self.market_odds = {}


def compute_baseline(metrics, situational, injury, historical, weights, scale):
    raw = 0.0
    for k, v in metrics.items():
        raw += weights.get(k, 0) * v
    for k, v in situational.items():
        raw += weights.get(k, 0) * v
    for k, v in injury.items():
        raw += weights.get(k, 0) * v

    raw += weights.get("historical", 0) * historical.get("adjusted_point_diff", 0)
    baseline = scale * (1.0 + raw * 0.12)
    return max(baseline, scale * 0.5)


def gauss_pair(mu_a, mu_b, sigma, corr=0.15):
    z1 = sum(random.random() for _ in range(12)) - 6.0
    z2 = sum(random.random() for _ in range(12)) - 6.0
    z2_corr = corr * z1 + math.sqrt(1 - corr ** 2) * z2
    return mu_a + sigma * z1, mu_b + sigma * z2_corr


def monte_carlo(baseline_a, baseline_b, sport, n=NUM_SIMULATIONS):
    sigma = SPORT_STD.get(sport, SPORT_STD["DEFAULT"])
    scores_a, scores_b = [], []

    for _ in range(n):
        sa, sb = gauss_pair(baseline_a, baseline_b, sigma)
        scores_a.append(sa)
        scores_b.append(sb)

    wins_a = sum(1 for a, b in zip(scores_a, scores_b) if a > b)
    win_prob_a = wins_a / n
    win_prob_b = 1.0 - win_prob_a

    proj_a = sum(scores_a) / n
    proj_b = sum(scores_b) / n

    diffs = [a - b for a, b in zip(scores_a, scores_b)]
    proj_spread = sum(diffs) / n
    spread_std = math.sqrt(sum((d - proj_spread) ** 2 for d in diffs) / n)

    sorted_a = sorted(scores_a)
    sorted_b = sorted(scores_b)

    ci_a = [sorted_a[int(0.05 * n)], sorted_a[int(0.95 * n)]]
    ci_b = [sorted_b[int(0.05 * n)], sorted_b[int(0.95 * n)]]

    mid = n // 2
    med_a = sorted_a[mid]
    med_b = sorted_b[mid]

    sample_idx = [int(i * n / 20) for i in range(20)]
    dist_sample = [(round(scores_a[i], 1), round(scores_b[i], 1)) for i in sample_idx]

    return {
        "TeamA_Score_Mean": round(proj_a, 2),
        "TeamB_Score_Mean": round(proj_b, 2),
        "TeamA_Score_Median": round(med_a, 2),
        "TeamB_Score_Median": round(med_b, 2),
        "TeamA_WinProb": round(win_prob_a, 4),
        "TeamB_WinProb": round(win_prob_b, 4),
        "Projected_Spread": round(proj_spread, 2),
        "Spread_StdDev": round(spread_std, 2),
        "ConfidenceIntervals": {
            "TeamA_Score": [round(ci_a[0], 1), round(ci_a[1], 1)],
            "TeamB_Score": [round(ci_b[0], 1), round(ci_b[1], 1)],
        },
        "Simulation_Distribution": dist_sample,
        "Simulations": n,
    }


def implied_prob(decimal_odds):
    return round(1.0 / decimal_odds, 4) if decimal_odds > 0 else 0.5


def compute_ev(win_prob, market_odds, stake=100):
    profit = (market_odds - 1) * stake
    ev = (win_prob * profit) - ((1 - win_prob) * stake)
    b = market_odds - 1
    kelly_raw = (win_prob * b - (1 - win_prob)) / b if b > 0 else 0
    kelly_quarter = 0.25 * max(0, kelly_raw)
    risk_adj_ev = ev * kelly_quarter
    return round(ev, 2), round(kelly_quarter * stake, 2), round(risk_adj_ev, 2)


# ═══════════════════════════════════════════════════════
# TELEGRAM BOT
# ═══════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "PropNinja Bot\n"
        "Live picks + Monte Carlo simulations\n\n"
        "Use /picks or /top",
    )


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN missing!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    logger.info("PropNinja Bot is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
