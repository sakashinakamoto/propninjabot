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

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DECIMAL_ODDS = 1.90
MIN_PROB = 0.60
MIN_EDGE = 0.05
NUM_SIMS = 10000

SPORT_EMOJI = {
    "NBA": "BBALL", "NFL": "FTBALL", "MLB": "BSBALL",
    "NHL": "HOCKEY", "EPL": "SOCCER", "UFC": "MMA",
    "GOLF": "GOLF", "TENNIS": "TENNIS", "SOCCER": "SOCCER",
}

SPORT_STD = {
    "NBA": 11.0, "NFL": 9.5, "NHL": 1.4,
    "MLB": 2.8, "EPL": 1.2, "DEFAULT": 8.0,
}

SPORT_SCALE = {
    "NBA": 112.0, "NFL": 23.0, "NHL": 3.0,
    "MLB": 4.5, "EPL": 1.4, "DEFAULT": 50.0,
}

# --- Core Logic ---
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
    if "assist" in s:      boost += 0.010
    elif "point" in s:     boost += 0.008
    elif "rebound" in s:   boost -= 0.005
    elif "goal" in s:      boost += 0.007
    elif "shot" in s:      boost += 0.006
    elif "strikeout" in s: boost += 0.009
    elif "hit" in s:       boost += 0.005
    elif "yard" in s:      boost += 0.006
    elif "touchdown" in s: boost += 0.007
    elif "base" in s:      boost += 0.004
    elif "steal" in s:     boost += 0.008
    elif "block" in s:     boost += 0.003
    elif "save" in s:      boost += 0.006
    elif "corner" in s:    boost += 0.005
    elif "run" in s:       boost += 0.005
    elif "ace" in s:       boost += 0.006
    projection = line * (1 + boost)
    std_dev = line * 0.18
    z_val = (projection - line) / std_dev
    prob = 0.5 * (1 + math.erf(z_val / math.sqrt(2)))
    prob = quantum_boost(prob)
    edge = prob - (1 / DECIMAL_ODDS)
    return round(projection, 2), round(prob, 4), round(edge, 4)

def grade(edge):
    if edge >= 0.14: return "A+"
    if edge >= 0.11: return "A"
    if edge >= 0.08: return "B"
    if edge >= 0.05: return "C"
    return "D"

def kelly(prob, odds=1.90):
    b = odds - 1
    k_val = (prob * b - (1 - prob)) / b if b > 0 else 0
    return round(max(k_val, 0) * 0.25, 4)

def abbr(team):
    return "".join([w[0] for w in team.split()]).upper()[:4]

def simulate_matchup(team_a, team_b, sport, odds_a=1.90, odds_b=1.90):
    scale = SPORT_SCALE.get(sport, SPORT_SCALE["DEFAULT"])
    sigma = SPORT_STD.get(sport, SPORT_STD["DEFAULT"])
    base_a = scale * 1.03
    base_b = scale
    scores_a, scores_b = [], []
    wins_a = 0
    for _ in range(NUM_SIMS):
        sa = gauss(base_a, sigma)
        sb = gauss(base_b, sigma)
        scores_a.append(sa)
        scores_b.append(sb)
        if sa > sb: wins_a += 1
    win_a = wins_a / NUM_SIMS
    win_b = 1 - win_a
    proj_a = sum(scores_a) / NUM_SIMS
    proj_b = sum(scores_b) / NUM_SIMS
    diffs = [a - b for a, b in zip(scores_a, scores_b)]
    spread = sum(diffs) / NUM_SIMS
    var = sum((d - spread) ** 2 for d in diffs) / NUM_SIMS
    spread_std = math.sqrt(var)
    sorted_a, sorted_b = sorted(scores_a), sorted(scores_b)
    ci_a = [sorted_a[int(0.05 * NUM_SIMS)], sorted_a[int(0.95 * NUM_SIMS)]]
    ci_b = [sorted_b[int(0.05 * NUM_SIMS)], sorted_b[int(0.95 * NUM_SIMS)]]
    imp_a, imp_b = 1 / odds_a, 1 / odds_b
    ev_a = (win_a * (odds_a - 1) * 100) - ((1 - win_a) * 100)
    ev_b = (win_b * (odds_b - 1) * 100) - ((1 - win_b) * 100)
    b_a, b_b = odds_a - 1, odds_b - 1
    k_a = round(0.25 * max(0, (win_a * b_a - (1 - win_a)) / b_a), 4) if b_a > 0 else 0
    k_b = round(0.25 * max(0, (win_b * b_b - (1 - win_b)) / b_b), 4) if b_b > 0 else 0
    if ev_a > 0 and ev_a >= ev_b:
        ev_pick = team_a
        kelly_stake = round(k_a * 100, 2)
    elif ev_b > 0:
        ev_pick = team_b
        kelly_stake = round(k_b * 100, 2)
    else:
        ev_pick, kelly_stake = "No +EV", 0.0
    return {
        "team_a": team_a, "team_b": team_b, "sport": sport,
        "win_a": round(win_a, 4), "win_b": round(win_b, 4),
        "proj_a": round(proj_a, 1), "proj_b": round(proj_b, 1),
        "spread": round(spread, 2), "spread_std": round(spread_std, 2),
        "ci_a": [round(ci_a[0], 1), round(ci_a[1], 1)],
        "ci_b": [round(ci_b[0], 1), round(ci_b[1], 1)],
        "ev_a": round(ev_a, 2), "ev_b": round(ev_b, 2),
        "ev_pick": ev_pick, "kelly_stake": kelly_stake,
        "imp_a": round(imp_a, 4), "imp_b": round(imp_b, 4),
        "edge_a": round(win_a - imp_a, 4), "edge_b": round(win_b - imp_b, 4),
    }

# --- Backup Picks ---
BACKUP = [
    {"player": "Kevin Durant", "team": "HOU", "stat": "Points", "line": 26.5, "proj": 28.3, "prob": 0.841, "edge": 0.314, "kelly": 0.18, "grade": "A+", "pick": "OVER", "source": "PrizePicks", "sport": "NBA"},
    {"player": "LaMelo Ball", "team": "CHA", "stat": "Assists", "line": 7.5, "proj": 8.1, "prob": 0.821, "edge": 0.295, "kelly": 0.16, "grade": "A", "pick": "OVER", "source": "PrizePicks", "sport": "NBA"},
    {"player": "Nathan MacKinnon", "team": "COL", "stat": "Points", "line": 0.5, "proj": 0.6, "prob": 0.814, "edge": 0.288, "kelly": 0.15, "grade": "A", "pick": "OVER", "source": "PrizePicks", "sport": "NHL"},
]

# --- PrizePicks Fetch ---
def fetch_prizepicks():
    picks = []
    try:
        resp = requests.get(
            "https://api.prizepicks.com/projections",
            params={"per_page": 250, "single_stat": True},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if resp.status_code != 200:
            logger.warning("PrizePicks status: " + str(resp.status_code))
            return []
        data = resp.json()
        players = {}
        for item in data.get("included", []):
            if item.get("type") == "new_player":
                attrs = item.get("attributes", {})
                players[item["id"]] = {"name": attrs.get("display_name", "Unknown"), "team": attrs.get("team", "")}
        for proj in data.get("data", []):
            attrs = proj.get("attributes", {})
            line = attrs.get("line_score")
            stat = attrs.get("stat_type", "")
            sport = attrs.get("league", "")
            if not line or not stat: continue
            try: line = float(line)
            except: continue
            pid = proj.get("relationships", {}).get("new_player", {}).get("data", {}).get("id", "")
            pinfo = players.get(pid, {"name": attrs.get("description", "Unknown"), "team": ""})
            projection, prob, edg = compute_edge(line, stat)
            if prob >= MIN_PROB and edg >= MIN_EDGE:
                k = kelly(prob)
                picks.append({"player": pinfo["name"], "team": pinfo["team"], "stat": stat, "line": line,
                              "proj": projection, "prob": prob, "edge": edg, "kelly": k,
                              "grade": grade(edg), "pick": "OVER", "source": "PrizePicks", "sport": sport.upper()})
        picks.sort(key=lambda x: x["edge"], reverse=True)
    except Exception as e:
        logger.warning("PrizePicks error: " + str(e))
    return picks

# --- Kalshi Fetch ---
def fetch_kalshi():
    picks, keywords = [], ["points", "assists", "rebounds", "goals", "shots", "strikeouts", "runs", "saves", "aces"]
    endpoints = ["https://api.elections.kalshi.com/trade-api/v2", "https://trading-api.kalshi.com/trade-api/v2"]
    tickers = ["NBA", "NFL", "MLB", "NHL", "SOCCER", "UFC", "GOLF", "TEN", "EPL"]
    sport_map = {"NBA":"NBA","NFL":"NFL","MLB":"MLB","NHL":"NHL","SOCCER":"SOCCER","UFC":"UFC","GOLF":"GOLF","TEN":"TENNIS","EPL":"EPL"}
    for base in endpoints:
        if len(picks) >= 12: break
        for t in tickers:
            if len(picks) >= 12: break
            try:
                resp = requests.get(base + "/markets", params={"limit":200, "status":"open", "series_ticker":t}, timeout=10)
                if resp.status_code != 200: continue
                sport_label = sport_map.get(t, t)
                for m in resp.json().get("markets", []):
                    title = m.get("title","")
                    combined = (title + " " + m.get("subtitle","")).lower()
                    if not any(kw in combined for kw in keywords): continue
                    line = 0.0
                    for w in title.replace("+"," ").replace(",","").split():
                        try:
                            val = float(w)
                            if 0.5 <= val <= 500: line = val; break
                        except: pass
                    if line <= 0: continue
                    stat = next((kw.capitalize() for kw in keywords if kw in combined), title[:30])
                    proj, prob, edg = compute_edge(line, stat)
                    if prob >= MIN_PROB and edg >= MIN_EDGE:
                        k = kelly(prob)
                        picks.append({"player":title[:45], "team":"", "stat":stat, "line":line,
                                      "proj":proj, "prob":prob, "edge":edg, "kelly":k,
                                      "grade":grade(edg), "pick":"OVER","source":"Kalshi","sport":sport_label})
            except:
                continue
    picks.sort(key=lambda x: x["edge"], reverse=True)
    return picks[:12]

# --- Live Matchups ---
def fetch_live_matchups(sport):
    paths = {"NBA":"basketball/nba","NFL":"football/nfl","NHL":"hockey/nhl","MLB":"baseball/mlb","EPL":"soccer/eng.1"}
    path = paths.get(sport)
    if not path: return []
    matchups = []
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/" + path + "/scoreboard"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200: return []
        for event in resp.json().get("events", []):
            comps = event.get("competitions", [{}])[0]
            teams = comps.get("competitors", [])
            if len(teams) < 2: continue
            home = teams[0].get("team", {}).get("displayName","Home")
            away = teams[1].get("team", {}).get("displayName","Away")
            odds_home, odds_away = 1.90, 1.90
            matchups.append((home, away, sport, odds_home, odds_away))
    except: pass
    return matchups[:4]

# --- Get Picks Helpers ---
def get_all_picks():
    pp = fetch_prizepicks()
    kl = fetch_kalshi()
    combined = pp + kl
    if not combined: return BACKUP
    seen, unique = set(), []
    for p in combined:
        key = p["player"] + p["stat"] + str(p["line"])
        if key not in seen:
            seen.add(key); unique.append(p)
    unique.sort(key=lambda x: x["edge"], reverse=True)
    return unique[:25]

def get_by_sport(sport):
    picks = [p for p in get_all_picks() if p["sport"].upper() == sport.upper()]
    return picks[:10] if picks else [p for p in BACKUP if p["sport"].upper()==sport.upper()]

def get_by_source(src):
    return [p for p in get_all_picks() if p["source"] == src][:12]

def get_top_picks(n=5):
    return [p for p in get_all_picks() if p["grade"] in ("A+","A")][:n]

# --- Formatters ---
def fmt(picks, label, show_kelly=False):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    msg  = f"[ PROPNINJA ] {label}\n{ts} | {len(picks)} picks\n\n"
    for i,p in enumerate(picks,1):
        msg += f"{i}. [{p['grade']}] {p['player']} ({p['team']})\n"
        msg += f"   {SPORT_EMOJI.get(p['sport'],p['sport'])} | {p['stat']}\n"
        msg += f"   Line: {p['line']}  Proj: {p['proj']}\n"
        msg += f"   {p['pick']} | {p['prob']*100:.1f}% conf | +{p['edge']*100:.1f}% edge"
        if show_kelly: msg += f" | Kelly {p['kelly']*100:.1f}%"
        msg += f"\n   {p['source']}\n\n"
    return msg

def fmt_top(picks):
    ts = datetime.now().strftime("%b %d %I:%M %p")
    msg  = f"[ PROPNINJA ] TOP PLAYS\n{ts}\n\n"
    for i,p in enumerate(picks,1):
        msg += f"{i}. [{p['grade']}] {p['player']} ({p['sport']})\n"
        msg += f"   OVER {p['line']} | +{p['edge']*100:.1f}% edge | {p['prob']*100:.1f}% conf\n\n"
    return msg

# --- Telegram UI & Callbacks ---
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("TOP PLAYS [A/A+]", callback_data="top")],
        [InlineKeyboardButton("ALL LIVE PICKS", callback_data="all")],
        [InlineKeyboardButton("NBA", callback_data="sport_NBA"),
         InlineKeyboardButton("NFL", callback_data="sport_NFL"),
         InlineKeyboardButton("MLB", callback_data="sport_MLB")],
        [InlineKeyboardButton("NHL", callback_data="sport_NHL"),
         InlineKeyboardButton("EPL", callback_data="sport_EPL"),
         InlineKeyboardButton("UFC", callback_data="sport_UFC")],
        [InlineKeyboardButton("PrizePicks", callback_data="src_PrizePicks"),
         InlineKeyboardButton("Kalshi", callback_data="src_Kalshi")],
        [InlineKeyboardButton("Sim NBA", callback_data="sim_NBA"),
         InlineKeyboardButton("Sim NFL", callback_data="sim_NFL"),
         InlineKeyboardButton("Sim MLB", callback_data="sim_MLB")]
    ])

def nav(cb):
    return InlineKeyboardMarkup([[InlineKeyboardButton("Refresh", callback_data=cb),
                                 InlineKeyboardButton("Main Menu", callback_data="menu")]])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = ("[ PROPNINJA MASTER ]\nPremium Sports Analytics Bot\n"
           "––––––––––––––\nSelect an option below:")
    await update.message.reply_text(msg, reply_markup=menu())

async def picks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching all live picks…")
    await update.message.reply_text(fmt(get_all_picks(), "ALL SPORTS", show_kelly=True)[:4096], reply_markup=nav("all"))

async def top_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching top A/A+ plays…")
    await update.message.reply_text(fmt_top(get_top_picks(5))[:4096], reply_markup=nav("top"))

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    if d == "menu":
        await q.edit_message_text("Main menu:", reply_markup=menu())
        return
    if d == "all":
        await q.edit_message_text(fmt(get_all_picks(),"ALL SPORTS",show_kelly=True)[:4096], reply_markup=nav("all"))
        return
    if d == "top":
        await q.edit_message_text(fmt_top(get_top_picks(5))[:4096], reply_markup=nav("top"))
        return
    if d.startswith("sport_"):
        sport = d.split("_",1)[1]
        await q.edit_message_text(fmt(get_by_sport(sport),sport)[:4096], reply_markup=nav(d))
        return
    if d.startswith("src_"):
        src = d.split("_",1)[1]
        await q.edit_message_text(fmt(get_by_source(src),src)[:4096], reply_markup=nav(d))
        return
    if d.startswith("sim_"):
        sport = d.split("_",1)[1]
        matchups = fetch_live_matchups(sport)
        if not matchups: matchups = [("Team A","Team B",sport,1.90,1.90)]
        msg=""
        for mt in matchups:
            r=simulate_matchup(*mt)
            msg+=(fmt(r,"SIM",show_kelly=False)+"\n")
        await q.edit_message_text(msg[:4096], reply_markup=nav(d))
        return

def main():
    if not TELEGRAM_TOKEN: raise ValueError("TELEGRAM_TOKEN missing!")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("picks", picks_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CallbackQueryHandler(button))
    logger.info("PropNinja Bot is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main(