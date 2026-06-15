"""Model parameters — the knobs the tuner optimizes.

These are the exact coefficients from the Excel engine. They live here (and, once
seeded, in the model_params table) so the tuner can propose new versions against a
backtest rather than anyone hand-editing magic numbers.

NOTE: "fine-tuning the model" means optimizing THESE numbers against held-out
tournaments. It does NOT mean fine-tuning Claude the LLM.
"""

DEFAULT_PARAMS = {
    # results term: points-per-game (normalized 0-1) * ppg_w
    "ppg_w": 40.0,
    # net goal-difference per game, clamped, * gd_w
    "gd_w": 8.0, "gd_cap_hi": 2.0, "gd_cap_lo": -2.5,
    # opposition-weighted defensive term: (def_anchor - avg_ga) clamped, * def_w
    "def_w": 6.0, "def_anchor": 1.3, "def_cap_hi": 1.3, "def_cap_lo": -0.7,
    # tactics terms
    "pass_w": 0.3, "press_w": 1.5,
    # expected-scoreline mapping: goals = score_base +/- power_gap/score_div
    "score_base": 1.3, "score_div": 30.0,
    # win/draw/loss probability model
    "draw_base": 0.56, "draw_slope": 48.0, "draw_floor": 0.19,
    "logistic_scale": 15.0,
    # in-tournament update strength (Elo-style K)
    "rerate_k": 6.0,
}

# Confederation default SoS. Per-team overrides live in team_form.sos (set by evidence:
# friendlies / AFCON / Copa). See CLAUDE.md "Strength of schedule".
CONFED_SOS = {
    "CONMEBOL": 1.12, "UEFA": 0.96, "AFC": 0.72,
    "CAF": 0.72, "CONCACAF": 0.72, "OFC": 0.55,
}
