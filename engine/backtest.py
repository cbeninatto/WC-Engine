"""Walk-forward backtest — pure (no I/O, no DB).

Replays played matches in kickoff order, predicting each from ratings re-rated only on
*earlier* finals (leakage-free), then folding the result in. The scoreboard and the tuner
both score the engine through this one path, so their numbers always agree, and — because
it's pure — a backtest needs no database (per the tuner spec).
"""
from __future__ import annotations

from .power import TeamForm, power, match_probs, expected_goals
from .rerate import apply_result
from .params import DEFAULT_PARAMS
from .scoring import outcome, evaluate


def _form_to_power(t: dict, p: dict):
    """A team_form dict -> prior power, or None if the team has no form yet."""
    if t.get("played") is None:
        return None
    return power(TeamForm(t["played"], t["wins"], t["draws"], t["losses"],
                          t["gf"], t["ga"], t["pass_acc"], t["pressing"], t["sos"]), p)


def walk_forward(team_forms: dict, finals: list[dict], p: dict = DEFAULT_PARAMS):
    """Replay finals leakage-free.

    team_forms : {team_id: form-dict with played/wins/draws/losses/gf/ga/pass_acc/pressing/sos}
    finals     : [{home_id, away_id, home_goals, away_goals, kickoff}, ...]
    returns (samples, rows): samples feed scoring.evaluate; rows carry per-match detail.
    """
    cur = {tid: pw for tid, t in team_forms.items()
           if (pw := _form_to_power(t, p)) is not None}
    samples, rows = [], []
    for m in sorted(finals, key=lambda x: x.get("kickoff") or ""):
        h, a = m["home_id"], m["away_id"]
        if h not in cur or a not in cur:
            continue
        probs = match_probs(cur[h], cur[a], p, raw=True)  # full precision for fair log-loss
        pg = tuple(round(g) for g in expected_goals(cur[h], cur[a], p))
        hg, ag = m["home_goals"], m["away_goals"]
        oc = outcome(hg, ag)
        samples.append({"probs": probs, "outcome": oc, "pred_goals": pg, "actual_goals": (hg, ag)})
        rows.append({"home_id": h, "away_id": a, "probs": probs, "pred_goals": pg,
                     "actual_goals": (hg, ag), "outcome": oc})
        cur[h], cur[a] = apply_result(cur[h], cur[a], hg, ag, p)  # then fold it in
    return samples, rows


def backtest(team_forms: dict, finals: list[dict], p: dict = DEFAULT_PARAMS) -> dict:
    """Aggregate scoring metrics for one param set over the backtest matches."""
    samples, _ = walk_forward(team_forms, finals, p)
    return evaluate(samples)
