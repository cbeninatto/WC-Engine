# CLAUDE.md — WC Engine

Project context for Claude Code. Read this before changing anything.

## What this is
A local World Cup 2026 prediction engine plus a fleet of runtime agents that keep it
current. It is the productionized version of an Excel model (`WorldCup2026_Analytics_Companion.xlsx`).
**Fully local: SQLite, single file `wc.db`. No Supabase, no server.** Only the runtime
agents make outbound calls (Anthropic API + web search).

## "Fine-tuning the model" means parameter tuning, NOT LLM fine-tuning
The "model" is the power formula and its coefficients in `engine/params.py`. Tuning =
optimizing those numbers against held-out past tournaments (a `scipy.optimize` loop +
backtest scored with Brier / log-loss). We never fine-tune Claude itself.

## Architecture
- `engine/` — pure functions, no I/O. The math lives here.
  - `power.py` — `power(form)` and `match_probs(a, b)`.
  - `rerate.py` — in-tournament Elo-style update of prior ratings from real results.
  - `params.py` — `DEFAULT_PARAMS` (the tunable knobs) + confederation SoS defaults.
- `lib/db.py` — all SQLite access. `lib/notify.py` — optional Telegram control plane.
- `agents/` — runtime workers (Anthropic API). `results_monitor.py` is built; squad
  monitor, ingest, and tuner are the next three (see Roadmap).
- `scripts/` — `seed_from_xlsx.py` (one-time bridge from the workbook), `predict.py`.
- `db/schema.sql` — the schema.

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
    ANTHROPIC_API_KEY=... python agents/results_monitor.py   # fetch finals, re-rate

## Roadmap (next agents, in order)
1. `agents/squad_monitor.py` — watch injury/lineup news; when a key player is out, write a
   `power_adjustment` to `squad_status` (proposed). Fixes the model's biggest blind spot.
2. `agents/ingest.py` — research/scraper agent: fetch & normalize new competitive results.
3. `agents/tuner.py` — backtest on Euro 2024 / WC 2022, optimize `params`, propose a new
   `model_params` version scored vs a baseline. Proposes only.
