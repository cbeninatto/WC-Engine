"""Seed the user's matchday-1 scoreline picks into wc.db (the fantasy-league bet slip).

These are the user's own picks (entered with the old Excel model), recorded verbatim. They
are user-provided facts, not an agent proposal, so they're written directly to
user_predictions. Idempotent (upsert), so it's safe to re-run. To bake them into the
canonical seed so a reseed reproduces them, run scripts/snapshot_db.py afterward.

Picks are listed by full team names (guardrail #3) and resolved to fixtures by the
home/away pair, so a wrong orientation fails loudly instead of scoring the wrong side.

    python scripts/seed_user_predictions.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import lib.db as db

SOURCE = "old-model"

# (home, away, pred_home, pred_away) — full names as the user gave them, normalized to ours.
PICKS = [
    ("Mexico", "South Africa", 2, 1),
    ("South Korea", "Czechia", 1, 2),
    ("Canada", "Bosnia and Herzegovina", 2, 1),
    ("United States", "Paraguay", 2, 1),
    ("Qatar", "Switzerland", 0, 2),
    ("Brazil", "Morocco", 2, 1),
    ("Haiti", "Scotland", 0, 1),
    ("Australia", "Türkiye", 2, 2),
    ("Germany", "Curaçao", 3, 0),
    ("Netherlands", "Japan", 2, 2),
    ("Ivory Coast", "Ecuador", 2, 0),
    ("Sweden", "Tunisia", 1, 2),
    ("Spain", "Cape Verde", 4, 0),
    ("Belgium", "Egypt", 2, 1),
    ("Saudi Arabia", "Uruguay", 1, 3),
    ("Iran", "New Zealand", 3, 1),
]


def main():
    conn = db.connect()
    db.init_db(conn)

    # Index fixtures by (home_name, away_name) so picks resolve to the right orientation.
    by_pair = {}
    for m in conn.execute(
        "SELECT m.id, h.name hn, a.name an FROM matches m "
        "JOIN teams h ON h.id=m.home_id JOIN teams a ON a.id=m.away_id"
    ):
        by_pair[(m["hn"], m["an"])] = m["id"]

    written, missing = 0, []
    for home, away, ph, pa in PICKS:
        mid = by_pair.get((home, away))
        if not mid:
            missing.append(f"{home} vs {away}")
            continue
        db.upsert_user_prediction(conn, mid, ph, pa, SOURCE)
        written += 1
    conn.commit()
    conn.close()

    print(f"Recorded {written}/{len(PICKS)} user picks (source='{SOURCE}').")
    if missing:
        raise SystemExit("No fixture matched (check name/orientation):\n  - "
                         + "\n  - ".join(missing))


if __name__ == "__main__":
    main()
