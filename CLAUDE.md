# CLAUDE.md — WC Engine

Project context for Claude Code. Read this before changing anything.

## What this is
A World Cup 2026 prediction engine plus a fleet of runtime agents that keep it current.
It is the productionized version of an Excel model (`WorldCup2026_Analytics_Companion.xlsx`).

**Two backends, one codebase** — `lib/db.py` switches on the `DATABASE_URL` env var:
- **Local (default):** SQLite, single file `wc.db`. No server. Dev + offline.
- **Hosted:** Supabase (Postgres) for state + Vercel for the dashboard, with the results
  monitor on GitHub Actions. Always-on, remote, no machine of yours required.

> History: this began **fully local** ("SQLite … No Supabase, no server"). On 2026-06-14 it
> was deliberately re-platformed to add the hosted option — see `docs/DEPLOY.md`. The local
> path still works unchanged; the hosted path activates only when `DATABASE_URL` is set, so
> "no Supabase" is no longer a constraint, it's just the default.

## "Fine-tuning the model" means parameter tuning, NOT LLM fine-tuning
The "model" is the power formula and its coefficients in `engine/params.py`. Tuning =
optimizing those numbers against held-out past tournaments (a `scipy.optimize` loop +
backtest scored with Brier / log-loss). We never fine-tune Claude itself.

## Architecture
- `engine/` — pure functions, no I/O. The math lives here.
  - `power.py` — `power(form)`, `match_probs(a, b)`, `expected_goals(a, b)`.
  - `rerate.py` — in-tournament Elo-style update of prior ratings from real results.
  - `scoring.py` — Brier / log-loss / RPS / accuracy. `backtest.py` — leakage-free
    walk-forward replay; both the scoreboard and the tuner score through this one path.
  - `params.py` — `DEFAULT_PARAMS` (the tunable knobs) + confederation SoS defaults.
- `lib/db.py` — all DB access, **dual-backend**: SQLite by default, Postgres (Supabase)
  when `DATABASE_URL` is set. Translates placeholders + coerces timestamps so callers are
  backend-agnostic. `lib/notify.py` — optional Telegram control plane.
- `agents/` — runtime workers. `results_monitor.py` + `telegram_bot.py` (Anthropic API) and
  `tuner.py` (scipy backtest, proposes a `model_params` version) are built; squad monitor
  and ingest are next (see Roadmap).
- `scripts/` — `seed_from_xlsx.py` (one-time bridge from the workbook), `predict.py`
  (exposes `recompute()`), `scoreboard.py` (grade forecasts vs finals; `--save` records the
  baseline), `migrate_to_postgres.py` (copy `wc.db` → Supabase).
- `app.py` + `webapp/` — FastAPI + Tailwind dashboard/control panel. `api/index.py` +
  `vercel.json` deploy it to Vercel; control actions fire GitHub Actions when serverless.
- `db/schema.sql` (SQLite) + `db/schema_postgres.sql` (Supabase) — the schema, two dialects.

Deeper references in `docs/`: [ARCHITECTURE](docs/ARCHITECTURE.md), [MODEL](docs/MODEL.md),
[DATABASE](docs/DATABASE.md), [DEPLOY](docs/DEPLOY.md) (Supabase + Vercel runbook). Claude
Code helpers live in `.claude/` — subagents
(`data-integrity-auditor`, `runtime-agent-builder`, `model-tuner`) and skills
(`/check-results`, `/record-result`, `/reseed`).

## The model (keep parity with the workbook)
    power = ( PPG/3 * 40
              + clamp(GF/g - GA/g, -2.5, 2) * 8     # net goal margin
              + clamp(1.3 - GA/g, -0.7, 1.3) * 6 )  # opposition-weighted defense
            * SoS
            + PassAcc * 0.3 + Pressing * 1.5
- PPG (points-per-game), not win%, so draws against strong sides count.
- The defense term is multiplied by SoS, so a clean sheet vs a strong side outweighs one
  vs a weak one.
- `match_probs`: Δ = Pa−Pb; draw% = max(0.19, 0.56 − |Δ|/48); win via logistic on Δ/15.

## Strength of schedule (the heart of it)
SoS is per-team and **evidence-based**, not just per-confederation. Confederation
defaults are in `params.CONFED_SOS` (CONMEBOL 1.12 … OFC 0.55); overrides live in
`team_form.sos`, set from real strong-opposition results (friendlies, AFCON 2025, Copa
América 2024). Example: Japan earns a higher SoS for beating Germany/Brazil; Norway's
perfect qualifying was discounted after losing 5-1 to Austria. `team_form.notes` records
the provenance for each team.

## Guardrails (non-negotiable)
1. **Agents propose; you approve.** Every agent action is logged to `agent_runs`. Pure
   observed facts (a final score) may auto-commit. Anything that changes model PARAMETERS
   or applies a squad adjustment writes a `proposed` row and waits for approval.
2. **Never fabricate data.** No invented friendlies/scores. If a result can't be sourced,
   skip it. Absence of evidence keeps a team on its confederation default.
3. **Full team names always** — "Ivory Coast", never a code. Slugs are lowercase-hyphen.
4. The three played group games stay as recorded; don't overwrite observed results.

## Run
    pip install -r requirements.txt
    python scripts/seed_from_xlsx.py            # build wc.db from the workbook
    python scripts/predict.py                   # compute ratings + predictions
    python scripts/scoreboard.py                # grade forecasts vs finals (Brier/log-loss/RPS)
    python agents/tuner.py                      # backtest + propose a tuned model_params (proposes only)
    ANTHROPIC_API_KEY=... python agents/results_monitor.py   # fetch finals, re-rate
    python app.py                               # dashboard at http://127.0.0.1:8000
                                                # (WC_WEB_PORT=8765 if 8000 is busy)

## Roadmap (next agents, in order)
1. `agents/squad_monitor.py` — watch injury/lineup news; when a key player is out, write a
   `power_adjustment` to `squad_status` (proposed). Fixes the model's biggest blind spot.
2. `agents/ingest.py` — research/scraper agent: fetch & normalize new competitive results.

Built: `agents/tuner.py` — backtests via `engine/backtest.py`, scipy-optimizes a bounded
knob subset, proposes a new `model_params` version vs a baseline (proposes only; never
edits `DEFAULT_PARAMS`). Held-out past tournaments (Euro 2024 / WC 2022) aren't in the repo
yet, so it defaults to backtesting the recorded WC2026 finals and flags that as a small
in-sample set — pass `--holdout <file>` when real past-tournament data exists.
