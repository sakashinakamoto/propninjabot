import asyncio
import aiohttp
import logging
from typing import List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PRIZEPICKS_URL = "https://api.prizepicks.com/projections"
KALSHI_URL = "https://trading-api.kalshi.com/trade-api/v2/markets"

MIN_PROB = 0.60
MIN_EDGE = 0.05
MAX_PICKS = 25
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3


# ==========================================================
# MODEL
# ==========================================================

def model_predict(line: float, projection: float) -> float:
    """
    Converts projection difference into probability.
    Replace with real ML model if available.
    """
    diff = projection - line
    probability = 0.5 + (diff * 0.08)
    return max(0.01, min(probability, 0.99))


def calculate_edge(model_prob: float, implied_prob: float) -> float:
    return model_prob - implied_prob


# ==========================================================
# NETWORK HELPERS
# ==========================================================

async def fetch_json_with_retry(
    session: aiohttp.ClientSession,
    url: str
) -> Dict[str, Any]:
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                logger.warning(f"{url} returned {response.status}")
        except Exception as exc:
            logger.warning(f"Attempt {attempt+1} failed for {url}: {exc}")
        await asyncio.sleep(1)

    logger.error(f"Failed to fetch after {MAX_RETRIES} attempts: {url}")
    return {}


# ==========================================================
# PRIZEPICKS
# ==========================================================

async def fetch_prizepicks(
    session: aiohttp.ClientSession
) -> List[Dict[str, Any]]:

    data = await fetch_json_with_retry(session, PRIZEPICKS_URL)
    results = []

    for item in data.get("data", []):
        attributes = item.get("attributes", {})

        player = attributes.get("description")
        line = attributes.get("line_score")
        stat_type = attributes.get("stat_type")
        projection = attributes.get("projection")

        if not player or line is None or projection is None:
            continue

        model_prob = model_predict(float(line), float(projection))
        implied_prob = 0.5
        edge = calculate_edge(model_prob, implied_prob)

        if model_prob >= MIN_PROB and edge >= MIN_EDGE:
            results.append({
                "player": player,
                "team": attributes.get("team", ""),
                "sport": attributes.get("league", ""),
                "stat_type": stat_type,
                "line": float(line),
                "projection": float(projection),
                "probability": model_prob,
                "edge": edge,
                "source": "PrizePicks"
            })

    logger.info(f"PrizePicks valid picks: {len(results)}")
    return results


# ==========================================================
# KALSHI
# ==========================================================

async def fetch_kalshi(
    session: aiohttp.ClientSession
) -> List[Dict[str, Any]]:

    data = await fetch_json_with_retry(session, KALSHI_URL)
    results = []

    for market in data.get("markets", []):
        title = market.get("title", "")
        yes_ask = market.get("yes_ask")
        strike_price = market.get("strike_price")

        if not title or yes_ask is None or strike_price is None:
            continue

        if yes_ask <= 0:
            continue

        implied_prob = float(yes_ask) / 100.0

        line = float(strike_price)
        projection = line * 1.05  # placeholder projection

        model_prob = model_predict(line, projection)
        edge = calculate_edge(model_prob, implied_prob)

        if model_prob >= MIN_PROB and edge >= MIN_EDGE:
            results.append({
                "player": title,
                "team": "",
                "sport": market.get("event_ticker", ""),
                "stat_type": "Market",
                "line": line,
                "projection": projection,
                "probability": model_prob,
                "edge": edge,
                "source": "Kalshi"
            })

    logger.info(f"Kalshi valid picks: {len(results)}")
    return results


# ==========================================================
# AGGREGATOR
# ==========================================================

async def get_live_picks() -> List[Dict[str, Any]]:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        prizepicks_task = fetch_prizepicks(session)
        kalshi_task = fetch_kalshi(session)

        prizepicks_results, kalshi_results = await asyncio.gather(
            prizepicks_task,
            kalshi_task
        )

    combined = prizepicks_results + kalshi_results
    combined.sort(key=lambda x: x["edge"], reverse=True)

    return combined[:MAX_PICKS]


# ==========================================================
# FORMATTER
# ==========================================================

def grade(probability: float) -> str:
    if probability >= 0.80:
        return "[A+]"
    if probability >= 0.75:
        return "[A]"
    if probability >= 0.70:
        return "[B+]"
    if probability >= 0.65:
        return "[B]"
    return "[C]"


def format_telegram_message(picks: List[Dict[str, Any]]) -> str:
    if not picks:
        return "⚠️ No live picks available. Model recalculating..."

    output_lines = []

    for index, pick in enumerate(picks, start=1):
        message_block = (
            f"{index}. {grade(pick['probability'])} {pick['player']}\n"
            f"{pick['sport']} | {pick['stat_type']}\n"
            f"Line: {pick['line']}  Proj: {round(pick['projection'], 2)}\n"
            f"OVER | {round(pick['probability'] * 100, 1)}% conf | "
            f"+{round(pick['edge'] * 100, 1)}% edge\n"
            f"{pick['source']}\n"
        )
        output_lines.append(message_block)

    return "\n".join(output_lines)


# ==========================================================
# PUBLIC ENTRY FUNCTION
# ==========================================================

async def generate_live_message() -> str:
    picks = await get_live_picks()
    return format_telegram_message(picks)


# ==========================================================
# SAFE STANDALONE RUNNER
# ==========================================================

async def main() -> None:
    message = await generate_live_message()
    print(message)


if __name__ == "__main__":
    asyncio.run(main())