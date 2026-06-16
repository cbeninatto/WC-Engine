"""Build a held-out backtest dataset for the tuner from a real past World Cup.

The tuner (agents/tuner.py) needs OUT-OF-SAMPLE matches; backtesting the live WC2026 finals
in-sample overfits (that's how model_params v2 "improved" Brier only by flattening every
prediction). SportDB's world-championship feed carries each cycle's qualifiers AND its
finals, so for a past tournament we can reconstruct every finalist's REAL pre-tournament
form from its qualifying results, then score the engine on the 64 finals — leakage-free,
because the form uses only matches dated before the finals window.

GUARDRAIL #2 (never fabricate): every number here is a sourced SportDB result.
  - 90-minute (regulation) scores are used, so a penalty shootout reads as the draw it was
    (lib.sportdb._ft_goals).
  - pass_acc / pressing aren't in a results feed, so each team gets the engine's OWN defaults
    (TeamForm: 80.0 / 6.0). That's a constant that cancels in the power GAPS the tuner tunes,
    not invented data.
  - SoS is the confederation default; a team's confederation is geography, not a fabricated
    result. Teams with too little sourced pre-tournament form are EXCLUDED and reported, never
    padded.

Output: data/holdout/wc<season>.json = {"team_forms": {...}, "finals": [...]}, the exact shape
engine/backtest.walk_forward + tuner.load_backtest_set already consume.

    python scripts/build_holdout.py                                   # default: WC2022
    python scripts/build_holdout.py --season 2022 --start 2022-11-20 --end 2022-12-18
    python scripts/build_holdout.py --max-form 20 --min-form 3 --out data/holdout/wc2022.json
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config  # noqa: F401  (loads .env so the API key resolves)
import lib.db as db
from lib import sportdb
from agents.ingest import _norm, NAME_ALIASES
from engine.params import CONFED_SOS
from engine.scoring import outcome

# FIFA confederation for past-tournament finalists that aren't in our 48-team WC2026 DB.
# This is geography (which confederation a country belongs to), not a fabricated match result.
FALLBACK_CONFED = {
    "cameroon": "CAF", "costarica": "CONCACAF", "denmark": "UEFA",
    "poland": "UEFA", "serbia": "UEFA", "wales": "UEFA",
}


def slugify(name: str) -> str:
    """Lowercase-hyphen slug from a team name (guardrail #3), e.g. 'Costa Rica' -> 'costa-rica'."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build_resolver(conn):
    """name -> (slug, confederation). Maps to our DB slug+confederation when the team exists
    (so holdout ids line up with real slugs), else slugify + the FIFA-confederation fallback."""
    teams = db.teams_with_form(conn)
    by_norm = {_norm(t["name"]): t for t in teams.values()}

    def resolve(api_name: str):
        n = NAME_ALIASES.get(_norm(api_name), _norm(api_name))
        t = by_norm.get(n)
        if t:
            return t["id"], t["confederation"]
        return slugify(api_name), FALLBACK_CONFED.get(n)

    return resolve


def team_record(name: str, matches: list[dict]) -> dict:
    """played/wins/draws/losses/gf/ga for `name` over its matches (FT scores, team's view)."""
    nn = NAME_ALIASES.get(_norm(name), _norm(name))
    rec = {"played": 0, "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0}
    for m in matches:
        home = NAME_ALIASES.get(_norm(m["home"]), _norm(m["home"])) == nn
        away = NAME_ALIASES.get(_norm(m["away"]), _norm(m["away"])) == nn
        if not (home or away):
            continue
        gf, ga = (m["home_goals"], m["away_goals"]) if home else (m["away_goals"], m["home_goals"])
        rec["played"] += 1
        rec["gf"] += gf
        rec["ga"] += ga
        rec["wins" if gf > ga else "draws" if gf == ga else "losses"] += 1
    return rec


def main():
    ap = argparse.ArgumentParser(description="Build a held-out WC backtest dataset from SportDB.")
    ap.add_argument("--season", type=int, default=2022)
    ap.add_argument("--start", default="2022-11-20", help="finals window start (YYYY-MM-DD)")
    ap.add_argument("--end", default="2022-12-18", help="finals window end (YYYY-MM-DD)")
    ap.add_argument("--max-form", type=int, default=20, help="most recent N pre-finals matches")
    ap.add_argument("--min-form", type=int, default=3,
                    help="exclude (and report) teams with fewer sourced pre-finals matches")
    ap.add_argument("--out", default=None, help="output path (default data/holdout/wc<season>.json)")
    args = ap.parse_args()

    conn = db.connect()
    db.init_db(conn)
    resolve = build_resolver(conn)
    conn.close()

    print(f"\nBUILD HOLDOUT  WC{args.season}  finals window {args.start}..{args.end}\n")
    feed = sportdb.tournament_matches(args.season)
    finals_raw = [m for m in feed if args.start <= m["date"] <= args.end]
    pre = [m for m in feed if m["date"] < args.start]
    print(f"  feed: {len(feed)} finished · finals-in-window: {len(finals_raw)} · "
          f"pre-finals (qualifiers): {len(pre)}")

    finalists = sorted({m["home"] for m in finals_raw} | {m["away"] for m in finals_raw})
    print(f"  finalist teams: {len(finalists)}")

    # --- per-team pre-tournament form (most recent <= max_form qualifiers) ---
    team_forms: dict[str, dict] = {}
    slug_of: dict[str, str] = {}
    excluded: list[str] = []
    no_confed: list[str] = []
    for name in finalists:
        slug, confed = resolve(name)
        slug_of[name] = slug
        mine = sorted([m for m in pre
                       if NAME_ALIASES.get(_norm(m["home"]), _norm(m["home"])) == NAME_ALIASES.get(_norm(name), _norm(name))
                       or NAME_ALIASES.get(_norm(m["away"]), _norm(m["away"])) == NAME_ALIASES.get(_norm(name), _norm(name))],
                      key=lambda m: m["date"], reverse=True)[:args.max_form]
        rec = team_record(name, mine)
        if rec["played"] < args.min_form:
            excluded.append(f"{name} ({rec['played']} pre-finals matches)")
            continue
        if confed is None:
            no_confed.append(name)
        rec.update(pass_acc=80.0, pressing=6.0, sos=CONFED_SOS.get(confed, 1.0))
        team_forms[slug] = rec

    # --- finals where BOTH teams have reconstructed form ---
    finals: list[dict] = []
    dropped: list[str] = []
    for m in finals_raw:
        hs, as_ = slug_of[m["home"]], slug_of[m["away"]]
        if hs in team_forms and as_ in team_forms:
            finals.append({"home_id": hs, "away_id": as_, "home_goals": m["home_goals"],
                           "away_goals": m["away_goals"], "kickoff": m["date"]})
        else:
            dropped.append(f"{m['home']} {m['home_goals']}-{m['away_goals']} {m['away']} ({m['date']})")

    # --- report ---
    if excluded:
        print(f"\n  excluded (< {args.min_form} sourced pre-finals matches — not padded):")
        for e in excluded:
            print(f"    - {e}")
    if no_confed:
        print(f"\n  [!] no confederation mapping (SoS fell back to 1.0): {', '.join(no_confed)}")
    if dropped:
        print(f"\n  {len(dropped)} final(s) dropped (a team lacked form):")
        for d in dropped:
            print(f"    - {d}")

    # --- validation: leakage + sanity ---
    assert all(m["date"] < args.start for m in pre), "leakage: a form match is inside the window"
    assert all(args.start <= f["kickoff"] <= args.end for f in finals), "a final is outside the window"
    draws = sum(1 for f in finals if outcome(f["home_goals"], f["away_goals"]) == 1)
    draw_rate = draws / len(finals) if finals else 0.0
    # spot-check: the 2022 final must read as a regulation draw, not a home win
    arg_fra = next((f for f in finals if {f["home_id"], f["away_id"]} == {"argentina", "france"}), None)

    print(f"\n  RESULT  team_forms: {len(team_forms)} · finals kept: {len(finals)} · "
          f"draws: {draws} ({draw_rate:.0%})")
    if arg_fra:
        oc = ["HOME", "DRAW", "AWAY"][outcome(arg_fra["home_goals"], arg_fra["away_goals"])]
        print(f"  spot-check Argentina–France: {arg_fra['home_goals']}-{arg_fra['away_goals']} -> {oc}"
              f"  (must be DRAW — pens stripped)")

    out = Path(args.out) if args.out else (config.ROOT / "data" / "holdout" / f"wc{args.season}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"team_forms": team_forms, "finals": finals}, indent=2,
                              ensure_ascii=False), encoding="utf-8")
    print(f"\n  Wrote {out}  ->  python agents/tuner.py --holdout {out}")


if __name__ == "__main__":
    main()
