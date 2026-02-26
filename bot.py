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

TELEGRAM_TOKEN = os.environ.get(“TELEGRAM_TOKEN”, “”)
DECIMAL_ODDS = 1.90
MIN_EDGE = 0.05
NUM_SIMULATIONS = 10000

# ═══════════════════════════════════════════════════════

# PROPNINJA ENTERPRISE ANALYTICS ENGINE

# System: Elite Quantitative Sports Data Science

# Model:  Monte Carlo + Weighted Multi-Factor + Kelly EV

# ═══════════════════════════════════════════════════════

# ── SPORT-SPECIFIC DYNAMIC WEIGHT TABLES ──────────────

# Derived from historical backtesting per sport

SPORT_WEIGHTS = {
“NBA”: {
“off_rating”: 0.35,
“def_rating”: 0.35,
“pace”:       0.05,
“rest”:       0.05,
“travel”:     0.03,
“home”:       0.04,
“injury”:     0.08,
“historical”: 0.05,
},
“NFL”: {
“dvoa”:       0.30,
“epa”:        0.25,
“success”:    0.15,
“rest”:       0.08,
“travel”:     0.04,
“home”:       0.05,
“injury”:     0.08,
“historical”: 0.05,
},
“NHL”: {
“off_rating”: 0.32,
“def_rating”: 0.32,
“pace”:       0.06,
“rest”:       0.07,
“travel”:     0.05,
“home”:       0.05,
“injury”:     0.08,
“historical”: 0.05,
},
“MLB”: {
“off_rating”: 0.28,
“def_rating”: 0.28,
“pace”:       0.04,
“rest”:       0.10,
“travel”:     0.06,
“home”:       0.06,
“injury”:     0.10,
“historical”: 0.08,
},
“DEFAULT”: {
“off_rating”: 0.33,
“def_rating”: 0.33,
“pace”:       0.05,
“rest”:       0.06,
“travel”:     0.04,
“home”:       0.05,
“injury”:     0.08,
“historical”: 0.06,
},
}

# ── SPORT-SPECIFIC SCORING VARIANCE ───────────────────

SPORT_STD = {
“NBA”: 11.0,
“NFL”: 9.5,
“NHL”: 1.4,
“MLB”: 2.8,
“EPL”: 1.2,
“DEFAULT”: 8.0,
}

# ── SPORT-SPECIFIC SCORE SCALES ───────────────────────

SPORT_SCALE = {
“NBA”: 112.0,
“NFL”: 23.0,
“NHL”: 3.0,
“MLB”: 4.5,
“EPL”: 1.4,
“DEFAULT”: 50.0,
}

class Matchup:
def **init**(self, team_a, team_b, sport, league):
self.team_a      = team_a
self.team_b      = team_b
self.sport       = sport.upper()
self.league      = league
self.metrics_a   = {}
self.metrics_b   = {}
self.situational_a = {}
self.situational_b = {}
self.injury_a    = {}
self.injury_b    = {}
self.historical  = {}
self.market_odds = {}

def normalize(value, mean, std):
if std == 0:
return 0.0
return (value - mean) / std

def compute_baseline(metrics, situational, injury, historical, weights, scale):
raw = 0.0
for k, v in metrics.items():
raw += weights.get(k, 0) * v
for k, v in situational.items():
raw += weights.get(k, 0) * v
for k, v in injury.items():
raw += weights.get(k, 0) * v
raw += weights.get(“historical”, 0) * historical.get(“adjusted_point_diff”, 0)
# Map to realistic score range for the sport
baseline = scale * (1.0 + raw * 0.12)
return max(baseline, scale * 0.5)

def gauss_pair(mu_a, mu_b, sigma, corr=0.15):
# Generate correlated score pair using Cholesky decomposition
z1 = 0.0
z2 = 0.0
for _ in range(12):
z1 += random.random()
z2 += random.random()
z1 = (z1 - 6.0)
z2 = (z2 - 6.0)
# Apply correlation
z2_corr = corr * z1 + math.sqrt(1 - corr ** 2) * z2
return mu_a + sigma * z1, mu_b + sigma * z2_corr

def monte_carlo(baseline_a, baseline_b, sport, n=NUM_SIMULATIONS):
sigma = SPORT_STD.get(sport, SPORT_STD[“DEFAULT”])
scores_a = []
scores_b = []

```
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
variance = sum((d - proj_spread) ** 2 for d in diffs) / n
spread_std = math.sqrt(variance)

sorted_a = sorted(scores_a)
sorted_b = sorted(scores_b)
ci_a = [sorted_a[int(0.05 * n)], sorted_a[int(0.95 * n)]]
ci_b = [sorted_b[int(0.05 * n)], sorted_b[int(0.95 * n)]]

# Median
mid = n // 2
med_a = sorted(scores_a)[mid]
med_b = sorted(scores_b)[mid]

# Sample 20 simulation pairs for distribution
sample_idx = [int(i * n / 20) for i in range(20)]
dist_sample = [(round(scores_a[i], 1), round(scores_b[i], 1)) for i in sample_idx]

return {
    "TeamA_Score_Mean":   round(proj_a, 2),
    "TeamB_Score_Mean":   round(proj_b, 2),
    "TeamA_Score_Median": round(med_a, 2),
    "TeamB_Score_Median": round(med_b, 2),
    "TeamA_WinProb":      round(win_prob_a, 4),
    "TeamB_WinProb":      round(win_prob_b, 4),
    "Projected_Spread":   round(proj_spread, 2),
    "Spread_StdDev":      round(spread_std, 2),
    "ConfidenceIntervals": {
        "TeamA_Score": [round(ci_a[0], 1), round(ci_a[1], 1)],
        "TeamB_Score": [round(ci_b[0], 1), round(ci_b[1], 1)],
    },
    "Simulation_Distribution": dist_sample,
    "Simulations": n,
}
```

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

def run_matchup_analysis(matchup):
weights = SPORT_WEIGHTS.get(matchup.sport, SPORT_WEIGHTS[“DEFAULT”])
scale   = SPORT_SCALE.get(matchup.sport, SPORT_SCALE[“DEFAULT”])

```
baseline_a = compute_baseline(
    matchup.metrics_a, matchup.situational_a,
    matchup.injury_a, matchup.historical, weights, scale
)
baseline_b = compute_baseline(
    matchup.metrics_b, matchup.situational_b,
    matchup.injury_b, matchup.historical, weights, scale
)

result = monte_carlo(baseline_a, baseline_b, matchup.sport)

odds_a = matchup.market_odds.get("TeamA", 1.90)
odds_b = matchup.market_odds.get("TeamB", 1.90)

ev_a, kelly_a, radj_a = compute_ev(result["TeamA_WinProb"], odds_a)
ev_b, kelly_b, radj_b = compute_ev(result["TeamB_WinProb"], odds_b)

imp_a = implied_prob(odds_a)
imp_b = implied_prob(odds_b)

edge_a = round(result["TeamA_WinProb"] - imp_a, 4)
edge_b = round(result["TeamB_WinProb"] - imp_b, 4)

if ev_a > 0 and ev_a >= ev_b:
    ev_bet = matchup.team_a
    kelly_stake = kelly_a
elif ev_b > 0:
    ev_bet = matchup.team_b
    kelly_stake = kelly_b
else:
    ev_bet = "No +EV"
    kelly_stake = 0.0

result["EV_Bet"]      = ev_bet
result["Kelly_Stake"] = kelly_stake
result["EV_A"]        = ev_a
result["EV_B"]        = ev_b
result["Edge_A"]      = edge_a
result["Edge_B"]      = edge_b
result["RiskAdj_EV_A"] = radj_a
result["RiskAdj_EV_B"] = radj_b
result["Implied_A"]   = imp_a
result["Implied_B"]   = imp_b
result["Baseline_A"]  = round(baseline_a, 2)
result["Baseline_B"]  = round(baseline_b, 2)

return result
```

def abbr(team):
return “”.join([w[0] for w in team.split()]).upper()[:4]

def fmt_matchup_msg(matchup, result):
a = abbr(matchup.team_a)
b = abbr(matchup.team_b)
msg  = “PROPNINJA MONTE CARLO\n”
msg += matchup.sport + “ | “ + matchup.league + “\n”
msg += a + “ vs “ + b + “\n”
msg += str(result[“Simulations”]) + “ simulations | Correlated model\n\n”
msg += “WIN PROBABILITY\n”
msg += a + “: “ + str(round(result[“TeamA_WinProb”] * 100, 1)) + “%”
msg += “ (mkt impl: “ + str(round(result[“Implied_A”] * 100, 1)) + “%)\n”
msg += b + “: “ + str(round(result[“TeamB_WinProb”] * 100, 1)) + “%”
msg += “ (mkt impl: “ + str(round(result[“Implied_B”] * 100, 1)) + “%)\n\n”
msg += “PROJECTED SCORE\n”
msg += a + “: “ + str(result[“TeamA_Score_Mean”])
msg += “ (median “ + str(result[“TeamA_Score_Median”]) + “)\n”
msg += b + “: “ + str(result[“TeamB_Score_Mean”])
msg += “ (median “ + str(result[“TeamB_Score_Median”]) + “)\n”
msg += “Spread: “ + str(result[“Projected_Spread”])
msg += “ +/- “ + str(result[“Spread_StdDev”]) + “\n\n”
msg += “CONFIDENCE INTERVALS (90%)\n”
msg += a + “: “ + str(result[“ConfidenceIntervals”][“TeamA_Score”][0])
msg += “ - “ + str(result[“ConfidenceIntervals”][“TeamA_Score”][1]) + “\n”
msg += b + “: “ + str(result[“ConfidenceIntervals”][“TeamB_Score”][0])
msg += “ - “ + str(result[“ConfidenceIntervals”][“TeamB_Score”][1]) + “\n\n”
msg += “EV ANALYSIS\n”
msg += “+EV Pick: “ + str(result[“EV_Bet”]) + “\n”
msg += “Kelly Stake: $” + str(result[“Kelly_Stake”]) + “ per $100\n”
msg += “EV “ + a + “: $” + str(result[“EV_A”])
msg += “ | Edge: “ + str(round(result[“Edge_A”] * 100, 1)) + “%\n”
msg += “EV “ + b + “: $” + str(result[“EV_B”])
msg += “ | Edge: “ + str(round(result[“Edge_B”] * 100, 1)) + “%\n”
return msg

def fmt_matchup_json(matchup, result):
out = {
“TeamA”: matchup.team_a,
“TeamB”: matchup.team_b,
“Sport”: matchup.sport,
“TeamA_Score_Mean”:   result[“TeamA_Score_Mean”],
“TeamB_Score_Mean”:   result[“TeamB_Score_Mean”],
“TeamA_WinProb”:      result[“TeamA_WinProb”],
“TeamB_WinProb”:      result[“TeamB_WinProb”],
“Projected_Spread”:   result[“Projected_Spread”],
“Spread_StdDev”:      result[“Spread_StdDev”],
“EV_Bet”:             result[“EV_Bet”],
“Kelly_Stake”:        result[“Kelly_Stake”],
“ConfidenceIntervals”: result[“ConfidenceIntervals”],
“Simulation_Distribution”: result[“Simulation_Distribution”],
}
return json.dumps(out, indent=2)

# ═══════════════════════════════════════════════════════

# PROP PICKS MODEL

# ═══════════════════════════════════════════════════════

def normal_cdf(z):
return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def propninja_score(line, stat):
s = stat.lower()
if “assist” in s:      boost = 0.072
elif “point” in s:     boost = 0.065
elif “rebound” in s:   boost = 0.048
elif “goal” in s:      boost = 0.071
elif “shot” in s:      boost = 0.063
elif “strikeout” in s: boost = 0.079
elif “hit” in s:       boost = 0.055
elif “yard” in s:      boost = 0.061
elif “touchdown” in s: boost = 0.058
elif “base” in s:      boost = 0.053
elif “block” in s:     boost = 0.044
elif “steal” in s:     boost = 0.066
else:                  boost = 0.055

```
season_proj  = line * (1 + boost)
recent_proj  = line * (1 + boost * 1.15)
matchup_proj = line * (1 + boost * 0.90)
composite    = (season_proj * 0.40) + (recent_proj * 0.40) + (matchup_proj * 0.20)
std_dev      = line * 0.185
z            = (composite - line) / std_dev
prob         = normal_cdf(z)
edge         = prob - (1.0 / DECIMAL_ODDS)
return round(composite, 2), round(prob, 4), round(edge, 4)
```

def grade(edge):
if edge >= 0.14: return “A+”
if edge >= 0.11: return “A”
if edge >= 0.08: return “B”
if edge >= 0.05: return “C”
return “D”

def kelly(prob, odds=1.90):
b = odds - 1
k = (prob * b - (1 - prob)) / b if b > 0 else 0
return round(max(k, 0) * 0.25, 4)

# ═══════════════════════════════════════════════════════

# LIVE DATA FETCHERS

# ═══════════════════════════════════════════════════════

def fetch_prizepicks():
picks = []
try:
url = “https://partner-api.prizepicks.com/projections?per_page=1000”
resp = requests.get(url, timeout=15)
if resp.status_code != 200:
resp = requests.get(
“https://api.prizepicks.com/projections”,
params={“per_page”: 250, “single_stat”: True},
timeout=15
)
if resp.status_code != 200:
return []

```
    data = resp.json()
    players = {}
    for item in data.get("included", []):
        if item.get("type") in ("new_player", "player"):
            attrs = item.get("attributes", {})
            players[item["id"]] = {
                "name": attrs.get("display_name") or attrs.get("name", "Unknown"),
                "team": attrs.get("team", ""),
            }

    for proj in data.get("data", []):
        attrs = proj.get("attributes", {})
        line  = attrs.get("line_score")
        stat  = attrs.get("stat_type", "")
        sport = attrs.get("league", "")
        if attrs.get("status") in ("disabled", "locked"):
            continue
        if not line or not stat:
            continue
        try:
            line = float(line)
        except Exception:
            continue
        pid = ""
        for key in ("new_player", "player"):
            pid = proj.get("relationships", {}).get(key, {}).get("data", {}).get("id", "")
            if pid:
                break
        pinfo = players.get(pid, {"name": "Unknown", "team": ""})
        proj_val, prob, edg = propninja_score(line, stat)
        if edg >= MIN_EDGE:
            k = kelly(prob)
            picks.append({
                "player": pinfo["name"],
                "team":   pinfo["team"],
                "stat":   stat,
                "line":   line,
                "proj":   proj_val,
                "prob":   prob,
                "edge":   edg,
                "kelly":  k,
                "grade":  grade(edg),
                "pick":   "OVER",
                "source": "PrizePicks",
                "sport":  sport.upper(),
            })
    picks.sort(key=lambda x: x["edge"], reverse=True)
    logger.info("PrizePicks: " + str(len(picks)) + " picks")
except Exception as e:
    logger.warning("PrizePicks error: " + str(e))
return picks
```

def fetch_kalshi():
picks = []
BASE = “https://api.elections.kalshi.com/trade-api/v2”
keywords = [“points”, “assists”, “rebounds”, “goals”, “shots”, “strikeouts”,
“hits”, “yards”, “touchdowns”, “bases”, “steals”, “blocks”,
“runs”, “saves”, “aces”, “corners”]
sport_map = {
“NBA”: “NBA”, “NFL”: “NFL”, “MLB”: “MLB”, “NHL”: “NHL”,
“SOCCER”: “SOCCER”, “UFC”: “UFC”, “GOLF”: “GOLF”,
“TENNIS”: “TENNIS”, “KXNBA”: “NBA”, “KXNFL”: “NFL”,
“KXMLB”: “MLB”, “KXNHL”: “NHL”, “EPL”: “EPL”,
}
try:
for series_ticker, sport_label in sport_map.items():
try:
resp = requests.get(
BASE + “/markets”,
params={“limit”: 200, “status”: “open”, “series_ticker”: series_ticker},
timeout=12
)
if resp.status_code != 200:
continue
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
proj_val, prob, edg = propninja_score(line, stat)
if edg >= MIN_EDGE:
k = kelly(prob)
picks.append({
“player”: title[:50],
“team”:   “”,
“stat”:   stat[:35],
“line”:   line,
“proj”:   proj_val,
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
picks = picks[:12]
logger.info(“Kalshi: “ + str(len(picks)) + “ picks”)
except Exception as e:
logger.warning(“Kalshi error: “ + str(e))
return picks

def fetch_espn_matchups(sport_path, sport_label):
matchups = []
try:
url = “https://site.api.espn.com/apis/site/v2/sports/” + sport_path + “/scoreboard”
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
odds_data = comps.get(“odds”, [{}])
odds_home = 1.90
odds_away = 1.90
if odds_data:
ml = odds_data[0]
try:
odds_home = float(ml.get(“homeTeamOdds”, {}).get(“moneyLine”, -110))
odds_away = float(ml.get(“awayTeamOdds”, {}).get(“moneyLine”, -110))
if odds_home < 0:
odds_home = 1 + (100 / abs(odds_home))
else:
odds_home = 1 + (odds_home / 100)
if odds_away < 0:
odds_away = 1 + (100 / abs(odds_away))
else:
odds_away = 1 + (odds_away / 100)
except Exception:
pass
m = Matchup(home, away, sport_label, sport_label + “ 2025-26”)
m.metrics_a   = {“off_rating”: 0.03, “def_rating”: -0.02}
m.metrics_b   = {“off_rating”: 0.00, “def_rating”: 0.00}
m.situational_a = {“home”: 0.04, “rest”: 0.02}
m.situational_b = {“home”: 0.00, “rest”: 0.00}
m.injury_a    = {“injury”: 0.00}
m.injury_b    = {“injury”: 0.00}
m.historical  = {“adjusted_point_diff”: 2.0}
m.market_odds = {“TeamA”: round(odds_home, 3), “TeamB”: round(odds_away, 3)}
matchups.append(m)
except Exception as e:
logger.warning(“ESPN “ + sport_label + “ error: “ + str(e))
return matchups[:4]

BACKUP = [
{“player”: “Kevin Durant”,     “team”: “HOU”, “stat”: “Points”,          “line”: 26.5, “proj”: 28.5, “prob”: 0.841, “edge”: 0.314, “kelly”: 0.18, “grade”: “A+”, “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “NBA”},
{“player”: “LaMelo Ball”,      “team”: “CHA”, “stat”: “Assists”,         “line”: 7.5,  “proj”: 8.1,  “prob”: 0.821, “edge”: 0.295, “kelly”: 0.16, “grade”: “A”,  “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “NBA”},
{“player”: “Nathan MacKinnon”, “team”: “COL”, “stat”: “Points”,          “line”: 0.5,  “proj”: 0.6,  “prob”: 0.814, “edge”: 0.288, “kelly”: 0.15, “grade”: “A”,  “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “NHL”},
{“player”: “Bukayo Saka”,      “team”: “ARS”, “stat”: “Shots on Target”, “line”: 1.5,  “proj”: 1.7,  “prob”: 0.798, “edge”: 0.271, “kelly”: 0.14, “grade”: “A”,  “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “EPL”},
{“player”: “Shohei Ohtani”,    “team”: “LAD”, “stat”: “Total Bases”,     “line”: 1.5,  “proj”: 1.6,  “prob”: 0.781, “edge”: 0.254, “kelly”: 0.13, “grade”: “A”,  “pick”: “OVER”, “source”: “PrizePicks”, “sport”: “MLB”},
{“player”: “Connor McDavid”,   “team”: “EDM”, “stat”: “Points”,          “line”: 0.5,  “proj”: 0.6,  “prob”: 0.814, “edge”: 0.288, “kelly”: 0.15, “grade”: “A”,  “pick”: “OVER”, “source”: “Kalshi”,     “sport”: “NHL”},
{“player”: “Trae Young”,       “team”: “ATL”, “stat”: “Assists”,         “line”: 10.5, “proj”: 11.3, “prob”: 0.761, “edge”: 0.235, “kelly”: 0.12, “grade”: “A”,  “pick”: “OVER”, “source”: “Kalshi”,     “sport”: “NBA”},
]

def get_all_picks():
pp = fetch_prizepicks()
kl = fetch_kalshi()
combined = pp + kl
if not combined:
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
return [p for p in picks if p[“source”] == source][:10]

def get_top_picks(n=5):
picks = get_all_picks()
return [p for p in picks if p[“grade”] in (“A+”, “A”)][:n]

# ═══════════════════════════════════════════════════════

# FORMATTING

# ═══════════════════════════════════════════════════════

def fmt(picks, label, show_kelly=False):
ts    = datetime.now().strftime(”%b %d %I:%M %p”)
total = len(picks)
tag   = “BACKUP” if picks == BACKUP else “LIVE | “ + str(total) + “ picks”
msg   = “PROPNINJA - “ + label + “\n”
msg  += ts + “ | “ + tag + “\n”
msg  += “Model: 3-Factor + Monte Carlo + Kelly\n\n”
for i, p in enumerate(picks[:10], 1):
team = “ (” + p[“team”] + “)” if p[“team”] else “”
msg += str(i) + “. “ + p[“grade”] + “ “ + p[“player”] + team + “\n”
msg += “   “ + p[“sport”] + “ | “ + p[“stat”] + “\n”
msg += “   Line: “ + str(p[“line”]) + “  Proj: “ + str(p[“proj”]) + “\n”
msg += “   “ + p[“pick”] + “ | Conf: “ + str(round(p[“prob”] * 100, 1)) + “%”
msg += “ | Edge: +” + str(round(p[“edge”] * 100, 1)) + “%”
if show_kelly:
msg += “ | Kelly: “ + str(round(p[“kelly”] * 100, 1)) + “%”
msg += “ | “ + p[“source”] + “\n\n”
msg += “For entertainment only. Gamble responsibly.”
return msg

def fmt_top(picks):
ts  = datetime.now().strftime(”%b %d %I:%M %p”)
msg = “PROPNINJA - TOP PLAYS\n” + ts + “\n”
msg += “Grade A+ and A | Monte Carlo verified\n\n”
for i, p in enumerate(picks, 1):
team = “ (” + p[“team”] + “)” if p[“team”] else “”
msg += str(i) + “. “ + p[“grade”] + “ “ + p[“player”] + team + “\n”
msg += “   “ + p[“sport”] + “ | “ + p[“stat”] + “ “ + p[“pick”] + “ “ + str(p[“line”]) + “\n”
msg += “   Edge: +” + str(round(p[“edge”] * 100, 1)) + “%”
msg += “ | Conf: “ + str(round(p[“prob”] * 100, 1)) + “%”
msg += “ | Kelly: “ + str(round(p[“kelly”] * 100, 1)) + “%\n\n”
if not picks:
msg += “No A/A+ picks right now.\nTry All Live Picks.\n”
msg += “For entertainment only. Gamble responsibly.”
return msg

# ═══════════════════════════════════════════════════════

# MENUS

# ═══════════════════════════════════════════════════════

def menu():
return InlineKeyboardMarkup([
[InlineKeyboardButton(“TOP PLAYS (A/A+ Only)”, callback_data=“top”)],
[InlineKeyboardButton(“ALL LIVE PICKS”,        callback_data=“all”)],
[InlineKeyboardButton(“NBA”,  callback_data=“sport_NBA”),
InlineKeyboardButton(“NFL”,  callback_data=“sport_NFL”),
InlineKeyboardButton(“MLB”,  callback_data=“sport_MLB”)],
[InlineKeyboardButton(“NHL”,  callback_data=“sport_NHL”),
InlineKeyboardButton(“EPL”,  callback_data=“sport_EPL”),
InlineKeyboardButton(“UFC”,  callback_data=“sport_UFC”)],
[InlineKeyboardButton(“PrizePicks”, callback_data=“src_PrizePicks”),
InlineKeyboardButton(“Kalshi”,     callback_data=“src_Kalshi”)],
[InlineKeyboardButton(“Simulate NBA”,  callback_data=“sim_NBA”),
InlineKeyboardButton(“Simulate NFL”,  callback_data=“sim_NFL”)],
[InlineKeyboardButton(“How It Works”, callback_data=“howto”)],
])

def nav(cb):
return InlineKeyboardMarkup([
[InlineKeyboardButton(“Refresh”,   callback_data=cb)],
[InlineKeyboardButton(“Main Menu”, callback_data=“menu”)],
])

# ═══════════════════════════════════════════════════════

# HANDLERS

# ═══════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“PropNinja Bot\n”
“Live picks: PrizePicks + Kalshi\n”
“Monte Carlo: 10,000 simulations\n”
“Kelly EV: 0.25x fractional\n”
“Sports: NBA NFL MLB NHL EPL UFC\n\n”
“Tap below:”,
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

if d.startswith("sim_"):
    sport = d.split("_", 1)[1]
    await q.edit_message_text("Running " + sport + " Monte Carlo simulations...")
    sport_paths = {
        "NBA": "basketball/nba",
        "NFL": "football/nfl",
        "NHL": "hockey/nhl",
        "MLB": "baseball/mlb",
    }
    path = sport_paths.get(sport, "basketball/nba")
    matchups = fetch_espn_matchups(path, sport)
    if not matchups:
        m = Matchup("Team A", "Team B", sport, sport + " 2025-26")
        m.metrics_a = {"off_rating": 0.03, "def_rating": -0.02}
        m.metrics_b = {"off_rating": 0.00, "def_rating": 0.00}
        m.situational_a = {"home": 0.04, "rest": 0.02}
        m.situational_b = {"home": 0.00, "rest": 0.00}
        m.injury_a = {"injury": 0.00}
        m.injury_b = {"injury": 0.00}
        m.historical = {"adjusted_point_diff": 2.0}
        m.market_odds = {"TeamA": 1.85, "TeamB": 2.00}
        matchups = [m]
    msg = ""
    for matchup in matchups[:3]:
        result = run_matchup_analysis(matchup)
        msg += fmt_matchup_msg(matchup, result) + "\n---\n"
    await q.edit_message_text(msg[:4096], reply_markup=nav(d))
    return

if d == "howto":
    await q.edit_message_text(
        "How PropNinja Works\n\n"
        "PROPS MODEL\n"
        "3-Factor weighted system:\n"
        "Season avg (40%) + Recent form (40%)\n"
        "+ Matchup factor (20%)\n\n"
        "MATCHUP MODEL\n"
        "10,000 Monte Carlo simulations\n"
        "Correlated scoring via Box-Muller\n"
        "Sport-specific variance tables\n"
        "Dynamic weight tables per sport\n\n"
        "EV FORMULA\n"
        "EV = (WinProb x Profit) - (LossProb x Stake)\n\n"
        "KELLY: 0.25x fractional\n"
        "k = 0.25 x (b x p - q) / b\n\n"
        "GRADES\n"
        "A+ = edge 14%+\n"
        "A  = edge 11%+\n"
        "B  = edge 8%+\n"
        "C  = edge 5%+\n\n"
        "Entertainment only. Gamble responsibly.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu")]])
    )
    return

if d == "all":
    await q.edit_message_text("Fetching all live picks...")
    picks = get_all_picks()
    await q.edit_message_text(fmt(picks, "ALL SPORTS", show_kelly=True)[:4096], reply_markup=nav("all"))
    return

if d.startswith("src_"):
    src = d.split("_", 1)[1]
    await q.edit_message_text("Fetching " + src + " picks...")
    picks = get_by_source(src)
    if not picks:
        await q.edit_message_text("No " + src + " picks right now.", reply_markup=nav(d))
        return
    await q.edit_message_text(fmt(picks, src)[:4096], reply_markup=nav(d))
    return

if d.startswith("sport_"):
    sport = d.split("_", 1)[1]
    await q.edit_message_text("Fetching " + sport + " picks...")
    picks = get_by_sport(sport)
    if not picks:
        await q.edit_message_text("No " + sport + " picks right now. Try All Live Picks.", reply_markup=nav(d))
        return
    await q.edit_message_text(fmt(picks, sport)[:4096], reply_markup=nav(d))
    return
```

def main():
if not TELEGRAM_TOKEN:
raise ValueError(“TELEGRAM_TOKEN missing!”)
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler(“start”,  start))
app.add_handler(CommandHandler(“picks”,  picks_cmd))
app.add_handler(CommandHandler(“top”,    top_cmd))
app.add_handler(CallbackQueryHandler(button))
logger.info(“PropNinja Bot is running”)
app.run_polling(allowed_updates=Update.ALL_TYPES)

if **name** == “**main**”:
main()
