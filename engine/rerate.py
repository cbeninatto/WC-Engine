"""In-tournament re-rate — the highest-value upgrade once real WC games are played.

Each team starts from its form-based `prior_power`. As actual World Cup results come
in, we nudge power with an Elo-style update keyed off the model's own logistic:

    expected_A = 1 / (1 + 10^(-(Pa - Pb)/scale))
    actual_A   = 1.0 win | 0.5 draw | 0.0 loss
    Pa += K * (actual_A - expected_A);   Pb -= K * (actual_A - expected_A)

This is a light Bayesian update: the form prior dominates early; real results pull the
rating toward what the team is actually doing on the pitch — the thing the static form
model cannot see.
"""
from __future__ import annotations
from .params import DEFAULT_PARAMS
from .power import expected_score


def apply_result(power_a, power_b, home_goals, away_goals, p=DEFAULT_PARAMS, k=None):
    """Return updated (power_a, power_b) after one final match."""
    k = p["rerate_k"] if k is None else k
    exp_a = expected_score(power_a, power_b, p)
    if home_goals > away_goals:
        actual_a = 1.0
    elif home_goals == away_goals:
        actual_a = 0.5
    else:
        actual_a = 0.0
    delta = k * (actual_a - exp_a)
    return round(power_a + delta, 2), round(power_b - delta, 2)


def rerate_all(prior_powers: dict[str, float], final_matches: list[dict],
               p: dict = DEFAULT_PARAMS) -> tuple[dict, dict]:
    """Fold every final WC match into the prior ratings, in kickoff order.

    prior_powers : {team_id: prior_power}
    final_matches: [{home_id, away_id, home_goals, away_goals, kickoff}, ...]
    returns (post_powers, wc_games_count)
    """
    power = dict(prior_powers)
    games: dict[str, int] = {t: 0 for t in prior_powers}
    for m in sorted(final_matches, key=lambda x: x.get("kickoff") or ""):
        h, a = m["home_id"], m["away_id"]
        if h not in power or a not in power:
            continue
        power[h], power[a] = apply_result(
            power[h], power[a], m["home_goals"], m["away_goals"], p
        )
        games[h] += 1
        games[a] += 1
    return power, games
