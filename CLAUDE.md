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
  - `fantasy.py` — fantasy-pool scoring of an exact-scoreline pick (15/9/8/7/5/0); grades
    the user's picks AND the engine's own rounded pick head-to-head (the Performance tab).
  - `params.py` — `DEFAULT_PARAMS` (the tunable knobs) + confederation SoS defaults.
- `lib/db.py` — all DB access, **dual-backend**: SQLite by default, Postgres (Supabase)
  when `DATABASE_URL` is set. Translates placeholders + coerces timestamps so callers are
  backend-agnostic. `lib/notify.py` — optional Telegram control plane.
- `agents/` — runtime workers. `results_monitor.py` + `telegram_bot.py` (Anthropic API),
  `tuner.py` (scipy backtest, proposes a `model_params` version), and `ingest.py` (SportDB
  result feed) are built; the squad monitor is next (see Roadmap).
- `scripts/` — `seed_from_snapshot.py` (canonical seed, from `data/seed_snapshot.json`) +
  `snapshot_db.py` (freeze a new baseline); `seed_from_xlsx.py` is the deprecated workbook
  bridge. `predict.py` (exposes `recompute()`), `scoreboard.py` (grade forecasts vs finals;
  `--save` records the baseline), `migrate_to_postgres.py` (copy `wc.db` → Supabase).
- `app.py` + `webapp/` — FastAPI + Tailwind dashboard/control panel. `api/index.py` +
  `vercel.json` deploy it to Vercel; control actions fire GitHub Actions when serverless.
  The **Performance** tab tracks results: engine forecast accuracy (Brier/log-loss/RPS via
  the same walk-forward path) plus you-vs-engine fantasy points, and an editor that POSTs
  scoreline picks to `/api/predictions` (stored in `user_predictions`, user-entered facts so
  they commit directly — not the agent proposal queue). `GET /api/scoreboard` serves it.
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
`team_form.sos`, derived from real strong-opposition results by `agents/sos_sourcer.py`
(scores each team's last-N official matches vs rated opposition; cross-confederation weighs
most). `team_form.notes` records the provenance (real scorelines) for each team. The applied
SoS lives in `team_form.sos`, is mirrored to `data/sos_overrides.json` as the record, and is
captured in `data/seed_snapshot.json` so a reseed reproduces it. NOTE: the sourcer is
window-limited (`--since`), so pre-window cross-confederation wins are under-weighted.
`scripts/audit_sos.py` flags teams whose SoS looks unsupported by the evidence.

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
    python scripts/seed_from_snapshot.py        # build wc.db from data/seed_snapshot.json (canonical)
    python scripts/snapshot_db.py               # freeze the current DB as a new seed baseline
    python agents/build_team_form.py            # rebuild team_form from real SportDB results (propose; --apply to commit)
    python agents/sos_sourcer.py --all          # evidence-based SoS for every team (propose; --apply to commit)
    python scripts/seed_user_predictions.py     # load the user's scoreline picks (fantasy bet slip); snapshot to bake in
    python scripts/predict.py                   # compute ratings + predictions
    python scripts/scoreboard.py                # grade forecasts vs finals (Brier/log-loss/RPS)
    python scripts/build_holdout.py             # build data/holdout/wc2022.json (real out-of-sample set)
    python agents/tuner.py --holdout data/holdout/wc2022.json   # tune on real OOS data (proposes only)
    python agents/sos_sourcer.py --dry-run      # propose evidence-based SoS overrides (proposes only)
    ANTHROPIC_API_KEY=... python agents/results_monitor.py   # fetch finals, re-rate
    python app.py                               # dashboard at http://127.0.0.1:8000
                                                # (WC_WEB_PORT=8765 if 8000 is busy)

## Roadmap (next agents, in order)
1. `agents/squad_monitor.py` — watch injury/lineup news; when a key player is out, write a
   `power_adjustment` to `squad_status` (proposed). Fixes the model's biggest blind spot.

Built: `agents/ingest.py` — pulls finished World Cup results from SportDB.dev (a REST proxy
over Flashscore; key `SPORTDB_API_KEY`, free tier 3 RPS) and records them. Maps Flashscore
team names to our slugs via an accent-stripping normalizer + alias table, filters to the
finals window so shared-feed qualifiers can't collide with a finals fixture, and re-rates via
`recompute()`. Observed finals auto-commit (guardrail #1). It's also wired to the dashboard's
"Run results monitor" button. Run: `python agents/ingest.py` (`--dry-run` to preview).

Built: `agents/tuner.py` — backtests via `engine/backtest.py`, scipy-optimizes a bounded
knob subset, proposes a new `model_params` version vs a baseline (proposes only; never
edits `DEFAULT_PARAMS`). Without `--holdout` it backtests the recorded WC2026 finals and flags
that as a small in-sample set; pass `--holdout data/holdout/wc2022.json` for a real OOS tune.

Built: `scripts/build_holdout.py` — builds a real out-of-sample backtest set from a past World
Cup via SportDB: the finals window + each finalist's pre-tournament form reconstructed from its
real qualifier results in the same feed (leakage-free). 90-minute scores, so a shootout reads
as a draw; missing tactical inputs use the engine's own defaults — no fabrication. Writes
`data/holdout/wc<season>.json` (the `--holdout` shape). `--season` parameterized (add Euro 2024,
WC 2018…). Finding: WC2022 alone shows the model is overconfident but can't pin the magnitude —
one tournament gives a degenerate tune, so add more before changing `logistic_scale`.

Built: `agents/sos_sourcer.py` — proposes evidence-based SoS overrides for teams on a bare
confederation default. Pulls each team's real results from reachable SportDB feeds (WC
qualifiers + continental cups + friendlies; unreachable feeds reported, never faked), scores
them vs rated opposition (cross-confederation weighs most), and writes a conservative SoS +
provenance quoting the real scorelines to `data/sos_overrides_proposed.json` + a `proposed`
agent_run. Proposes only (guardrail #1); teams without enough evidence keep their default
(guardrail #2). Run: `python agents/sos_sourcer.py --dry-run`.
