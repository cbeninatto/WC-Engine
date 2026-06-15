"""Scoreboard — grade the engine's pre-match forecasts against observed finals.

Honest and leakage-free. The stored `predictions` rows are computed off post-result
ratings (predict.py re-rates on every final, then predicts all matches), so grading those
directly would leak each result into its own forecast. Instead we WALK FORWARD (see
engine/backtest.py): predict each played match from ratings re-rated only on *earlier*
finals — exactly what the engine knew before kickoff — then fold the result in. Matchday-1
games are graded off pure pre-tournament form; later games get the in-tournament re-rate,
which is the whole point of that mechanism.

    python scripts/scoreboard.py            # print the report
    python scripts/scoreboard.py --save     # also store baseline metrics in model_params v1
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # render Türkiye / Curaçao on Windows consoles
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import lib.db as db
from engine.backtest import walk_forward
from engine.params import DEFAULT_PARAMS
from engine.scoring import evaluate

VERSION = 1
LABEL = {0: "H", 1: "D", 2: "A"}


def main():
    save = "--save" in sys.argv
    conn = db.connect()
    db.init_db(conn)
    teams = db.teams_with_form(conn)
    finals = db.final_matches(conn)
    samples, rows = walk_forward(teams, finals, DEFAULT_PARAMS)
    m = evaluate(samples)
    if not m["n"]:
        print("No final matches to score yet.")
        conn.close()
        return

    # uniform 1/3 guess over the same outcomes — the bar any real model must clear.
    base = evaluate([{**s, "probs": (1 / 3, 1 / 3, 1 / 3)} for s in samples])

    print(f"\nWALK-FORWARD SCOREBOARD   {m['n']} finals · params v{VERSION}\n")
    print(f"  {'match':37s} {'pred':>5}   {'P(H / D / A)':<16} {'act':>5}  call")
    print(f"  {'-'*37} {'-'*5}   {'-'*16} {'-'*5}  ----")
    for r in rows:
        hn, an = teams[r["home_id"]]["name"], teams[r["away_id"]]["name"]
        probs, pg, ag = r["probs"], r["pred_goals"], r["actual_goals"]
        pick = max(range(3), key=lambda i: probs[i])
        flag = "OK " if pick == r["outcome"] else " x "
        match = f"{hn} v {an}"[:37]
        print(f"  {match:37s} {pg[0]}-{pg[1]}   "
              f"{probs[0]:.2f}/{probs[1]:.2f}/{probs[2]:.2f}  {ag[0]}-{ag[1]}  {LABEL[pick]} {flag}")

    def line(label, model_v, base_v, better="lower"):
        edge = "beats" if (model_v < base_v) == (better == "lower") else "WORSE than"
        print(f"  {label:18s} {model_v:8.4f}   (1/3 guess {base_v:.4f} · {edge})")

    print(f"\n  Winner accuracy    {m['winner_hits']}/{m['n']}  {m['winner_acc']*100:.1f}%")
    print(f"  Exact score        {m['exact_hits']}/{m['n']}  {m['exact_rate']*100:.1f}%")
    line("Brier (W/D/L)", m["brier"], base["brier"])
    line("Log-loss", m["log_loss"], base["log_loss"])
    line("RPS (ordered)", m["rps"], base["rps"])
    print(f"  Draws              predicted {m['pred_draws']}, actual {m['actual_draws']}")

    if save:
        db.upsert_model_params(
            conn, VERSION, DEFAULT_PARAMS, round(m["brier"], 4), round(m["log_loss"], 4),
            f"baseline: walk-forward over {m['n']} finals", approved=1)
        conn.commit()
        print(f"\n  Saved baseline metrics to model_params v{VERSION} (the tuner's bar to beat).")
    conn.close()


if __name__ == "__main__":
    main()
