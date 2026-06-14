---
name: reseed
description: Rebuild wc.db from the source workbook and recompute ratings + predictions — the clean-slate reset for the WC Engine. Use for "reseed", "rebuild the database", "start fresh from the workbook", or when wc.db is missing/corrupt on a new machine.
---

# Reseed

Rebuild the local database from `WorldCup2026_Analytics_Companion.xlsx`, then recompute.

## ⚠️ Before you run

Re-seeding **rewrites `teams`, `team_form`, and `matches` from the workbook.** The workbook
only contains the originally-observed group results — so **any results later logged by the
agent or entered manually (sources `agent:web_search` / `manual:user`) that aren't also in
the workbook will be lost.** `wc.db` is gitignored and doesn't sync, so it has no backup
unless you made one.

- If the DB has agent/manual results you care about, **back it up first**:
  `cp wc.db wc.db.bak` — and consider whether you should reseed at all.
- Reseeding is the right move when: setting up a fresh clone, the DB is corrupt, or you
  deliberately want to discard logged results and return to the workbook baseline.

## Steps

1. **Check what you'd lose:**
   ```bash
   sqlite3 wc.db "SELECT source, count(*) FROM matches WHERE status='final' GROUP BY source;" 2>/dev/null || echo "no db yet — safe to seed"
   ```
   If anything other than `seed:xlsx` appears, confirm with the user before proceeding.

2. **Seed** (auto-finds the workbook in cwd or `~/Downloads`; pass the path if elsewhere):
   ```bash
   python scripts/seed_from_xlsx.py
   ```
   Expect: `Seeded 48 teams + 72 matches`.

3. **Recompute** ratings + predictions:
   ```bash
   python scripts/predict.py
   ```

4. **Sanity check:**
   ```bash
   sqlite3 wc.db "SELECT (SELECT count(*) FROM teams), (SELECT count(*) FROM matches), (SELECT count(*) FROM matches WHERE status='final');"
   ```
   Report counts back (48 teams, 72 matches, N finals) and the current top 5 from
   `predict.py`'s output.

## Notes
- The seed is idempotent (ON CONFLICT upserts) and reads kickoff dates from the workbook's
  `Date` column.
- To set `ANTHROPIC_API_KEY` etc., copy `.env.example` → `.env` first (config auto-loads it).
