#!/usr/bin/env python3
‘’’
QuantPicks Elite — Open Access Build (No Subscription)
Deploy on Render as a Web Service. Start command: python bot.py

=== REQUIRED ENV VARS (set in Render dashboard) ===
TELEGRAM_TOKEN  — From @BotFather
ODDS_API_KEY    — From https://the-odds-api.com (free tier: 500 req/mo)
ADMIN_CHAT_ID   — Your personal Telegram chat ID (get from @userinfobot)
PORT            — Auto-set by Render (default 8080)
DB_PATH         — Optional: /data/quantpicks.db (requires Render disk addon)

=== requirements.txt ===
python-telegram-bot>=20.7
numpy
flask
requests
‘’’

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
from telegram.ext import (
Application,
CommandHandler,
ContextTypes,
)

# ─────────────────────────────────────────────────────────────────────────────

# LOGGING

# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
level=logging.INFO,
format=’%(asctime)s [%(levelname)s] %(name)s — %(message)s’,
)
log = logging.getLogger(‘QuantPicks’)

# ─────────────────────────────────────────────────────────────────────────────

# CONFIGURATION

# ─────────────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get(‘TELEGRAM_TOKEN’, ‘’)
ODDS_API_KEY   = os.environ.get(‘ODDS_API_KEY’, ‘’)
ADMIN_CHAT_ID  = int(os.environ.get(‘ADMIN_CHAT_ID’, ‘0’))
PORT           = int(os.environ.get(‘PORT’, ‘8080’))
DB_PATH        = os.environ.get(‘DB_PATH’, ‘quantpicks.db’)

VALID_SPORTS = [‘NBA’, ‘NFL’, ‘MLB’, ‘NHL’, ‘NCAAB’, ‘NCAAF’, ‘EPL’]

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
c.execute(’’’
CREATE TABLE IF NOT EXISTS users (
chat_id  INTEGER PRIMARY KEY,
username TEXT,
joined   TEXT
)
‘’’)
c.execute(’’’
CREATE TABLE IF NOT EXISTS picks_log (
id        INTEGER PRIMARY KEY AUTOINCREMENT,
chat_id   INTEGER,
sport     TEXT,
matchup   TEXT,
ev_bet    TEXT,
win_prob  REAL,
kelly     REAL,
timestamp TEXT
)
‘’’)
conn.commit()
conn.close()
log.info(‘Database ready at %s’, DB_PATH)

def upsert_user(chat_id: int, username: str) -> None:
conn = _db_connect()
c = conn.cursor()
c.execute(’’’
INSERT OR IGNORE INTO users (chat_id, username, joined)
VALUES (?, ?, ?)
‘’’, (chat_id, username, datetime.utcnow().isoformat()))
conn.commit()
conn.close()

def log_pick(
chat_id: int,
sport: str,
matchup: str,
ev_bet: str,
win_prob: float,
kelly: float,
) -> None:
conn = _db_connect()
c = conn.cursor()
c.execute(’’’
INSERT INTO picks_log (chat_id, sport, matchup, ev_bet, win_prob, kelly, timestamp)
VALUES (?, ?, ?, ?, ?, ?, ?)
‘’’, (chat_id, sport, matchup, ev_bet, win_prob, kelly,
datetime.utcnow().isoformat()))
conn.commit()
conn.close()

# ─────────────────────────────────────────────────────────────────────────────

# QUANT ENGINE

# ─────────────────────────────────────────────────────────────────────────────

SPORT_STD:  Dict[str, float] = {‘NBA’: 12.0, ‘NFL’: 10.0, ‘MLB’: 3.5, ‘NHL’: 2.5}
SPORT_BASE: Dict[str, float] = {‘NBA’: 115.0, ‘NFL’: 23.0, ‘MLB’: 4.5, ‘NHL’: 3.0}

@dataclass
class MatchupInput:
team_a:        str
team_b:        str
sport:         str
baseline_diff: float
market_odds_a: float
market_odds_b: float

def american_to_decimal(odds: float) -> float:
if odds > 0:
return 1.0 + (odds / 100.0)
return 1.0 + (100.0 / abs(odds))

def remove_vig(p_a: float, p_b: float) -> Tuple[float, float]:
total = p_a + p_b
return p_a / total, p_b / total

def fractional_kelly(win_prob: float, dec_odds: float, fraction: float = 0.25) -> float:
b = dec_odds - 1.0
if b <= 0:
return 0.0
q = 1.0 - win_prob
kelly = (b * win_prob - q) / b
return max(kelly * fraction, 0.0)

def run_quant_engine(m: MatchupInput, simulations: int = 20000) -> dict:
std  = SPORT_STD.get(m.sport, 10.0)
base = SPORT_BASE.get(m.sport, 100.0)

```
mean_a = base + m.baseline_diff / 2.0
mean_b = base - m.baseline_diff / 2.0

cov = [
    [std ** 2,        0.15 * std ** 2],
    [0.15 * std ** 2, std ** 2],
]
scores = np.random.multivariate_normal([mean_a, mean_b], cov, simulations)
sa, sb = scores[:, 0], scores[:, 1]

win_prob_a = float(np.mean(sa > sb))
win_prob_b = 1.0 - win_prob_a
spread     = float(np.mean(sa - sb))
spread_std = float(np.std(sa - sb))

dec_a = american_to_decimal(m.market_odds_a)
dec_b = american_to_decimal(m.market_odds_b)

ev_a = win_prob_a * (dec_a - 1.0) - (1.0 - win_prob_a)
ev_b = win_prob_b * (dec_b - 1.0) - (1.0 - win_prob_b)

if ev_a > 0 and ev_a >= ev_b:
    ev_bet        = m.team_a
    kelly         = fractional_kelly(win_prob_a, dec_a)
    edge          = ev_a
    win_prob_pick = win_prob_a
elif ev_b > 0:
    ev_bet        = m.team_b
    kelly         = fractional_kelly(win_prob_b, dec_b)
    edge          = ev_b
    win_prob_pick = win_prob_b
else:
    ev_bet        = 'No +EV'
    kelly         = 0.0
    edge          = 0.0
    win_prob_pick = max(win_prob_a, win_prob_b)

return {
    'team_a':        m.team_a,
    'team_b':        m.team_b,
    'win_prob_a':    win_prob_a,
    'win_prob_b':    win_prob_b,
    'spread':        spread,
    'spread_std':    spread_std,
    'ev_bet':        ev_bet,
    'kelly':         kelly,
    'edge':          edge,
    'win_prob_pick': win_prob_pick,
    'ci_a':          [float(np.percentile(sa, 5)), float(np.percentile(sa, 95))],
    'ci_b':          [float(np.percentile(sb, 5)), float(np.percentile(sb, 95))],
}
```

# ─────────────────────────────────────────────────────────────────────────────

# ODDS API

# ─────────────────────────────────────────────────────────────────────────────

SPORT_KEYS: Dict[str, str] = {
‘NBA’:   ‘basketball_nba’,
‘NFL’:   ‘americanfootball_nfl’,
‘MLB’:   ‘baseball_mlb’,
‘NHL’:   ‘icehockey_nhl’,
‘NCAAB’: ‘basketball_ncaab’,
‘NCAAF’: ‘americanfootball_ncaaf’,
‘EPL’:   ‘soccer_epl’,
}

def fetch_live_odds(sport: str) -> List[dict]:
sport_key = SPORT_KEYS.get(sport)
if not sport_key or not ODDS_API_KEY:
return []
url = f’https://api.the-odds-api.com/v4/sports/{sport_key}/odds/’
params = {
‘apiKey’:     ODDS_API_KEY,
‘regions’:    ‘us’,
‘markets’:    ‘h2h’,
‘oddsFormat’: ‘american’,
‘dateFormat’: ‘iso’,
}
try:
resp = requests.get(url, params=params, timeout=10)
if resp.status_code == 200:
return resp.json()
log.warning(‘Odds API returned %s’, resp.status_code)
return []
except Exception as exc:
log.error(‘Odds API error: %s’, exc)
return []

def parse_matchup(game: dict, sport: str) -> Optional[MatchupInput]:
try:
home       = game[‘home_team’]
away       = game[‘away_team’]
bookmakers = game.get(‘bookmakers’, [])
if not bookmakers:
return None
outcomes  = bookmakers[0][‘markets’][0][‘outcomes’]
odds_map  = {o[‘name’]: float(o[‘price’]) for o in outcomes}
odds_a    = odds_map.get(home, -110.0)
odds_b    = odds_map.get(away, -110.0)
dec_a     = american_to_decimal(odds_a)
dec_b     = american_to_decimal(odds_b)
implied_a = 1.0 / dec_a
implied_b = 1.0 / dec_b
true_a, _ = remove_vig(implied_a, implied_b)
baseline_diff = (true_a - 0.5) * SPORT_STD.get(sport, 10.0) * 1.2
return MatchupInput(
team_a=home,
team_b=away,
sport=sport,
baseline_diff=baseline_diff,
market_odds_a=odds_a,
market_odds_b=odds_b,
)
except Exception as exc:
log.error(‘parse_matchup error: %s’, exc)
return None

# ─────────────────────────────────────────────────────────────────────────────

# MESSAGE FORMATTER

# ─────────────────────────────────────────────────────────────────────────────

def format_pick(result: dict, sport: str) -> str:
a          = result[‘team_a’]
b          = result[‘team_b’]
wp_a       = result[‘win_prob_a’] * 100.0
wp_b       = result[‘win_prob_b’] * 100.0
spread     = result[‘spread’]
spread_std = result[‘spread_std’]
edge       = result[‘edge’] * 100.0
kelly      = result[‘kelly’] * 100.0
ev_bet     = result[‘ev_bet’]
stars      = ‘⭐’ * min(5, max(1, int(edge / 3.0)))
ci_a       = result[‘ci_a’]
ci_b       = result[‘ci_b’]
timestamp  = datetime.utcnow().strftime(’%b %d %Y  %H:%M UTC’)

```
lines = [
    '━━━━━━━━━━━━━━━━━━━━━━━━',
    '🎯 *QUANTPICKS ELITE*',
    f'📊 `{sport}`  ·  _{timestamp}_',
    '━━━━━━━━━━━━━━━━━━━━━━━━',
    f'🏠 *{a}*',
    f'✈️  *{b}*',
    '',
    '📈 *Win Probability*',
    f'  {a}:  `{wp_a:.1f}%`',
    f'  {b}:  `{wp_b:.1f}%`',
    '',
    f'📐 Projected Spread:  `{spread:+.1f}`  (±{spread_std:.1f})',
    '',
    f'💡 *+EV Pick:*  `{ev_bet}`',
    f'🔥 Model Edge:  `{edge:.2f}%`  {stars}',
    f'💰 Kelly Stake (0.25×):  `{kelly:.2f}%` of bankroll',
    '',
    '📊 *90% Score Confidence Intervals*',
    f'  {a}:  `{ci_a[0]:.1f} – {ci_a[1]:.1f}`',
    f'  {b}:  `{ci_b[0]:.1f} – {ci_b[1]:.1f}`',
    '',
    'Simulations: `20,000`',
    '━━━━━━━━━━━━━━━━━━━━━━━━',
    '_Past performance does not guarantee future results._',
]
return '\n'.join(lines)
```

# ─────────────────────────────────────────────────────────────────────────────

# TELEGRAM HANDLERS

# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
chat_id  = update.effective_chat.id
username = update.effective_user.username or ‘unknown’
upsert_user(chat_id, username)

```
text = (
    '🏆 *Welcome to QuantPicks Elite*\n\n'
    'Institutional-grade sports analytics — Monte Carlo simulation, '
    'Kelly Criterion stake sizing, and live market edge detection.\n\n'
    '━━━━━━━━━━━━━━━━━━━━━━━━\n'
    '⚡ *Commands*\n'
    '`/pick [SPORT]` — Get a live +EV pick\n'
    '`/sports`       — List available sports\n'
    '`/help`         — All commands\n\n'
    'Supported: `NBA · NFL · MLB · NHL · NCAAB · NCAAF · EPL`\n'
    'Example:   `/pick NBA`'
)
await update.message.reply_text(text, parse_mode='Markdown')
```

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
text = (
‘📖 *QuantPicks Commands*\n\n’
‘`/pick`       — NBA pick (default)\n’
‘`/pick NBA`   — NBA pick\n’
‘`/pick NFL`   — NFL pick\n’
‘`/pick MLB`   — MLB pick\n’
‘`/pick NHL`   — NHL pick\n’
‘`/pick NCAAB` — College basketball\n’
‘`/pick NCAAF` — College football\n’
‘`/pick EPL`   — English Premier League\n’
‘`/sports`     — Show all supported sports\n’
‘`/start`      — Welcome message\n’
)
await update.message.reply_text(text, parse_mode=‘Markdown’)

async def cmd_sports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
text = (
‘🏟 *Supported Sports*\n\n’
‘`NBA`   — Basketball\n’
‘`NFL`   — American Football\n’
‘`MLB`   — Baseball\n’
‘`NHL`   — Hockey\n’
‘`NCAAB` — College Basketball\n’
‘`NCAAF` — College Football\n’
‘`EPL`   — English Premier League\n\n’
‘Usage: `/pick NFL`’
)
await update.message.reply_text(text, parse_mode=‘Markdown’)

async def cmd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
chat_id  = update.effective_chat.id
username = update.effective_user.username or ‘unknown’
upsert_user(chat_id, username)

```
sport = context.args[0].upper() if context.args else 'NBA'

if sport not in VALID_SPORTS:
    sports_str = ', '.join(VALID_SPORTS)
    await update.message.reply_text(
        f'❌ Invalid sport `{sport}`.\nChoose from: `{sports_str}`',
        parse_mode='Markdown',
    )
    return

status_msg = await update.message.reply_text(
    f'🔄 Fetching live `{sport}` odds & running 20,000 simulations...',
    parse_mode='Markdown',
)

games = fetch_live_odds(sport)

if not games:
    demo = MatchupInput(
        team_a='Home Team',
        team_b='Away Team',
        sport=sport,
        baseline_diff=3.5,
        market_odds_a=-130,
        market_odds_b=110,
    )
    result = run_quant_engine(demo)
    result['team_a'] = 'Home Team (Demo)'
    result['team_b'] = 'Away Team (Demo)'
    msg  = format_pick(result, sport)
    msg += '\n\n⚠️ _Demo mode — set `ODDS_API_KEY` env var for live data._'
    await status_msg.delete()
    await update.message.reply_text(msg, parse_mode='Markdown')
    return

best_result: Optional[dict] = None
best_edge:   float          = -999.0

for game in games[:15]:
    m = parse_matchup(game, sport)
    if m is None:
        continue
    r = run_quant_engine(m)
    if r['edge'] > best_edge:
        best_edge   = r['edge']
        best_result = r

await status_msg.delete()

if best_result is None or best_result['ev_bet'] == 'No +EV':
    await update.message.reply_text(
        f'⚠️ No +EV opportunities detected in today\'s `{sport}` slate. Try another sport.',
        parse_mode='Markdown',
    )
    return

msg = format_pick(best_result, sport)
await update.message.reply_text(msg, parse_mode='Markdown')

team_a_val  = best_result['team_a']
team_b_val  = best_result['team_b']
ev_bet_val  = best_result['ev_bet']
wp_val      = best_result['win_prob_pick']
kelly_val   = best_result['kelly']

log_pick(
    chat_id,
    sport,
    f'{team_a_val} vs {team_b_val}',
    ev_bet_val,
    wp_val,
    kelly_val,
)
```

# ─────────────────────────────────────────────────────────────────────────────

# ADMIN COMMANDS

# ─────────────────────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if update.effective_chat.id != ADMIN_CHAT_ID:
await update.message.reply_text(‘❌ Unauthorized.’)
return

```
conn = _db_connect()
c    = conn.cursor()
c.execute('SELECT COUNT(*) FROM users')
total_users = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM picks_log')
total_picks = c.fetchone()[0]
c.execute('''
    SELECT sport, COUNT(*) as cnt
    FROM picks_log
    GROUP BY sport
    ORDER BY cnt DESC
''')
sport_rows = c.fetchall()
conn.close()

sport_str = '\n'.join(f'  {s}: {n}' for s, n in sport_rows) or '  None yet'
text = (
    '🛠 *Admin Dashboard*\n\n'
    f'Total Users:   `{total_users}`\n'
    f'Picks Served:  `{total_picks}`\n\n'
    f'Picks by Sport:\n{sport_str}'
)
await update.message.reply_text(text, parse_mode='Markdown')
```

# ─────────────────────────────────────────────────────────────────────────────

# FLASK HEALTH SERVER  (Render requires HTTP on PORT)

# ─────────────────────────────────────────────────────────────────────────────

flask_app = Flask(‘QuantPicks’)

@flask_app.route(’/’)
def index():
return {‘service’: ‘QuantPicks Elite’, ‘status’: ‘running’,
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
raise RuntimeError(
‘TELEGRAM_TOKEN is not set. Add it as an environment variable in Render.’
)

```
init_db()

flask_thread = threading.Thread(target=run_flask, daemon=True, name='flask')
flask_thread.start()
log.info('Flask health server started on port %s', PORT)

application = Application.builder().token(TELEGRAM_TOKEN).build()

application.add_handler(CommandHandler('start',  cmd_start))
application.add_handler(CommandHandler('help',   cmd_help))
application.add_handler(CommandHandler('sports', cmd_sports))
application.add_handler(CommandHandler('pick',   cmd_pick))
application.add_handler(CommandHandler('admin',  cmd_admin))

log.info('QuantPicks Elite (Open Access) — polling started.')
application.run_polling(allowed_updates=Update.ALL_TYPES)
```

if **name** == ‘**main**’:
main()
