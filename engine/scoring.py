"""Scoring — grade W/D/L forecasts against observed results. Pure functions, no I/O.

This is the foundation the tuner optimizes and the scoreboard reports. A match outcome is
encoded as an ordinal class:

    0 = home win   1 = draw   2 = away win

and a forecast is a probability vector (p_home, p_draw, p_away). Brier / log-loss / RPS are
all "lower is better"; accuracy and exact-score rates are "higher is better".
"""
from __future__ import annotations
import math


def outcome(home_goals: int, away_goals: int) -> int:
    """Observed W/D/L class from a final scoreline."""
    if home_goals > away_goals:
        return 0
    if home_goals == away_goals:
        return 1
    return 2


def _norm(probs) -> list[float]:
    """Guard against rounding drift / degenerate inputs so the metrics stay well-defined."""
    s = sum(probs)
    return [p / s for p in probs] if s > 0 else [1 / 3, 1 / 3, 1 / 3]


def brier(probs, oc: int) -> float:
    """Multiclass Brier score: sum_i (p_i - y_i)^2 over the one-hot outcome. Range 0..2."""
    p = _norm(probs)
    return sum((p[i] - (1.0 if i == oc else 0.0)) ** 2 for i in range(3))


def log_loss(probs, oc: int, eps: float = 1e-15) -> float:
    """Negative log-likelihood of the observed outcome (clipped to stay finite)."""
    p = _norm(probs)
    return -math.log(max(p[oc], eps))


def rps(probs, oc: int) -> float:
    """Ranked Probability Score for ordered W/D/L.

    Because home>draw>away is an ordinal scale, RPS penalizes being far off in order:
    calling a draw when the away side wins scores better than calling a home win. Range
    0..1; 0 is perfect.
    """
    p = _norm(probs)
    o = [1.0 if i == oc else 0.0 for i in range(3)]
    cum_p = cum_o = total = 0.0
    for i in range(2):  # r-1 = 2 cumulative steps for 3 ordered categories
        cum_p += p[i]
        cum_o += o[i]
        total += (cum_p - cum_o) ** 2
    return total / 2.0


def _pick(probs) -> int:
    """The model's called result = most likely class."""
    return max(range(3), key=lambda i: probs[i])


def evaluate(samples) -> dict:
    """Aggregate metrics over graded predictions.

    samples: iterable of dicts with keys
        probs        (p_home, p_draw, p_away)
        outcome      observed class 0/1/2
        pred_goals   (home, away) ints  — optional, for exact-score rate
        actual_goals (home, away) ints  — optional
    """
    samples = list(samples)
    n = len(samples)
    if not n:
        return {"n": 0}
    hits = sum(1 for s in samples if _pick(s["probs"]) == s["outcome"])
    exact = sum(1 for s in samples
                if s.get("pred_goals") is not None
                and tuple(s["pred_goals"]) == tuple(s["actual_goals"]))
    return {
        "n": n,
        "winner_hits": hits,
        "winner_acc": hits / n,
        "exact_hits": exact,
        "exact_rate": exact / n,
        "brier": sum(brier(s["probs"], s["outcome"]) for s in samples) / n,
        "log_loss": sum(log_loss(s["probs"], s["outcome"]) for s in samples) / n,
        "rps": sum(rps(s["probs"], s["outcome"]) for s in samples) / n,
        "pred_draws": sum(1 for s in samples if _pick(s["probs"]) == 1),
        "actual_draws": sum(1 for s in samples if s["outcome"] == 1),
    }
