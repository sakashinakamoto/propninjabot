HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://app.prizepicks.com/",
    "Origin": "https://app.prizepicks.com"
}
# ==========================================
# TEAM ABBREVIATIONS (NBA example)
# ==========================================

TEAM_ABBR = {
    "Los Angeles Lakers": "LAL",
    "Los Angeles Clippers": "LAC",
    "Golden State Warriors": "GSW",
    "Boston Celtics": "BOS",
    "Milwaukee Bucks": "MIL",
    "Phoenix Suns": "PHX",
    "Denver Nuggets": "DEN",
    "Miami Heat": "MIA",
    "Dallas Mavericks": "DAL",
    "Philadelphia 76ers": "PHI",
    "New York Knicks": "NYK",
    "Chicago Bulls": "CHI",
    "Brooklyn Nets": "BKN",
    "Cleveland Cavaliers": "CLE",
    "Atlanta Hawks": "ATL",
    # Add more as needed
}
#!/usr/bin/env python3

import os
import logging
import sqlite3
import threading
import numpy as np
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List
import requests
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO, format=’%(asctime)s [%(levelname)s] %(name)s - %(message)s’)
log = logging.getLogger(‘propninjabot’)

TELEGRAM_TOKEN = os.environ.get(‘TELEGRAM_TOKEN’, ‘’)
ODDS_API_KEY   = os.environ.get(‘ODDS_API_KEY’, ‘’)
ADMIN_CHAT_ID  = int(os.environ.get(‘ADMIN_CHAT_ID’, ‘0’))
PORT           = int(os.environ.get(‘PORT’, ‘8080’))
DB_PATH        = os.environ.get(‘DB_PATH’, ‘propninjabot.db’)

VALID_SPORTS = [‘NBA’, ‘NFL’, ‘MLB’, ‘NHL’, ‘NCAAB’, ‘NCAAF’, ‘EPL’]

SPORT_KEYS: Dict[str, str] = {
‘NBA’:   ‘basketball_nba’,
‘NFL’:   ‘americanfootball_nfl’,
‘MLB’:   ‘baseball_mlb’,
‘NHL’:   ‘icehockey_nhl’,
‘NCAAB’: ‘basketball_ncaab’,
‘NCAAF’: ‘americanfootball_ncaaf’,
‘EPL’:   ‘soccer_epl’,
}

SPORT_STD:  Dict[str, float] = {‘NBA’: 12.0, ‘NFL’: 10.0, ‘MLB’: 3.5, ‘NHL’: 2.5, ‘NCAAB’: 14.0, ‘NCAAF’: 10.0}
SPORT_BASE: Dict[str, float] = {‘NBA’: 115.0, ‘NFL’: 23.0, ‘MLB’: 4.5, ‘NHL’: 3.0, ‘NCAAB’: 72.0, ‘NCAAF’: 23.0}

# ─────────────────────────────────────────────────────────────────────────────

# DATABASE

# ─────────────────────────────────────────────────────────────────────────────

def _db_connect() -> sqlite3.Connection:
db_dir = os.path.dirname(DB_PATH)
if db_dir:
os.makedirs(db_dir, exist_ok=True)
return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db() -> None:
conn = _db_connect()
c = conn.cursor()
c.execute(
’CREATE TABLE IF NOT EXISTS users ’
‘(chat_id INTEGER PRIMARY KEY, username TEXT, joined TEXT)’
)
c.execute(
’CREATE TABLE IF NOT EXISTS picks_log ’
’(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, ’
‘sport TEXT, matchup TEXT, ev_bet TEXT, win_prob REAL, kelly REAL, timestamp TEXT)’
)
conn.commit()
conn.close()
log.info(‘DB ready at %s’, DB_PATH)

def upsert_user(chat_id: int, username: str) -> None:
conn = _db_connect()
c = conn.cursor()
c.execute(
‘INSERT OR IGNORE INTO users (chat_id, username, joined) VALUES (?, ?, ?)’,
(chat_id, username, datetime.utcnow().isoformat())
)
conn.commit()
conn.close()

def log_pick(chat_id: int, sport: str, matchup: str, ev_bet: str, win_prob: float, kelly: float) -> None:
conn = _db_connect()
c = conn.cursor()
c.execute(
’INSERT INTO picks_log (chat_id, sport, matchup, ev_bet, win_prob, kelly, timestamp) ’
‘VALUES (?, ?, ?, ?, ?, ?, ?)’,
(chat_id, sport, matchup, ev_bet, win_prob, kelly, datetime.utcnow().isoformat())
)
conn.commit()
conn.close()

# ─────────────────────────────────────────────────────────────────────────────

# QUANT ENGINE

# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MatchupInput:
team_a:        str
team_b:        str
sport:         str
baseline_diff: float
market_odds_a: float
market_odds_b: float
source:        str
game_time:     str

def american_to_decimal(odds: float) -> float:
if odds > 0:
return 1.0 + (odds / 100.0)
return 1.0 + (100.0 / abs(odds))

def decimal_to_american(dec: float) -> str:
if dec >= 2.0:
return ‘+’ + str(int(round((dec - 1.0) * 100)))
return str(int(round(-100.0 / (dec - 1.0))))

def remove_vig(p_a: float, p_b: float) -> Tuple[float, float]:
total = p_a + p_b
return p_a / total, p_b / total

def fractional_kelly(win_prob: float, dec_odds: float, fraction: float = 0.25) -> float:
b = dec_odds - 1.0
if b <= 0:
return 0.0
return max(((b * win_prob - (1.0 - win_prob)) / b) * fraction, 0.0)

def classify_risk(win_prob: float, spread: float) -> str:
if abs(spread) > 6.5:
return ‘HIGH’
if win_prob < 52.0:
return ‘HIGH’
if win_prob < 58.0:
return ‘MED’
return ‘LOW’

def run_quant(m: MatchupInput, sims: int = 20000) -> dict:
std    = SPORT_STD.get(m.sport, 10.0)
base   = SPORT_BASE.get(m.sport, 100.0)
mean_a = base + m.baseline_diff / 2.0
mean_b = base - m.baseline_diff / 2.0
cov    = [[std**2, 0.15*std**2], [0.15*std**2, std**2]]
sc     = np.random.multivariate_normal([mean_a, mean_b], cov, sims)
sa     = sc[:, 0]
sb     = sc[:, 1]

```
wp_a   = float(np.mean(sa > sb))
wp_b   = 1.0 - wp_a
spread = float(np.mean(sa - sb))
dec_a  = american_to_decimal(m.market_odds_a)
dec_b  = american_to_decimal(m.market_odds_b)
ev_a   = wp_a * (dec_a - 1.0) - wp_b
ev_b   = wp_b * (dec_b - 1.0) - wp_a

if ev_a > 0 and ev_a >= ev_b:
    ev_bet = m.team_a
    kelly  = fractional_kelly(wp_a, dec_a)
    edge   = ev_a
    wp     = wp_a
    dec    = dec_a
elif ev_b > 0:
    ev_bet = m.team_b
    kelly  = fractional_kelly(wp_b, dec_b)
    edge   = ev_b
    wp     = wp_b
    dec    = dec_b
else:
    ev_bet = 'No Edge'
    kelly  = 0.0
    edge   = 0.0
    wp     = max(wp_a, wp_b)
    dec    = dec_a if wp_a >= wp_b else dec_b

risk  = classify_risk(wp * 100, spread)
stars = min(5, max(1, int(edge * 100 / 3.0)))

return {
    'team_a':    m.team_a,
    'team_b':    m.team_b,
    'sport':     m.sport,
    'source':    m.source,
    'game_time': m.game_time,
    'ev_bet':    ev_bet,
    'wp_a':      round(wp_a * 100, 1),
    'wp_b':      round(wp_b * 100, 1),
    'spread':    round(spread, 1),
    'kelly':     round(kelly * 100, 2),
    'edge':      round(edge * 100, 2),
    'stars':     stars,
    'win_prob':  round(wp * 100, 1),
    'ev_dec':    dec,
    'has_edge':  ev_bet != 'No Edge',
    'risk':      risk,
}
```

# ─────────────────────────────────────────────────────────────────────────────

# PARLAY ENGINE

# ─────────────────────────────────────────────────────────────────────────────

def build_optimal_parlay(picks: List[dict], max_legs: int = 5) -> dict:
ev_picks = [p for p in picks if p.get(‘has_edge’) and p.get(‘risk’) != ‘HIGH’]
ev_picks.sort(key=lambda x: x.get(‘edge’, 0) + x.get(‘win_prob’, 0) * 0.3, reverse=True)
legs = ev_picks[:max_legs]
if not legs:
legs = sorted(picks, key=lambda x: x.get(‘win_prob’, 0), reverse=True)[:max_legs]
if not legs:
return {‘error’: ‘No picks available’}

```
sport_count: Dict[str, int] = {}
for l in legs:
    s = l.get('sport', 'X')
    sport_count[s] = sport_count.get(s, 0) + 1

combined   = 1.0
parlay_dec = 1.0
for l in legs:
    wp = l['win_prob'] / 100.0
    if sport_count.get(l.get('sport', 'X'), 1) > 1:
        wp *= 0.97
    combined   *= wp
    parlay_dec *= l.get('ev_dec', 1.9)

b         = parlay_dec - 1.0
ev        = combined * b - (1.0 - combined)
kelly_raw = (combined * b - (1.0 - combined)) / b if b > 0 else 0.0
kelly     = max(kelly_raw * 0.25, 0.0)
high_risk = [l for l in legs if l.get('risk') == 'HIGH']
grade     = ('A' if len(high_risk) == 0 and ev > 0 else
             'B' if len(high_risk) <= 1 and ev > 0 else
             'C' if len(high_risk) <= 2 else 'D')

warnings = []
if high_risk:
    warnings.append('Kill legs: ' + ', '.join(l['ev_bet'] for l in high_risk))
if len(legs) > 6:
    warnings.append('7+ leg parlay: under 2% hit rate historically')
if combined < 0.05:
    warnings.append('Combined prob under 5%')

return {
    'legs':        legs,
    'combined':    round(combined * 100, 2),
    'parlay_odds': decimal_to_american(parlay_dec),
    'ev':          round(ev * 100, 2),
    'kelly':       round(kelly * 100, 2),
    'grade':       grade,
    'kill_legs':   len(high_risk),
    'warnings':    warnings,
}
```

# ─────────────────────────────────────────────────────────────────────────────

# ODDS API

# ─────────────────────────────────────────────────────────────────────────────

def fetch_odds(sport: str) -> List[MatchupInput]:
key = SPORT_KEYS.get(sport)
if not key or not ODDS_API_KEY:
return []
url    = ‘https://api.the-odds-api.com/v4/sports/’ + key + ‘/odds/’
params = {
‘apiKey’: ODDS_API_KEY, ‘regions’: ‘us’,
‘markets’: ‘h2h’, ‘oddsFormat’: ‘american’, ‘dateFormat’: ‘iso’,
}
try:
resp = requests.get(url, params=params, timeout=10)
if resp.status_code != 200:
return []
results = []
for game in resp.json()[:20]:
try:
home      = game[‘home_team’]
away      = game[‘away_team’]
game_time = game.get(‘commence_time’, ‘’)[:16].replace(‘T’, ’ ’)
bks       = game.get(‘bookmakers’, [])
if not bks:
continue
best_a, best_b, book = None, None, ‘Sportsbook’
for bk in bks:
try:
om = {o[‘name’]: float(o[‘price’]) for o in bk[‘markets’][0][‘outcomes’]}
oa = om.get(home)
ob = om.get(away)
if oa and ob:
if best_a is None or oa > best_a:
best_a = oa
book   = bk.get(‘title’, ‘Sportsbook’)
if best_b is None or ob > best_b:
best_b = ob
except Exception:
continue
if best_a is None or best_b is None:
continue
dec_a     = american_to_decimal(best_a)
dec_b     = american_to_decimal(best_b)
true_a, _ = remove_vig(1.0 / dec_a, 1.0 / dec_b)
bdiff     = (true_a - 0.5) * SPORT_STD.get(sport, 10.0) * 1.2
results.append(MatchupInput(
team_a=home, team_b=away, sport=sport,
baseline_diff=bdiff, market_odds_a=best_a, market_odds_b=best_b,
source=book, game_time=game_time,
))
except Exception:
continue
return results
except Exception as exc:
log.error(‘Odds API: %s’, exc)
return []

# ─────────────────────────────────────────────────────────────────────────────

# MESSAGE FORMATTERS

# ─────────────────────────────────────────────────────────────────────────────

def format_pick(r: dict, sport: str) -> str:
stars = ‘*’ * min(5, max(1, r[‘stars’]))
risk  = r.get(‘risk’, ‘MED’)
risk_note = ’ – PARLAY KILL RISK’ if risk == ‘HIGH’ else ‘’
lines = [
‘––––––––––––––––––––’,
’PROPNINJABOT | ’ + sport + ’ | ’ + r.get(‘game_time’, ‘’),
‘––––––––––––––––––––’,
’HOME : ’ + r[‘team_a’],
‘AWAY : ’ + r[‘team_b’],
‘’,
‘WIN PROBABILITY’,
’  ’ + r[‘team_a’] + ’ : ’ + str(r[‘wp_a’]) + ‘%’,
’  ’ + r[‘team_b’] + ’ : ’ + str(r[‘wp_b’]) + ‘%’,
‘’,
‘SPREAD   : ’ + (’+’ if r[‘spread’] > 0 else ‘’) + str(r[‘spread’]),
’+EV PICK : ’ + r[‘ev_bet’],
’EDGE     : ’ + str(r[‘edge’]) + ’%  ’ + stars,
’KELLY    : ’ + str(r[‘kelly’]) + ‘% of bankroll’,
’RISK     : ’ + risk + risk_note,
’BOOK     : ’ + r.get(‘source’, ‘N/A’),
‘––––––––––––––––––––’,
‘Sims: 20,000 | Past results do not guarantee future outcomes.’,
]
return ‘\n’.join(lines)

def format_parlay(p: dict) -> str:
if p.get(‘error’):
return ’No parlay available: ’ + p[‘error’]

```
legs_text = ''
for i, l in enumerate(p['legs']):
    legs_text += str(i + 1) + '. ' + l['ev_bet'] + ' (' + l['sport'] + ')' + \
                 ' | ' + str(l['win_prob']) + '% | RISK: ' + l['risk'] + '\n'

warn_text = ''
for w in p.get('warnings', []):
    warn_text += 'WARN: ' + w + '\n'

lines = [
    '========================================',
    'PROPNINJABOT | OPTIMAL PARLAY',
    '========================================',
    legs_text.rstrip(),
    '',
    'GRADE       : ' + p['grade'],
    'HIT PROB    : ' + str(p['combined']) + '%',
    'PARLAY ODDS : ' + p['parlay_odds'],
    'EV          : ' + str(p['ev']) + '%',
    'KELLY STAKE : ' + str(p['kelly']) + '% of bankroll',
    'KILL LEGS   : ' + str(p['kill_legs']),
]
if warn_text:
    lines.append('')
    lines.append(warn_text.rstrip())
lines += [
    '========================================',
    'Lesson from past slips:',
    '- Props >= 20 pts for non-elite scorers = HIGH RISK',
    '- Underdogs covering > 6.5 pts = HIGH RISK',
    '- Trust grade A/B parlays only for real money',
    '========================================',
]
return '\n'.join(lines)
```

# ─────────────────────────────────────────────────────────────────────────────

# HANDLERS

# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
chat_id  = update.effective_chat.id
username = update.effective_user.username or ‘unknown’
upsert_user(chat_id, username)
text = (
‘PROPNINJABOT - QuantPicks Elite\n\n’
‘Institutional-grade sports analytics.\n’
‘Monte Carlo, Kelly sizing, parlay builder, risk scoring.\n\n’
‘COMMANDS\n’
‘/pick [SPORT]   - Best +EV pick for a sport\n’
‘/parlay [SPORT] - AI-built optimal parlay\n’
‘/parlay all     - Cross-sport optimal parlay\n’
‘/sports         - All supported sports\n’
‘/help           - All commands\n\n’
‘Sports: NBA NFL MLB NHL NCAAB NCAAF EPL\n’
‘Examples:\n’
’  /pick NBA\n’
’  /parlay NBA\n’
’  /parlay all’
)
await update.message.reply_text(text)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
text = (
‘PROPNINJABOT COMMANDS\n\n’
‘/pick         - NBA pick (default)\n’
‘/pick NBA     - NBA pick\n’
‘/pick NFL     - NFL pick\n’
‘/pick MLB     - MLB pick\n’
‘/pick NHL     - NHL pick\n’
‘/pick NCAAB   - College basketball\n’
‘/pick NCAAF   - College football\n’
‘/pick EPL     - Premier League\n’
‘/parlay [SPORT] - Optimal parlay for sport\n’
‘/parlay all     - Cross-sport parlay\n’
‘/sports         - All supported sports\n’
‘/start          - Welcome\n’
)
await update.message.reply_text(text)

async def cmd_sports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
text = (
‘SUPPORTED SPORTS\n\n’
‘NBA   - Basketball\n’
‘NFL   - American Football\n’
‘MLB   - Baseball\n’
‘NHL   - Hockey\n’
‘NCAAB - College Basketball\n’
‘NCAAF - College Football\n’
‘EPL   - English Premier League\n\n’
‘Usage: /pick NFL  or  /parlay NBA’
)
await update.message.reply_text(text)

async def cmd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
chat_id  = update.effective_chat.id
username = update.effective_user.username or ‘unknown’
upsert_user(chat_id, username)

```
sport = context.args[0].upper() if context.args else 'NBA'
if sport not in VALID_SPORTS:
    await update.message.reply_text(
        'Invalid sport: ' + sport + '\nChoose from: ' + ', '.join(VALID_SPORTS)
    )
    return

status = await update.message.reply_text(
    'Fetching live ' + sport + ' odds and running 20,000 simulations...'
)

games = fetch_odds(sport)

if not games:
    demo = MatchupInput(
        team_a='Home Team', team_b='Away Team', sport=sport,
        baseline_diff=3.5, market_odds_a=-130, market_odds_b=110,
        source='Demo', game_time='N/A',
    )
    r = run_quant(demo)
    r['team_a'] = 'Home Team (Demo)'
    r['team_b'] = 'Away Team (Demo)'
    msg = format_pick(r, sport) + '\n\nDEMO MODE - Set ODDS_API_KEY for live data.'
    await status.delete()
    await update.message.reply_text(msg)
    return

best_result: Optional[dict] = None
best_edge:   float          = -999.0
for game in games:
    r = run_quant(game)
    if r['edge'] > best_edge:
        best_edge   = r['edge']
        best_result = r

await status.delete()

if best_result is None or not best_result['has_edge']:
    await update.message.reply_text(
        'No +EV opportunities found in todays ' + sport + ' slate. Try another sport.'
    )
    return

await update.message.reply_text(format_pick(best_result, sport))
log_pick(
    chat_id, sport,
    best_result['team_a'] + ' vs ' + best_result['team_b'],
    best_result['ev_bet'], best_result['win_prob'], best_result['kelly'],
)
```

async def cmd_parlay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
chat_id  = update.effective_chat.id
username = update.effective_user.username or ‘unknown’
upsert_user(chat_id, username)

```
arg   = context.args[0].upper() if context.args else 'NBA'
cross = arg == 'ALL'

sports_to_fetch = VALID_SPORTS if cross else ([arg] if arg in VALID_SPORTS else ['NBA'])

status = await update.message.reply_text(
    'Building optimal parlay across ' + (', '.join(sports_to_fetch)) + '...'
)

all_picks: List[dict] = []
for sport in sports_to_fetch:
    games = fetch_odds(sport)
    for game in games:
        all_picks.append(run_quant(game))

await status.delete()

if not all_picks:
    demo_picks = []
    for sport in sports_to_fetch[:3]:
        demo = MatchupInput(
            team_a='Home Team', team_b='Away Team', sport=sport,
            baseline_diff=3.5, market_odds_a=-130, market_odds_b=110,
            source='Demo', game_time='N/A',
        )
        r = run_quant(demo)
        r['team_a'] = 'Home (Demo)'
        r['team_b'] = 'Away (Demo)'
        demo_picks.append(r)
    parlay = build_optimal_parlay(demo_picks, max_legs=3)
    msg = format_parlay(parlay) + '\n\nDEMO MODE - Set ODDS_API_KEY for live data.'
    await update.message.reply_text(msg)
    return

parlay_4 = build_optimal_parlay(all_picks, max_legs=4)
parlay_5 = build_optimal_parlay(all_picks, max_legs=5)

await update.message.reply_text(format_parlay(parlay_4))
await update.message.reply_text(format_parlay(parlay_5))
```

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if update.effective_chat.id != ADMIN_CHAT_ID:
await update.message.reply_text(‘Unauthorized.’)
return
conn = _db_connect()
c    = conn.cursor()
c.execute(‘SELECT COUNT(*) FROM users’)
total_users = c.fetchone()[0]
c.execute(’SELECT COUNT(*) FROM picks_log’)
total_picks = c.fetchone()[0]
c.execute(‘SELECT sport, COUNT(*) as cnt FROM picks_log GROUP BY sport ORDER BY cnt DESC’)
rows = c.fetchall()
conn.close()
sport_str = ‘\n’.join(’  ’ + s + ‘: ’ + str(n) for s, n in rows) or ’  None yet’
text = (
‘ADMIN DASHBOARD\n\n’
’Total Users  : ’ + str(total_users) + ‘\n’
’Picks Served : ’ + str(total_picks) + ‘\n\n’
‘Picks by Sport:\n’ + sport_str
)
await update.message.reply_text(text)

# ─────────────────────────────────────────────────────────────────────────────

# FLASK HEALTH

# ─────────────────────────────────────────────────────────────────────────────

flask_app = Flask(‘propninjabot’)

@flask_app.route(’/’)
def index():
return {‘service’: ‘propninjabot’, ‘status’: ‘running’,
‘timestamp’: datetime.utcnow().isoformat()}, 200

@flask_app.route(’/health’)
def health():
return {‘status’: ‘ok’}, 200

def run_flask() -> None:
flask_app.run(host=‘0.0.0.0’, port=PORT, use_reloader=False)

# ─────────────────────────────────────────────────────────────────────────────

# MAIN

# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
if not TELEGRAM_TOKEN:
raise RuntimeError(‘TELEGRAM_TOKEN env var is not set.’)
init_db()
threading.Thread(target=run_flask, daemon=True, name=‘flask’).start()
log.info(‘Flask health server started on port %s’, PORT)
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler(‘start’,  cmd_start))
app.add_handler(CommandHandler(‘help’,   cmd_help))
app.add_handler(CommandHandler(‘sports’, cmd_sports))
app.add_handler(CommandHandler(‘pick’,   cmd_pick))
app.add_handler(CommandHandler(‘parlay’, cmd_parlay))
app.add_handler(CommandHandler(‘admin’,  cmd_admin))
log.info(‘propninjabot polling started.’)
app.run_polling(allowed_updates=Update.ALL_TYPES)

if **name** == ‘**main**’:
main()