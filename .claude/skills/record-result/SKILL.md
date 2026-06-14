---
name: record-result
description: Manually record a real, user-provided final score for a World Cup match and refresh ratings/predictions — the safe path for entering an observed result by hand (without the web-search agent). Use when the user gives you a score directly, e.g. "Brazil beat Morocco 2-1" or "record Germany 7-1 Curaçao".
---

# Record result

Enter a final score the **user has provided** (an observed fact) and recompute. This is the
one sanctioned way to write a result by hand — it still must be a real result, never one
you inferred or guessed.

## Steps

1. **Resolve the match.** Map the two teams (full names) to their slugs and find the
   fixture:
   ```bash
   sqlite3 wc.db "SELECT m.id, th.name, ta.name, m.status FROM matches m JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id WHERE th.name LIKE '%Brazil%' OR ta.name LIKE '%Brazil%';"
   ```
   Confirm orientation: the score must be entered as **home_goals, away_goals** for that
   row's `home_id`/`away_id`. If the user gave it the other way round, flip it.

2. **Guardrail check before writing:**
   - The match must exist and be a real fixture.
   - If it's already `final` from `seed:xlsx` (an originally-observed group game), **do not
     overwrite it** (guardrail #4) — confirm with the user first; it's likely a mistake.
   - Goals are non-negative integers.

3. **Record it** through the data layer (sets score, `status='final'`, source):
   ```bash
   python -c "import config, lib.db as db; c=db.connect(); db.record_result(c,'g-10',2,1,'manual:user'); c.commit()"
   ```
   Use `source='manual:user'` so provenance is honest and auditable.

4. **Recompute** ratings + predictions:
   ```bash
   python scripts/predict.py
   ```

5. **Confirm** back to the user: the row now reads `Home X–Y Away [final]`, and report the
   re-rate effect on the two teams' power.

## Notes
- Records an **observed fact** → auto-commits (no proposal needed).
- Never invent or "fill in" a score the user didn't give. One match per invocation keeps
  orientation unambiguous.
