"""Team-form builder — rebuild every team's form from real SportDB results, not the workbook.

This is the agent that lets us retire WorldCup2026_Analytics_Companion.xlsx as the form
source. The workbook seeded `team_form` with an inconsistent mix of windows (8 qualifying
games for Austria, 20 for France) — and because engine.power is RATE-based and sample-size
agnostic, an 8-game weak-schedule run scored like a 20-game elite one (Austria ranked #1).
The fix is a SINGLE, uniform, sourced window for all 48 teams.

WHAT IT DOES
  For each of our 48 teams, pull its last `--window` (default 20) OFFICIAL matches from the
  same reachable SportDB feeds the SoS sourcer uses (WC2026 qualifiers + continental cups +
  friendlies), most-recent-first, and recompute played/wins/draws/losses/gf/ga from the real
  90-minute scorelines. pass_acc / pressing / sos / notes are NOT touched — they aren't in a
  results feed, so the curated DB values are preserved (SoS is the SoS sourcer's job).

GUARDRAILS
  #1 propose, don't apply -> default writes data/team_form_proposed.json + a 'proposed'
     agent_run; the live team_form is untouched. Re-run with --apply to commit the reviewed
     snapshot (writes team_form, logs 'applied', and recomputes ratings).
  #2 never fabricate -> every number is a sourced SportDB result (90-minute, so a shootout
     reads as a draw). Feeds that 500 / return nothing are REPORTED and skipped. A team with
     fewer than --min-form sourced matches KEEPS its existing form and is flagged, never padded.
  #3 full names / lowercase-hyphen slugs -> reuses the ingest normalizer + alias table.

    python agents/build_team_form.py --dry-run     # print the rebuilt form + provenance, write nothing
    python agents/build_team_form.py               # write data/team_form_proposed.json + agent_run
    python agents/build_team_form.py --apply        # commit the snapshot to team_form, then recompute
    python agents/build_team_form.py --window 20 --since 2024-01-01 --min-form 8
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

import config
import lib.db as db
from agents.ingest import _norm, NAME_ALIASES
from agents.sos_sourcer import gather, nkey
from scripts.build_holdout import team_record
from engine.power import TeamForm, power
from scripts.predict import recompute

CITE = 4  # most-recent scorelines quoted in a provenance note


def team_matches(team_name: str, pool: list[dict], window: int) -> list[dict]:
    """The team's most-recent `window` matches from the sourced pool (most-recent-first)."""
    tk = nkey(team_name)
    mine = [m for m in pool if tk in (nkey(m["home"]), nkey(m["away"]))]
    mine.sort(key=lambda m: m["date"], reverse=True)
    return mine[:window]


def senior_allowlist(pool: list[dict], teams: dict) -> set:
    """nkeys of senior national teams = everyone who appears in a senior COMPETITION feed
    (WCQ / Euro / Copa / AFCON / Asian Cup), plus our own 48. Clubs and youth selects only
    ever show up in the friendly-international feed, never in a senior competition, so this
    set is exactly the legitimate-opponent filter — derived from the data, not hardcoded."""
    allow = {nkey(t["name"]) for t in teams.values()}
    for m in pool:
        if m["source"] != "Friendlies":
            allow.add(nkey(m["home"]))
            allow.add(nkey(m["away"]))
    return allow


def drop_non_senior(pool: list[dict], allow: set) -> tuple[list[dict], list[str]]:
    """Keep every competition match; keep a friendly only if BOTH sides are senior nations.
    Returns (kept, sample of dropped club/youth opponents) — reported, never silently faked."""
    kept, dropped = [], []
    for m in pool:
        if m["source"] != "Friendlies" or (nkey(m["home"]) in allow and nkey(m["away"]) in allow):
            kept.append(m)
        else:
            bad = m["away"] if nkey(m["home"]) in allow else m["home"]
            dropped.append(bad)
    return kept, dropped


def provenance(team_name: str, mine: list[dict]) -> str:
    """A human-readable, auditable note: window, date span, source mix, recent scorelines."""
    tk = nkey(team_name)
    srcs: dict[str, int] = {}
    for m in mine:
        srcs[m["source"]] = srcs.get(m["source"], 0) + 1
    span = f"{mine[-1]['date']}..{mine[0]['date']}" if mine else "n/a"
    mix = ", ".join(f"{k} {v}" for k, v in sorted(srcs.items(), key=lambda kv: -kv[1]))
    cites = "; ".join(
        f"{m['home_goals']}-{m['away_goals']} "
        f"{'vs ' + m['away'] if nkey(m['home']) == tk else '@ ' + m['home']}"
        for m in mine[:CITE])
    return (f"Form = last {len(mine)} official matches ({span}); sources: {mix}. "
            f"Recent: {cites}. [src: SportDB; PROPOSED, awaiting approval]")


PROPOSAL = "team_form_proposed.json"


def apply_from_file(conn, teams: dict) -> None:
    """Commit the REVIEWED proposal snapshot (not a fresh pull) so what's applied is exactly
    what was inspected — a re-pull is non-deterministic (a feed can time out between runs).
    Only the results columns (W/D/L/GF/GA + provenance note) are overwritten; the curated
    pass_acc / pressing / sos stay as the live DB has them (those aren't in a results feed)."""
    path = config.ROOT / "data" / PROPOSAL
    if not path.is_file():
        raise SystemExit(f"No proposal at {path}. Run without --apply first to generate it.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    forms = payload.get("forms", {})
    print(f"\nAPPLY TEAM FORM  ·  {len(forms)} reviewed rows from {path.name} "
          f"(window {payload.get('window')}, since {payload.get('since')})\n")
    for tid, f in forms.items():
        t = teams.get(tid, {})
        db.upsert_form(conn, tid, played=f["played"], wins=f["wins"], draws=f["draws"],
                       losses=f["losses"], gf=f["gf"], ga=f["ga"],
                       pass_acc=t.get("pass_acc") or 80.0, pressing=t.get("pressing") or 6.0,
                       sos=t.get("sos") or 1.0, notes=f.get("notes"))
    db.log_run(conn, "build_team_form", "apply_team_form",
               {"applied": len(forms), "window": payload.get("window"), "since": payload.get("since"),
                "source": PROPOSAL}, status="applied")
    conn.commit()
    post, prior, wc_games, _t, n = recompute(conn)
    conn.commit()
    conn.close()
    print(f"  Applied {len(forms)} reviewed form rows; re-rated over {n} finals. team_form now sourced.")


def main():
    ap = argparse.ArgumentParser(description="Rebuild team_form from real SportDB results.")
    ap.add_argument("--window", type=int, default=20, help="most recent N official matches per team")
    ap.add_argument("--since", default="2024-01-01", help="ignore results before this date")
    ap.add_argument("--min-form", type=int, default=8,
                    help="below this many sourced matches, keep existing form and flag (no padding)")
    ap.add_argument("--dry-run", action="store_true", help="print only; write no file or agent_run")
    ap.add_argument("--apply", action="store_true",
                    help="commit the reviewed proposal to team_form and recompute (else propose only)")
    args = ap.parse_args()

    conn = db.connect()
    db.init_db(conn)
    teams = db.teams_with_form(conn)

    if args.apply:
        apply_from_file(conn, teams)
        return

    print(f"\nBUILD TEAM FORM  ·  {len(teams)} teams · last {args.window} official · since {args.since}\n")
    pool, reached, skipped = gather(args.since)
    print(f"  sources reached: {', '.join(reached) if reached else 'none'}")
    if skipped:
        print(f"  sources skipped (reported, not faked): {', '.join(skipped)}")
    print(f"  total sourced matches in window: {len(pool)}")

    allow = senior_allowlist(pool, teams)
    pool, dropped = drop_non_senior(pool, allow)
    if dropped:
        uniq = sorted(set(dropped))
        print(f"  dropped {len(dropped)} non-senior friendly(ies) — club/youth sides in the feed "
              f"({len(uniq)} distinct, e.g. {', '.join(uniq[:5])})")
    print(f"  senior-international matches used: {len(pool)}\n")

    rebuilt: dict[str, dict] = {}      # slug -> proposed form (+ provenance)
    kept: list[str] = []               # teams with too little evidence (keep existing)
    for t in sorted(teams.values(), key=lambda x: x["name"]):
        mine = team_matches(t["name"], pool, args.window)
        rec = team_record(t["name"], mine)
        if rec["played"] < args.min_form:
            kept.append(f"{t['name']} ({rec['played']} sourced — below --min-form {args.min_form})")
            continue
        # preserve curated, non-results-feed fields (tactics + SoS belong to other paths)
        rebuilt[t["id"]] = {
            "name": t["name"], **rec,
            "pass_acc": t.get("pass_acc") or 80.0, "pressing": t.get("pressing") or 6.0,
            "sos": t.get("sos") or 1.0, "notes": provenance(t["name"], mine),
        }

    # ---- report: old vs new power, sorted by the biggest rating move ----
    def pow_of(f: dict) -> float:
        return power(TeamForm(f["played"], f["wins"], f["draws"], f["losses"],
                              f["gf"], f["ga"], f["pass_acc"], f["pressing"], f["sos"]))

    rows = []
    for tid, f in rebuilt.items():
        t = teams[tid]
        old = pow_of({**t, "pass_acc": t.get("pass_acc") or 80.0,
                      "pressing": t.get("pressing") or 6.0, "sos": t.get("sos") or 1.0})
        rows.append((pow_of(f) - old, t["name"],
                     f"{t['wins']}-{t['draws']}-{t['losses']}", f"{f['wins']}-{f['draws']}-{f['losses']}",
                     round(old, 1), round(pow_of(f), 1)))
    rows.sort(key=lambda r: r[0])
    print(f"REBUILT form ({len(rebuilt)} teams)  ·  old -> new power (sorted by move):")
    print(f"  {'team':22} {'old W-D-L':>9} {'new W-D-L':>9} {'old':>6} {'new':>6} {'Δ':>6}")
    for d, name, oldwdl, newwdl, old, new in rows:
        print(f"  {name[:22]:22} {oldwdl:>9} {newwdl:>9} {old:6.1f} {new:6.1f} {d:+6.1f}")

    if kept:
        print(f"\n  kept existing form (guardrail #2 — insufficient sourced matches): {len(kept)}")
        for k in kept:
            print(f"    - {k}")

    if args.dry_run:
        print("\n  --dry-run: nothing written. Drop --dry-run to propose, then --apply to commit.")
        conn.close()
        return

    # ---- propose: write the reviewable snapshot + a 'proposed' agent_run (guardrail #1) ----
    out = config.ROOT / "data" / PROPOSAL
    payload = {
        "_comment": "PROPOSED team_form rebuilt from real SportDB results by "
                    "agents/build_team_form.py (last-N official matches, uniform window). "
                    "Replaces the workbook as the form source. Review, then re-run with --apply.",
        "window": args.window, "since": args.since,
        "forms": {tid: {k: f[k] for k in
                        ("name", "played", "wins", "draws", "losses", "gf", "ga", "notes")}
                  for tid, f in rebuilt.items()},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    db.log_run(conn, "build_team_form", "propose_team_form", {
        "window": args.window, "since": args.since, "rebuilt": len(rebuilt),
        "kept_existing": len(kept), "sources_reached": reached, "sources_skipped": skipped,
    }, status="proposed")
    conn.commit()
    conn.close()
    print(f"\n  Wrote {out} and logged a 'proposed' agent_run. team_form untouched (guardrail #1).")
    print("  Review it, then re-run with --apply to commit the reviewed snapshot.")


if __name__ == "__main__":
    main()
