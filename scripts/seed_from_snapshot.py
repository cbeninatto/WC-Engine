"""Seed wc.db from data/seed_snapshot.json — the workbook-free rebuild.

The clean-slate reset that replaces scripts/seed_from_xlsx.py: restores teams + team_form
(form, tactics, evidence-based SoS, provenance) + matches from the snapshot, then recomputes
power ratings + predictions. Pairs with scripts/snapshot_db.py (which writes the snapshot).

Idempotent (ON CONFLICT upserts), so it's safe to re-run. To refresh form from live results
afterward, run agents/build_team_form.py; to re-snapshot the new baseline, run snapshot_db.py.

    python scripts/seed_from_snapshot.py
    python scripts/seed_from_snapshot.py path/to/seed_snapshot.json
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
from scripts.predict import recompute


def main(path: str | None = None):
    p = Path(path) if path else (config.ROOT / "data" / "seed_snapshot.json")
    if not p.is_file():
        raise SystemExit(
            f"No snapshot at {p}.\nCreate one from a populated DB with:\n"
            "  python scripts/snapshot_db.py")
    snap = json.loads(p.read_text(encoding="utf-8"))

    conn = db.connect()
    db.init_db(conn)
    for t in snap["teams"]:
        db.upsert_team(conn, t["id"], t["name"], t["confederation"], t["group_code"])
    for f in snap["team_form"]:
        f = dict(f)
        db.upsert_form(conn, f.pop("team_id"), **f)
    for m in snap["matches"]:
        db.upsert_match(conn, m)
    conn.commit()

    post, _prior, _wc, _teams, n = recompute(conn)
    conn.commit()
    conn.close()
    print(f"Seeded {len(snap['teams'])} teams + {len(snap['matches'])} matches from {p.name}; "
          f"re-rated over {n} finals.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
