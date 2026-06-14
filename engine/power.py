"""The prediction engine — power rating + match probabilities.

This is a direct port of the Excel model so results stay identical:

    power = ( PPG/3 * 40
              + clamp(GF/g - GA/g, -2.5, 2) * 8
              + clamp(1.3 - GA/g, -0.7, 1.3) * 6 ) * SoS
            + PassAcc * 0.3 + Pressing * 1.5
"""
from __future__ import annotations
from dataclasses import dataclass
from .params import DEFAULT_PARAMS


@dataclass
class TeamForm:
    played: int
    wins: int
    draws: int
    losses: int
    gf: int
    ga: int
    pass_acc: float = 80.0
    pressing: float = 6.0
    sos: float = 1.0
    power_adjustment: float = 0.0  # from squad_status (injuries etc.)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def power(form: TeamForm, p: dict = DEFAULT_PARAMS) -> float:
    """Per-game power rating. Sample-size agnostic by design (uses rates, not totals)."""
    if form.played <= 0:
        return 0.0
    ppg3 = (3 * form.wins + form.draws) / (3 * form.played)
    avg_gf = form.gf / form.played
    avg_ga = form.ga / form.played
    gd = _clamp(avg_gf - avg_ga, p["gd_cap_lo"], p["gd_cap_hi"])
    dfn = _clamp(p["def_anchor"] - avg_ga, p["def_cap_lo"], p["def_cap_hi"])
    inner = ppg3 * p["ppg_w"] + gd * p["gd_w"] + dfn * p["def_w"]
    raw = inner * form.sos + form.pass_acc * p["pass_w"] + form.pressing * p["press_w"]
    return round(raw + form.power_adjustment, 1)


def match_probs(power_a: float, power_b: float, p: dict = DEFAULT_PARAMS):
    """Win/Draw/Loss for A vs B. Draw curve is widest for evenly matched sides."""
    d = power_a - power_b
    draw = max(p["draw_floor"], p["draw_base"] - abs(d) / p["draw_slope"])
    win_a = (1 / (1 + 10 ** (-d / p["logistic_scale"]))) * (1 - draw)
    win_b = 1 - win_a - draw
    return round(win_a, 3), round(draw, 3), round(win_b, 3)


def expected_score(power_a: float, power_b: float, p: dict = DEFAULT_PARAMS) -> float:
    """Logistic expectation for A (1=win .. 0=loss). Used by the in-tournament re-rate."""
    return 1 / (1 + 10 ** (-(power_a - power_b) / p["logistic_scale"]))
