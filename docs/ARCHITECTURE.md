# Architecture

How the pieces fit. The design goal is **a local-first engine with a clean seam between
pure math and everything else**, so the model can be tested, tuned, and reasoned about in
isolation while agents and UIs come and go around it.

## Layers

```
                 ┌─────────────────────────────────────────────┐
                 │  Browser dashboard  (app.py + webapp/)       │  FastAPI + Tailwind
                 │   view standings/preds · run agents · approve│
                 └───────────────┬─────────────────────────────┘
   Telegram  ◄──────────┐        │ HTTP (localhost)
                        │        ▼
 ┌──────────────┐  ┌────┴────────────────┐      ┌──────────────────────────┐
 │ runtime      │  │   lib/db.py          │◄────►│   wc.db  (single SQLite) │
 │ agents/      │─►│  (all SQLite access) │      │   8 tables, schema.sql   │
 │ (Anthropic + │  └────┬────────────────┘      └──────────────────────────┘
 │  web search) │       │ pure dicts/values
 └──────────────┘       ▼
                 ┌──────────────────────┐
                 │   engine/  (no I/O)   │  power(), match_probs(), rerate_all()
                 └──────────────────────┘
```

**The rule:** `engine/` imports nothing from `lib/`, `agents/`, or `app.py`. It takes
numbers in and returns numbers out. Everything stateful (the DB, the network, the UI)
depends on the engine, never the reverse. That seam is what makes the model testable and
the tuner possible.

## Components

| Layer | Files | Responsibility | Talks to |
|-------|-------|----------------|----------|
| Engine | `engine/power.py`, `rerate.py`, `params.py` | The math. Pure functions over `TeamForm` + params. | nothing |
| Data | `lib/db.py` | Connect, schema init, typed upserts/reads. Stdlib `sqlite3` only. | `wc.db` |
| Notify | `lib/notify.py` | Optional Telegram `sendMessage`. No-ops if unset. | Telegram API |
| Config | `config.py` | Paths + `load_dotenv()`. Loaded by everything. | `.env` |
| Agents | `agents/results_monitor.py` | Fetch finals → record → re-rate → log → notify. | Anthropic, `lib/db` |
| Scripts | `scripts/seed_from_xlsx.py`, `predict.py` | One-time seed; recompute ratings+predictions. | workbook, `lib/db` |
| Web | `app.py`, `webapp/index.html` | Read views + control endpoints. | `lib/db`, subprocess |

## Data flow

### 1. Seed (one-time)
`seed_from_xlsx.py` reads the workbook (`Form_L20`, `Tactics`, `Predictions` sheets) into
`teams`, `team_form`, and `matches`. Match IDs are `g-{row}` (the source spreadsheet row).
Played scores in the `Predictions` tab become `final` matches; everything else is
`scheduled`. Kickoff dates come from the `Date` column.

### 2. Predict (recompute)
`predict.py` is the canonical recompute and is idempotent:
1. `prior_power` for every team from its form (`engine.power.power`).
2. Fold all `final` matches into the priors in kickoff order (`engine.rerate.rerate_all`)
   to get `post` power.
3. `match_probs` for every fixture off the `post` ratings → `predictions` table.

Run it after seeding and after any new results land.

### 3. Monitor (ongoing)
`agents/results_monitor.py` asks Claude (with the `web_search` tool) for final scores of
matches that have kicked off but aren't `final`. For each: `record_result` (auto-commit),
incremental `apply_result` re-rate of the two teams, an `agent_runs` audit row, and a
Telegram ping. Re-run `predict.py` (or hit the dashboard's *Recompute*) to refresh all
predictions consistently.

### 4. Serve
`app.py` exposes the same data and actions over HTTP. `POST /api/run-monitor` shells out
to the monitor then to `predict.py`; `POST /api/recompute` runs `predict.py`. Read state
is assembled fresh from `wc.db` on every `GET /api/state`.

## Why these choices

- **SQLite single file.** Zero infra, trivially portable, `wc.db` *is* the state. Back up
  by copying one file.
- **Engine has no I/O.** Lets the tuner backtest the formula on past tournaments without a
  database, and lets parity with the workbook be checked directly.
- **Agents propose, you approve.** The `agent_runs` table is the audit log and the
  approval queue (`proposed` → `applied`/`rejected`). Observed facts (scores) skip the
  queue; parameter and squad changes do not. See [CLAUDE.md](../CLAUDE.md).
- **Tailwind via CDN.** No build step, no `node_modules` — keeps the repo Python-only.

## Remote access

The dashboard binds `127.0.0.1` — **local only**. Pushing the repo to GitHub makes the
*code* portable, not the running app. For true remote operation:

- **Autonomous updates** → a scheduled **GitHub Actions** workflow runs `results_monitor`
  on cron. Store `ANTHROPIC_API_KEY` + Telegram tokens as **Actions secrets**. Because
  runners are ephemeral and `wc.db` is gitignored, the workflow must **commit `wc.db`
  back** (or cache it) for tournament state to persist between runs.
- **On-demand from your phone** → trigger that workflow via `workflow_dispatch` from the
  GitHub mobile app — no always-on server needed.
- **Two-way Telegram control** (typing commands to a bot) needs an always-on host to hold
  a long-poll open; a scheduled Action can't. `lib/notify.py` is currently outbound only.

## State portability caveat

`wc.db` is gitignored, so agent-logged results live **only on the machine that ran the
agent**. A fresh clone rebuilds from the workbook (the original seed state) and loses any
web-sourced results unless you persist `wc.db` (commit it from CI, or sync it out of band).
