"""Recompute power ratings + match predictions and store them.

Run after seeding, and again after the results monitor folds in new results.

    python scripts/predict.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lib.db as db
from engine.power import TeamForm, power, match_probs
from engine.rerate import rerate_all
from engine.params import DEFAULT_PARAMS

VERSION = 1


def recompute(conn=None):
    """Recompute power ratings + predictions. Pure DB + math (no I/O, no subprocess),
    so it runs inline anywhere — CLI, the web app, or a serverless function.

    Returns (post_powers, prior_powers, wc_games, teams, n_finals) for callers that want
    to report. Caller owns the connection if one is passed; otherwise we open+close ours.
    """
    own = conn is None
    if own:
        conn = db.connect()
    db.init_db(conn)  # ensure tables exist even on a fresh checkout

    # 1) prior power from form
    teams = db.teams_with_form(conn)
    if not teams:
        raise SystemExit(
            "No teams in the database yet. Seed it first:\n"
            "  python scripts\\seed_from_xlsx.py "
            '"%USERPROFILE%\\Downloads\\WorldCup2026_Analytics_Companion.xlsx"'
        )
    prior = {}
    for tid, t in teams.items():
        if t.get("played") is None:
            continue
        prior[tid] = power(TeamForm(
            t["played"], t["wins"], t["draws"], t["losses"], t["gf"], t["ga"],
            t["pass_acc"], t["pressing"], t["sos"],
        ))

    # 2) fold in any final WC results (in-tournament re-rate)
    finals = db.final_matches(conn)
    post, wc_games = rerate_all(prior, finals, DEFAULT_PARAMS)
    for tid in prior:
        db.upsert_power(conn, tid, round(post[tid], 1), round(prior[tid], 1),
                        wc_games.get(tid, 0), VERSION)

    # 3) predictions for every match off the (post) ratings
    for m in db.all_matches(conn):
        h, a = m["home_id"], m["away_id"]
        if h not in post or a not in post:
            continue
        wh, dr, wa = match_probs(post[h], post[a], DEFAULT_PARAMS)
        # crude expected scoreline from the power gap
        gap = (post[h] - post[a]) / 30.0
        ph, pa = round(max(0, 1.3 + gap), 1), round(max(0, 1.3 - gap), 1)
        db.upsert_prediction(conn, m["id"], wh, dr, wa, ph, pa, VERSION)

    conn.commit()
    if own:
        conn.close()
    return post, prior, wc_games, teams, len(finals)


def main():
    post, prior, wc_games, teams, n_finals = recompute()
    ranked = sorted(post.items(), key=lambda kv: -kv[1])
    print(f"Re-rated {len(post)} teams over {n_finals} final matches. Top 5:")
    for tid, pw in ranked[:5]:
        tag = f"  (prior {prior[tid]:.1f}, {wc_games.get(tid,0)} WC games)"
        print(f"  {teams[tid]['name']:22s} {pw:5.1f}{tag}")


if __name__ == "__main__":
    main()
