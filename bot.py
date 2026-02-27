#!/usr/bin/env python3

‘’’
PropNinjaBot - Private Picks Dashboard v2
Parlay builder, live stats feed, risk-scored legs.
Deploy on Render. Start command: python app.py

=== ENV VARS ===
ODDS_API_KEY      - https://the-odds-api.com
ACCESS_PASSWORD   - Your private login password
SECRET_KEY        - Any random string
PORT              - Auto-set by Render

=== requirements.txt ===
flask==3.0.3
requests==2.31.0
numpy==1.26.4
gunicorn==21.2.0
‘’’

import os
import logging
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, List
import requests
from flask import Flask, jsonify, render_template_string, request, session, redirect

# ─────────────────────────────────────────────────────────────────────────────

# CONFIG

# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format=’%(asctime)s %(levelname)s %(message)s’)
log = logging.getLogger(‘propninja’)

ODDS_API_KEY    = os.environ.get(‘ODDS_API_KEY’, ‘’)
ACCESS_PASSWORD = os.environ.get(‘ACCESS_PASSWORD’, ‘ninja2025’)
PORT            = int(os.environ.get(‘PORT’, ‘8080’))
SECRET_KEY      = os.environ.get(‘SECRET_KEY’, ‘propninja-secret-change-me’)

VALID_SPORTS = [‘NBA’, ‘NFL’, ‘MLB’, ‘NHL’, ‘NCAAB’, ‘EPL’]

SPORT_KEYS: Dict[str, str] = {
‘NBA’:   ‘basketball_nba’,
‘NFL’:   ‘americanfootball_nfl’,
‘MLB’:   ‘baseball_mlb’,
‘NHL’:   ‘icehockey_nhl’,
‘NCAAB’: ‘basketball_ncaab’,
‘EPL’:   ‘soccer_epl’,
}

SPORT_STD:  Dict[str, float] = {
‘NBA’: 12.0, ‘NFL’: 10.0, ‘MLB’: 3.5,
‘NHL’: 2.5,  ‘NCAAB’: 14.0, ‘EPL’: 1.2,
}
SPORT_BASE: Dict[str, float] = {
‘NBA’: 115.0, ‘NFL’: 23.0, ‘MLB’: 4.5,
‘NHL’: 3.0,   ‘NCAAB’: 72.0, ‘EPL’: 1.5,
}

# ─────────────────────────────────────────────────────────────────────────────

# LESSON BANK  (learned from past slips)

# Patterns that historically kill parlays — used to score each leg

# ─────────────────────────────────────────────────────────────────────────────

KILL_LEG_RULES = [
{‘label’: ‘High player prop threshold’, ‘desc’: ‘Props set >= 20 pts for non-elite scorers. Wembanyama 20+ was the slip killer.’},
{‘label’: ‘Large spread dog’,           ‘desc’: ‘Betting underdogs to cover > 6.5 pts. Florida -6.5 missed.’},
{‘label’: ‘Totals on defensive teams’,  ‘desc’: ‘Overs/unders on pace-controlled matchups are high variance.’},
]

def classify_leg_risk(leg: dict) -> str:
leg_type = leg.get(‘leg_type’, ‘’)
line     = float(leg.get(‘line’, 0))
win_prob = float(leg.get(‘win_prob’, 50))

```
# Lesson 1: high player prop threshold
if leg_type == 'prop' and line >= 20:
    return 'HIGH'
# Lesson 2: large spread
if leg_type in ('spread', 'runline') and abs(line) > 6.5:
    return 'HIGH'
# Lesson 3: low model confidence
if win_prob < 52:
    return 'HIGH'
if win_prob < 58:
    return 'MED'
return 'LOW'
```

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
val = int(round(-100.0 / (dec - 1.0)))
return str(val)

def remove_vig(p_a: float, p_b: float) -> Tuple[float, float]:
total = p_a + p_b
return p_a / total, p_b / total

def fractional_kelly(win_prob: float, dec_odds: float, fraction: float = 0.25) -> float:
b = dec_odds - 1.0
if b <= 0:
return 0.0
q = 1.0 - win_prob
k = (b * win_prob - q) / b
return max(k * fraction, 0.0)

def run_quant_engine(m: MatchupInput, simulations: int = 20000) -> dict:
std    = SPORT_STD.get(m.sport, 10.0)
base   = SPORT_BASE.get(m.sport, 100.0)
mean_a = base + m.baseline_diff / 2.0
mean_b = base - m.baseline_diff / 2.0
cov    = [[std**2, 0.15*std**2], [0.15*std**2, std**2]]
scores = np.random.multivariate_normal([mean_a, mean_b], cov, simulations)
sa, sb = scores[:, 0], scores[:, 1]

```
win_prob_a = float(np.mean(sa > sb))
win_prob_b = 1.0 - win_prob_a
spread     = float(np.mean(sa - sb))
spread_std = float(np.std(sa - sb))

dec_a = american_to_decimal(m.market_odds_a)
dec_b = american_to_decimal(m.market_odds_b)
ev_a  = win_prob_a * (dec_a - 1.0) - (1.0 - win_prob_a)
ev_b  = win_prob_b * (dec_b - 1.0) - (1.0 - win_prob_b)

if ev_a > 0 and ev_a >= ev_b:
    ev_bet = m.team_a
    kelly  = fractional_kelly(win_prob_a, dec_a)
    edge   = ev_a
    wp     = win_prob_a
    odds   = m.market_odds_a
elif ev_b > 0:
    ev_bet = m.team_b
    kelly  = fractional_kelly(win_prob_b, dec_b)
    edge   = ev_b
    wp     = win_prob_b
    odds   = m.market_odds_b
else:
    ev_bet = 'No Edge'
    kelly  = 0.0
    edge   = 0.0
    wp     = max(win_prob_a, win_prob_b)
    odds   = m.market_odds_a if win_prob_a >= win_prob_b else m.market_odds_b

stars = min(5, max(1, int(edge * 100 / 3.0)))
risk  = classify_leg_risk({
    'leg_type': 'moneyline',
    'line': abs(spread),
    'win_prob': wp * 100,
})

return {
    'id':         m.team_a.replace(' ', '') + '_' + m.team_b.replace(' ', '') + '_' + m.sport,
    'team_a':     m.team_a,
    'team_b':     m.team_b,
    'sport':      m.sport,
    'source':     m.source,
    'game_time':  m.game_time,
    'win_prob_a': round(win_prob_a * 100, 1),
    'win_prob_b': round(win_prob_b * 100, 1),
    'spread':     round(spread, 1),
    'spread_std': round(spread_std, 1),
    'ev_bet':     ev_bet,
    'ev_odds':    odds,
    'ev_dec':     american_to_decimal(odds),
    'kelly':      round(kelly * 100, 2),
    'edge':       round(edge * 100, 2),
    'stars':      stars,
    'win_prob':   round(wp * 100, 1),
    'ci_a':       [round(float(np.percentile(sa, 5)), 1), round(float(np.percentile(sa, 95)), 1)],
    'ci_b':       [round(float(np.percentile(sb, 5)), 1), round(float(np.percentile(sb, 95)), 1)],
    'has_edge':   ev_bet != 'No Edge',
    'risk':       risk,
    'leg_type':   'moneyline',
}
```

# ─────────────────────────────────────────────────────────────────────────────

# PARLAY ENGINE

# ─────────────────────────────────────────────────────────────────────────────

def calculate_parlay(legs: List[dict]) -> dict:
if not legs:
return {}

```
# Combined probability — apply correlation penalty for same-sport legs
sport_counts: Dict[str, int] = {}
for leg in legs:
    s = leg.get('sport', 'UNK')
    sport_counts[s] = sport_counts.get(s, 0) + 1

combined_prob = 1.0
parlay_dec    = 1.0

for leg in legs:
    wp  = leg.get('win_prob', 50) / 100.0
    dec = leg.get('ev_dec', 1.9)
    # Correlation penalty: same-sport legs are not fully independent
    same_sport_count = sport_counts.get(leg.get('sport', 'UNK'), 1)
    if same_sport_count > 1:
        wp = wp * 0.97  # 3% correlation haircut per same-sport leg
    combined_prob *= wp
    parlay_dec    *= dec

ev         = combined_prob * (parlay_dec - 1.0) - (1.0 - combined_prob)
kelly_raw  = (combined_prob * (parlay_dec - 1.0) - (1.0 - combined_prob)) / (parlay_dec - 1.0)
kelly      = max(kelly_raw * 0.25, 0.0)

high_risk_legs  = [l for l in legs if l.get('risk') == 'HIGH']
kill_leg_count  = len(high_risk_legs)
parlay_grade    = 'A' if kill_leg_count == 0 and ev > 0 else \
                  'B' if kill_leg_count <= 1 and ev > 0 else \
                  'C' if kill_leg_count <= 2 else 'D'

warnings = []
if kill_leg_count > 0:
    names = [l.get('ev_bet', '?') for l in high_risk_legs]
    warnings.append('HIGH risk legs detected: ' + ', '.join(names) + '. These are historical parlay killers.')
if len(legs) > 6:
    warnings.append('Parlays with 7+ legs have < 2% hit rate on average. Consider trimming.')
if combined_prob < 0.05:
    warnings.append('Combined probability below 5%. Extremely unlikely to hit.')

return {
    'legs':          legs,
    'num_legs':      len(legs),
    'combined_prob': round(combined_prob * 100, 2),
    'parlay_odds':   round(parlay_dec, 2),
    'parlay_american': decimal_to_american(parlay_dec),
    'ev':            round(ev * 100, 2),
    'kelly_pct':     round(kelly * 100, 2),
    'grade':         parlay_grade,
    'kill_legs':     kill_leg_count,
    'warnings':      warnings,
    'has_ev':        ev > 0,
}
```

def build_optimal_parlay(all_picks: List[dict], max_legs: int = 5) -> dict:
ev_picks = [p for p in all_picks if p.get(‘has_edge’) and p.get(‘risk’) != ‘HIGH’]
ev_picks.sort(key=lambda x: (x.get(‘edge’, 0) + x.get(‘win_prob’, 0) * 0.5), reverse=True)
legs = ev_picks[:max_legs]
if not legs:
legs = sorted(all_picks, key=lambda x: x.get(‘win_prob’, 0), reverse=True)[:max_legs]
return calculate_parlay(legs)

# ─────────────────────────────────────────────────────────────────────────────

# STATS FEED  (ESPN public + balldontlie public)

# ─────────────────────────────────────────────────────────────────────────────

def fetch_espn_scores(sport_slug: str) -> List[dict]:
url = ‘https://site.api.espn.com/apis/site/v2/sports/’ + sport_slug + ‘/scoreboard’
try:
resp = requests.get(url, timeout=8)
if resp.status_code != 200:
return []
data   = resp.json()
events = data.get(‘events’, [])
games  = []
for evt in events[:10]:
try:
comps = evt.get(‘competitions’, [{}])[0]
comps_list = comps.get(‘competitors’, [])
if len(comps_list) < 2:
continue
home_team  = next((c for c in comps_list if c.get(‘homeAway’) == ‘home’), comps_list[0])
away_team  = next((c for c in comps_list if c.get(‘homeAway’) == ‘away’), comps_list[1])
status     = evt.get(‘status’, {}).get(‘type’, {}).get(‘description’, ‘’)
home_score = home_team.get(‘score’, ‘-’)
away_score = away_team.get(‘score’, ‘-’)
home_name  = home_team.get(‘team’, {}).get(‘displayName’, ‘’)
away_name  = away_team.get(‘team’, {}).get(‘displayName’, ‘’)
home_rec   = home_team.get(‘records’, [{}])[0].get(‘summary’, ‘’) if home_team.get(‘records’) else ‘’
away_rec   = away_team.get(‘records’, [{}])[0].get(‘summary’, ‘’) if away_team.get(‘records’) else ‘’
games.append({
‘home’:       home_name,
‘away’:       away_name,
‘home_score’: home_score,
‘away_score’: away_score,
‘home_rec’:   home_rec,
‘away_rec’:   away_rec,
‘status’:     status,
‘date’:       evt.get(‘date’, ‘’)[:16].replace(‘T’, ’ ’),
})
except Exception:
continue
return games
except Exception as exc:
log.error(‘ESPN scores error: %s’, exc)
return []

ESPN_SPORT_SLUGS: Dict[str, str] = {
‘NBA’:   ‘basketball/nba’,
‘NFL’:   ‘football/nfl’,
‘MLB’:   ‘baseball/mlb’,
‘NHL’:   ‘hockey/nhl’,
‘NCAAB’: ‘basketball/mens-college-basketball’,
‘EPL’:   ‘soccer/eng.1’,
}

def fetch_nba_player_stats() -> List[dict]:
url = ‘https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/byathlete’
params = {‘season’: ‘2025’, ‘seasontype’: ‘2’, ‘limit’: ‘50’, ‘sort’: ‘offensive.avgPoints:desc’}
try:
resp = requests.get(url, params=params, timeout=8)
if resp.status_code != 200:
return []
data    = resp.json()
athletes = data.get(‘athletes’, [])
players  = []
for a in athletes:
try:
info  = a.get(‘athlete’, {})
stats = a.get(‘statistics’, {}).get(‘splits’, {}).get(‘categories’, [])
name  = info.get(‘displayName’, ‘’)
team  = info.get(‘team’, {}).get(‘abbreviation’, ‘’)
pts   = 0.0
reb   = 0.0
ast   = 0.0
for cat in stats:
if cat.get(‘name’) == ‘offensive’:
for s in cat.get(‘stats’, []):
if s.get(‘name’) == ‘avgPoints’:
pts = float(s.get(‘value’, 0))
if s.get(‘name’) == ‘avgRebounds’:
reb = float(s.get(‘value’, 0))
if s.get(‘name’) == ‘avgAssists’:
ast = float(s.get(‘value’, 0))
if name:
players.append({
‘name’: name,
‘team’: team,
‘avg_pts’: round(pts, 1),
‘avg_reb’: round(reb, 1),
‘avg_ast’: round(ast, 1),
})
except Exception:
continue
return players
except Exception as exc:
log.error(‘ESPN player stats error: %s’, exc)
return []

# ─────────────────────────────────────────────────────────────────────────────

# ODDS API

# ─────────────────────────────────────────────────────────────────────────────

def fetch_the_odds_api(sport: str) -> List[MatchupInput]:
sport_key = SPORT_KEYS.get(sport)
if not sport_key or not ODDS_API_KEY:
return []
url    = ‘https://api.the-odds-api.com/v4/sports/’ + sport_key + ‘/odds/’
params = {
‘apiKey’:     ODDS_API_KEY,
‘regions’:    ‘us’,
‘markets’:    ‘h2h’,
‘oddsFormat’: ‘american’,
‘dateFormat’: ‘iso’,
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
best_a, best_b, best_book = None, None, ‘Sportsbook’
for bk in bks:
try:
outs = bk[‘markets’][0][‘outcomes’]
om   = {o[‘name’]: float(o[‘price’]) for o in outs}
oa   = om.get(home)
ob   = om.get(away)
if oa and ob:
if best_a is None or oa > best_a:
best_a    = oa
best_book = bk.get(‘title’, ‘Sportsbook’)
if best_b is None or ob > best_b:
best_b = ob
except Exception:
continue
if best_a is None or best_b is None:
continue
dec_a    = american_to_decimal(best_a)
dec_b    = american_to_decimal(best_b)
imp_a    = 1.0 / dec_a
imp_b    = 1.0 / dec_b
true_a, _ = remove_vig(imp_a, imp_b)
bdiff     = (true_a - 0.5) * SPORT_STD.get(sport, 10.0) * 1.2
results.append(MatchupInput(
team_a=home,
team_b=away,
sport=sport,
baseline_diff=bdiff,
market_odds_a=best_a,
market_odds_b=best_b,
source=best_book,
game_time=game_time,
))
except Exception:
continue
return results
except Exception as exc:
log.error(‘TheOddsAPI error: %s’, exc)
return []

def fetch_prizepicks_props() -> List[dict]:
try:
resp = requests.get(
‘https://partner-api.prizepicks.com/projections’,
headers={‘Content-Type’: ‘application/json’},
timeout=10,
)
if resp.status_code != 200:
return []
data     = resp.json()
included = {i[‘id’]: i for i in data.get(‘included’, []) if i.get(‘type’) == ‘new_player’}
props    = []
for item in data.get(‘data’, [])[:40]:
try:
attrs = item.get(‘attributes’, {})
rels  = item.get(‘relationships’, {})
pid   = rels.get(‘new_player’, {}).get(‘data’, {}).get(‘id’, ‘’)
pdata = included.get(pid, {})
pname = pdata.get(‘attributes’, {}).get(‘display_name’, ‘Unknown’)
stat  = attrs.get(‘stat_type’, ‘N/A’)
line  = float(attrs.get(‘line_score’, 0))
league = attrs.get(‘league’, ‘N/A’)
desc  = attrs.get(‘description’, ‘’)
start = attrs.get(‘start_time’, ‘’)[:16].replace(‘T’, ’ ‘)
risk  = classify_leg_risk({‘leg_type’: ‘prop’, ‘line’: line, ‘win_prob’: 50})
props.append({
‘id’:        ‘pp_’ + item.get(‘id’, str(len(props))),
‘player’:    pname,
‘stat’:      stat,
‘line’:      line,
‘league’:    league,
‘matchup’:   desc,
‘game_time’: start,
‘source’:    ‘PrizePicks’,
‘leg_type’:  ‘prop’,
‘risk’:      risk,
‘win_prob’:  50.0,
‘ev_bet’:    pname + ’ ’ + stat + ’ O’ + str(line),
‘ev_dec’:    1.85,
‘sport’:     league,
})
except Exception:
continue
return props
except Exception as exc:
log.error(‘PrizePicks error: %s’, exc)
return []

def fetch_kalshi_markets() -> List[dict]:
try:
url     = ‘https://trading-api.kalshi.com/trade-api/v2/markets’
params  = {‘limit’: 25, ‘category’: ‘Sports’}
headers = {‘accept’: ‘application/json’}
resp    = requests.get(url, params=params, headers=headers, timeout=10)
if resp.status_code != 200:
return []
results = []
for m in resp.json().get(‘markets’, []):
try:
yes_ask = m.get(‘yes_ask’, 0) / 100.0
if yes_ask <= 0:
continue
risk = classify_leg_risk({‘leg_type’: ‘prediction’, ‘line’: 0, ‘win_prob’: yes_ask * 100})
results.append({
‘id’:         ‘kalshi_’ + m.get(‘ticker’, str(len(results))),
‘title’:      m.get(‘title’, ‘’),
‘yes_prob’:   round(yes_ask * 100, 1),
‘no_prob’:    round((1.0 - yes_ask) * 100, 1),
‘yes_price’:  yes_ask,
‘volume’:     m.get(‘volume’, 0),
‘close_time’: m.get(‘close_time’, ‘’)[:16].replace(‘T’, ’ ’),
‘source’:     ‘Kalshi’,
‘leg_type’:   ‘prediction’,
‘risk’:       risk,
‘win_prob’:   round(yes_ask * 100, 1),
‘ev_bet’:     m.get(‘title’, ‘’)[:40],
‘ev_dec’:     round(1.0 / yes_ask, 3) if yes_ask > 0 else 2.0,
‘sport’:      ‘General’,
})
except Exception:
continue
return results
except Exception as exc:
log.error(‘Kalshi error: %s’, exc)
return []

# ─────────────────────────────────────────────────────────────────────────────

# MASTER DATA FETCH

# ─────────────────────────────────────────────────────────────────────────────

def get_all_data() -> dict:
picks_by_sport: Dict[str, List[dict]] = {}
all_flat: List[dict] = []

```
for sport in VALID_SPORTS:
    matchups = fetch_the_odds_api(sport)
    results  = [run_quant_engine(m) for m in matchups]
    results.sort(key=lambda x: x['edge'], reverse=True)
    if results:
        picks_by_sport[sport] = results
        all_flat.extend(results)

scores: Dict[str, List[dict]] = {}
for sport, slug in ESPN_SPORT_SLUGS.items():
    s = fetch_espn_scores(slug)
    if s:
        scores[sport] = s

props       = fetch_prizepicks_props()
kalshi      = fetch_kalshi_markets()
player_stats = fetch_nba_player_stats()

# Build optimal parlay from all +EV legs
optimal_parlay_4 = build_optimal_parlay(all_flat, max_legs=4)
optimal_parlay_5 = build_optimal_parlay(all_flat, max_legs=5)

return {
    'sportsbook_picks': picks_by_sport,
    'all_picks':        all_flat,
    'scores':           scores,
    'prizepicks_props': props,
    'kalshi_markets':   kalshi,
    'player_stats':     player_stats[:30],
    'optimal_parlay_4': optimal_parlay_4,
    'optimal_parlay_5': optimal_parlay_5,
    'timestamp':        datetime.utcnow().strftime('%b %d %Y  %H:%M UTC'),
    'has_live_data':    bool(ODDS_API_KEY),
    'kill_leg_rules':   KILL_LEG_RULES,
}
```

# ─────────────────────────────────────────────────────────────────────────────

# HTML DASHBOARD

# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = r’’’<!DOCTYPE html>

<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PropNinjaBot</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#060a0f;--bg2:#0b1118;--bg3:#111a24;--border:#1a2a3a;--green:#00ff88;--cyan:#00c8ff;--amber:#ffb800;--red:#ff4466;--purple:#a855f7;--text:#c9d1d9;--muted:#4a5568;--white:#f0f6fc}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:13px;min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(ellipse 60% 40% at 10% 0%,rgba(0,255,136,.04) 0%,transparent 60%),radial-gradient(ellipse 50% 50% at 90% 100%,rgba(0,200,255,.04) 0%,transparent 60%);pointer-events:none;z-index:0}
.header{position:sticky;top:0;z-index:100;background:rgba(6,10,15,.96);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:56px}
.logo{font-family:'Syne',sans-serif;font-weight:800;font-size:18px;color:var(--white);letter-spacing:-.5px}
.logo span{color:var(--green)}
.header-right{display:flex;align-items:center;gap:16px}
.live-badge{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--green);text-transform:uppercase;letter-spacing:1px}
.live-dot{width:7px;height:7px;background:var(--green);border-radius:50%;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.7)}}
.btn{background:var(--bg3);border:1px solid var(--border);color:var(--cyan);font-family:'JetBrains Mono',monospace;font-size:11px;padding:6px 14px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;transition:all .2s}
.btn:hover{background:var(--cyan);color:var(--bg);border-color:var(--cyan)}
.btn-green{color:var(--green);border-color:rgba(0,255,136,.3)}
.btn-green:hover{background:var(--green);color:var(--bg);border-color:var(--green)}
.btn-amber{color:var(--amber);border-color:rgba(255,184,0,.3)}
.btn-amber:hover{background:var(--amber);color:var(--bg)}
.statbar{display:flex;border-bottom:1px solid var(--border);overflow-x:auto;position:relative;z-index:1}
.stat-item{flex:1;min-width:120px;padding:12px 20px;border-right:1px solid var(--border)}
.stat-item:last-child{border-right:none}
.stat-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}
.stat-value{font-size:20px;font-weight:700;color:var(--white)}
.green{color:var(--green)}.cyan{color:var(--cyan)}.amber{color:var(--amber)}.red{color:var(--red)}.purple{color:var(--purple)}
.tabs{display:flex;border-bottom:1px solid var(--border);padding:0 24px;position:relative;z-index:1;overflow-x:auto}
.tab{padding:14px 20px;font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all .2s;white-space:nowrap}
.tab:hover{color:var(--text)}
.tab.active{color:var(--green);border-bottom-color:var(--green)}
.main{padding:24px;position:relative;z-index:1}
.panel{display:none}.panel.active{display:block}
.section-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.section-title{font-family:'Syne',sans-serif;font-size:15px;font-weight:700;color:var(--white);letter-spacing:-.3px}
.section-meta{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.sport-filter{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}
.sport-btn{background:var(--bg3);border:1px solid var(--border);color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:11px;padding:5px 12px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;transition:all .15s}
.sport-btn:hover{border-color:var(--cyan);color:var(--cyan)}
.sport-btn.active{background:var(--green);border-color:var(--green);color:var(--bg)}
.picks-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.pick-card{background:var(--bg2);border:1px solid var(--border);padding:18px;transition:border-color .2s,transform .2s;animation:fadeIn .4s ease both;cursor:pointer;user-select:none}
.pick-card:hover{border-color:var(--green);transform:translateY(-1px)}
.pick-card.selected{border-color:var(--green);background:rgba(0,255,136,.05)}
.pick-card.no-edge{opacity:.55}
.pick-card.no-edge:hover{border-color:var(--muted)}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px}
.card-sport{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted)}
.card-source{font-size:10px;color:var(--cyan);text-transform:uppercase;letter-spacing:1px}
.card-teams{margin-bottom:14px}
.team-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)}
.team-row:last-child{border-bottom:none}
.team-name{font-size:13px;color:var(--white);font-weight:500}
.team-name.picked{color:var(--green)}
.team-prob{font-size:13px;font-weight:700;color:var(--text)}
.team-prob.high{color:var(--green)}
.card-stats{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:14px}
.cstat-label{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:3px}
.cstat-value{font-size:13px;font-weight:700;color:var(--white)}
.ev-banner{margin-top:14px;padding:8px 12px;background:rgba(0,255,136,.07);border:1px solid rgba(0,255,136,.2);display:flex;justify-content:space-between;align-items:center}
.ev-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.ev-pick{font-size:12px;color:var(--green);font-weight:700}
.stars{color:var(--amber);font-size:11px;letter-spacing:1px}
.game-time{font-size:10px;color:var(--muted);margin-top:10px}
.risk-badge{display:inline-block;padding:2px 8px;font-size:9px;text-transform:uppercase;letter-spacing:1px;margin-top:8px}
.risk-LOW{background:rgba(0,255,136,.1);color:var(--green);border:1px solid rgba(0,255,136,.2)}
.risk-MED{background:rgba(255,184,0,.1);color:var(--amber);border:1px solid rgba(255,184,0,.2)}
.risk-HIGH{background:rgba(255,68,102,.1);color:var(--red);border:1px solid rgba(255,68,102,.2)}
/* PARLAY BUILDER */
.parlay-layout{display:grid;grid-template-columns:1fr 340px;gap:24px;align-items:start}
.parlay-slip{background:var(--bg2);border:1px solid var(--border);padding:20px;position:sticky;top:80px}
.parlay-slip-title{font-family:'Syne',sans-serif;font-size:14px;font-weight:700;color:var(--white);margin-bottom:16px;display:flex;justify-content:space-between;align-items:center}
.slip-legs{min-height:60px;margin-bottom:16px}
.slip-leg{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:var(--bg3);border:1px solid var(--border);margin-bottom:6px;font-size:11px}
.slip-leg-name{color:var(--white);font-weight:500;max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slip-leg-prob{color:var(--green);font-weight:700}
.slip-leg-remove{color:var(--red);cursor:pointer;padding:0 4px;font-size:14px;line-height:1}
.slip-leg-remove:hover{color:var(--white)}
.slip-empty{color:var(--muted);font-size:11px;text-align:center;padding:20px 0}
.parlay-result{border-top:1px solid var(--border);padding-top:16px}
.pr-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;font-size:12px}
.pr-label{color:var(--muted)}
.pr-value{color:var(--white);font-weight:700}
.pr-value.green{color:var(--green)}
.pr-value.amber{color:var(--amber)}
.pr-value.red{color:var(--red)}
.grade-badge{display:inline-block;padding:2px 10px;font-size:11px;font-weight:700;letter-spacing:1px}
.grade-A{background:rgba(0,255,136,.15);color:var(--green);border:1px solid var(--green)}
.grade-B{background:rgba(0,200,255,.15);color:var(--cyan);border:1px solid var(--cyan)}
.grade-C{background:rgba(255,184,0,.15);color:var(--amber);border:1px solid var(--amber)}
.grade-D{background:rgba(255,68,102,.15);color:var(--red);border:1px solid var(--red)}
.slip-warnings{margin-top:12px}
.slip-warning{font-size:10px;color:var(--amber);padding:5px 8px;background:rgba(255,184,0,.07);border-left:2px solid var(--amber);margin-bottom:4px;line-height:1.4}
.optimal-box{background:var(--bg2);border:1px solid rgba(0,255,136,.2);padding:20px;margin-bottom:24px}
.optimal-title{font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--green);margin-bottom:14px;display:flex;align-items:center;gap:8px}
.optimal-legs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
.optimal-leg{padding:4px 10px;background:rgba(0,255,136,.07);border:1px solid rgba(0,255,136,.15);font-size:11px;color:var(--text)}
.optimal-leg strong{color:var(--green)}
.optimal-stats{display:flex;gap:20px;flex-wrap:wrap}
.os-item .os-label{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:2px}
.os-item .os-value{font-size:16px;font-weight:700}
/* SCORES */
.scores-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.score-card{background:var(--bg2);border:1px solid var(--border);padding:16px;animation:fadeIn .4s ease both}
.score-sport{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:10px}
.score-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid rgba(26,42,58,.5)}
.score-row:last-child{border-bottom:none}
.score-team{color:var(--white);font-size:13px}
.score-num{font-size:18px;font-weight:700;color:var(--green)}
.score-rec{font-size:10px;color:var(--muted);margin-left:6px}
.score-status{font-size:10px;color:var(--cyan);margin-top:8px;text-align:right}
/* PROPS TABLE */
.props-table{width:100%;border-collapse:collapse;font-size:12px}
.props-table th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);padding:10px 14px;border-bottom:1px solid var(--border);background:var(--bg2)}
.props-table td{padding:11px 14px;border-bottom:1px solid rgba(26,42,58,.5);color:var(--text);vertical-align:middle}
.props-table tr:hover td{background:var(--bg2)}
.prop-player{color:var(--white);font-weight:500}
.prop-line{color:var(--green);font-weight:700;font-size:15px}
.prop-add{background:none;border:1px solid rgba(0,255,136,.3);color:var(--green);font-family:'JetBrains Mono',monospace;font-size:10px;padding:3px 8px;cursor:pointer;text-transform:uppercase;letter-spacing:1px}
.prop-add:hover{background:var(--green);color:var(--bg)}
.prop-add.added{background:rgba(0,255,136,.1);border-color:var(--green)}
/* KALSHI */
.kalshi-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.kalshi-card{background:var(--bg2);border:1px solid var(--border);padding:16px;animation:fadeIn .4s ease both;cursor:pointer;transition:border-color .2s}
.kalshi-card:hover{border-color:var(--amber)}
.kalshi-card.selected{border-color:var(--amber);background:rgba(255,184,0,.04)}
.kalshi-title{font-size:13px;color:var(--white);font-weight:500;margin-bottom:12px;line-height:1.4}
.kalshi-probs{display:flex;gap:10px;margin-bottom:10px}
.kprob{flex:1;padding:8px;text-align:center}
.kprob.yes{background:rgba(0,255,136,.07);border:1px solid rgba(0,255,136,.15)}
.kprob.no{background:rgba(255,68,102,.07);border:1px solid rgba(255,68,102,.15)}
.kprob-label{font-size:9px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}
.kprob.yes .kprob-label{color:var(--green)}
.kprob.no .kprob-label{color:var(--red)}
.kprob-val{font-size:20px;font-weight:700}
.kprob.yes .kprob-val{color:var(--green)}
.kprob.no .kprob-val{color:var(--red)}
.kalshi-meta{display:flex;justify-content:space-between;font-size:10px;color:var(--muted)}
/* PLAYER STATS */
.stats-table{width:100%;border-collapse:collapse;font-size:12px}
.stats-table th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);padding:10px 14px;border-bottom:1px solid var(--border);background:var(--bg2)}
.stats-table td{padding:10px 14px;border-bottom:1px solid rgba(26,42,58,.5);color:var(--text)}
.stats-table tr:hover td{background:var(--bg2)}
.pname{color:var(--white);font-weight:500}
.pts{color:var(--green);font-weight:700}
/* NO DATA */
.no-data{text-align:center;padding:60px 20px;color:var(--muted)}
.no-data-title{font-family:'Syne',sans-serif;font-size:18px;color:var(--text);margin-bottom:8px}
.no-data-sub{font-size:12px;line-height:1.6}
/* TIMESTAMP */
.timestamp-bar{text-align:right;padding:8px 24px;font-size:10px;color:var(--muted);border-bottom:1px solid var(--border);position:relative;z-index:1}
/* LOADING */
#loading{display:none;position:fixed;inset:0;background:rgba(6,10,15,.85);z-index:999;align-items:center;justify-content:center;flex-direction:column;gap:16px}
#loading.show{display:flex}
.spinner{width:40px;height:40px;border:2px solid var(--border);border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.loading-text{font-size:12px;color:var(--green);text-transform:uppercase;letter-spacing:2px}
@media(max-width:900px){.parlay-layout{grid-template-columns:1fr}.parlay-slip{position:static}}
@media(max-width:600px){.picks-grid{grid-template-columns:1fr}.kalshi-grid{grid-template-columns:1fr}.card-stats{grid-template-columns:1fr 1fr}.main{padding:14px}.tabs,.header{padding:0 14px}}
</style>
</head>
<body>
<div id="loading"><div class="spinner"></div><div class="loading-text">Running 20k sims...</div></div>
<header class="header">
  <div class="logo">Prop<span>Ninja</span>Bot</div>
  <div class="header-right">
    <div class="live-badge"><div class="live-dot"></div>Live</div>
    <button class="btn btn-green" onclick="loadData()">Refresh</button>
    <a href="/logout" class="btn">Logout</a>
  </div>
</header>
<div class="statbar">
  <div class="stat-item"><div class="stat-label">Total Picks</div><div class="stat-value green" id="s-total">—</div></div>
  <div class="stat-item"><div class="stat-label">+EV Picks</div><div class="stat-value cyan" id="s-ev">—</div></div>
  <div class="stat-item"><div class="stat-label">Best Edge</div><div class="stat-value amber" id="s-edge">—</div></div>
  <div class="stat-item"><div class="stat-label">Parlay Legs</div><div class="stat-value purple" id="s-parlay">0</div></div>
  <div class="stat-item"><div class="stat-label">PP Props</div><div class="stat-value green" id="s-pp">—</div></div>
</div>
<div class="timestamp-bar" id="ts-bar">—</div>
<nav class="tabs">
  <div class="tab active" data-panel="parlay">Parlay Builder</div>
  <div class="tab" data-panel="sportsbook">Sportsbooks</div>
  <div class="tab" data-panel="scores">Live Scores</div>
  <div class="tab" data-panel="prizepicks">PrizePicks</div>
  <div class="tab" data-panel="kalshi">Kalshi</div>
  <div class="tab" data-panel="players">Player Stats</div>
</nav>
<main class="main">

<!-- PARLAY BUILDER -->

<div class="panel active" id="panel-parlay">
  <div id="optimal-container"></div>
  <div class="parlay-layout">
    <div>
      <div class="section-head">
        <div class="section-title">Click Picks to Build Parlay</div>
        <div class="section-meta">Risk-scored · Correlation-adjusted · Kelly-sized</div>
      </div>
      <div class="sport-filter" id="parlay-sport-filter"></div>
      <div class="picks-grid" id="parlay-picks-grid"></div>
    </div>
    <div class="parlay-slip">
      <div class="parlay-slip-title">
        <span>Parlay Slip</span>
        <button class="btn" onclick="clearSlip()" style="font-size:10px;padding:3px 8px">Clear</button>
      </div>
      <div class="slip-legs" id="slip-legs"><div class="slip-empty">Click picks to add legs</div></div>
      <div class="parlay-result" id="parlay-result"></div>
    </div>
  </div>
</div>

<!-- SPORTSBOOK -->

<div class="panel" id="panel-sportsbook">
  <div class="section-head">
    <div class="section-title">Live Sportsbook Picks</div>
    <div class="section-meta">Monte Carlo · 20,000 sims · Kelly sized</div>
  </div>
  <div class="sport-filter" id="sb-sport-filter"></div>
  <div class="picks-grid" id="sb-picks-grid"></div>
</div>

<!-- SCORES -->

<div class="panel" id="panel-scores">
  <div class="section-head">
    <div class="section-title">Live &amp; Recent Scores</div>
    <div class="section-meta">Via ESPN</div>
  </div>
  <div class="sport-filter" id="scores-filter"></div>
  <div class="scores-grid" id="scores-grid"></div>
</div>

<!-- PRIZEPICKS -->

<div class="panel" id="panel-prizepicks">
  <div class="section-head">
    <div class="section-title">PrizePicks Props</div>
    <div class="section-meta">Add to parlay slip</div>
  </div>
  <div id="pp-container"></div>
</div>

<!-- KALSHI -->

<div class="panel" id="panel-kalshi">
  <div class="section-head">
    <div class="section-title">Kalshi Prediction Markets</div>
    <div class="section-meta">Click to add to parlay slip</div>
  </div>
  <div class="kalshi-grid" id="kalshi-grid"></div>
</div>

<!-- PLAYERS -->

<div class="panel" id="panel-players">
  <div class="section-head">
    <div class="section-title">NBA Player Season Averages</div>
    <div class="section-meta">Use to evaluate prop difficulty</div>
  </div>
  <div id="players-container"></div>
</div>

</main>

<script>
var allData = null;
var parlayLegs = {};
var activeSport = 'ALL';
var activeScoreSport = 'NBA';

document.querySelectorAll('.tab').forEach(function(t){
  t.addEventListener('click', function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active')});
    document.querySelectorAll('.panel').forEach(function(x){x.classList.remove('active')});
    t.classList.add('active');
    document.getElementById('panel-' + t.dataset.panel).classList.add('active');
  });
});

async function loadData(){
  document.getElementById('loading').classList.add('show');
  try{
    var r = await fetch('/api/picks');
    allData = await r.json();
    renderAll(allData);
  }catch(e){console.error(e)}
  document.getElementById('loading').classList.remove('show');
}

function renderAll(d){
  document.getElementById('ts-bar').textContent = 'Last updated: ' + (d.timestamp||'—');
  var flat = d.all_picks||[];
  var ev   = flat.filter(function(p){return p.has_edge});
  var top  = ev.length > 0 ? Math.max.apply(null,ev.map(function(p){return p.edge})) : 0;
  document.getElementById('s-total').textContent = flat.length;
  document.getElementById('s-ev').textContent    = ev.length;
  document.getElementById('s-edge').textContent  = top > 0 ? top.toFixed(1)+'%' : '—';
  document.getElementById('s-pp').textContent    = (d.prizepicks_props||[]).length;

  renderOptimal(d.optimal_parlay_4, d.optimal_parlay_5);
  renderParlayPicks(d.sportsbook_picks||{});
  renderSportsbook(d.sportsbook_picks||{});
  renderScores(d.scores||{});
  renderPrizePicks(d.prizepicks_props||[]);
  renderKalshi(d.kalshi_markets||[]);
  renderPlayers(d.player_stats||[]);
  recalcSlip();
}

// ── OPTIMAL PARLAY ──
function renderOptimal(p4, p5){
  var c = document.getElementById('optimal-container');
  if(!p4 || !p4.legs || p4.legs.length === 0){c.innerHTML=''; return}
  function oplHtml(p, label){
    if(!p||!p.legs||p.legs.length===0) return '';
    var legs = p.legs.map(function(l){
      return '<div class="optimal-leg"><strong>'+l.ev_bet+'</strong> · '+l.win_prob+'% · <span class="risk-badge risk-'+l.risk+'">'+l.risk+'</span></div>';
    }).join('');
    var grade = '<span class="grade-badge grade-'+p.grade+'">'+p.grade+'</span>';
    return '<div class="optimal-box">'+
      '<div class="optimal-title">⚡ AI Optimal '+label+' — Grade '+grade+'</div>'+
      '<div class="optimal-legs">'+legs+'</div>'+
      '<div class="optimal-stats">'+
        '<div class="os-item"><div class="os-label">Hit Prob</div><div class="os-value green">'+p.combined_prob+'%</div></div>'+
        '<div class="os-item"><div class="os-label">Parlay Odds</div><div class="os-value amber">'+p.parlay_american+'</div></div>'+
        '<div class="os-item"><div class="os-label">EV</div><div class="os-value '+(p.has_ev?'green':'red')+'">'+p.ev+'%</div></div>'+
        '<div class="os-item"><div class="os-label">Kelly Stake</div><div class="os-value cyan">'+p.kelly_pct+'%</div></div>'+
        '<div class="os-item"><div class="os-label">Kill Legs</div><div class="os-value '+(p.kill_legs>0?'red':'green')+'">'+p.kill_legs+'</div></div>'+
      '</div>'+
      (p.warnings.length > 0 ? '<div class="slip-warnings" style="margin-top:12px">'+p.warnings.map(function(w){return '<div class="slip-warning">⚠ '+w+'</div>'}).join('')+'</div>' : '')+
    '</div>';
  }
  c.innerHTML = oplHtml(p4,'4-Leg') + oplHtml(p5,'5-Leg');
}

// ── PARLAY PICKS ──
function renderParlayPicks(bySport){
  var sports = Object.keys(bySport);
  var f = document.getElementById('parlay-sport-filter');
  f.innerHTML = '';
  var allBtn = document.createElement('button');
  allBtn.className = 'sport-btn'+(activeSport==='ALL'?' active':'');
  allBtn.textContent = 'ALL';
  allBtn.onclick = function(){activeSport='ALL'; renderParlayPicks(bySport)};
  f.appendChild(allBtn);
  sports.forEach(function(s){
    var btn = document.createElement('button');
    btn.className = 'sport-btn'+(activeSport===s?' active':'');
    btn.textContent = s;
    btn.onclick = function(){activeSport=s; renderParlayPicks(bySport)};
    f.appendChild(btn);
  });
  var grid = document.getElementById('parlay-picks-grid');
  grid.innerHTML = '';
  var picks = [];
  if(activeSport==='ALL'){
    sports.forEach(function(s){(bySport[s]||[]).forEach(function(p){picks.push(p)})});
    picks.sort(function(a,b){return b.edge-a.edge});
  } else {
    picks = bySport[activeSport]||[];
  }
  if(picks.length===0){grid.innerHTML=noData('No picks available','Set ODDS_API_KEY for live data.'); return}
  picks.forEach(function(p,i){
    grid.appendChild(makePickCard(p, i, true));
  });
}

function makePickCard(p, i, clickable){
  var aHigh = p.win_prob_a >= p.win_prob_b;
  var stars = '★'.repeat(p.stars||1)+'☆'.repeat(5-(p.stars||1));
  var sel   = parlayLegs[p.id] ? ' selected' : '';
  var card  = document.createElement('div');
  card.className = 'pick-card'+(p.has_edge?'':' no-edge')+sel;
  card.style.animationDelay = (i*0.04)+'s';
  card.dataset.id = p.id;
  card.innerHTML =
    '<div class="card-top"><div class="card-sport">'+p.sport+'</div><div class="card-source">'+p.source+'</div></div>'+
    '<div class="card-teams">'+
      '<div class="team-row"><div class="team-name'+(p.ev_bet===p.team_a?' picked':'')+'">'+p.team_a+'</div><div class="team-prob'+(aHigh?' high':'')+'">'+p.win_prob_a+'%</div></div>'+
      '<div class="team-row"><div class="team-name'+(p.ev_bet===p.team_b?' picked':'')+'">'+p.team_b+'</div><div class="team-prob'+(!aHigh?' high':'')+'">'+p.win_prob_b+'%</div></div>'+
    '</div>'+
    '<div class="card-stats">'+
      '<div class="cstat"><div class="cstat-label">Spread</div><div class="cstat-value">'+(p.spread>0?'+':'')+p.spread+'</div></div>'+
      '<div class="cstat"><div class="cstat-label">Edge</div><div class="cstat-value'+(p.edge>0?' green':'')+'">'+p.edge+'%</div></div>'+
      '<div class="cstat"><div class="cstat-label">Kelly</div><div class="cstat-value amber">'+p.kelly+'%</div></div>'+
    '</div>'+
    (p.has_edge?'<div class="ev-banner"><div><div class="ev-label">+EV Pick</div><div class="ev-pick">'+p.ev_bet+'</div></div><div class="stars">'+stars+'</div></div>':'')+
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">'+
      '<span class="risk-badge risk-'+p.risk+'">'+p.risk+' RISK</span>'+
      '<span class="game-time">'+p.game_time+'</span>'+
    '</div>';
  if(clickable){
    card.addEventListener('click', function(){toggleLeg(p)});
  }
  return card;
}

function toggleLeg(p){
  if(parlayLegs[p.id]){
    delete parlayLegs[p.id];
  } else {
    parlayLegs[p.id] = p;
  }
  document.getElementById('s-parlay').textContent = Object.keys(parlayLegs).length;
  // re-render cards to reflect selection state
  if(allData) {
    renderParlayPicks(allData.sportsbook_picks||{});
  }
  recalcSlip();
}

function clearSlip(){
  parlayLegs = {};
  document.getElementById('s-parlay').textContent = '0';
  if(allData) renderParlayPicks(allData.sportsbook_picks||{});
  recalcSlip();
}

function recalcSlip(){
  var legs = Object.values(parlayLegs);
  var container = document.getElementById('slip-legs');
  var result = document.getElementById('parlay-result');
  if(legs.length===0){
    container.innerHTML = '<div class="slip-empty">Click picks to add legs</div>';
    result.innerHTML = '';
    return;
  }
  container.innerHTML = legs.map(function(l){
    return '<div class="slip-leg">'+
      '<div class="slip-leg-name">'+l.ev_bet+'</div>'+
      '<div style="display:flex;align-items:center;gap:8px">'+
        '<span class="slip-leg-prob">'+l.win_prob+'%</span>'+
        '<span class="risk-badge risk-'+l.risk+'" style="margin-top:0">'+l.risk+'</span>'+
        '<span class="slip-leg-remove" onclick="removeLeg(\''+l.id+'\')">×</span>'+
      '</div>'+
    '</div>';
  }).join('');
  // calculate parlay client-side
  var combined = 1.0;
  var parlayDec = 1.0;
  var sportCount = {};
  legs.forEach(function(l){
    var s = l.sport||'X';
    sportCount[s] = (sportCount[s]||0)+1;
  });
  legs.forEach(function(l){
    var wp = l.win_prob/100.0;
    if((sportCount[l.sport||'X']||1)>1) wp *= 0.97;
    combined  *= wp;
    parlayDec *= (l.ev_dec||1.9);
  });
  var ev = combined*(parlayDec-1.0)-(1.0-combined);
  var kellyRaw = parlayDec > 1 ? (combined*(parlayDec-1.0)-(1.0-combined))/(parlayDec-1.0) : 0;
  var kelly = Math.max(kellyRaw*0.25, 0.0);
  var highRisk = legs.filter(function(l){return l.risk==='HIGH'});
  var grade = highRisk.length===0&&ev>0?'A':highRisk.length<=1&&ev>0?'B':highRisk.length<=2?'C':'D';
  var american = parlayDec >= 2 ? '+'+Math.round((parlayDec-1)*100) : String(Math.round(-100/(parlayDec-1)));
  var warnings = [];
  if(highRisk.length>0) warnings.push('HIGH risk legs: '+highRisk.map(function(l){return l.ev_bet}).join(', '));
  if(legs.length>6) warnings.push('7+ leg parlays hit under 2% of the time');
  if(combined<0.05) warnings.push('Under 5% combined probability');

  result.innerHTML =
    '<div class="pr-row"><span class="pr-label">Legs</span><span class="pr-value">'+legs.length+'</span></div>'+
    '<div class="pr-row"><span class="pr-label">Hit Probability</span><span class="pr-value '+(combined*100>=20?'green':'amber')+'">'+((combined*100).toFixed(2))+'%</span></div>'+
    '<div class="pr-row"><span class="pr-label">Parlay Odds</span><span class="pr-value amber">'+american+'</span></div>'+
    '<div class="pr-row"><span class="pr-label">EV</span><span class="pr-value '+(ev>0?'green':'red')+'">'+(ev*100).toFixed(2)+'%</span></div>'+
    '<div class="pr-row"><span class="pr-label">Kelly Stake</span><span class="pr-value cyan">'+((kelly*100).toFixed(2))+'% bankroll</span></div>'+
    '<div class="pr-row"><span class="pr-label">Kill Legs</span><span class="pr-value '+(highRisk.length>0?'red':'green')+'">'+highRisk.length+'</span></div>'+
    '<div class="pr-row"><span class="pr-label">Grade</span><span class="grade-badge grade-'+grade+'">'+grade+'</span></div>'+
    (warnings.length>0?'<div class="slip-warnings" style="margin-top:12px">'+warnings.map(function(w){return '<div class="slip-warning">⚠ '+w+'</div>'}).join('')+'</div>':'')+
    '<button class="btn btn-green" style="width:100%;margin-top:16px;padding:10px" onclick="clearSlip()">Clear Slip</button>';
}

function removeLeg(id){
  delete parlayLegs[id];
  document.getElementById('s-parlay').textContent = Object.keys(parlayLegs).length;
  if(allData) renderParlayPicks(allData.sportsbook_picks||{});
  recalcSlip();
}

// ── SPORTSBOOK ──
function renderSportsbook(bySport){
  var sports = Object.keys(bySport);
  var f = document.getElementById('sb-sport-filter');
  f.innerHTML = '';
  var allBtn = document.createElement('button');
  allBtn.className = 'sport-btn active';
  allBtn.textContent = 'ALL';
  allBtn.onclick = function(){
    document.querySelectorAll('#sb-sport-filter .sport-btn').forEach(function(b){b.classList.remove('active')});
    allBtn.classList.add('active');
    renderSbCards(bySport, 'ALL');
  };
  f.appendChild(allBtn);
  sports.forEach(function(s){
    var btn = document.createElement('button');
    btn.className = 'sport-btn';
    btn.textContent = s;
    btn.onclick = function(){
      document.querySelectorAll('#sb-sport-filter .sport-btn').forEach(function(b){b.classList.remove('active')});
      btn.classList.add('active');
      renderSbCards(bySport, s);
    };
    f.appendChild(btn);
  });
  renderSbCards(bySport, 'ALL');
}

function renderSbCards(bySport, sport){
  var grid = document.getElementById('sb-picks-grid');
  grid.innerHTML = '';
  var picks = [];
  if(sport==='ALL'){
    Object.values(bySport).forEach(function(arr){arr.forEach(function(p){picks.push(p)})});
    picks.sort(function(a,b){return b.edge-a.edge});
  } else {
    picks = bySport[sport]||[];
  }
  if(picks.length===0){grid.innerHTML=noData('No sportsbook data','Set ODDS_API_KEY env var for live lines.'); return}
  picks.forEach(function(p,i){grid.appendChild(makePickCard(p,i,false))});
}

// ── SCORES ──
function renderScores(scores){
  var sports = Object.keys(scores);
  var f = document.getElementById('scores-filter');
  f.innerHTML = '';
  sports.forEach(function(s,i){
    var btn = document.createElement('button');
    btn.className = 'sport-btn'+(i===0?' active':'');
    btn.textContent = s;
    btn.onclick = function(){
      document.querySelectorAll('#scores-filter .sport-btn').forEach(function(b){b.classList.remove('active')});
      btn.classList.add('active');
      renderScoreCards(scores[s], s);
    };
    f.appendChild(btn);
  });
  if(sports.length>0) renderScoreCards(scores[sports[0]], sports[0]);
  else document.getElementById('scores-grid').innerHTML = noData('No score data','ESPN scores update in real time during active slates.');
}

function renderScoreCards(games, sport){
  var grid = document.getElementById('scores-grid');
  grid.innerHTML = '';
  if(!games||games.length===0){grid.innerHTML=noData('No games','No '+sport+' games found right now.'); return}
  games.forEach(function(g,i){
    var card = document.createElement('div');
    card.className = 'score-card';
    card.style.animationDelay = (i*0.04)+'s';
    card.innerHTML =
      '<div class="score-sport">'+sport+'</div>'+
      '<div class="score-row">'+
        '<div><span class="score-team">'+g.away+'</span><span class="score-rec">'+g.away_rec+'</span></div>'+
        '<div class="score-num">'+g.away_score+'</div>'+
      '</div>'+
      '<div class="score-row">'+
        '<div><span class="score-team">'+g.home+'</span><span class="score-rec">'+g.home_rec+'</span></div>'+
        '<div class="score-num">'+g.home_score+'</div>'+
      '</div>'+
      '<div class="score-status">'+g.status+' · '+g.date+'</div>';
    grid.appendChild(card);
  });
}

// ── PRIZEPICKS ──
function renderPrizePicks(props){
  var c = document.getElementById('pp-container');
  if(props.length===0){c.innerHTML=noData('No PrizePicks data','Props appear during active game slates.'); return}
  var html = '<table class="props-table"><thead><tr><th>Player</th><th>Stat</th><th>Line</th><th>Risk</th><th>League</th><th>Time</th><th></th></tr></thead><tbody>';
  props.forEach(function(p){
    html += '<tr>'+
      '<td class="prop-player">'+p.player+'</td>'+
      '<td style="color:var(--cyan)">'+p.stat+'</td>'+
      '<td class="prop-line">'+p.line+'</td>'+
      '<td><span class="risk-badge risk-'+p.risk+'">'+p.risk+'</span></td>'+
      '<td>'+p.league+'</td>'+
      '<td style="color:var(--muted)">'+p.game_time+'</td>'+
      '<td><button class="prop-add'+(parlayLegs[p.id]?' added':'')+'" onclick="toggleProp(this,\''+p.id+'\')">'+
        (parlayLegs[p.id]?'Added':'+ Add')+
      '</button></td>'+
    '</tr>';
  });
  html += '</tbody></table>';
  c.innerHTML = html;

  // Store props for lookup
  if(!window._propMap) window._propMap = {};
  props.forEach(function(p){ window._propMap[p.id] = p; });
}

function toggleProp(btn, id){
  var p = (window._propMap||{})[id];
  if(!p) return;
  if(parlayLegs[id]){
    delete parlayLegs[id];
    btn.textContent = '+ Add';
    btn.classList.remove('added');
  } else {
    parlayLegs[id] = p;
    btn.textContent = 'Added';
    btn.classList.add('added');
  }
  document.getElementById('s-parlay').textContent = Object.keys(parlayLegs).length;
  recalcSlip();
}

// ── KALSHI ──
function renderKalshi(markets){
  var grid = document.getElementById('kalshi-grid');
  grid.innerHTML = '';
  if(!markets||markets.length===0){grid.innerHTML=noData('No Kalshi data','Sports prediction markets may not always be active.'); return}
  if(!window._kalshiMap) window._kalshiMap = {};
  markets.forEach(function(m,i){
    window._kalshiMap[m.id] = m;
    var card = document.createElement('div');
    card.className = 'kalshi-card'+(parlayLegs[m.id]?' selected':'');
    card.style.animationDelay = (i*0.04)+'s';
    card.innerHTML =
      '<div class="kalshi-title">'+m.title+'</div>'+
      '<div class="kalshi-probs">'+
        '<div class="kprob yes"><div class="kprob-label">YES</div><div class="kprob-val">'+m.yes_prob+'%</div></div>'+
        '<div class="kprob no"><div class="kprob-label">NO</div><div class="kprob-val">'+m.no_prob+'%</div></div>'+
      '</div>'+
      '<div class="kalshi-meta"><span>Vol: '+(m.volume||0).toLocaleString()+'</span><span>'+m.close_time+'</span></div>'+
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px">'+
        '<span class="risk-badge risk-'+m.risk+'">'+m.risk+' RISK</span>'+
        '<button class="prop-add'+(parlayLegs[m.id]?' added':'')+'" onclick="toggleKalshi(\''+m.id+'\')">'+
          (parlayLegs[m.id]?'Added':'+ Add to Slip')+
        '</button>'+
      '</div>';
    grid.appendChild(card);
  });
}

function toggleKalshi(id){
  var m = (window._kalshiMap||{})[id];
  if(!m) return;
  if(parlayLegs[id]){
    delete parlayLegs[id];
  } else {
    parlayLegs[id] = m;
  }
  document.getElementById('s-parlay').textContent = Object.keys(parlayLegs).length;
  renderKalshi(Object.values(window._kalshiMap||{}));
  recalcSlip();
}

// ── PLAYERS ──
function renderPlayers(players){
  if(!players||players.length===0){
    document.getElementById('players-container').innerHTML = noData('No player data','ESPN player stats load during the NBA season.');
    return;
  }
  var html = '<table class="stats-table"><thead><tr><th>#</th><th>Player</th><th>Team</th><th>PPG</th><th>RPG</th><th>APG</th><th>Prop Guide</th></tr></thead><tbody>';
  players.forEach(function(p,i){
    var guide = p.avg_pts >= 20 ? 'Elite scorer — 20+ pts prop is realistic' :
                p.avg_pts >= 15 ? 'Mid-range — 20+ pts is HIGH RISK' :
                'Light scorer — any pts prop > 15 is HIGH RISK';
    var guideColor = p.avg_pts >= 20 ? 'var(--green)' : p.avg_pts >= 15 ? 'var(--amber)' : 'var(--red)';
    html += '<tr>'+
      '<td style="color:var(--muted)">'+(i+1)+'</td>'+
      '<td class="pname">'+p.name+'</td>'+
      '<td style="color:var(--muted)">'+p.team+'</td>'+
      '<td class="pts">'+p.avg_pts+'</td>'+
      '<td>'+p.avg_reb+'</td>'+
      '<td>'+p.avg_ast+'</td>'+
      '<td style="color:'+guideColor+';font-size:11px">'+guide+'</td>'+
    '</tr>';
  });
  html += '</tbody></table>';
  document.getElementById('players-container').innerHTML = html;
}

function noData(title, sub){
  return '<div class="no-data"><div class="no-data-title">'+title+'</div><div class="no-data-sub">'+sub+'</div></div>';
}

loadData();
setInterval(loadData, 120000);
</script>

</body>
</html>'''

LOGIN_HTML = ‘’’<!DOCTYPE html>

<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PropNinjaBot</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@800&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#060a0f;font-family:'JetBrains Mono',monospace;min-height:100vh;display:flex;align-items:center;justify-content:center;background-image:radial-gradient(ellipse 60% 40% at 50% 0%,rgba(0,255,136,.05) 0%,transparent 60%)}
.box{width:340px;padding:40px;background:#0b1118;border:1px solid #1a2a3a}
.logo{font-family:'Syne',sans-serif;font-size:22px;font-weight:800;color:#f0f6fc;margin-bottom:6px}
.logo span{color:#00ff88}
.sub{font-size:11px;color:#4a5568;margin-bottom:28px;text-transform:uppercase;letter-spacing:1px}
input{width:100%;padding:10px 14px;background:#111a24;border:1px solid #1a2a3a;color:#c9d1d9;font-family:'JetBrains Mono',monospace;font-size:13px;outline:none;margin-bottom:12px}
input:focus{border-color:#00ff88}
button{width:100%;padding:10px;background:#00ff88;border:none;color:#060a0f;font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:2px;cursor:pointer}
button:hover{opacity:.85}
.err{font-size:11px;color:#ff4466;margin-top:10px}
</style>
</head>
<body>
<div class="box">
  <div class="logo">Prop<span>Ninja</span>Bot</div>
  <div class="sub">Private Access</div>
  <form method="POST" action="/login">
    <input type="password" name="password" placeholder="Access password..." autofocus>
    <button type="submit">Enter</button>
    {% if error %}<div class="err">Incorrect password.</div>{% endif %}
  </form>
</div>
</body>
</html>'''

# ─────────────────────────────────────────────────────────────────────────────

# FLASK ROUTES

# ─────────────────────────────────────────────────────────────────────────────

app = Flask(‘propninjabot’)
app.secret_key = SECRET_KEY

@app.route(’/login’, methods=[‘GET’, ‘POST’])
def login():
if request.method == ‘POST’:
if request.form.get(‘password’,’’) == ACCESS_PASSWORD:
session[‘auth’] = True
return redirect(’/’)
return render_template_string(LOGIN_HTML, error=True)
return render_template_string(LOGIN_HTML, error=False)

@app.route(’/logout’)
def logout():
session.clear()
return redirect(’/login’)

@app.route(’/’)
def index():
if not session.get(‘auth’):
return redirect(’/login’)
return render_template_string(DASHBOARD_HTML)

@app.route(’/api/picks’)
def api_picks():
if not session.get(‘auth’):
return jsonify({‘error’: ‘unauthorized’}), 401
return jsonify(get_all_data())

@app.route(’/api/parlay’, methods=[‘POST’])
def api_parlay():
if not session.get(‘auth’):
return jsonify({‘error’: ‘unauthorized’}), 401
legs = request.json.get(‘legs’, [])
return jsonify(calculate_parlay(legs))

@app.route(’/health’)
def health():
return jsonify({‘status’: ‘ok’}), 200

# ─────────────────────────────────────────────────────────────────────────────

# MAIN

# ─────────────────────────────────────────────────────────────────────────────

if **name** == ‘**main**’:
log.info(‘PropNinjaBot v2 starting on port %s’, PORT)
app.run(host=‘0.0.0.0’, port=PORT, debug=False)
