"""Monte Carlo tournament simulation — pure (no I/O, no DB).

Integrates over the whole rest of the tournament: each trial finishes the remaining group
games (sampling scorelines from the engine's expected goals), reads off the 12 final tables
to seed the knockout bracket (top two per group + the 8 best third-placed teams), then plays
out the Round of 32 through the Final. Aggregating many trials gives each team's probability
of reaching every round and lifting the cup.

It's pure on purpose (same contract as backtest.py): feed it powers + fixtures and it needs
no database, so the API, a script, or a test can all drive it.

    powers    : {team_id: power_rating}              (live ratings, re-rated on played games)
    groups    : {group_code: [team_id, ...]}         (4 teams each)
    played    : [(group, home_id, away_id, hg, ag)]  (group-stage finals already recorded)
    remaining : [(group, home_id, away_id)]          (group-stage games not yet played)

Modelling notes (no fabrication — these are explicit, documented choices):
  * Group scorelines are Poisson draws around the engine's `expected_goals` means, which is
    the same scoreline model the live engine and scoreboard use.
  * Group ranking uses points, then goal difference, then goals for, then a random tiebreak
    (FIFA's deeper tiebreakers — head-to-head, fair play, drawing of lots — are not modelled;
    sampled scorelines make exact ties rare anyway).
  * Knockout ties resolve via `bracket.advance_prob` (90' result, draw decided by ET/pens).
"""
from __future__ import annotations

import math
import random

from .power import expected_goals
from .params import DEFAULT_PARAMS
from . import bracket

ROUNDS = ("qual", "r16", "qf", "sf", "final", "champ")


def _poisson(lam: float, rng: random.Random) -> int:
    """Knuth's sampler — fine for the small means (~0-4 goals) we deal with."""
    if lam <= 0:
        return 0
    target = math.exp(-lam)
    k, prod = 0, 1.0
    while True:
        k += 1
        prod *= rng.random()
        if prod <= target:
            return k - 1


def _apply(table: dict, h: str, a: str, hg: int, ag: int) -> None:
    """Fold one result into a {team: [pts, gd, gf]} table (mutates in place)."""
    th, ta = table[h], table[a]
    th[1] += hg - ag; ta[1] += ag - hg
    th[2] += hg;      ta[2] += ag
    if hg > ag:
        th[0] += 3
    elif hg < ag:
        ta[0] += 3
    else:
        th[0] += 1; ta[0] += 1


def simulate(powers: dict, groups: dict, played: list, remaining: list,
             p: dict = DEFAULT_PARAMS, n: int = 4000, seed=None) -> dict:
    """Run `n` trials. Returns {team_id: {round: probability}} over ROUNDS."""
    rng = random.Random(seed)

    # Baseline standings from games already played (constant across trials).
    base = {tid: [0, 0, 0] for members in groups.values() for tid in members}
    for (_g, h, a, hg, ag) in played:
        if h in base and a in base:
            _apply(base, h, a, hg, ag)

    # Pre-compute the Poisson means for each remaining group game once.
    rem = [(g, h, a, expected_goals(powers[h], powers[a], p)) for (g, h, a) in remaining]

    counts = {tid: dict.fromkeys(ROUNDS, 0) for tid in powers}

    for _ in range(n):
        table = {tid: row[:] for tid, row in base.items()}
        for (_g, h, a, (lam_h, lam_a)) in rem:
            _apply(table, h, a, _poisson(lam_h, rng), _poisson(lam_a, rng))

        # Read off each group's finish (random 4th key breaks any exact tie).
        winners, runners, thirds, third_recs = {}, {}, {}, []
        for g, members in groups.items():
            ranked = sorted(members, key=lambda t: (table[t][0], table[t][1], table[t][2], rng.random()),
                            reverse=True)
            winners[g], runners[g], thirds[g] = ranked[0], ranked[1], ranked[2]
            r = table[ranked[2]]
            third_recs.append((r[0], r[1], r[2], rng.random(), g))

        # The 8 best third-placed teams qualify; route them into their bracket slots.
        third_recs.sort(reverse=True)
        qual_groups = [g for (*_r, g) in third_recs[:8]]
        assignment = bracket.assign_thirds(qual_groups)

        for g in groups:
            counts[winners[g]]["qual"] += 1
            counts[runners[g]]["qual"] += 1
        for g in qual_groups:
            counts[thirds[g]]["qual"] += 1

        # Play the bracket. winner_of[m] = team that wins match m.
        pairs = bracket.r32_pairings(winners, runners, thirds, assignment)
        winner_of = {}
        for mno, (ta, tb) in pairs.items():
            w = ta if rng.random() < bracket.advance_prob(powers[ta], powers[tb], p) else tb
            winner_of[mno] = w
            counts[w]["r16"] += 1
        for mno in bracket.KO_ORDER:
            sa, sb = bracket.KO_TREE[mno]
            ta, tb = winner_of[sa], winner_of[sb]
            w = ta if rng.random() < bracket.advance_prob(powers[ta], powers[tb], p) else tb
            winner_of[mno] = w
            counts[w][bracket.ADVANCE_LABEL[mno]] += 1

    return {tid: {r: c[r] / n for r in ROUNDS} for tid, c in counts.items()}


def project_bracket(powers: dict, groups: dict, played: list, remaining: list,
                    p: dict = DEFAULT_PARAMS) -> dict:
    """The single most-likely knockout bracket — deterministic, no sampling.

    Finishes the group stage on the engine's expected scorelines (rounded), seeds the R32
    exactly as the live tournament would (top two per group + the 8 best third-placed teams),
    then advances the favourite of each tie (higher `bracket.advance_prob`) through to a
    projected champion. Pure — same input contract as `simulate`.

    Returns:
        matches : {match_no: {"a", "b", "p_adv", "winner"}}  team ids; p_adv = P(a advances)
        champion: team id that wins the Final (match 104)
        seeds   : {"winners","runners","thirds": group->team, "assignment": slot->group}
        qual_groups : the 8 groups whose third-placed team qualifies
    """
    table = {tid: [0, 0, 0] for members in groups.values() for tid in members}
    for (_g, h, a, hg, ag) in played:
        if h in table and a in table:
            _apply(table, h, a, hg, ag)
    for (_g, h, a) in remaining:
        if h in table and a in table:
            lam_h, lam_a = expected_goals(powers[h], powers[a], p)
            _apply(table, h, a, int(round(lam_h)), int(round(lam_a)))

    # Rank each group (points, GD, GF, then power as a deterministic final tiebreak).
    winners, runners, thirds, third_recs = {}, {}, {}, []
    for g, members in groups.items():
        ranked = sorted(members,
                        key=lambda t: (table[t][0], table[t][1], table[t][2], powers[t]),
                        reverse=True)
        winners[g], runners[g], thirds[g] = ranked[0], ranked[1], ranked[2]
        r = table[ranked[2]]
        third_recs.append((r[0], r[1], r[2], powers[ranked[2]], g))

    third_recs.sort(reverse=True)
    qual_groups = [g for (*_r, g) in third_recs[:8]]
    assignment = bracket.assign_thirds(qual_groups)

    matches, winner_of = {}, {}

    def _play(mno: int, ta, tb) -> None:
        p_adv = bracket.advance_prob(powers[ta], powers[tb], p)
        winner_of[mno] = ta if p_adv >= 0.5 else tb
        matches[mno] = {"a": ta, "b": tb, "p_adv": p_adv, "winner": winner_of[mno]}

    for mno, (ta, tb) in bracket.r32_pairings(winners, runners, thirds, assignment).items():
        _play(mno, ta, tb)
    for mno in bracket.KO_ORDER:
        sa, sb = bracket.KO_TREE[mno]
        _play(mno, winner_of[sa], winner_of[sb])

    return {
        "matches": matches,
        "champion": winner_of[104],
        "qual_groups": qual_groups,
        "seeds": {"winners": winners, "runners": runners, "thirds": thirds,
                  "assignment": assignment},
    }


if __name__ == "__main__":
    # Build a synthetic but well-formed world: 12 groups of 4, no games played yet, so the
    # simulation drives the entire tournament. Equal powers => everyone equally likely.
    groups = {chr(ord("A") + i): [f"t{i}-{j}" for j in range(4)] for i in range(12)}
    powers = {tid: 70.0 for members in groups.values() for tid in members}
    remaining = [(g, m[i], m[k])  # all 6 round-robin games per group, none played yet
                 for g, m in groups.items()
                 for i in range(4) for k in range(i + 1, 4)]
    n = 2000
    probs = simulate(powers, groups, [], remaining, n=n, seed=1)

    # Per trial there are exactly 32 qualifiers, 16/8/4/2 survivors, 1 champion. Aggregate
    # totals must match exactly (the counts are integers / n).
    tot = {r: round(sum(p[r] for p in probs.values()) * n) for r in ROUNDS}
    assert tot == {"qual": 32 * n, "r16": 16 * n, "qf": 8 * n, "sf": 4 * n,
                   "final": 2 * n, "champ": 1 * n}, tot
    # Monotonic: reaching a later round implies reaching every earlier one.
    for tid, pr in probs.items():
        assert pr["qual"] >= pr["r16"] >= pr["qf"] >= pr["sf"] >= pr["final"] >= pr["champ"]
    # With equal strength, champion odds should sit near 1/48 for everyone.
    champ = [p["champ"] for p in probs.values()]
    assert abs(max(champ) - 1 / 48) < 0.02 and abs(min(champ) - 1 / 48) < 0.02

    # Projected bracket: a full, well-formed tree (16 R32 + 15 later ties) with a champion.
    proj = project_bracket(powers, groups, [], remaining)
    assert len(proj["matches"]) == 31, len(proj["matches"])
    assert proj["champion"] in powers
    assert len(proj["qual_groups"]) == 8 and len(set(proj["qual_groups"])) == 8
    print(f"engine.simulate self-check OK ({n} trials + projected bracket verified)")
