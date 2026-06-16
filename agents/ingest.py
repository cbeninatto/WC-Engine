"""Ingest agent — pull finished World Cup results from SportDB.dev and record them.

This is the roadmap's agents/ingest.py: a real sports-data feed (SportDB.dev, a REST proxy
over Flashscore) instead of the LLM web-search guess. Recording an observed FINAL score is
pure observed fact, so it AUTO-COMMITS (CLAUDE.md guardrail #1) — same class of action as the
results monitor.

REST, not MCP: this job is a headless, deterministic ETL (CLI, the dashboard subprocess, and
GitHub Actions/cron) with no LLM in the loop, so a plain REST client is the right tool — no
tokens, reproducible, runs anywhere. MCP would only suit interactive ad-hoc querying.

Guardrails honored:
  #2 never fabricate  -> a team it can't confidently map to a slug is SKIPPED + reported,
                         never guessed; results outside the finals window are ignored so a
                         shared-feed qualifier can't masquerade as a finals result.
  #3 full names/slugs -> maps Flashscore names to EXISTING team slugs via an accent-stripping
                         normalizer + alias table; it never invents a new slug.
  #4 don't overwrite  -> only fills matches still pending; already-final rows (incl. the
                         seeded group games) are excluded from the write set.

    python agents/ingest.py                 # record any newly-finished WC2026 results
    python agents/ingest.py --dry-run       # show what it WOULD record + the team mapping
    python agents/ingest.py --date 2026-06-16
    python agents/ingest.py --season 2026
"""
from __future__ import annotations

import sys
import re
import argparse
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config  # noqa: F401  (loads .env so the API key + DB path resolve)
import lib.db as db
from lib import sportdb
from lib.notify import notify
from scripts.predict import recompute

# Flashscore team name (normalized) -> our team name (normalized). Only the cases that still
# diverge after accent/space/punctuation stripping; everything else matches directly.
NAME_ALIASES = {
    "korearepublic": "southkorea",
    "usa": "unitedstates",
    "turkey": "turkiye",
    "czechrepublic": "czechia",
    "congodr": "drcongo",
    "bosnia": "bosniaandherzegovina",
    "bosniaherzegovina": "bosniaandherzegovina",   # Flashscore: "Bosnia & Herzegovina"
    "capeverdeislands": "capeverde",
    "cotedivoire": "ivorycoast",
}


def _norm(s: str) -> str:
    """Accent/space/punctuation-insensitive key, e.g. 'Côte d'Ivoire' -> 'cotedivoire'."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def build_resolver(teams: dict):
    """Return name -> slug (or None). Built from our own team names so accented slugs
    (türkiye, curaçao) are reached without ever hardcoding them."""
    norm_to_slug = {_norm(t["name"]): tid for tid, t in teams.items()}

    def resolve(api_name: str) -> str | None:
        n = _norm(api_name)
        n = NAME_ALIASES.get(n, n)
        return norm_to_slug.get(n)

    return resolve


def run(season: int, date: str | None, dry_run: bool) -> int:
    conn = db.connect()
    db.init_db(conn)
    teams = db.teams_with_form(conn)
    resolve = build_resolver(teams)

    # Index our schedule by unordered team-pair. Pending = writable; final = already recorded.
    pending_by_pair: dict[frozenset, list[dict]] = {}
    final_pairs: set[frozenset] = set()
    for m in db.all_matches(conn):
        pair = frozenset((m["home_id"], m["away_id"]))
        if m["status"] == "final":
            final_pairs.add(pair)
        else:
            pending_by_pair.setdefault(pair, []).append(m)

    results = sportdb.world_cup_results(season=season)
    if date:
        results = [r for r in results if r["date"] == date]
    if not results:
        print(f"SportDB returned no finished WC{season} results"
              + (f" on {date}" if date else "") + " in the finals window.")
        conn.close()
        return 0

    to_record: list[tuple[dict, str, int, int]] = []   # (match, api-label, our_hg, our_ag)
    unmapped: list[str] = []
    not_in_schedule: list[str] = []
    already: list[str] = []

    for r in results:
        ah, aa = resolve(r["home"]), resolve(r["away"])
        label = f"{r['home']} {r['home_goals']}-{r['away_goals']} {r['away']} ({r['date']})"

        if not ah or not aa:
            miss = " & ".join(n for n, ok in ((r["home"], ah), (r["away"], aa)) if not ok)
            unmapped.append(f"{label}  [unmapped: {miss}]")
            continue

        pair = frozenset((ah, aa))
        candidates = pending_by_pair.get(pair, [])
        if not candidates:
            (already if pair in final_pairs else not_in_schedule).append(label)
            continue

        # Disambiguate (rare for group stage) by matching the kickoff date when possible.
        m = next((c for c in candidates if (c["kickoff"] or "")[:10] == r["date"]), candidates[0])
        # Orient goals to OUR home/away, which may differ from Flashscore's orientation.
        if m["home_id"] == ah:
            hg, ag = r["home_goals"], r["away_goals"]
        else:
            hg, ag = r["away_goals"], r["home_goals"]
        to_record.append((m, label, hg, ag))

    # ---- report ----
    print(f"\nINGEST  SportDB · WC{season}"
          + (f" · {date}" if date else "") + f" · {len(results)} finished results in window\n")
    for m, label, hg, ag in to_record:
        hn, an = teams[m["home_id"]]["name"], teams[m["away_id"]]["name"]
        print(f"  {'WOULD RECORD' if dry_run else 'RECORD':12} {m['id']:6} "
              f"{hn} {hg}-{ag} {an}   (Flashscore: {label})")
    for label in already:
        print(f"  {'already final':12} {'':6} {label}")
    for label in not_in_schedule:
        print(f"  {'not in sched':12} {'':6} {label}")
    for label in unmapped:
        print(f"  {'SKIP':12} {'':6} {label}")

    if not to_record:
        print("\n  Nothing new to record.")
        conn.close()
        return 0

    if dry_run:
        print(f"\n  --dry-run: {len(to_record)} match(es) would be recorded. Re-run without "
              f"--dry-run to commit, then predictions refresh automatically.")
        conn.close()
        return 0

    # ---- write (observed facts -> auto-commit), then authoritative recompute ----
    for m, _label, hg, ag in to_record:
        db.record_result(conn, m["id"], hg, ag, source="sportdb")
        hn, an = teams[m["home_id"]]["name"], teams[m["away_id"]]["name"]
        db.log_run(conn, "ingest", "logged_result",
                   {"match": m["id"], "score": f"{hg}-{ag}", "source": "sportdb"},
                   status="applied")
        notify(f"*{hn} {hg}-{ag} {an}* logged (SportDB).")
    conn.commit()

    # recompute() re-rates from ALL finals walk-forward (leakage-free) and refreshes
    # predictions, so we don't apply the in-tournament re-rate by hand here.
    post, prior, wc_games, _teams, n = recompute(conn)
    conn.commit()
    conn.close()

    print(f"\n  Recorded {len(to_record)} result(s); re-rated over {n} finals. Biggest moves:")
    moved = sorted(((tid, post[tid] - prior[tid]) for tid in prior if wc_games.get(tid)),
                   key=lambda kv: -abs(kv[1]))[:6]
    for tid, d in moved:
        print(f"    {teams[tid]['name']:22} {prior[tid]:5.1f} -> {post[tid]:5.1f}  ({d:+.1f})")
    return len(to_record)


def main():
    ap = argparse.ArgumentParser(description="Ingest finished WC results from SportDB.dev.")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--date", default=None, help="restrict to one date, YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the mapping + what would be recorded; write nothing")
    args = ap.parse_args()
    run(args.season, args.date, args.dry_run)


if __name__ == "__main__":
    main()
