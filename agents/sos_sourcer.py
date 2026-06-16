"""SoS sourcer — propose evidence-based strength-of-schedule overrides from real results.

21/48 teams sit on a bare confederation-default SoS (scripts/audit_sos.py flags them). SoS is
supposed to be per-team and evidence-based. This agent pulls each default team's REAL recent
results from SportDB, scores them against rated opposition, and PROPOSES a SoS override with a
provenance note quoting the actual scorelines — for human approval.

GUARDRAILS:
  #1 propose, don't apply -> writes data/sos_overrides_proposed.json + a 'proposed' agent_run.
     It never touches team_form.sos or data/sos_overrides.json. Approving = a human merges the
     accepted entries into data/sos_overrides.json and re-seeds.
  #2 never fabricate -> every cited scoreline is a sourced SportDB result (90-minute, so a
     shootout reads as a draw). Sources that 500 / return nothing are REPORTED and SKIPPED,
     never faked. A team without enough sourced strong-opposition evidence STAYS on its
     confederation default ("absence of evidence keeps the default").
  #3 full names / lowercase-hyphen slugs -> reuses the ingest normalizer + alias table.

The proposed number is a conservative starting point; the provenance (real games) is the
substance a human reviews. Only opponents that are one of our 48 (so we have a power rating +
confederation) count as "rated" evidence; cross-confederation results weigh most, since that's
exactly what SoS encodes.

    python agents/sos_sourcer.py --dry-run      # print evidence + proposals, write nothing
    python agents/sos_sourcer.py                # also write the proposal file + agent_run
    python agents/sos_sourcer.py --since 2024-01-01 --min-evidence 3
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
from lib import sportdb
from agents.ingest import _norm, NAME_ALIASES
from engine.params import CONFED_SOS
from scripts.predict import recompute

PROPOSAL = "sos_overrides_proposed.json"
CURATED = "sos_overrides.json"


def apply_proposal(conn, teams: dict) -> None:
    """Commit the REVIEWED proposal (data/sos_overrides_proposed.json) to team_form.sos — the
    human-approved counterpart to the proposes-only default (guardrail #1: agent proposes, you
    apply). Updates the live DB, keeps data/sos_overrides.json in sync as the SoS record, and
    recomputes. Teams not in the proposal (no evidence to move them) keep their current SoS."""
    path = config.ROOT / "data" / PROPOSAL
    if not path.is_file():
        raise SystemExit(f"No proposal at {path}. Run 'python agents/sos_sourcer.py --all' first.")
    prop = json.loads(path.read_text(encoding="utf-8")).get("overrides", {})
    print(f"\nAPPLY SoS  ·  {len(prop)} reviewed evidence-based overrides from {path.name}"
          f"  (teams not listed keep their current SoS)\n")
    for tid, p in sorted(prop.items(), key=lambda kv: kv[1]["sos"]):
        note = p["notes"].replace("PROPOSED, awaiting approval", "applied from evidence")
        conn.execute("UPDATE team_form SET sos=?, notes=?, updated_at=CURRENT_TIMESTAMP "
                     "WHERE team_id=?", (p["sos"], note, tid))
        cur = (teams.get(tid) or {}).get("sos")
        print(f"  {p['name']:24} {cur if cur is not None else '?':>5} -> {p['sos']}")

    # keep the curated SoS record in sync so it survives a future snapshot reseed
    cpath = config.ROOT / "data" / CURATED
    curated = json.loads(cpath.read_text(encoding="utf-8")) if cpath.is_file() else {"overrides": {}}
    for tid, p in prop.items():
        note = p["notes"].replace("PROPOSED, awaiting approval", "applied from evidence")
        curated.setdefault("overrides", {})[tid] = {"name": p["name"], "sos": p["sos"], "notes": note}
    cpath.write_text(json.dumps(curated, indent=2, ensure_ascii=False), encoding="utf-8")

    db.log_run(conn, "sos_sourcer", "apply_sos_overrides",
               {"applied": len(prop), "source": PROPOSAL}, status="applied")
    conn.commit()
    _post, _prior, _wc, _t, n = recompute(conn)
    conn.commit()
    conn.close()
    print(f"\n  Applied {len(prop)} SoS overrides + synced {CURATED}; re-rated over {n} finals.")

# Real-data evidence sources (discovered via /api/flashscore/football/<region> listings). Each
# is best-effort: any that 500s or returns nothing is reported and skipped. Continental cups are
# intra-confederation (who's strong among peers — what a confed SoS encodes); friendlies carry
# the cross-confederation signal. Paths include the 'football/' prefix like sportdb.WORLD_CUP_PATH.
# AFCON / Asian Cup are filed by EDITION year, not calendar year: the tournaments played in
# Jan-Feb 2024 are season "2023", and their finals fall inside a 2024+ window (so AFCON must
# use 2023, not 2024 — which the feed returns empty). The Dec-2025 AFCON and mid-2025 Gold Cup
# editions currently return a persistent upstream 500, so they're attempted-and-reported-skipped
# (guardrail #2: report, never fake), not silently dropped.
SOURCES = [
    ("WC2026 qualifiers", "football/world:8/world-championship:lvUBR5F8", [2026], ("2025-01-01", "2026-06-10")),
    ("Euro 2024",         "football/south-america:6/euro:KQMVOQ0g", [2024], None),
    ("Copa America 2024", "football/africa:3/copa-america:02x8YFgF", [2024], None),
    ("AFCON",             "football/north-central-america:1/africa-cup-of-nations:8bP2bXmH", [2023, 2025], None),
    ("Asian Cup",         "football/australia-oceania:5/asian-cup:GCHgI4hp", [2023, 2024], None),
    ("Gold Cup",          "football/asia:2/gold-cup:zckREQFJ", [2025, 2023], None),
    ("Friendlies",        "football/world:8/friendly-international:f1GbGBCd", [2026, 2025, 2024], None),
]

MAX_DELTA = 0.15      # SoS never moves more than this from the confederation default (conservative)
GLOBAL_LO, GLOBAL_HI = 0.55, 1.12   # keep within the CONFED_SOS range
# delta scales with the AVERAGE evidence per rated game (not the sum), so a team that simply
# played more games can't saturate the bound — only a consistent signal moves the number.
AVG_SCALE = 0.06
CITE = 5              # max scorelines quoted in a provenance note


def nkey(name: str) -> str:
    return NAME_ALIASES.get(_norm(name), _norm(name))


def gather(since: str) -> tuple[list[dict], list[str], list[str]]:
    """Pull all reachable source matches dated >= `since`. Returns (matches, reached, skipped).

    Deduplicates on (date, team-pair): the shared Flashscore feed lists some matches twice, and
    two sides meet at most once on a date, so a same-date repeat is a feed duplicate — counting
    it twice would inflate the evidence (guardrail #2)."""
    pool, reached, skipped = [], [], []
    seen: set = set()
    for label, path, seasons, window in SOURCES:
        got = 0
        for season in seasons:
            start = window[0] if window else None
            end = window[1] if window else None
            try:
                ms = sportdb.tournament_matches(season, start=start, end=end, competition_path=path)
            except Exception as e:  # 500 / 404 / quota — report, never fake
                skipped.append(f"{label} {season}: {type(e).__name__}")
                continue
            for m in ms:
                if m["date"] < since:
                    continue
                key = (m["date"], frozenset((nkey(m["home"]), nkey(m["away"]))))
                if key in seen:
                    continue
                seen.add(key)
                pool.append({**m, "source": label})
                got += 1
        if got:
            reached.append(f"{label} ({got})")
    return pool, reached, skipped


def tiers(powers: list[float]) -> tuple[float, float]:
    """(mid_lo, strong_lo) power thresholds = 33rd / 67th percentile among our 48."""
    s = sorted(powers)
    return s[len(s) // 3], s[2 * len(s) // 3]


def main():
    ap = argparse.ArgumentParser(description="Propose evidence-based SoS overrides from SportDB.")
    ap.add_argument("--since", default="2024-01-01", help="ignore results before this date")
    ap.add_argument("--min-evidence", type=int, default=3,
                    help="min rated results vs strong/mid opposition before proposing a number")
    ap.add_argument("--dry-run", action="store_true", help="print only; write no file or agent_run")
    ap.add_argument("--apply", action="store_true",
                    help="commit the reviewed proposal (data/sos_overrides_proposed.json) to "
                         "team_form.sos + recompute, instead of proposing (no feed pull)")
    ap.add_argument("--all", action="store_true",
                    help="evaluate EVERY team, not just those on a bare default — re-derives "
                         "evidence-based SoS from the confederation default for teams whose curated "
                         "override predates the SportDB form rebuild (e.g. Austria's hand-set 1.0)")
    args = ap.parse_args()

    conn = db.connect()
    db.init_db(conn)
    teams = db.teams_with_form(conn)

    if args.apply:
        apply_proposal(conn, teams)
        return

    power = {r["team_id"]: float(r["power"]) for r in conn.execute("SELECT team_id,power FROM power_ratings")}
    by_nkey = {nkey(t["name"]): t for t in teams.values()}            # our 48, for opponent rating
    mid_lo, strong_lo = tiers([power.get(t["id"], 0.0) for t in teams.values()])

    def tier(tid: str) -> str:
        p = power.get(tid, 0.0)
        return "STRONG" if p >= strong_lo else "MID" if p >= mid_lo else "WEAK"

    if args.all:
        targets = [t for t in teams.values() if CONFED_SOS.get(t["confederation"]) is not None]
        scope = "all teams (re-deriving curated overrides from evidence)"
    else:
        targets = [t for t in teams.values()
                   if CONFED_SOS.get(t["confederation"]) is not None
                   and abs((t["sos"] or 0) - CONFED_SOS[t["confederation"]]) < 1e-9]
        scope = "teams on a bare confederation default"

    print(f"\nSoS SOURCER  ·  {len(targets)} {scope}  ·  since {args.since}\n")
    pool, reached, skipped = gather(args.since)
    print(f"  sources reached: {', '.join(reached) if reached else 'none'}")
    if skipped:
        print(f"  sources skipped (reported, not faked): {', '.join(skipped)}")
    print(f"  total sourced matches in window: {len(pool)}\n")

    # win/draw/loss scoring vs each tier (from the target team's perspective)
    WDL = {"STRONG": {"W": 2.0, "D": 1.0, "L": 0.0}, "MID": {"W": 0.5, "D": 0.0, "L": -1.0},
           "WEAK": {"W": 0.0, "D": -1.0, "L": -2.0}}

    proposals: dict[str, dict] = {}
    kept_default: list[str] = []
    for t in sorted(targets, key=lambda x: x["confederation"]):
        tk = nkey(t["name"])
        default = CONFED_SOS[t["confederation"]]
        evidence, score = [], 0.0
        for m in pool:
            hk, ak = nkey(m["home"]), nkey(m["away"])
            if tk not in (hk, ak):
                continue
            opp_name = m["away"] if hk == tk else m["home"]
            opp = by_nkey.get(nkey(opp_name))
            if not opp or opp["id"] == t["id"]:
                continue                              # only rated (in-our-48) opponents count
            gf, ga = (m["home_goals"], m["away_goals"]) if hk == tk else (m["away_goals"], m["home_goals"])
            res = "W" if gf > ga else "D" if gf == ga else "L"
            otier = tier(opp["id"])
            cross = opp["confederation"] != t["confederation"]
            pts = WDL[otier][res] * (1.5 if cross else 1.0)
            if res == "L" and ga - gf >= 3:  # a heavy loss is extra evidence the prior was too high
                pts -= 0.5
            score += pts
            evidence.append({"date": m["date"], "src": m["source"], "opp": opp["name"],
                             "tier": otier, "cross": cross, "gf": gf, "ga": ga, "res": res})

        rated_strong_mid = [e for e in evidence if e["tier"] in ("STRONG", "MID")]
        if len(rated_strong_mid) < args.min_evidence:
            kept_default.append(f"{t['name']} ({len(rated_strong_mid)} rated result(s) — insufficient)")
            continue

        avg = score / len(evidence)            # mean evidence per rated game (sample-size robust)
        delta = max(-MAX_DELTA, min(MAX_DELTA, round(avg * AVG_SCALE, 2)))
        proposed = round(max(GLOBAL_LO, min(GLOBAL_HI, default + delta)), 2)
        if proposed == round(default, 2):
            kept_default.append(f"{t['name']} (evidence net-neutral — stays at default)")
            continue

        evidence.sort(key=lambda e: (e["tier"] != "STRONG", not e["cross"], e["date"]), reverse=False)
        cites = "; ".join(
            f"{e['res']} {e['gf']}-{e['ga']} vs {e['opp']}({e['tier']}{',X' if e['cross'] else ''}) [{e['src']}]"
            for e in evidence[:CITE])
        direction = "raised" if proposed > default else "lowered"
        note = (f"SoS {proposed} ({direction} from {t['confederation']} default {default}): "
                f"{len(rated_strong_mid)} rated results vs strong/mid sides over "
                f"{len(evidence)} rated games, avg {avg:+.2f}/game. "
                f"{cites}. [src: SportDB; X=cross-confederation; PROPOSED, awaiting approval]")
        proposals[t["id"]] = {"name": t["name"], "sos": proposed, "notes": note,
                              "_default": default, "_n": len(rated_strong_mid), "_score": round(score, 1)}

    # ---- report ----
    print(f"PROPOSED overrides ({len(proposals)}):")
    if proposals:
        print(f"  {'team':22} {'conf':9} {'default':>7} {'proposed':>8} {'n':>3} {'score':>6}")
        for tid, p in sorted(proposals.items(), key=lambda kv: kv[1]["sos"] - kv[1]["_default"]):
            t = teams[tid]
            print(f"  {p['name'][:22]:22} {t['confederation']:9} {p['_default']:7.2f} "
                  f"{p['sos']:8.2f} {p['_n']:3} {p['_score']:+6.1f}")
    print(f"\nKept on default (guardrail #2 — absence of evidence keeps the default): {len(kept_default)}")
    for k in kept_default:
        print(f"  - {k}")

    if args.dry_run:
        print(f"\n  --dry-run: nothing written. Drop --dry-run to write the proposal + agent_run.")
        conn.close()
        return

    out = config.ROOT / "data" / "sos_overrides_proposed.json"
    payload = {
        "_comment": "PROPOSED evidence-based SoS overrides from agents/sos_sourcer.py (real "
                    "SportDB results). NOT applied. Review each note, then commit with "
                    "'python agents/sos_sourcer.py --apply' (updates team_form.sos + recomputes).",
        "overrides": {tid: {"name": p["name"], "sos": p["sos"], "notes": p["notes"]}
                      for tid, p in proposals.items()},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    db.log_run(conn, "sos_sourcer", "propose_sos_overrides", {
        "since": args.since, "sources_reached": reached, "sources_skipped": skipped,
        "proposed": {teams[tid]["name"]: p["sos"] for tid, p in proposals.items()},
        "kept_default": len(kept_default),
    }, status="proposed")
    conn.commit()
    conn.close()
    print(f"\n  Wrote {out} and logged a 'proposed' agent_run. Live SoS untouched (guardrail #1).")


if __name__ == "__main__":
    main()
