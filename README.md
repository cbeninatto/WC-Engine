# WC Engine

Local World Cup 2026 prediction engine + runtime agents. Productionized from the Excel
model. **Fully local — SQLite, single file, no server.** Only the agents reach the network
(Anthropic API + web search).

## Setup

Works on native **Windows (PowerShell)**, macOS, or Linux/WSL — it's just Python +
SQLite, no server. (The WSL2 requirement applies only to *Claude Code itself*, not to
running this app.)

### Windows / PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # optional; skip if deps already global
pip install -r requirements.txt
copy "$env:USERPROFILE\Downloads\WorldCup2026_Analytics_Companion.xlsx" .
python scripts\seed_from_xlsx.py    # auto-finds the workbook (cwd / Downloads)
python scripts\predict.py
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # set env var BEFORE the command
python agents\results_monitor.py
```

If `Activate.ps1` is blocked, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### macOS / Linux / WSL

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_from_xlsx.py /path/to/WorldCup2026_Analytics_Companion.xlsx
python scripts/predict.py
ANTHROPIC_API_KEY=sk-ant-... python agents/results_monitor.py
```

The seeder auto-locates the workbook in the current folder or `~/Downloads`; pass the
path explicitly if it lives elsewhere. `predict.py` prints the current top 5.

## Run the results monitor

```bash
python agents/results_monitor.py            # one pass: fetch finals, re-rate, notify
python agents/results_monitor.py --loop 300 # poll every 5 minutes
```

It finds matches that have kicked off, asks Claude (with web search) for the final
scores, records them, folds each result into the ratings (in-tournament re-rate), and —
if you set the Telegram vars — pings you. Then re-run `predict.py` to refresh predictions.

## Layout

```
engine/   pure math (power, probabilities, re-rate, params) — no I/O
lib/      db.py (SQLite), notify.py (Telegram)
agents/   results_monitor.py  (squad_monitor, ingest, tuner = next)
scripts/  seed_from_xlsx.py, predict.py
db/       schema.sql
```

## Inspect the data

```bash
sqlite3 wc.db "SELECT name, power, prior_power, wc_games FROM power_ratings \
  JOIN teams USING(id) ORDER BY power DESC LIMIT 10;"   # if you alias team_id->id
sqlite3 wc.db "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT 10;"
```

See `CLAUDE.md` for architecture, the model formula, the SoS philosophy, guardrails, and
the agent roadmap. Point Claude Code at this folder and it has full context.

## Host

For always-on running: cron the agents on your Mac mini home server (it's on Tailscale),
or Fly.io + Docker like the Fastmail agent. The DB is a single file — back it up by copying
`wc.db`.
