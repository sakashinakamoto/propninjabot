import math
import random
import json
from dataclasses import dataclass, asdict
from typing import Dict, Tuple, List
import numpy as np


# =========================
# DATA STRUCTURES
# =========================

@dataclass
class MatchupInput:
    team_a: str
    team_b: str
    sport: str
    metrics_a: Dict[str, float]
    metrics_b: Dict[str, float]
    home_field_advantage: float
    rest_diff: float
    injury_impact_a: float
    injury_impact_b: float
    historical_diff: float
    market_odds_a: float
    market_odds_b: float


# =========================
# UTILITY FUNCTIONS
# =========================

def american_to_decimal(odds: float) -> float:
    if odds > 0:
        return 1 + (odds / 100)
    return 1 + (100 / abs(odds))


def remove_vig(prob_a: float, prob_b: float) -> Tuple[float, float]:
    total = prob_a + prob_b
    return prob_a / total, prob_b / total


def fractional_kelly(win_prob: float, decimal_odds: float, fraction: float = 0.25) -> float:
    b = decimal_odds - 1
    q = 1 - win_prob
    kelly = (b * win_prob - q) / b if b > 0 else 0
    return max(kelly * fraction, 0)


# =========================
# CORE MODEL
# =========================

class QuantEngine:

    SPORT_STD = {
        "NBA": 12.0,
        "NFL": 10.0,
        "MLB": 3.5
    }

    def __init__(self, simulations: int = 10000):
        self.simulations = simulations

    def aggregate_baseline(self, m: MatchupInput) -> float:
        eff_diff = sum(m.metrics_a.values()) - sum(m.metrics_b.values())
        situational = m.home_field_advantage + m.rest_diff
        injury = m.injury_impact_b - m.injury_impact_a
        historical = m.historical_diff
        return eff_diff + situational + injury + historical

    def monte_carlo(self, baseline_diff: float, sport: str) -> Tuple[np.ndarray, np.ndarray]:
        std = self.SPORT_STD.get(sport, 10.0)

        mean_a = 100 + baseline_diff / 2
        mean_b = 100 - baseline_diff / 2

        cov_matrix = [[std**2, 0.15 * std**2],
                      [0.15 * std**2, std**2]]

        scores = np.random.multivariate_normal(
            [mean_a, mean_b],
            cov_matrix,
            self.simulations
        )

        return scores[:, 0], scores[:, 1]

    def evaluate(self, m: MatchupInput) -> Dict:

        baseline = self.aggregate_baseline(m)
        scores_a, scores_b = self.monte_carlo(baseline, m.sport)

        wins_a = np.sum(scores_a > scores_b)
        win_prob_a = wins_a / self.simulations
        win_prob_b = 1 - win_prob_a

        mean_a = float(np.mean(scores_a))
        mean_b = float(np.mean(scores_b))
        spread = mean_a - mean_b
        spread_std = float(np.std(scores_a - scores_b))

        dec_a = american_to_decimal(m.market_odds_a)
        dec_b = american_to_decimal(m.market_odds_b)

        implied_a = 1 / dec_a
        implied_b = 1 / dec_b

        true_implied_a, true_implied_b = remove_vig(implied_a, implied_b)

        ev_a = win_prob_a * (dec_a - 1) - (1 - win_prob_a)
        ev_b = win_prob_b * (dec_b - 1) - (1 - win_prob_b)

        if ev_a > 0 and ev_a > ev_b:
            ev_bet = m.team_a
            kelly = fractional_kelly(win_prob_a, dec_a)
        elif ev_b > 0:
            ev_bet = m.team_b
            kelly = fractional_kelly(win_prob_b, dec_b)
        else:
            ev_bet = "No +EV"
            kelly = 0.0

        return {
            "TeamA": m.team_a,
            "TeamB": m.team_b,
            "TeamA_Score_Mean": mean_a,
            "TeamB_Score_Mean": mean_b,
            "TeamA_WinProb": win_prob_a,
            "TeamB_WinProb": win_prob_b,
            "Projected_Spread": spread,
            "Spread_StdDev": spread_std,
            "EV_Bet": ev_bet,
            "Kelly_Stake": kelly,
            "ConfidenceIntervals": {
                "TeamA_Score": [
                    float(np.percentile(scores_a, 5)),
                    float(np.percentile(scores_a, 95))
                ],
                "TeamB_Score": [
                    float(np.percentile(scores_b, 5)),
                    float(np.percentile(scores_b, 95))
                ]
            }
        }


# =========================
# EXECUTION ENTRY
# =========================

if __name__ == "__main__":

    sample_matchup = MatchupInput(
        team_a="LAL",
        team_b="BOS",
        sport="NBA",
        metrics_a={"off_rating": 3.2, "def_rating": -1.4, "pace": 1.1},
        metrics_b={"off_rating": 1.5, "def_rating": -0.8, "pace": 0.6},
        home_field_advantage=2.5,
        rest_diff=1.0,
        injury_impact_a=-0.5,
        injury_impact_b=-1.2,
        historical_diff=1.3,
        market_odds_a=-110,
        market_odds_b=-110
    )

    engine = QuantEngine(simulations=10000)
    result = engine.evaluate(sample_matchup)

    print(json.dumps(result, indent=4))
