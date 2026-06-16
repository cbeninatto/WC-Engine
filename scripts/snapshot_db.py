"""Snapshot the live DB to data/seed_snapshot.json — the workbook-free seed source.

Replaces WorldCup2026_Analytics_Companion.xlsx as the canonical rebuild source. Once form is
sourced from real results (agents/build_team_form.py) and SoS is evidence-based
(agents/sos_sourcer.py), the DB — not the workbook — is the source of truth. This freezes the
curated teams + form (incl. SoS/provenance) + fixtures into a JSON that scripts/seed_from_snapshot.py
replays. power_ratings/predictions are NOT snapshotted; they're derived by recompute on reseed.

Re-run whenever you want to capture a new known-good baseline.

    python scripts/snapshot_db.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config
import lib.db as db

TEAM_COLS = ["id", "name", "confederation", "group_code"]
FORM_COLS = ["team_id", "played", "wins", "draws", "losses", "gf", "ga",
             "pass_acc", "pressing", "sos", "notes"]
MATCH_COLS = ["id", "stage", "group_code", "kickoff", "home_id", "away_id",
              "home_goals", "away_goals", "status", "source"]


def main():
    conn = db.connect()
    db.init_db(conn)

    def dump(table, cols):
        return [{c: r[c] for c in cols}
                for r in conn.execute(f"SELECT {','.join(cols)} FROM {table}")]

    snap = {
        "_comment": "Workbook-free seed snapshot of wc.db (teams + team_form + matches), written "
                    "by scripts/snapshot_db.py and replayed by scripts/seed_from_snapshot.py. This "
                    "is the source of truth that replaces WorldCup2026_Analytics_Companion.xlsx. "
                    "power_ratings/predictions are derived (recompute), so they're intentionally absent.",
        "teams": dump("teams", TEAM_COLS),
        "team_form": dump("team_form", FORM_COLS),
        "matches": dump("matches", MATCH_COLS),
    }
    conn.close()

    out = config.ROOT / "data" / "seed_snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    finals = sum(1 for m in snap["matches"] if m["status"] == "final")
    print(f"Wrote {out}\n  {len(snap['teams'])} teams · {len(snap['team_form'])} form rows · "
          f"{len(snap['matches'])} matches ({finals} final)")


if __name__ == "__main__":
    main()
