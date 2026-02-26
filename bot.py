 import os
import math
import logging
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')

DECIMAL_ODDS = 1.90
MIN_PROB = 0.60
MIN_EDGE = 0.05

BASE_VOL = 0.18


def stat_boost(stat):
    s = stat.lower()
    boost = 0.055

    if 'assist' in s:
        boost += 0.012
    elif 'point' in s:
        boost += 0.010
    elif 'rebound' in s:
        boost -= 0.004
    elif 'goal' in s:
        boost += 0.009
    elif 'shot' in s:
        boost += 0.007
    elif 'strikeout' in s:
        boost += 0.011
    elif 'hit' in s:
        boost += 0.006
    elif 'yard' in s:
        boost += 0.008
    elif 'touchdown' in s:
        boost += 0.013

    return boost


def dynamic_volatility(line, stat):
    vol = BASE_VOL

    if line <= 1.5:
        vol *= 0.85
    elif line >= 25:
        vol *= 1.15

    if 'strikeout' in stat.lower():
        vol *= 1.10
    if 'rebound' in stat.lower():
        vol *= 0.95

    return vol


def compute_edge(line, stat):
    if line <= 0:
        return 0, 0, 0

    boost = stat_boost(stat)
    projection = line * (1 + boost)

    vol = dynamic_volatility(line, stat)
    std_dev = line * vol

    if std_dev == 0:
        return 0, 0, 0

    z = (projection - line) / std_dev
    prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))

    implied = 1 / DECIMAL_ODDS
    raw_edge = prob - implied

    sharpness = 1 + (abs(raw_edge) * 0.5)
    edge = raw_edge * sharpness

    return round(projection, 2), round(prob, 4), round(edge, 4)


def grade(edge):
    if edge >= 0.14:
        return 'A+'
    if edge >= 0.12:
        return 'A'
    if edge >= 0.09:
        return 'B'
    return 'C'


def fetch_prizepicks():
    picks = []

    try:
        resp = requests.get(
            'https://api.prizepicks.com/projections',
            params={'per_page': 250, 'single_stat': True},
            headers={'Content-Type': 'application/json'},
            timeout=15
        )

        if resp.status_code != 200:
            logger.warning('PrizePicks status: ' + str(resp.status_code))
            return []

        data = resp.json()
        players = {}

        for item in data.get('included', []):
            if item.get('type') == 'new_player':
                attrs = item.get('attributes', {})
                players[item['id']] = {
                    'name': attrs.get('display_name', 'Unknown'),
                    'team': attrs.get('team', '')
                }

        for proj in data.get('data', []):
            attrs = proj.get('attributes', {})
            line = attrs.get('line_score')
            stat = attrs.get('stat_type', '')
            sport = attrs.get('league', '')

            if not line or not stat:
                continue

            line = float(line)

            pid = proj.get('relationships', {}).get('new_player', {}).get('data', {}).get('id', '')
            pinfo = players.get(pid, {'name': attrs.get('description', 'Unknown'), 'team': ''})

            projection, prob, edg = compute_edge(line, stat)

            if prob >= MIN_PROB and edg >= MIN_EDGE:
                picks.append({
                    'player': pinfo['name'],
                    'team': pinfo['team'],
                    'stat': stat,
                    'line': line,
                    'proj': projection,
                    'prob': prob,
                    'edge': edg,
                    'grade': grade(edg),
                    'pick': 'OVER',
                    'source': 'PrizePicks',
                    'sport': sport.upper()
                })

        logger.info('PrizePicks: ' + str(len(picks)) + ' picks')

    except Exception as e:
        logger.warning('PrizePicks error: ' + str(e))

    return picks


def fetch_kalshi():
    picks = []
    tickers = ['NBA', 'NFL', 'MLB', 'NHL', 'SOCCER', 'UFC', 'GOLF', 'TEN']

    try:
        for ticker in tickers:
            try:
                resp = requests.get(
                    'https://trading-api.kalshi.com/trade-api/v2/markets',
                    params={'limit': 100, 'status': 'open', 'series_ticker': ticker},
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )

                if resp.status_code != 200:
                    continue

                for market in resp.json().get('markets', []):
                    title = market.get('title', '')
                    subtitle = market.get('subtitle', '')

                    keywords = ['points', 'assists', 'rebounds', 'goals', 'shots',
                                'strikeouts', 'hits', 'yards', 'touchdowns']

                    if not any(k in title.lower() for k in keywords):
                        continue

                    line = 0.0
                    for word in title.split():
                        try:
                            val = float(word.replace('+', '').replace(',', ''))
                            if val > 0:
                                line = val
                                break
                        except:
                            continue

                    if line <= 0:
                        continue

                    stat = subtitle if subtitle else title[:30]

                    projection, prob, edg = compute_edge(line, stat)

                    if prob >= MIN_PROB and edg >= MIN_EDGE:
                        picks.append({
                            'player': title[:40],
                            'team': '',
                            'stat': stat[:30],
                            'line': line,
                            'proj': projection,
                            'prob': prob,
                            'edge': edg,
                            'grade': grade(edg),
                            'pick': 'OVER',
                            'source': 'Kalshi',
                            'sport': ticker
                        })

            except:
                continue

        logger.info('Kalshi: ' + str(len(picks)) + ' picks')

    except Exception as e:
        logger.warning('Kalshi error: ' + str(e))

    return picks


def get_all_picks():
    all_picks = fetch_prizepicks() + fetch_kalshi()

    all_picks.sort(key=lambda x: x['edge'], reverse=True)

    seen = set()
    unique = []

    for p in all_picks:
        key = p['player'] + p['stat'] + str(p['line'])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique[:25]


def get_by_sport(sport):
    return [p for p in get_all_picks() if p['sport'].upper() == sport.upper()]


def fmt(picks, label):
    ts = datetime.now().strftime('%b %d %I:%M %p')
    msg = 'PROPNINJA - ' + label + '\n'
    msg += ts + ' | ' + str(len(picks)) + ' picks\n\n'

    for i, p in enumerate(picks[:10], 1):
        msg += str(i) + '. ' + p['grade'] + ' ' + p['player']
        if p['team']:
            msg += ' (' + p['team'] + ')'
        msg += ' [' + p['source'] + ']\n'
        msg += '   ' + p['stat'] + ' | Line: ' + str(p['line']) + ' Proj: ' + str(p['proj']) + '\n'
        msg += '   OVER | Conf: ' + str(round(p['prob'] * 100, 1)) + '% | Edge: +' + str(round(p['edge'] * 100, 1)) + '% | ' + p['sport'] + '\n\n'

    msg += 'For entertainment only. Gamble responsibly.'
    return msg


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('ALL LIVE PICKS', callback_data='all')],
        [InlineKeyboardButton('NBA', callback_data='sport_NBA'),
         InlineKeyboardButton('NFL', callback_data='sport_NFL'),
         InlineKeyboardButton('MLB', callback_data='sport_MLB')],
        [InlineKeyboardButton('NHL', callback_data='sport_NHL'),
         InlineKeyboardButton('SOCCER', callback_data='sport_SOCCER'),
         InlineKeyboardButton('UFC', callback_data='sport_UFC')],
        [InlineKeyboardButton('PrizePicks Only', callback_data='src_PrizePicks'),
         InlineKeyboardButton('Kalshi Only', callback_data='src_Kalshi')]
    ])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'PropNinja Bot\nLive picks from PrizePicks and Kalshi\nTap below:',
        reply_markup=menu()
    )


async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data

    if d == 'all':
        picks = get_all_picks()
        await q.edit_message_text(fmt(picks, 'ALL SPORTS')[:4096], reply_markup=menu())
        return

    if d.startswith('sport_'):
        sport = d.split('_', 1)[1]
        picks = get_by_sport(sport)
        await q.edit_message_text(fmt(picks, sport)[:4096], reply_markup=menu())
        return

    if d.startswith('src_'):
        src = d.split('_', 1)[1]
        picks = [p for p in get_all_picks() if p['source'] == src]
        await q.edit_message_text(fmt(picks, src)[:4096], reply_markup=menu())
        return


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError('TELEGRAM_TOKEN missing')

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button))

    logger.info('PropNinja running')
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
