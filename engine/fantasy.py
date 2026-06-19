"""Fantasy-league scoring — grade a scoreline guess against the final result.

Pure functions, no I/O (same contract as engine/scoring.py). Where scoring.py grades the
engine's *probabilistic* W/D/L forecast (Brier/log-loss/RPS), this grades an *exact
scoreline* pick the way a prediction pool does — so the same rules can score the user's
picks and the engine's own rounded projection head-to-head.

The point ladder (a Brazilian "bolão" system), highest applicable wins:

    15  Cravada            exact score            (2x1, was 2x1)
     9  Vencedor + Saldo   right winner + margin  (3x1, was 2x0)
     8  Empate (não exato) right draw, wrong score(1x1, was 2x2)
     7  Vencedor + Gols    right winner + their goal count (2x1, was 2x0)
     5  Apenas Vencedor    right winner only      (2x1, was 4x0)
     0  miss               wrong outcome
"""
from __future__ import annotations

# category key -> (points, human label). Ordered high to low for readability.
CATEGORIES = {
    "exact":   (15, "Cravada (exact score)"),
    "saldo":   (9,  "Vencedor + Saldo (winner + margin)"),
    "draw":    (8,  "Empate (right draw, wrong score)"),
    "gols":    (7,  "Vencedor + Gols (winner + their goals)"),
    "winner":  (5,  "Apenas Vencedor (winner only)"),
    "miss":    (0,  "Errou (wrong outcome)"),
}


def _category(ph: int, pa: int, ah: int, aa: int) -> str:
    """Best-matching scoring category for pick (ph,pa) vs actual (ah,aa)."""
    if (ph, pa) == (ah, aa):
        return "exact"
    pred_margin, act_margin = ph - pa, ah - aa
    # Both draws but not identical -> "empate não exato".
    if pred_margin == 0 and act_margin == 0:
        return "draw"
    # Same (non-draw) winner: rank margin match above winner's-goal match.
    if pred_margin != 0 and act_margin != 0 and (pred_margin > 0) == (act_margin > 0):
        if pred_margin == act_margin:
            return "saldo"
        winner_pred = ph if pred_margin > 0 else pa
        winner_act = ah if act_margin > 0 else aa
        if winner_pred == winner_act:
            return "gols"
        return "winner"
    return "miss"


def score(pred_home: int, pred_away: int, act_home: int, act_away: int) -> dict:
    """Grade one scoreline pick. Returns {points, category, label}."""
    cat = _category(int(pred_home), int(pred_away), int(act_home), int(act_away))
    points, label = CATEGORIES[cat]
    return {"points": points, "category": cat, "label": label}


if __name__ == "__main__":
    # Self-check against the five worked examples in the league's rules + edges.
    cases = [
        ((2, 1), (2, 1), "exact", 15),
        ((3, 1), (2, 0), "saldo", 9),
        ((1, 1), (2, 2), "draw", 8),
        ((2, 1), (2, 0), "gols", 7),
        ((2, 1), (4, 0), "winner", 5),
        ((0, 2), (1, 1), "miss", 0),      # predicted away win, was a draw
        ((1, 2), (0, 2), "gols", 7),      # away win, away's goal count matches (margin differs)
        ((2, 2), (2, 2), "exact", 15),    # exact draw beats the draw bucket
    ]
    for (p, a, want_cat, want_pts) in cases:
        r = score(*p, *a)
        assert r["category"] == want_cat and r["points"] == want_pts, (p, a, r)
    print(f"engine.fantasy self-check OK ({len(cases)} cases)")
