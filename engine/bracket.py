"""The 2026 World Cup knockout bracket — structure + slot resolution. Pure, no I/O.

The 2026 format adds a Round of 32: the top two of each of the 12 groups (24 teams) plus
the 8 best third-placed teams advance. FIFA's Annex C fixes the R32 pairings so that no
team meets a group-mate, and every third-placed team faces a group winner. The exact
matchups are published structure (sourced from the official bracket), encoded below.

Slot codes used in `R32`:
    ("W", "A")  -> winner of Group A
    ("R", "A")  -> runner-up of Group A
    ("T", 74)   -> the best third-placed team routed into match 74 (pool in THIRD_POOLS)

The eight third-placed slots each list the groups whose third-placed team may land there.
Which of the 12 third-placed teams actually qualify isn't known until the groups finish,
so `assign_thirds` resolves a concrete routing for any set of 8 qualifying groups via the
same constraints FIFA uses (a perfect matching of qualifying groups to eligible slots).
NOTE: FIFA's Annex C picks one specific assignment per scenario; when several valid
matchings exist we pick a deterministic one — it can differ from the official table in
*which* winner faces a given third, which is immaterial to aggregate simulation odds.
"""
from __future__ import annotations

from .power import match_probs
from .params import DEFAULT_PARAMS

# --- the bracket graph (official 2026 structure) -----------------------------

# Round of 32: (match_no, slot_a, slot_b).
R32 = [
    (73, ("R", "A"), ("R", "B")),
    (74, ("W", "E"), ("T", 74)),
    (75, ("W", "F"), ("R", "C")),
    (76, ("W", "C"), ("R", "F")),
    (77, ("W", "I"), ("T", 77)),
    (78, ("R", "E"), ("R", "I")),
    (79, ("W", "A"), ("T", 79)),
    (80, ("W", "L"), ("T", 80)),
    (81, ("W", "D"), ("T", 81)),
    (82, ("W", "G"), ("T", 82)),
    (83, ("R", "K"), ("R", "L")),
    (84, ("W", "H"), ("R", "J")),
    (85, ("W", "B"), ("T", 85)),
    (86, ("W", "J"), ("R", "H")),
    (87, ("W", "K"), ("T", 87)),
    (88, ("R", "D"), ("R", "G")),
]

# Eligible group pools for each best-third-placed slot (FIFA Annex C).
THIRD_POOLS = {
    74: "ABCDF",
    77: "CDFGH",
    79: "CEFHI",
    80: "EHIJK",
    81: "BEFIJ",
    82: "AEHIJ",
    85: "EFGIJ",
    87: "DEIJL",
}
THIRD_SLOTS = list(THIRD_POOLS)  # the 8 match numbers that host a third-placed team

# Knockout tree from the Round of 16 on: match -> (source match A, source match B).
# 103 is the third-place play-off (ignored for advancement); 104 is the Final.
KO_TREE = {
    89: (74, 77), 90: (73, 75), 91: (76, 78), 92: (79, 80),
    93: (83, 84), 94: (81, 82), 95: (86, 88), 96: (85, 87),
    97: (89, 90), 98: (93, 94), 99: (91, 92), 100: (95, 96),
    101: (97, 98), 102: (99, 100),
    104: (101, 102),
}
# Matches after the R32, in the order they must be played (each needs its feeders done).
KO_ORDER = [89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 104]

# The round a match's *winner* earns by winning it.
ADVANCE_LABEL = {m: "r16" for m in range(73, 89)}
ADVANCE_LABEL.update({m: "qf" for m in (89, 90, 91, 92, 93, 94, 95, 96)})
ADVANCE_LABEL.update({m: "sf" for m in (97, 98, 99, 100)})
ADVANCE_LABEL.update({m: "final" for m in (101, 102)})
ADVANCE_LABEL[104] = "champ"


# --- third-placed routing ----------------------------------------------------

def assign_thirds(qualified_groups) -> dict[int, str]:
    """Map each third-placed slot to one of the 8 qualifying groups.

    Returns {match_no: group_letter}. Uses MRV backtracking to find a perfect matching of
    qualifying groups to eligible slots (FIFA guarantees one exists for every combination
    of 8 of the 12 groups). Deterministic given the input set.
    """
    qualified = set(qualified_groups)
    cand = {m: set(THIRD_POOLS[m]) & qualified for m in THIRD_SLOTS}
    assignment: dict[int, str] = {}
    used: set[str] = set()

    def backtrack(slots: list[int]) -> bool:
        if not slots:
            return True
        # Most-constrained slot first (fewest still-available eligible groups).
        m = min(slots, key=lambda s: len(cand[s] - used))
        for g in sorted(cand[m] - used):
            used.add(g)
            assignment[m] = g
            if backtrack([s for s in slots if s != m]):
                return True
            used.discard(g)
            del assignment[m]
        return False

    if not backtrack(THIRD_SLOTS):
        # Should be unreachable; degrade gracefully rather than crash a simulation.
        leftover = [g for g in qualified if g not in used]
        for m in THIRD_SLOTS:
            if m not in assignment and leftover:
                assignment[m] = leftover.pop()
    return assignment


def _resolve(slot, winners, runners, thirds, assignment):
    kind, key = slot
    if kind == "W":
        return winners[key]
    if kind == "R":
        return runners[key]
    return thirds[assignment[key]]  # ("T", match_no)


def r32_pairings(winners, runners, thirds, assignment) -> dict[int, tuple]:
    """Concrete R32 team pairs: {match_no: (team_a, team_b)}.

    winners/runners/thirds map group letter -> team id; assignment is from assign_thirds.
    """
    return {mno: (_resolve(a, winners, runners, thirds, assignment),
                  _resolve(b, winners, runners, thirds, assignment))
            for (mno, a, b) in R32}


# --- knockout match resolution -----------------------------------------------

def advance_prob(power_a: float, power_b: float, p: dict = DEFAULT_PARAMS) -> float:
    """P(team A advances past B) in a knockout tie.

    A knockout has no draw: a level game at 90' goes to extra time / penalties. We take the
    engine's 90' win/draw/loss split and award the draw mass to the two sides in proportion
    to their relative win probability (a modest edge to the stronger side, ~50/50 when even).
    """
    win_a, draw, win_b = match_probs(power_a, power_b, p, raw=True)
    denom = win_a + win_b
    share = win_a / denom if denom > 0 else 0.5
    return win_a + draw * share


if __name__ == "__main__":
    # Structure sanity: 16 R32 ties, 8 of them hosting a third-placed team.
    assert len(R32) == 16
    assert sum(1 for _, _, b in R32 if b[0] == "T") == 8
    assert sorted(THIRD_SLOTS) == sorted(b[1] for _, _, b in R32 if b[0] == "T")

    # advance_prob is a valid complementary probability.
    for pa, pb in [(90, 60), (75, 75), (50, 80)]:
        a, b = advance_prob(pa, pb), advance_prob(pb, pa)
        assert 0.0 <= a <= 1.0 and abs(a + b - 1.0) < 1e-9, (pa, pb, a, b)
    assert abs(advance_prob(70, 70) - 0.5) < 1e-9  # even tie is a coin flip

    # assign_thirds finds a valid perfect matching for every combination of 8 of 12 groups.
    from itertools import combinations
    groups = "ABCDEFGHIJKL"
    checked = 0
    for combo in combinations(groups, 8):
        a = assign_thirds(combo)
        assert len(a) == 8 and len(set(a.values())) == 8
        assert set(a.values()) <= set(combo)
        for m, g in a.items():
            assert g in THIRD_POOLS[m], (combo, m, g)
        checked += 1
    print(f"engine.bracket self-check OK ({checked} third-place scenarios, all matched)")
