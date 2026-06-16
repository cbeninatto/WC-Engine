---
name: reseed
description: Rebuild wc.db from the DB snapshot (data/seed_snapshot.json) and recompute ratings + predictions — the clean-slate reset for the WC Engine. Use for "reseed", "rebuild the database", "start fresh", or when wc.db is missing/corrupt on a new machine.
---

# Reseed

Rebuild the local database from `data/seed_snapshot.json`, then recompute. The snapshot —
not the old Excel workbook — is the source of truth: it captures the SportDB-sourced
`team_form`, evidence-based SoS + provenance, and the fixtures/results.

## ⚠️ Before you run

Re-seeding **rewrites `teams`, `team_form`, and `matches` from the snapshot.** The snapshot is
a frozen baseline — **any results logged (ingest/manual) AFTER the last snapshot, and any form
refresh from `agents/build_team_form.py` that wasn't re-snapshotted, will be lost.** `wc.db` is
gitignored, so it has no backup unless you made one.

- If the live DB has changes since the last snapshot you care about, **capture them first**:
  `python scripts/snapshot_db.py` (re-freezes the baseline), or back up: `cp wc.db wc.db.bak`.
- Reseeding is the right move when: setting up a fresh clone, the DB is corrupt, or you
  deliberately want to discard recent changes and return to the snapshot baseline.

## Steps

1. **Check what you'd lose** (results not yet in the snapshot):
   ```bash
   sqlite3 wc.db "SELECT source, count(*) FROM matches WHERE status='final' GROUP BY source;" 2>/dev/null || echo "no db yet — safe to seed"
   ```
   If results were logged since the last `snapshot_db.py`, confirm with the user (and consider
   re-snapshotting) before proceeding.

2. **Seed** from the snapshot (recomputes ratings + predictions inline):
   ```bash
   python scripts/seed_from_snapshot.py
   ```
   Expect: `Seeded 48 teams + 72 matches from seed_snapshot.json; re-rated over N finals.`

3. **Sanity check:**
   ```bash
   sqlite3 wc.db "SELECT (SELECT count(*) FROM teams), (SELECT count(*) FROM matches), (SELECT count(*) FROM matches WHERE status='final');"
   ```
   Report counts back (48 teams, 72 matches, N finals) and the current top 5 from `predict.py`.

## Notes
- The seed is idempotent (ON CONFLICT upserts).
- To refresh form from live results after seeding: `python agents/build_team_form.py` (propose),
  then `--apply`; then `python scripts/snapshot_db.py` to capture the new baseline.
- Legacy: `scripts/seed_from_xlsx.py` (workbook → DB) still exists as a one-time bridge if all
  you have is the Excel file, but the snapshot is the canonical source now.
- To set `ANTHROPIC_API_KEY` etc., copy `.env.example` → `.env` first (config auto-loads it).
