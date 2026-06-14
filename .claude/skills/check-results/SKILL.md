---
name: check-results
description: Fetch newly-finished World Cup match results via the results monitor, verify they don't violate the data guardrails, refresh predictions, and summarize what changed. Use when the user asks to "check results", "update results", "any new scores?", or "run the monitor".
---

# Check results

Run one pass of the results monitor, then make the new state consistent and trustworthy.

## Steps

1. **Pre-state.** Note current counts so you can report a diff:
   ```bash
   sqlite3 wc.db "SELECT count(*) FROM matches WHERE status='final';"
   ```
   Also list matches that are overdue (kicked off, not yet final) — those are what should
   resolve:
   ```bash
   sqlite3 wc.db "SELECT id, kickoff, group_code FROM matches WHERE status!='final' AND date(kickoff)<=date('now') ORDER BY kickoff;"
   ```

2. **Run the monitor (one pass).** Requires `ANTHROPIC_API_KEY` in `.env`. On Windows set
   `PYTHONIOENCODING=utf-8` for the team names:
   ```bash
   python agents/results_monitor.py
   ```
   It fetches finals (Claude + web search), records them (`source='agent:web_search'`),
   re-rates the two teams per match, and logs to `agent_runs`. It will **skip** matches it
   can't confirm as finished — that is correct, not a bug.

3. **Refresh predictions** off the new ratings:
   ```bash
   python scripts/predict.py
   ```

4. **Verify the guardrails on what was just logged** (don't trust blindly):
   - Each new `final` has a real `source` (no blanks).
   - Team names are full names; observed group results from `seed:xlsx` were **not**
     overwritten.
   - No negative/absurd scorelines.
   If anything looks off, surface it — consider the `data-integrity-auditor` subagent.

5. **Summarize.** Report: results newly logged (with scores), how many are still pending/
   overdue and why (not finished yet), and the biggest rating swings:
   ```bash
   sqlite3 wc.db "SELECT t.name, p.prior_power, p.power FROM power_ratings p JOIN teams t ON t.id=p.team_id WHERE p.wc_games>0 ORDER BY abs(p.power-p.prior_power) DESC LIMIT 8;"
   ```

## Notes
- This logs **observed facts**, which auto-commit per guardrail #1 — no approval needed.
- If `ANTHROPIC_API_KEY` is missing, stop and ask for it; never invent scores to fill gaps.
