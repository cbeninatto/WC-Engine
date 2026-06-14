# WC Engine

Local **World Cup 2026 prediction engine** + a fleet of runtime agents that keep it
current, with a browser dashboard on top. Productionized from an Excel model
(`WorldCup2026_Analytics_Companion.xlsx`).

**Fully local — SQLite, single file `wc.db`, no server, no cloud DB.** The only outbound
calls are the runtime agents (Anthropic API + web search) and optional Telegram pings.

---

## What's in here

| Piece | Path | Role |
|-------|------|------|
| **Engine** | `engine/` | Pure math — power rating, match probabilities, in-tournament re-rate. No I/O. |
| **Data layer** | `lib/db.py` | All SQLite access (stdlib only). |
| **Control plane** | `lib/notify.py` | Optional Telegram notifications. |
| **Runtime agents** | `agents/` | `results_monitor.py` (built). `squad_monitor`, `ingest`, `tuner` are next. |
| **Scripts** | `scripts/` | `seed_from_xlsx.py` (one-time bridge), `predict.py` (recompute). |
| **Web app** | `app.py` + `webapp/` | FastAPI dashboard + control panel. |
| **Schema** | `db/schema.sql` | The 8-table SQLite schema. |

Deeper references live in [`docs/`](docs/): [architecture](docs/ARCHITECTURE.md) ·
[the model](docs/MODEL.md) · [database](docs/DATABASE.md). Project rules and the agent
roadmap are in [`CLAUDE.md`](CLAUDE.md).

---

## Quickstart

> Works on native **Windows (PowerShell)**, macOS, or Linux/WSL — just Python + SQLite.

### Windows / PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # optional
pip install -r requirements.txt
copy "$env:USERPROFILE\Downloads\WorldCup2026_Analytics_Companion.xlsx" .
python scripts\seed_from_xlsx.py        # builds wc.db from the workbook
python scripts\predict.py               # compute ratings + predictions
```

### macOS / Linux / WSL

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_from_xlsx.py /path/to/WorldCup2026_Analytics_Companion.xlsx
python scripts/predict.py
```

The seeder auto-locates the workbook in the current folder or `~/Downloads`; pass the
path explicitly if it lives elsewhere.

### Secrets (`.env`)

Only the runtime agents need a key. Copy the template and fill it in — `.env` is
gitignored and auto-loaded by `config.py`:

```bash
cp .env.example .env        # then edit: ANTHROPIC_API_KEY=sk-ant-...
```

Optional `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` enable phone notifications.

---

## The browser dashboard

```bash
python app.py                    # http://127.0.0.1:8000
WC_WEB_PORT=8765 python app.py   # pick another port if 8000 is busy
```

A FastAPI + Tailwind dashboard that reads/writes the same `wc.db`:

- **View** — group standings, upcoming matches with win/draw/win probabilities, power
  ratings (with Δ vs the pre-tournament prior), and final results.
- **Control** — buttons to *run the results monitor* and *recompute predictions*, plus an
  approval queue for anything an agent proposes (see Guardrails).

Endpoints: `GET /api/state`, `POST /api/run-monitor`, `POST /api/recompute`,
`POST /api/proposals/{id}/{approve|reject}`. Localhost only — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#remote-access) for remote hosting.

---

## The results monitor

```bash
python agents/results_monitor.py            # one pass: fetch finals, re-rate, notify
python agents/results_monitor.py --loop 300 # poll every 5 minutes
```

Finds matches that have kicked off but aren't final, asks Claude (with web search) for
the final scores, records them, folds each into the ratings (in-tournament re-rate),
logs to `agent_runs`, and pings Telegram if configured. Then refreshes predictions.

---

## Guardrails (non-negotiable)

1. **Agents propose; you approve.** Observed facts (a final score) auto-commit. Anything
   that changes model **parameters** or applies a squad adjustment writes a `proposed`
   row to `agent_runs` and waits.
2. **Never fabricate data.** No invented friendlies or scores. Unsourceable → skip.
3. **Full team names always** ("Ivory Coast", never a code). Slugs are lowercase-hyphen.
4. **Observed group games are immutable** — don't overwrite recorded results.

Full detail in [CLAUDE.md](CLAUDE.md).

---

## Claude Code tooling

This repo ships its own Claude Code scaffolding under [`.claude/`](.claude/):

- **Subagents** (`.claude/agents/`): `data-integrity-auditor`, `runtime-agent-builder`,
  `model-tuner`.
- **Skills** (`.claude/skills/`): `/check-results`, `/record-result`, `/reseed`.

Point Claude Code at this folder and it has full context plus these helpers.

---

## Inspect the data

```bash
sqlite3 wc.db "SELECT t.name, power, prior_power, wc_games FROM power_ratings p \
  JOIN teams t ON t.id=p.team_id ORDER BY power DESC LIMIT 10;"
sqlite3 wc.db "SELECT agent, action, status, created_at FROM agent_runs ORDER BY id DESC LIMIT 10;"
```

## Host it

The DB is a single file — back it up by copying `wc.db`. For always-on operation, run the
agents on cron (home server / Tailscale) or a small box, or schedule them via GitHub
Actions (store secrets as Actions secrets, never commit them). See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#remote-access).
