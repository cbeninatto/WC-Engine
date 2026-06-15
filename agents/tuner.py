"""Tuner — backtest the engine's params and PROPOSE a better-calibrated version.

  1. Backtest the current DEFAULT_PARAMS over a set of real, observed matches (the
     leakage-free walk-forward in engine/backtest.py) -> baseline Brier / log-loss / RPS.
  2. scipy.optimize a small, bounded subset of knobs to minimize the chosen objective.
  3. Write the winner to model_params as a NEW version with approved=0, plus a 'proposed'
     agent_runs row.

GUARDRAIL (CLAUDE.md #1): the tuner proposes, it never applies. It does not edit
DEFAULT_PARAMS, does not touch predictions, does not flip `approved`. Approving a proposal
(copying the knob values into engine/params.py and re-running predict.py) is a human step.

DATA HONESTY (.claude/agents/model-tuner.md): the methodology wants held-out PAST
tournaments (Euro 2024, WC 2022). Those aren't in this repo, so by default the tuner
backtests on the real WC2026 finals already recorded — a small, IN-SAMPLE set: treat the
output as low-confidence calibration, not a validated refit. It will NOT fabricate past
results to pad the backtest; point --holdout at a real dataset when you have one.

    python agents/tuner.py                                   # propose (default knobs/objective)
    python agents/tuner.py --knobs logistic_scale,draw_base,draw_slope,rerate_k
    python agents/tuner.py --objective log_loss --maxiter 120
    python agents/tuner.py --holdout data/euro2024.json      # backtest a real hold-out set
    python agents/tuner.py --dry-run                         # report only, write no proposal
"""
import sys
import os
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import lib.db as db
from engine.params import DEFAULT_PARAMS
from engine.backtest import backtest

# Bounds keep each knob interpretable (caps stay caps; weights non-negative). The tuner only
# moves the knobs you name; everything else stays at DEFAULT_PARAMS.
BOUNDS = {
    "ppg_w": (0.0, 80.0), "gd_w": (0.0, 20.0), "def_w": (0.0, 15.0),
    "def_anchor": (0.5, 2.0), "pass_w": (0.0, 1.0), "press_w": (0.0, 4.0),
    "draw_base": (0.20, 0.70), "draw_slope": (15.0, 150.0), "draw_floor": (0.0, 0.35),
    "logistic_scale": (5.0, 50.0), "rerate_k": (0.0, 20.0),
}
# Default knobs target exactly what the scoreboard exposed: overconfidence (logistic_scale)
# and draw frequency/shape (draw_base, draw_slope). The power-formula weights are left fixed
# — that's the model we're keeping, not retuning.
DEFAULT_KNOBS = ["logistic_scale", "draw_base", "draw_slope"]
REPORT_METRICS = ("brier", "log_loss", "rps", "winner_acc")
MIN_SANE = 25  # fewer backtest matches than this -> loudly flag low confidence


def load_backtest_set(conn, holdout):
    """(team_forms, finals, source-label). A --holdout JSON must supply real data."""
    if holdout:
        if not os.path.isfile(holdout):
            raise SystemExit(f"--holdout file not found: {holdout}\n"
                             "Provide a real dataset; the tuner will not fabricate one.")
        data = json.loads(Path(holdout).read_text(encoding="utf-8"))
        return data["team_forms"], data["finals"], f"holdout:{Path(holdout).name}"
    return db.teams_with_form(conn), db.final_matches(conn), "db:wc2026-finals"


def main():
    ap = argparse.ArgumentParser(description="Backtest + propose tuned model params.")
    ap.add_argument("--knobs", default=",".join(DEFAULT_KNOBS),
                    help=f"comma-separated; choose from {list(BOUNDS)}")
    ap.add_argument("--objective", default="brier", choices=["brier", "log_loss", "rps"])
    ap.add_argument("--maxiter", type=int, default=80)
    ap.add_argument("--seed", type=int, default=0, help="for reproducible optimization")
    ap.add_argument("--holdout", default=None, help="path to a real hold-out dataset (JSON)")
    ap.add_argument("--dry-run", action="store_true", help="report only; write no proposal")
    args = ap.parse_args()

    try:
        from scipy.optimize import differential_evolution
    except ImportError:
        raise SystemExit("The tuner needs scipy:  pip install scipy")

    knobs = [k.strip() for k in args.knobs.split(",") if k.strip()]
    bad = [k for k in knobs if k not in BOUNDS]
    if bad:
        raise SystemExit(f"Unknown/untunable knobs: {bad}. Choose from {list(BOUNDS)}")

    conn = db.connect()
    db.init_db(conn)
    team_forms, finals, source = load_backtest_set(conn, args.holdout)
    n = len(finals)
    if n == 0:
        raise SystemExit("No matches to backtest. Record some finals first.")

    obj = args.objective
    base = backtest(team_forms, finals, DEFAULT_PARAMS)

    def loss(x):
        cand = {**DEFAULT_PARAMS, **dict(zip(knobs, x))}
        return backtest(team_forms, finals, cand)[obj]

    res = differential_evolution(
        loss, [BOUNDS[k] for k in knobs], seed=args.seed, maxiter=args.maxiter,
        tol=1e-4, mutation=(0.5, 1.0), recombination=0.7, polish=True)
    best = {**DEFAULT_PARAMS, **{k: round(float(v), 4) for k, v in zip(knobs, res.x)}}
    cand = backtest(team_forms, finals, best)

    # ---- report: baseline vs candidate ----
    print(f"\nTUNER   source {source} · {n} matches · objective '{obj}' · knobs {knobs}\n")
    print(f"  {'metric':12s} {'baseline':>10} {'candidate':>10} {'delta':>10}")
    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*10}")
    for k in REPORT_METRICS:
        b, c = base[k], cand[k]
        better = (c > b) if k == "winner_acc" else (c < b)
        print(f"  {k:12s} {b:10.4f} {c:10.4f} {c-b:+10.4f}  {'better' if better else 'worse'}")
    print(f"\n  knob diffs (only the tuned ones move):")
    for k in knobs:
        print(f"    {k:16s} {DEFAULT_PARAMS[k]:8.3f} -> {best[k]:8.3f}")

    if n < MIN_SANE:
        print(f"\n  [!] {n} matches is a SMALL, in-sample set: low-confidence calibration,"
              f"\n      NOT a validated refit. For a real tune, backtest held-out past"
              f"\n      tournaments via --holdout (the tuner won't fabricate them).")

    improved = cand[obj] < base[obj]
    if not improved:
        print(f"\n  Candidate does not beat baseline on '{obj}'. Nothing worth proposing.")
        conn.close()
        return
    if args.dry_run:
        print(f"\n  --dry-run: candidate beats baseline but no proposal was written.")
        conn.close()
        return

    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS m FROM model_params").fetchone()["m"] + 1
    note = (f"tuner: {obj} {base[obj]:.4f}->{cand[obj]:.4f} on {n} {source} matches; "
            f"tuned {','.join(knobs)}; awaiting approval")
    db.upsert_model_params(conn, version, best, round(cand["brier"], 4),
                           round(cand["log_loss"], 4), note=note, approved=0)
    db.log_run(conn, "tuner", "propose_params", {
        "version": version, "objective": obj, "source": source, "n": n,
        "baseline": {k: round(base[k], 4) for k in ("brier", "log_loss", "rps")},
        "candidate": {k: round(cand[k], 4) for k in ("brier", "log_loss", "rps")},
        "knobs": {k: best[k] for k in knobs},
    }, status="proposed")
    conn.commit()
    conn.close()

    print(f"\n  Proposed model_params v{version} (approved=0) — AWAITING APPROVAL.")
    print(f"  To approve: copy the knob values above into engine/params.py DEFAULT_PARAMS,")
    print(f"  set model_params v{version}.approved=1, then re-run scripts/predict.py.")


if __name__ == "__main__":
    main()
