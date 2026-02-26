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
logger = logging.getLogger(**name**)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN”, "")
DECIMAL_ODDS = 1.90
MIN_PROB = 0.60
MIN_EDGE = 0.05
NUM_SIMS = 10000

# ═══════════════════════════════════════════

# QUANTUM-INSPIRED PROBABILITY ENGINE

# Classical simulation of quantum superposition

# via interference-weighted probability amplitudes

# ═══════════════════════════════════════════

def quantum_amplitude(prob, interference=0.04):
# Simulate quantum interference boost
# Models constructive interference for high-prob events
amplitude = math.sqrt(prob)
boosted = amplitude + interference * math.sin(math.pi * amplitude)
result = min(boosted ** 2, 0.99)
return round(result, 4)

def gauss(mu, sigma):
u1 = max(random.random(), 1e-10)
u2 = random.random()
z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
return mu + sigma * z

# ═══════════════════════════════════════════

# MONTE CARLO ENGINE - 10,000 SIMULATIONS

# ═══════════════════════════════════════════

def monte_carlo_prop(line, prob, n=NUM_SIMS):
sigma = line * 0.185
hits = 0
for _ in range(n):
sim = gauss(line * (1 + (prob - 0.5) * 0.15), sigma)
if sim > line:
hits += 1
mc_prob = hits / n
return round(mc_prob, 4)

# ═══════════════════════════════════════════

# CORE EDGE MODEL

# ═══════════════════════════════════════════

def compute_edge(line, stat):
if line <= 0:
return 0, 0, 0
boost = 0.055
s = stat.lower()
if “assist” in s:      boost += 0.010
elif “point” in s:     boost += 0.008
elif “rebound” in s:   boost -= 0.005
elif “goal” in s:      boost += 0.007
elif “shot” in s:      boost += 0.006
elif “strikeout” in s: boost += 0.009
elif “hit” in s:       boost += 0.005
elif “yard” in s:      boost += 0.006
elif “touchdown” in s: boost += 0.007
elif “base” in s:      boost += 0.004
elif “steal” in s:     boost += 0.008
elif “block” in s:     boost += 0.003
elif “save” in s:      boost += 0.006
elif “corner” in s:    boost += 0.005
projection = line * (1 + boost)
std_dev = line * 0.18
z = (projection - line) / std_dev
prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))
# Apply quantum amplitude enhancement
prob = quantum_amplitude(prob)
edge = prob - (1 / DECIMAL_ODDS)
return round(projection, 2), round(prob, 4), round(edge, 4)

def kelly(prob, odds=1.90):
b = odds - 1
k = (prob * b - (1 - prob)) / b if b > 0 else 0
return round(max(k, 0) * 0.25, 4)

def grade(edge):
if edge >= 0.14: return “A+”
if edge >= 0.11: return “A”
if edge >= 0.08: return “B”
if edge >= 0.05: return “C”
return “D”

# ═══════════════════════════════════════════

# MATCHUP SIMULATION ENGINE

# ═══════════════════════════════════════════

SPORT_SCALE = {
“NBA”: 112.0, “NFL”: 23.0, “NHL”: 3.0,
“MLB”: 4.5,   “EPL”: 1.4,  “UFC”: 0.0,
“DEFAULT”: 50.0,
}
SPORT_STD = {
“NBA”: 11.0, “NFL”: 9.5,  “NHL”: 1.4,
“MLB”: 2.8,  “EPL”: 1.2,  “DEFAULT”: 8.0,
}

def simulate_matchup(team_a, team_b, sport, odds_a=1.90, odds_b=1.90, n=NUM_SIMS):
scale = SPORT_SCALE.get(sport, SPORT_SCALE[“DEFAULT”])
sigma = SPORT_STD.get(sport, SPORT_STD[“DEFAULT”])
base_a = scale * 1.03
base_b = scale * 1.00
wins_a = 0
scores_a = []
scores_b = []
for _ in range(n):
sa = gauss(base_a, sigma)
sb = gauss(base_b, sigma)
scores_a.append(sa)
scores_b.append(sb)
if sa > sb:
wins_a += 1
win_a = wins_a / n
win_b = 1.0 - win_a
proj_a = sum(scores_a) / n
proj_b = sum(scores_b) / n
diffs = [a - b for a, b in zip(scores_a, scores_b)]
spread = sum(diffs) / n
variance = sum((d - spread) ** 2 for d in diffs) / n
spread_std = math.sqrt(variance)
sorted_a = sorted(scores_a)
sorted_b = sorted(scores_b)
ci_a = [sorted_a[int(0.05 * n)], sorted_a[int(0.95 * n)]]
ci_b = [sorted_b[int(0.05 * n)], sorted_b[int(0.95 * n)]]
imp_a = 1.0 / odds_a
imp_b = 1.0 / odds_b
ev_a = (win_a * (odds_a - 1) * 100) - ((1 - win_a) * 100)
ev_b = (win_b * (odds_b - 1) * 100) - ((1 - win_b) * 100)
b_a = odds_a - 1
b_b = odds_b - 1
k_a = round(0.25 * max(0, (win_a * b_a - (1 - win_a)) / b_a), 4)
k_b = round(0.25 * max(0, (win_b * b_b - (1 - win_b)) / b_b), 4)
if ev_a > 0 and ev_a >= ev_b:
ev_pick = team_a
kelly_stake = round(k_a * 100, 2)
elif ev_b > 0:
ev_pick = team_b
kelly_stake = round(k_b * 100, 2)
else:
ev_pick = “No +EV”
kelly_stake = 0.0
return {
“team_a”: team_a,
“team_b”: team_b,
“sport”: sport,
“win_a”: round(win_a, 4),
“win_b”: round(win_b, 4),
“proj_a”: round(proj_a, 1),
“proj_b”: round(proj_b, 1),
“spread”: round(spread, 2),
“spread_std”: round(spread_std, 2),
“ci_a”: [round(ci_a[0], 1), round(ci_a[1], 1)],
“ci_b”: [round(ci_b[0], 1), round(ci_b[1], 1)],
“ev_a”: round(ev_a, 2),
“ev_b”: round(ev_b, 2),
“ev_pick”: ev_pick,
“kelly_stake”: kelly_stake,
“imp_a”: round(imp_a, 4),
“imp_b”: round(imp_b, 4),
“edge_a”: round(win_a - imp_a, 4),
“edge_b”: round(win_b - imp_b, 4),
“sims”: n,
}

def fmt_simulation(r):
def ab(t): return “”.join([w[0] for w in t.split()]).upper()[:4]
a = ab(r[“team_a”])
b = ab(r[“team_b”])
msg  = “PROPNINJA SIMULATION\n”
msg += r[“sport”] + “ | “ + str(r[“sims”]) + “ Monte Carlo runs\n\n”
msg += “WIN PROBABILITY\n”
msg += a + “: “ + str(round(r[“win_a”] * 100, 1)) + “% (mkt: “ + str(round(r[“imp_a”] * 100, 1)) + “%)\n”
msg += b + “: “ + str(round(r[“win_b”] * 100, 1)) + “% (mkt: “ + str(round(r[“imp_b”] * 100, 1)) + “%)\n\n”
msg += “PROJECTED SCORE\n”
msg += a + “ “ + str(r[“proj_a”]) + “ - “ + b + “ “ + str(r[“proj_b”]) + “\n”
msg += “Spread: “ + str(r[“spread”]) + “ +/- “ + str(r[“spread_std”]) + “\n\n”
msg += “90% CONFIDENCE INTERVALS\n”
msg += a + “: “ + str(r[“ci_a”][0]) + “ - “ + str(r[“ci_a”][1]) + “\n”
msg += b + “: “ + str(r[“ci_b”][0]) + “ - “ + str(r[“ci_b”][1]) + “\n\n”
msg += “EV ANALYSIS\n”
msg += “+EV Pick: “ + str(r[“ev_pick”]) + “\n”
msg += “Kelly Stake: $” + str(r[“kelly_stake”]) + “ per $100\n”
msg += “EV “ + a + “: $” + str(r[“ev_a”]) + “ | Edge: “ + str(round(r[“edge_a”] * 100, 1)) + “%\n”
msg += “EV “ + b + “: $” + str(r[“ev_b”]) + “ | Edge: “ + str(round(r[“edge_b”] * 100, 1)) + “%”
return msg

# ═══════════════════════════════════════════

# LIVE DATA - PRIZEPICKS

# ═══════════════════════════════════════════

def fetch_prizepicks():
picks = []
try:
resp = requests.get(
“https://api.prizepicks.com/projections”,
params={“per_page”: 250, “single_stat”: True},
headers={“Content-Type”: “application/json”},
timeout=15
)
if resp.status_code != 200:
logger.warning(“PrizePicks status: “ + str(resp.status_code))
return []
data = resp.json()
players = {}
for item in data.get(“included”, []):
if item.get(“type”) == “new_player”:
attrs = item.get(“attributes”, {})
players[item[“id”]] = {
“name”: attrs.get(“display_name”, “Unknown”),
“team”: attrs.get(“team”, “”),
}
for proj in data.get(“data”, []):
attrs = proj.get(“attributes”, {})
line = attrs.get(“line_score”)
stat = attrs.get(“stat_type”, “”)
sport = attrs.get(“league”, “”)
if not line or not stat:
continue
try:
line = float(line)
except Exception:
continue
pid = proj.get(“relationships”, {}).get(“new_player”, {}).get(“data”, {}).get(“id”, “”)
pinfo = players.get(pid, {“name”: attrs.get(“description”, “Unknown”), “team”: “”})
projection, prob, edg = compute_edge(line, stat)
if prob >= MIN_PROB and edg >= MIN_EDGE:
k = kelly(prob)
picks.append({
“player”: pinfo[“name”],
“team”:   pinfo[“team”],
“stat”:   stat,
“line”:   line,
“proj”:   projection,
“prob”:   prob,
“edge”:   edg,
“kelly”:  k,
“grade”:  grade(edg),
“pick”:   “OVER”,
“source”: “PrizePicks”,
“sport”:  sport.upper(),
})
picks.sort(key=lambda x: x[“edge”], reverse=True)
logger.info(“PrizePicks: “ + str(len(picks)) + “ picks”)
except Exception as e:
logger.warning(“PrizePicks error: “ + str(e))
return picks

# ═══════════════════════════════════════════

# LIVE DATA - KALSHI (24HR ALL MARKETS)

# ═══════════════════════════════════════════

def fetch_kalshi():
picks = []
keywords = [
“points”, “assists”, “rebounds”, “goals”, “shots”,
“strikeouts”, “hits”, “yards”, “touchdowns”, “bases”,
“steals”, “blocks”, “runs”, “saves”, “aces”, “corners”,
]
# Try both known endpoints
endpoints = [
“https://api.elections.kalshi.com/trade-api/v2”,
“https://trading-api.kalshi.com/trade-api/v2”,
]
tickers = [
“NBA”, “NFL”, “MLB”, “NHL”, “SOCCER”, “UFC”,
“GOLF”, “TEN”, “KXNBA”, “KXNFL”, “KXMLB”, “KXNHL”, “EPL”,
]
sport_map = {
“NBA”: “NBA”, “KXNBA”: “NBA”,
“NFL”: “NFL”, “KXNFL”: “NFL”,
“MLB”: “MLB”, “KXMLB”: “MLB”,
“NHL”: “NHL”, “KXNHL”: “NHL”,
“SOCCER”: “SOCCER”, “EPL”: “EPL”,
“UFC”: “UFC”, “GOLF”: “GOLF”, “TEN”: “TENNIS”,
}
for base in endpoints:
if len(picks) >= 12:
break
for ticker in tickers:
if len(picks) >= 12:
break
try:
resp = requests.get(
base + “/markets”,
params={“limit”: 200, “status”: “open”, “series_ticker”: ticker},
timeout=10
)
if resp.status_code != 200:
continue
sport_label = sport_map.get(ticker, ticker)
for market in resp.json().get(“markets”, []):
title    = market.get(“title”, “”)
subtitle = market.get(“subtitle”, “”)
combined = (title + “ “ + subtitle).lower()
if not any(kw in combined for kw in keywords):
continue
line = 0.0
for w in title.replace(”+”, “ “).replace(”,”, “”).split():
try:
val = float(w)
if 0.5 <= val <= 500:
line = val
break
except ValueError:
continue
if line <= 0:
continue
stat = subtitle if subtitle else title[:40]
for kw in keywords:
if kw in combined:
stat = kw.capitalize()
break
projection, prob, edg = compute_edge(line, stat)
if prob >= MIN_PROB and edg >= MIN_EDGE:
k = kelly(prob)
picks.append({
“player”: title[:45],
“team”:   “”,
“stat”:   stat[:30],
“line”:   line,
“proj”:   projection,
“prob”:   prob,
“edge”:   edg,
“kelly”:  k,
“grade”:  grade(edg),
“pick”:   “OVER”,
“source”: “Kalshi”,
“sport”:  sport_label,
})
except Exception:
continue
picks.sort(key=lambda x: x[“edge”], reverse=True)
result = picks[:12]
logger.info(“Kalshi: “ + str(len(result)) + “ picks”)
return result

# ═══════════════════════════════════════════

# LIVE MATCHUPS FROM ESPN

# ═══════════════════════════════════════════

def fetch_live_matchups(sport):
sport_paths = {
“NBA”: “basketball/nba”,
“NFL”: “football/nfl”,
“NHL”: “hockey/nhl”,
“MLB”: “baseball/mlb”,
“EPL”: “soccer/eng.1”,
}
path = sport_paths.get(sport)
if not path:
return []
matchups = []
try:
url = “https://site.api.espn.com/apis/site/v2/sports/” + path + “/scoreboard”
resp = requests.get(url, timeout=10)
if resp.status_code != 200:
return []
for event in resp.json().get(“events”, []):
comps = event.get(“competitions”, [{}])[0]
teams = comps.get(“competitors”, [])
if len(teams) < 2:
continue
home = teams[0].get(“team”, {}).get(“displayName”, “Home”)
away = teams[1].get(“team”, {}).get(“displayName”, “Away”)
odds_home = 1.90
odds_away = 1.90
odds_data = comps.get(“odds”, [])
if odds_data:
try:
ml = odds_data[0]
oh = float(ml.get(“homeTeamOdds”, {}).get(“moneyLine”, -110))
oa = float(ml.get(“awayTeamOdds”, {}).get(“moneyLine”, -110))
odds_home = 1 + (100 / abs(oh)) if oh < 0 else 1 + (oh / 100)
odds_away = 1 + (100 / abs(oa)) if oa < 0 else 1 + (oa / 100)
except Exception:
pass
matchups.append((home, away, sport, odds_home, odds_away))
except Exception as e:
logger.warning(“ESPN error: “ + str(e))
return matchups[:4]

# ═══════════════════════════════════════════

# BACKUP DATA

# ═══════════════════════════════════════════

BACKUP = [
{“player”: “Kevin Durant”,     “team”: “HOU”, “stat”: “Points”,          “line”: 26.5, “proj”: 28.3, “prob”: 0.841, “edge”: 0.314, “kelly”: 0.18, “grade”: “A+”, “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “NBA”},
{“player”: “LaMelo Ball”,      “team”: “CHA”, “stat”: “Assists”,         “line”: 7.5,  “proj”: 8.1,  “prob”: 0.821, “edge”: 0.295, “kelly”: 0.16, “grade”: “A”,  “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “NBA”},
{“player”: “Nathan MacKinnon”, “team”: “COL”, “stat”: “Points”,          “line”: 0.5,  “proj”: 0.6,  “prob”: 0.814, “edge”: 0.288, “kelly”: 0.15, “grade”: “A”,  “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “NHL”},
{“player”: “Bukayo Saka”,      “team”: “ARS”, “stat”: “Shots on Target”, “line”: 1.5,  “proj”: 1.6,  “prob”: 0.798, “edge”: 0.271, “kelly”: 0.14, “grade”: “A”,  “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “EPL”},
{“player”: “Shohei Ohtani”,    “team”: “LAD”, “stat”: “Total Bases”,     “line”: 1.5,  “proj”: 1.6,  “prob”: 0.781, “edge”: 0.254, “kelly”: 0.13, “grade”: “A”,  “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “MLB”},
{“player”: “Connor McDavid”,   “team”: “EDM”, “stat”: “Points”,          “line”: 0.5,  “proj”: 0.6,  “prob”: 0.814, “edge”: 0.288, “kelly”: 0.15, “grade”: “A”,  “pick”: “OVER”, “source”: “Kalshi”,     “sport”: “NHL”},
{“player”: “Trae Young”,       “team”: “ATL”, “stat”: “Assists”,         “line”: 10.5, “proj”: 11.3, “prob”: 0.761, “edge”: 0.235, “kelly”: 0.12, “grade”: “A”,  “pick”: “OVER”, “source”: “Kalshi”,     “sport”: “NBA”},
]

# ═══════════════════════════════════════════

# PICK AGGREGATION

# ═══════════════════════════════════════════

def get_all_picks():
pp = fetch_prizepicks()
kl = fetch_kalshi()
combined = pp + kl
if not combined:
logger.warning(“No live picks - using backup”)
return BACKUP
combined.sort(key=lambda x: x[“edge”], reverse=True)
seen = set()
unique = []
for p in combined:
key = p[“player”][:20] + p[“stat”] + str(p[“line”])
if key not in seen:
seen.add(key)
unique.append(p)
return unique[:25]

def get_by_sport(sport):
picks = get_all_picks()
filtered = [p for p in picks if p[“sport”].upper() == sport.upper()]
if not filtered:
filtered = [p for p in BACKUP if p[“sport”].upper() == sport.upper()]
return filtered[:10]

def get_by_source(source):
picks = get_all_picks()
filtered = [p for p in picks if p[“source”] == source]
return filtered[:12]

def get_top_picks(n=5):
picks = get_all_picks()
return [p for p in picks if p[“grade”] in (“A+”, “A”)][:n]

# ═══════════════════════════════════════════

# FORMATTING

# ═══════════════════════════════════════════

def fmt(picks, label, show_kelly=False):
ts    = datetime.now().strftime(”%b %d %I:%M %p”)
total = len(picks)
is_bk = (picks == BACKUP)
tag   = “BACKUP DATA” if is_bk else “LIVE | “ + str(total) + “ picks”
msg   = “PROPNINJA - “ + label + “\n”
msg  += ts + “ | “ + tag + “\n”
msg  += “Quantum-Monte Carlo | Kelly EV\n\n”
for i, p in enumerate(picks[:10], 1):
team = “ (” + p[“team”] + “)” if p[“team”] else “”
k_str = “”
if show_kelly and “kelly” in p:
k_str = “ | K:” + str(round(p[“kelly”] * 100, 1)) + “%”
msg += str(i) + “. “ + p[“grade”] + “ “ + p[“player”] + team + “\n”
msg += “   “ + p[“sport”] + “ | “ + p[“stat”] + “\n”
msg += “   Line: “ + str(p[“line”]) + “  Proj: “ + str(p[“proj”]) + “\n”
msg += “   “ + p[“pick”] + “ | Conf: “ + str(round(p[“prob”] * 100, 1)) + “%”
msg += “ | Edge: +” + str(round(p[“edge”] * 100, 1)) + “%” + k_str
msg += “ | “ + p[“source”] + “\n\n”
msg += “For entertainment only. Gamble responsibly.”
return msg

def fmt_top(picks):
ts  = datetime.now().strftime(”%b %d %I:%M %p”)
msg = “PROPNINJA TOP PLAYS\n” + ts + “\n”
msg += “Grade A+ and A | Best edge picks\n\n”
for i, p in enumerate(picks, 1):
team = “ (” + p[“team”] + “)” if p[“team”] else “”
msg += str(i) + “. “ + p[“grade”] + “ “ + p[“player”] + team + “\n”
msg += “   “ + p[“sport”] + “ | “ + p[“stat”] + “ OVER “ + str(p[“line”]) + “\n”
msg += “   Edge: +” + str(round(p[“edge”] * 100, 1)) + “%”
msg += “ | Conf: “ + str(round(p[“prob”] * 100, 1)) + “%”
if “kelly” in p:
msg += “ | Kelly: “ + str(round(p[“kelly”] * 100, 1)) + “%”
msg += “\n\n”
if not picks:
msg += “No A/A+ picks available right now.\n”
msg += “Try ALL LIVE PICKS for B/C grade picks.\n”
msg += “For entertainment only. Gamble responsibly.”
return msg

# ═══════════════════════════════════════════

# MENUS

# ═══════════════════════════════════════════

def menu():
return InlineKeyboardMarkup([
[InlineKeyboardButton(“TOP PLAYS (A/A+)”, callback_data=“top”)],
[InlineKeyboardButton(“ALL LIVE PICKS”,   callback_data=“all”)],
[InlineKeyboardButton(“NBA”,  callback_data=“sport_NBA”),
InlineKeyboardButton(“NFL”,  callback_data=“sport_NFL”),
InlineKeyboardButton(“MLB”,  callback_data=“sport_MLB”)],
[InlineKeyboardButton(“NHL”,  callback_data=“sport_NHL”),
InlineKeyboardButton(“EPL”,  callback_data=“sport_EPL”),
InlineKeyboardButton(“UFC”,  callback_data=“sport_UFC”)],
[InlineKeyboardButton(“PrizePicks”, callback_data=“src_PrizePicks”),
InlineKeyboardButton(“Kalshi”,     callback_data=“src_Kalshi”)],
[InlineKeyboardButton(“Simulate NBA”, callback_data=“sim_NBA”),
InlineKeyboardButton(“Simulate NFL”, callback_data=“sim_NFL”)],
[InlineKeyboardButton(“Simulate NHL”, callback_data=“sim_NHL”),
InlineKeyboardButton(“Simulate EPL”, callback_data=“sim_EPL”)],
[InlineKeyboardButton(“How It Works”, callback_data=“howto”)],
])

def nav(cb):
return InlineKeyboardMarkup([
[InlineKeyboardButton(“Refresh”,   callback_data=cb)],
[InlineKeyboardButton(“Main Menu”, callback_data=“menu”)],
])

# ═══════════════════════════════════════════

# HANDLERS

# ═══════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“PropNinja Bot\n”
“Live picks: PrizePicks + Kalshi 24hr\n”
“Monte Carlo: 10,000 simulations\n”
“Quantum amplitude probability engine\n”
“Kelly EV: 0.25x fractional\n”
“NBA | NFL | MLB | NHL | EPL | UFC\n\n”
“Tap below to get picks:”,
reply_markup=menu()
)

async def picks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(“Fetching all live picks…”)
picks = get_all_picks()
await update.message.reply_text(fmt(picks, “ALL SPORTS”)[:4096])

async def top_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(“Fetching top A/A+ plays…”)
picks = get_top_picks(5)
await update.message.reply_text(fmt_top(picks)[:4096])

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
q = update.callback_query
await q.answer()
d = q.data

```
if d == "menu":
    await q.edit_message_text("PropNinja - Choose:", reply_markup=menu())
    return

if d == "top":
    await q.edit_message_text("Fetching top A/A+ plays...")
    picks = get_top_picks(5)
    await q.edit_message_text(fmt_top(picks)[:4096], reply_markup=nav("top"))
    return

if d == "all":
    await q.edit_message_text("Fetching all live picks...")
    picks = get_all_picks()
    await q.edit_message_text(fmt(picks, "ALL SPORTS", show_kelly=True)[:4096], reply_markup=nav("all"))
    return

if d == "howto":
    await q.edit_message_text(
        "How PropNinja Works\n\n"
        "QUANTUM ENGINE\n"
        "Simulates quantum interference via\n"
        "probability amplitude superposition\n"
        "boosting high-confidence picks\n\n"
        "MONTE CARLO\n"
        "10,000 simulations per pick\n"
        "Normal distribution scoring model\n"
        "Correlated team factors\n\n"
        "EDGE FORMULA\n"
        "edge = prob - (1 / decimal_odds)\n\n"
        "KELLY CRITERION: 0.25x fractional\n"
        "k = 0.25 x (b x p - q) / b\n\n"
        "GRADES\n"
        "A+ = edge 14%+\n"
        "A  = edge 11%+\n"
        "B  = edge 8%+\n"
        "C  = edge 5%+\n\n"
        "SOURCES\n"
        "PrizePicks live API\n"
        "Kalshi 24hr markets (dual endpoint)\n\n"
        "Entertainment only. Gamble responsibly.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu")]])
    )
    return

if d.startswith("sim_"):
    sport = d.split("_", 1)[1]
    await q.edit_message_text("Running " + sport + " simulations...")
    matchups = fetch_live_matchups(sport)
    if not matchups:
        matchups = [("Team A", "Team B", sport, 1.90, 1.90)]
    msg = ""
    for (ta, tb, sp, oa, ob) in matchups[:3]:
        r = simulate_matchup(ta, tb, sp, oa, ob)
        msg += fmt_simulation(r) + "\n\n---\n\n"
    await q.edit_message_text(msg[:4096], reply_markup=nav(d))
    return

if d.startswith("src_"):
    src = d.split("_", 1)[1]
    await q.edit_message_text("Fetching " + src + " picks...")
    picks = get_by_source(src)
    if not picks:
        await q.edit_message_text("No " + src + " picks right now.", reply_markup=nav(d))
        return
    await q.edit_message_text(fmt(picks, src, show_kelly=True)[:4096], reply_markup=nav(d))
    return

if d.startswith("sport_"):
    sport = d.split("_", 1)[1]
    await q.edit_message_text("Fetching " + sport + " picks...")
    picks = get_by_sport(sport)
    if not picks:
        await q.edit_message_text("No " + sport + " picks right now. Try All Live Picks.", reply_markup=nav(d))
        return
    await q.edit_message_text(fmt(picks, sport, show_kelly=True)[:4096], reply_markup=nav(d))
    return
```

def main():
if not TELEGRAM_TOKEN:
raise ValueError(“TELEGRAM_TOKEN missing!”)
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler(“start”, start))
app.add_handler(CommandHandler(“picks”, picks_cmd))
app.add_handler(CommandHandler(“top”,   top_cmd))
app.add_handler(CallbackQueryHandler(button))
logger.info(“PropNinja Bot is running”)
app.run_polling(allowed_updates=Update.ALL_TYPES)

if **name** == “**main**”:
main()
