# bot.py

import os
import math
import random
import logging
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


# ─────────────────────────────────────
# CONFIG
# ─────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or ''

NUM_SIMULATIONS = 10000
MIN_EDGE = 0.05
DEFAULT_ODDS = 1.90


SPORT_STD = {
    'NBA': 11.0,
    'NFL': 9.5,
    'NHL': 1.4,
    'MLB': 2.8,
    'EPL': 1.2,
    'DEFAULT': 8.0
}

SPORT_BASELINE = {
    'NBA': 112.0,
    'NFL': 23.0,
    'NHL': 3.0,
    'MLB': 4.5,
    'EPL': 1.4,
    'DEFAULT': 50.0
}


# ─────────────────────────────────────
# MONTE CARLO ENGINE
# ─────────────────────────────────────

def gaussian_pair(mu_a, mu_b, sigma, corr=0.15):
    z1 = sum(random.random() for _ in range(12)) - 6.0
    z2 = sum(random.random() for _ in range(12)) - 6.0
    z2_corr = corr * z1 + math.sqrt(1 - corr ** 2) * z2
    return mu_a + sigma * z1, mu_b + sigma * z2_corr


def monte_carlo(mu_a, mu_b, sport, n=NUM_SIMULATIONS):
    sigma = SPORT_STD.get(sport, SPORT_STD['DEFAULT'])

    wins_a = 0
    total_a = 0
    total_b = 0

    for _ in range(n):
        a, b = gaussian_pair(mu_a, mu_b, sigma)
        total_a += a
        total_b += b
        if a > b:
            wins_a += 1

    mean_a = total_a / n
    mean_b = total_b / n
    win_prob = wins_a / n

    return {
        'mean_a': round(mean_a, 2),
        'mean_b': round(mean_b, 2),
        'win_prob_a': round(win_prob, 4),
        'win_prob_b': round(1 - win_prob, 4),
        'simulations': n
    }


# ─────────────────────────────────────
# EDGE + KELLY
# ─────────────────────────────────────

def compute_ev(win_prob, odds, stake=100):
    b = odds - 1
    profit = b * stake
    ev = (win_prob * profit) - ((1 - win_prob) * stake)
    k = (win_prob * b - (1 - win_prob)) / b if b > 0 else 0
    k_fractional = max(k, 0) * 0.25
    return round(ev, 2), round(k_fractional * stake, 2)


def grade_pick(edge):
    if edge >= 0.15:
        return 'A+'
    if edge >= 0.10:
        return 'A'
    if edge >= 0.07:
        return 'B+'
    if edge >= 0.05:
        return 'B'
    return 'No Bet'


# ─────────────────────────────────────
# LIVE ESPN FETCH
# ─────────────────────────────────────

def fetch_espn_scoreboard(sport):
    try:
        url = 'https://site.api.espn.com/apis/site/v2/sports/' + sport.lower() + '/scoreboard'
        r = requests.get(url, timeout=10)
        data = r.json()
        return data
    except Exception as e:
        logger.warning('ESPN fetch failed')
        return None


# ─────────────────────────────────────
# ANALYSIS PIPELINE
# ─────────────────────────────────────

def run_analysis(sport):

    baseline = SPORT_BASELINE.get(sport, SPORT_BASELINE['DEFAULT'])
    baseline_b = baseline * 0.97

    result = monte_carlo(baseline, baseline_b, sport)

    implied_prob = 1 / DEFAULT_ODDS
    edge = result['win_prob_a'] - implied_prob

    ev, kelly = compute_ev(result['win_prob_a'], DEFAULT_ODDS)

    result['edge'] = round(edge, 4)
    result['ev'] = ev
    result['kelly'] = kelly
    result['grade'] = grade_pick(edge)

    return result


# ─────────────────────────────────────
# FORMAT OUTPUT
# ─────────────────────────────────────

def format_output(sport, result):

    msg = ''
    msg += 'PROPNINJA QUANT ENGINE\n'
    msg += sport + ' Analysis\n\n'
    msg += 'Mean A: ' + str(result['mean_a']) + '\n'
    msg += 'Mean B: ' + str(result['mean_b']) + '\n\n'
    msg += 'Win Prob A: ' + str(round(result['win_prob_a'] * 100, 2)) + '%\n'
    msg += 'Edge: ' + str(round(result['edge'] * 100, 2)) + '%\n'
    msg += 'EV per 100: $' + str(result['ev']) + '\n'
    msg += 'Kelly Stake: $' + str(result['kelly']) + '\n'
    msg += 'Grade: ' + result['grade'] + '\n'
    msg += '\nSimulations: ' + str(result['simulations'])

    return msg


# ─────────────────────────────────────
# TELEGRAM MENU
# ─────────────────────────────────────

def build_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('NBA Simulation', callback_data='NBA')],
        [InlineKeyboardButton('NFL Simulation', callback_data='NFL')],
        [InlineKeyboardButton('MLB Simulation', callback_data='MLB')]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'PropNinja Live\nSelect sport:',
        reply_markup=build_menu()
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sport = query.data

    result = run_analysis(sport)
    msg = format_output(sport, result)

    await query.edit_message_text(msg)


# ─────────────────────────────────────
# MAIN
# ─────────────────────────────────────

def main():

    if not TELEGRAM_TOKEN:
        raise ValueError('TELEGRAM_TOKEN missing')

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(handle_button))

    logger.info('Bot started')
    app.run_polling()


if __name__ == '__main__':
    main()
