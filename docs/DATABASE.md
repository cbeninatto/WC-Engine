# Database

One local SQLite file: **`wc.db`** (gitignored). Schema is
[`db/schema.sql`](../db/schema.sql); all access goes through
[`lib/db.py`](../lib/db.py) (stdlib `sqlite3`, `row_factory = sqlite3.Row`,
`PRAGMA foreign_keys = ON`). Rebuild from scratch any time with
`python scripts/seed_from_xlsx.py`.

## Tables

### `teams` — the 48 finalists
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | slug, lowercase-hyphen, e.g. `ivory-coast` |
| `name` | TEXT | **full name always** — "Ivory Coast", never a code |
| `confederation` | TEXT | `CONMEBOL`/`UEFA`/`CAF`/`AFC`/`CONCACAF`/`OFC` |
| `group_code` | TEXT | `A`…`L` |

### `team_form` — model inputs (the `Form_L20` row, normalized)
`played, wins, draws, losses, gf, ga` (last-20 form) · `pass_acc` (def 80) ·
`pressing` (def 6) · **`sos`** (evidence-based, the key knob) · `notes` (SoS provenance).
PK `team_id` → `teams(id)`.

### `matches` — fixtures + results
| Column | Notes |
|--------|-------|
| `id` | `g-{row}` from the source spreadsheet row |
| `stage` | `group`/`r32`/`r16`/`qf`/`sf`/`final` (currently all `group`) |
| `group_code`, `kickoff` | `kickoff` is an ISO date string |
| `home_id`, `away_id` | → `teams(id)` |
| `home_goals`, `away_goals` | **NULL until played** |
| `status` | `scheduled`/`live`/`final` |
| `source` | `seed:xlsx`, `agent:web_search`, … (provenance) |

> Guardrail #4: the recorded group results are **immutable**. Never overwrite an observed
> score.

### `predictions` — engine output per match
`win_home, draw, win_away, pred_home_goals, pred_away_goals, params_version`. PK
`match_id`. Overwritten wholesale by `predict.py`.

### `power_ratings` — the ratings
`power` (folds in WC results) · `prior_power` (pre-tournament, from form) · `wc_games`
(count folded in) · `params_version`. PK `team_id`.

### `model_params` — versioned knobs (tuner territory)
`version` PK · `params` (JSON) · `brier`, `log_loss` (backtest scores) · `note` ·
`approved` (0/1). The tuner proposes a new version; you approve.

### `squad_status` — availability (squad monitor writes here)
`player`, `status` (`out`/`doubt`/`available`), `importance`, **`power_adjustment`** (fed
into `engine.power` via `TeamForm.power_adjustment`), `source`. Auto-increment PK.

### `agent_runs` — audit log **and** approval queue
| Column | Notes |
|--------|-------|
| `agent`, `action` | who did what |
| `payload` | JSON detail |
| `status` | **`proposed`** (waiting) → `applied` / `rejected` |
| `created_at` | timestamp |

This table *is* the guardrail surface. Observed facts (a score) land as `applied`.
Parameter/squad changes land as `proposed` and wait for approval (CLI, dashboard, or
future Telegram). The dashboard's `POST /api/proposals/{id}/{approve|reject}` flips
`status`.

## Relationships

```
teams ──1:1── team_form
teams ──1:1── power_ratings
teams ──1:N── matches (home_id, away_id)   matches ──1:1── predictions
teams ──1:N── squad_status
model_params (standalone, versioned)        agent_runs (standalone, audit)
```
`ON DELETE CASCADE` from `teams` to its dependents.

## `lib/db.py` cheat-sheet

| Function | Use |
|----------|-----|
| `connect()` / `init_db(conn)` | open; create tables from `schema.sql` |
| `teams_with_form(conn)` | `{id: {team+form fields}}` |
| `all_matches` / `final_matches` / `matches_to_check` | match reads (by kickoff) |
| `prior_powers(conn)` | `{team_id: prior_power}` |
| `upsert_team` / `upsert_form` / `upsert_match` | writes (idempotent ON CONFLICT) |
| `record_result(conn, id, hg, ag, source)` | set score + `status='final'` |
| `upsert_power` / `upsert_prediction` | engine outputs |
| `log_run(conn, agent, action, payload, status)` | append to `agent_runs` |

## Common queries

```sql
-- Top 10 by current power
SELECT t.name, p.power, p.prior_power, p.wc_games
FROM power_ratings p JOIN teams t ON t.id = p.team_id
ORDER BY p.power DESC LIMIT 10;

-- Matches played but not yet recorded as final (overdue)
SELECT id, kickoff, group_code FROM matches
WHERE status != 'final' AND date(kickoff) <= date('now') ORDER BY kickoff;

-- Pending approvals
SELECT id, agent, action, payload FROM agent_runs WHERE status = 'proposed';
```
