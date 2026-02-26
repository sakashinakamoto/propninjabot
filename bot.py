import os
import math
import logging
import random
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
DECIMAL_ODDS = 1.90
MIN_PROB = 0.60
MIN_EDGE = 0.05
NUM_SIMS = 10000

SPORT_EMOJI = {
    'NBA': 'BBALL', 'NFL': 'FTBALL', 'MLB': 'BSBALL',
    'NHL': 'HOCKEY', 'EPL': 'SOCCER', 'UFC': 'MMA',
    'GOLF': 'GOLF', 'TENNIS': 'TENNIS', 'SOCCER': 'SOCCER',
}

SPORT_STD = {
    'NBA': 11.0, 'NFL': 9.5, 'NHL': 1.4,
    'MLB': 2.8, 'EPL': 1.2, 'DEFAULT': 8.0,
}

SPORT_SCALE = {
    'NBA': 112.0, 'NFL': 23.0, 'NHL': 3.0,
    'MLB': 4.5, 'EPL': 1.4, 'DEFAULT': 50.0,
}

# --- Probability / Simulation Functions ---

def quantum_boost(prob):
    amplitude = math.sqrt(max(prob, 0.01))
    interference = 0.04 * math.sin(math.pi * amplitude)
    boosted = min((amplitude + interference) ** 2, 0.99)
    return round(boosted, 4)

def gauss(mu, sigma):
    u1 = max(random.random(), 1e-10)
    u2 = random.random()
    z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
    return mu + sigma * z

def compute_edge(line, stat):
    if line <= 0:
        return 0, 0, 0
    boost = 0.055
    s = stat.lower()
    if 'assist' in s:      boost += 0.010
    elif 'point' in s:     boost += 0.008
    elif 'rebound' in s:   boost -= 0.005
    elif 'goal' in s:      boost += 0.007
    elif 'shot' in s:      boost += 0.006
    elif 'strikeout' in s: boost += 0.009
    elif 'hit' in s:       boost += 0.005
    elif 'yard' in s:      boost += 0.006
    elif 'touchdown' in s: boost += 0.007
    elif 'base' in s:      boost += 0.004
    elif 'steal' in s:     boost += 0.008
    elif 'block' in s:     boost += 0.003
    elif 'save' in s:      boost += 0.006
    elif 'corner' in s:    boost += 0.005
    elif 'run' in s:       boost += 0.005
    elif 'ace' in s:       boost += 0.006
    projection = line * (1 + boost)
    std_dev = line * 0.18
    z = (projection - line) / std_dev
    prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    prob = quantum_boost(prob)
    edge = prob - (1 / DECIMAL_ODDS)
    return round(projection, 2), round(prob, 4), round(edge, 4)

def grade(edge):
    if edge >= 0.14: return 'A+'
    if edge >= 0.11: return 'A'
    if edge >= 0.08: return 'B'
    if edge >= 0.05: return 'C'
    return 'D'

def kelly(prob, odds=1.90):
    b = odds - 1
    k = (prob * b - (1 - prob)) / b if b > 0 else 0
    return round(max(k, 0) * 0.25, 4)

def simulate_matchup(team_a, team_b, sport, odds_a=1.90, odds_b=1.90):
    scale = SPORT_SCALE.get(sport, SPORT_SCALE['DEFAULT'])
    sigma = SPORT_STD.get(sport, SPORT_STD['DEFAULT'])
    base_a = scale * 1.03
    base_b = scale * 1.00
    wins_a = 0
    scores_a = []
    scores_b = []
    for _ in range(NUM_SIMS):
        sa = gauss(base_a, sigma)
        sb = gauss(base_b, sigma)
        scores_a.append(sa)
        scores_b.append(sb)
        if sa > sb:
            wins_a += 1
    win_a = wins_a / NUM_SIMS
    win_b = 1.0 - win_a
    proj_a = sum(scores_a) / NUM_SIMS
    proj_b = sum(scores_b) / NUM_SIMS
    diffs = [a - b for a, b in zip(scores_a, scores_b)]
    spread = sum(diffs) / NUM_SIMS
    variance = sum((d - spread) ** 2 for d in diffs) / NUM_SIMS
    spread_std = math.sqrt(variance)
    sorted_a = sorted(scores_a)
    sorted_b = sorted(scores_b)
    ci_a = [sorted_a[int(0.05 * NUM_SIMS)], sorted_a[int(0.95 * NUM_SIMS)]]
    ci_b = [sorted_b[int(0.05 * NUM_SIMS)], sorted_b[int(0.95 * NUM_SIMS)]]
    imp_a = 1.0 / odds_a
    imp_b = 1.0 / odds_b
    ev_a = (win_a * (odds_a - 1) * 100) - ((1 - win_a) * 100)
    ev_b = (win_b * (odds_b - 1) * 100) - ((1 - win_b) * 100)
    b_a = odds_a - 1
    b_b = odds_b - 1
    k_a = round(0.25 * max(0, (win_a * b_a - (1 - win_a)) / b_a), 4) if b_a > 0 else 0
    k_b = round(0.25 * max(0, (win_b * b_b - (1 - win_b)) / b_b), 4) if b_b > 0 else 0
    if ev_a > 0 and ev_a >= ev_b:
        ev_pick = team_a
        kelly_stake = round(k_a * 100, 2)
    elif ev_b > 0:
        ev_pick = team_b
        kelly_stake = round(k_b * 100, 2)
    else:
        ev_pick = 'No +EV'
        kelly_stake = 0.0
    return {
        'team_a': team_a, 'team_b': team_b, 'sport': sport,
        'win_a': round(win_a, 4), 'win_b': round(win_b, 4),
        'proj_a': round(proj_a, 1), 'proj_b': round(proj_b, 1),
        'spread': round(spread, 2), 'spread_std': round(spread_std, 2),
        'ci_a': [round(ci_a[0], 1), round(ci_a[1], 1)],
        'ci_b': [round(ci_b[0], 1), round(ci_b[1], 1)],
        'ev_a': round(ev_a, 2), 'ev_b': round(ev_b, 2),
        'ev_pick': ev_pick, 'kelly_stake': kelly_stake,
        'imp_a': round(imp_a, 4), 'imp_b': round(imp_b, 4),
        'edge_a': round(win_a - imp_a, 4),
        'edge_b': round(win_b - imp_b, 4),
    }

def abbr(team):
    return ''.join([w[0] for w in team.split()]).upper()[:4]

def fmt_sim(r):
    a = abbr(r['team_a'])
    b = abbr(r['team_b'])
    msg  = '[ PROPNINJA SIMULATION ]\n'
    msg += r['sport'] + ' | ' + str(NUM_SIMS) + ' Monte Carlo runs\n'
    msg += r['team_a'] + ' vs ' + r['team_b'] + '\n\n'
    msg += 'WIN PROBABILITY\n'
    msg += a + ': ' + str(round(r['win_a'] * 100, 1)) + '%'
    msg += ' (mkt: ' + str(round(r['imp_a'] * 100, 1)) + '%)\n'
    msg += b + ': ' + str(round(r['win_b'] * 100, 1)) + '%'
    msg += ' (mkt: ' + str(round(r['imp_b'] * 100, 1)) + '%)\n\n'
    msg += 'PROJECTED SCORE\n'
    msg += a + ' ' + str(r['proj_a']) + ' - ' + b + ' ' + str(r['proj_b']) + '\n'
    msg += 'Spread: ' + str(r['spread']) + ' +/- ' + str(r['spread_std']) + '\n\n'
    msg += '90% CONFIDENCE INTERVALS\n'
    msg += a + ': ' + str(r['ci_a'][0]) + ' - ' + str(r['ci_a'][1]) + '\n'
    msg += b + ': ' + str(r['ci_b'][0]) + ' - ' + str(r['ci_b'][1]) + '\n\n'
    msg += 'EV ANALYSIS\n'
    msg += '+EV Pick: ' + str(r['ev_pick']) + '\n'
    msg += 'Kelly Stake: $' + str(r['kelly_stake']) + ' per $100\n'
    msg += 'EV ' + a + ': $' + str(r['ev_a'])
    msg += ' | Edge: +' + str(round(r['edge_a'] * 100, 1)) + '%\n'
    msg += 'EV ' + b + ': $' + str(r['ev_b'])
    msg += ' | Edge: +' + str(round(r['edge_b'] * 100, 1)) + '%'
    return msg

# --- API Fetching functions (PrizePicks, Kalshi, ESPN) ---
# ... omitted here for brevity, same as in your code, just replace quotes

# --- Menu and Formatting functions ---
# ... also same, with straight quotes and proper indentation

# --- Telegram Handlers ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg  = '[ PROPNINJA MASTER ]\n'
    msg += 'Premium Sports Analytics Bot\n'
    msg += '––––––––––––––\n'
    msg += 'Live picks: PrizePicks + Kalshi\n'
    msg += 'Monte Carlo: 10,000 simulations\n'
    msg += 'Quantum amplitude probability\n'
    msg += 'Kelly EV: 0.25x fractional\n'
    msg += 'NBA | NFL | MLB | NHL | EPL | UFC\n'
    msg += '––––––––––––––\n'
    msg += 'Select an option below:'
    await update.message.reply_text(msg, reply_markup=menu())

# --- Add picks_cmd, top_cmd, button with proper indentation ---

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError('TELEGRAM_TOKEN missing!')
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('picks', picks_cmd))
    app.add_handler(CommandHandler('top', top_cmd))
    app.add_handler(CallbackQueryHandler(button))
    logger.info('PropNinja Bot is running')
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()