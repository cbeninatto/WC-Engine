# The Model

The "model" is a **power-rating formula plus a probability curve**, ported verbatim from
the Excel engine so outputs stay identical. "Fine-tuning the model" means optimizing the
coefficients in [`engine/params.py`](../engine/params.py) against past tournaments — it
does **not** mean fine-tuning an LLM.

All code referenced here lives in [`engine/`](../engine/) and has no I/O.

## 1. Power rating

Per-team, per-game rating. Uses **rates, not totals**, so it's sample-size agnostic — a
team with 8 games and one with 20 are comparable.

```
power = ( PPG/3 · 40
          + clamp(GF/g − GA/g, −2.5, 2) · 8        # net goal margin
          + clamp(1.3 − GA/g, −0.7, 1.3) · 6 )     # opposition-weighted defense
        · SoS
        + PassAcc · 0.3 + Pressing · 1.5
        + power_adjustment                          # injuries etc. (squad monitor)
```

Where `g = played`, and `PPG/3 = (3·wins + draws) / (3·played)` is points-per-game
normalized to 0–1.

| Term | Meaning | Why it's shaped this way |
|------|---------|--------------------------|
| **PPG**, not win % | points per game | A draw against a strong side still earns credit. |
| **Net goal margin** | `GF/g − GA/g`, clamped `[−2.5, 2]` | Rewards dominance but caps blowouts so one 7–1 doesn't distort form. |
| **Defense** | `1.3 − GA/g`, clamped `[−0.7, 1.3]` | Anchored at 1.3 goals; conceding less than that is positive. |
| **× SoS** | strength of schedule | The results **and** defense terms scale with SoS, so a clean sheet vs a strong side outweighs one vs a minnow. |
| **Tactics** | pass accuracy, pressing | Small additive nudges (`+PassAcc·0.3 + Pressing·1.5`) outside the SoS multiplier. |

Coefficients (`ppg_w`, `gd_w`, `def_w`, `def_anchor`, caps, `pass_w`, `press_w`) are all in
`DEFAULT_PARAMS`. `power()` returns `0.0` for a team with `played <= 0`.

## 2. Strength of schedule (SoS) — the heart of it

SoS is **per-team and evidence-based**, not merely per-confederation.

- **Confederation defaults** (`params.CONFED_SOS`): CONMEBOL 1.12, UEFA 0.96, AFC/CAF/
  CONCACAF 0.72, OFC 0.55.
- **Per-team overrides** live in `team_form.sos`, set from real strong-opposition results
  (friendlies, AFCON 2025, Copa América 2024). `team_form.notes` records the provenance.

Examples (the philosophy in practice):
- **Japan** earns a higher SoS for beating Germany/Brazil — its AFC default understates it.
- **Norway's** perfect qualifying was discounted after losing 5–1 to Austria.

> **Guardrail:** absence of evidence keeps a team on its confederation default. Never
> invent a friendly to justify an SoS bump. See [CLAUDE.md](../CLAUDE.md).

## 3. Match probabilities

`match_probs(power_a, power_b)` turns a power gap `Δ = Pa − Pb` into win/draw/win:

```
draw  = max(0.19, 0.56 − |Δ|/48)                    # widest when evenly matched
win_a = logistic(Δ / 15) · (1 − draw)               # 1 / (1 + 10^(−Δ/15))
win_b = 1 − win_a − draw
```

- The **draw curve** peaks for evenly matched sides (floor 0.19) and shrinks as the gap
  grows — mismatches rarely draw.
- Win probability is a **logistic** on the scaled gap, then split out of the non-draw mass.

Knobs: `draw_base 0.56`, `draw_slope 48`, `draw_floor 0.19`, `logistic_scale 15`.

## 4. In-tournament re-rate

The form model is static — it can't see what a team is doing *at the tournament*. As real
results land, [`engine/rerate.py`](../engine/rerate.py) nudges power with an Elo-style
update keyed off the model's own logistic:

```
expected_A = 1 / (1 + 10^(−(Pa − Pb)/15))           # engine.power.expected_score
actual_A   = 1.0 win | 0.5 draw | 0.0 loss
Pa += K · (actual_A − expected_A)                    # K = rerate_k = 6
Pb −= K · (actual_A − expected_A)
```

- Each team starts from its form-based `prior_power`; results pull `power` toward what's
  actually happening on the pitch. A light Bayesian update — the prior dominates early.
- `rerate_all` folds every `final` match into the priors **in kickoff order**, returning
  post-tournament powers and a per-team WC-game count.
- Worked example (from real data): Australia beat a higher-rated Türkiye 2–0 → Australia
  `+5.3`, Türkiye `−5.3` (a draw-expectation upset produces the biggest swing).

## 5. Tuning (roadmap)

The future `agents/tuner.py` will backtest `DEFAULT_PARAMS` on Euro 2024 / WC 2022,
optimize with `scipy.optimize`, score by **Brier / log-loss** against a baseline, and
**propose** a new `model_params` version (write a `proposed` row — never auto-apply).
Because the engine is pure, the backtest needs no database. See
[CLAUDE.md](../CLAUDE.md) "Roadmap".
