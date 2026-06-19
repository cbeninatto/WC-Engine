"""WC Engine — local browser dashboard + control panel (FastAPI).

Read views (standings, predictions, ratings, results) plus controls that drive the
existing engine: run the results monitor, recompute predictions, and work the
agent_runs approval queue. Fully local — reads/writes the same wc.db, no extra server.

    python app.py                 # serve on http://127.0.0.1:8000
    uvicorn app:app --reload      # dev
"""
from __future__ import annotations

import os
import sys
import subprocess
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402  (loads .env)
import lib.db as db  # noqa: E402
from engine import fantasy  # noqa: E402
from engine.backtest import walk_forward  # noqa: E402
from engine.params import DEFAULT_PARAMS  # noqa: E402
from engine.scoring import evaluate  # noqa: E402
from engine.simulate import project_bracket, simulate  # noqa: E402
from engine import bracket as bk  # noqa: E402

app = FastAPI(title="WC Engine")

WEBAPP = ROOT / "webapp"
POINTS = {"W": 3, "D": 1, "L": 0}


# --- helpers -----------------------------------------------------------------

def _run(script_args: list[str], timeout: int) -> dict:
    """Run a project script in a subprocess with .env + utf-8, capture output."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run(
            [sys.executable, *script_args],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"timed out after {timeout}s"}
    out = (p.stdout or "") + (("\n" + p.stderr) if p.returncode else "")
    return {"ok": p.returncode == 0, "output": out.strip()}


def _standings(conn) -> list[dict]:
    teams = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM teams")}
    table: dict[str, dict] = {}
    for t in teams.values():
        table[t["id"]] = {
            "team": t["name"], "group": t["group_code"],
            "P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "GD": 0, "Pts": 0,
        }
    for m in conn.execute("SELECT * FROM matches WHERE status='final'"):
        h, a, hg, ag = m["home_id"], m["away_id"], m["home_goals"], m["away_goals"]
        if h not in table or a not in table or hg is None:
            continue
        for tid, gf, ga in ((h, hg, ag), (a, ag, hg)):
            row = table[tid]
            row["P"] += 1
            row["GF"] += gf
            row["GA"] += ga
            row["GD"] = row["GF"] - row["GA"]
            res = "W" if gf > ga else ("D" if gf == ga else "L")
            row[res] += 1
            row["Pts"] += POINTS[res]
    groups: dict[str, list] = {}
    for row in table.values():
        groups.setdefault(row["group"], []).append(row)
    out = []
    for g in sorted(k for k in groups if k):
        rows = sorted(groups[g], key=lambda r: (-r["Pts"], -r["GD"], -r["GF"], r["team"]))
        out.append({"group": g, "rows": rows})
    return out


# --- API ---------------------------------------------------------------------

@app.get("/api/state")
def state():
    conn = db.connect()
    db.init_db(conn)
    teams = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM teams")}
    preds = {r["match_id"]: dict(r) for r in conn.execute("SELECT * FROM predictions")}
    user_preds = db.user_predictions(conn)
    today = date.today().isoformat()

    matches = []
    for m in conn.execute("SELECT * FROM matches ORDER BY kickoff, id"):
        h, a = teams.get(m["home_id"]), teams.get(m["away_id"])
        if not h or not a:
            continue
        p = preds.get(m["id"], {})
        up = user_preds.get(m["id"], {})
        overdue = (m["status"] != "final" and m["kickoff"] is not None
                   and m["kickoff"] <= today)
        matches.append({
            "id": m["id"], "group": m["group_code"], "kickoff": m["kickoff"],
            "home": h["name"], "away": a["name"], "status": m["status"],
            "home_goals": m["home_goals"], "away_goals": m["away_goals"],
            "source": m["source"], "overdue": overdue,
            "win_home": p.get("win_home"), "draw": p.get("draw"),
            "win_away": p.get("win_away"),
            "pred_home_goals": p.get("pred_home_goals"),
            "pred_away_goals": p.get("pred_away_goals"),
            "user_home": up.get("pred_home"), "user_away": up.get("pred_away"),
        })

    ratings = []
    for r in conn.execute(
        "SELECT p.*, t.name, t.confederation FROM power_ratings p "
        "JOIN teams t ON t.id=p.team_id ORDER BY p.power DESC"
    ):
        ratings.append({
            "team": r["name"], "confederation": r["confederation"],
            "power": r["power"], "prior_power": r["prior_power"],
            "delta": round((r["power"] or 0) - (r["prior_power"] or 0), 1),
            "wc_games": r["wc_games"],
        })

    proposals = [dict(r) for r in conn.execute(
        "SELECT * FROM agent_runs WHERE status='proposed' ORDER BY created_at DESC")]
    recent = [dict(r) for r in conn.execute(
        "SELECT * FROM agent_runs ORDER BY id DESC LIMIT 12")]

    standings = _standings(conn)
    finals = sum(1 for m in matches if m["status"] == "final")
    pending = sum(1 for m in matches if m["status"] != "final")
    overdue = sum(1 for m in matches if m["overdue"])
    conn.close()
    return {
        "today": today,
        "counts": {"finals": finals, "pending": pending,
                   "overdue": overdue, "proposals": len(proposals)},
        "standings": standings, "matches": matches, "ratings": ratings,
        "proposals": proposals, "recent": recent,
    }


def _trigger_workflow() -> dict:
    """Fire the GitHub Actions results-monitor workflow via workflow_dispatch.

    Used when the app can't run the long web-search agent itself (serverless / Vercel).
    Needs GITHUB_DISPATCH_TOKEN (a token with `actions:write`) and GITHUB_REPO ("owner/repo").
    """
    import httpx
    token, repo = os.environ.get("GITHUB_DISPATCH_TOKEN"), os.environ.get("GITHUB_REPO")
    if not token or not repo:
        return {"ok": False, "output": "GITHUB_DISPATCH_TOKEN / GITHUB_REPO not set"}
    url = f"https://api.github.com/repos/{repo}/actions/workflows/results-monitor.yml/dispatches"
    r = httpx.post(url, headers={"Authorization": f"Bearer {token}",
                                 "Accept": "application/vnd.github+json"},
                   json={"ref": "main"}, timeout=20)
    ok = r.status_code == 204
    return {"ok": ok, "output": "Triggered GitHub Actions run." if ok
            else f"dispatch failed: {r.status_code} {r.text[:120]}"}


@app.post("/api/run-monitor")
def run_monitor():
    # The "Run results monitor" button runs the SportDB ingest agent (agents/ingest.py): a
    # fast, deterministic pull of finished results from real data — the path that reliably
    # confirms finals, vs the LLM web-search results_monitor. Ingest records new finals and
    # recomputes predictions itself, so there's no inline recompute here.
    # Serverless (Vercel): delegate to the GitHub Actions workflow instead.
    if os.environ.get("GITHUB_DISPATCH_TOKEN"):
        return JSONResponse(_trigger_workflow())
    return JSONResponse(_run(["agents/ingest.py"], timeout=120))


@app.post("/api/recompute")
def recompute():
    # Pure DB + math — safe to run inline anywhere (incl. serverless).
    from scripts.predict import recompute as _recompute
    post, _, _, _, n = _recompute()
    return JSONResponse({"ok": True, "output": f"Recomputed {len(post)} teams over {n} finals."})


@app.post("/api/proposals/{run_id}/{action}")
def decide(run_id: int, action: str):
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve|reject")
    status = "applied" if action == "approve" else "rejected"
    conn = db.connect()
    cur = conn.execute(
        "UPDATE agent_runs SET status=? WHERE id=? AND status='proposed'",
        (status, run_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        raise HTTPException(404, "no pending proposal with that id")
    return {"ok": True, "id": run_id, "status": status}


# --- performance / fantasy ----------------------------------------------------

def _model_metrics(samples) -> dict:
    """Engine accuracy vs the 1/3-guess baseline — the same numbers scoreboard.py prints."""
    m = evaluate(samples)
    if not m["n"]:
        return {"n": 0}
    base = evaluate([{**s, "probs": (1 / 3, 1 / 3, 1 / 3)} for s in samples])
    return {
        "n": m["n"],
        "winner_acc": m["winner_acc"], "winner_hits": m["winner_hits"],
        "exact_rate": m["exact_rate"], "exact_hits": m["exact_hits"],
        "brier": m["brier"], "log_loss": m["log_loss"], "rps": m["rps"],
        "base_brier": base["brier"], "base_log_loss": base["log_loss"], "base_rps": base["rps"],
        "pred_draws": m["pred_draws"], "actual_draws": m["actual_draws"],
    }


def _scoreboard(conn) -> dict:
    """You-vs-engine fantasy scoring + engine accuracy over the played matches.

    The engine's scoreline pick is taken from the walk-forward backtest (leakage-free: each
    match predicted from ratings re-rated only on *earlier* finals), so it's a fair grade —
    not the post-result `predictions` rows. Both picks are scored by engine/fantasy.py.
    """
    teams = db.teams_with_form(conn)
    finals = db.final_matches(conn)
    user_preds = db.user_predictions(conn)
    samples, rows = walk_forward(teams, finals, DEFAULT_PARAMS)
    eng_by_id = {r["id"]: r["pred_goals"] for r in rows if r.get("id")}

    out_rows, you_total, eng_total, you_games, eng_games = [], 0, 0, 0, 0
    you_cats, eng_cats = {}, {}
    for m in finals:
        hg, ag = m["home_goals"], m["away_goals"]
        h, a = teams.get(m["home_id"]), teams.get(m["away_id"])
        if h is None or a is None or hg is None:
            continue
        row = {"id": m["id"], "kickoff": m["kickoff"], "group": m["group_code"],
               "home": h["name"], "away": a["name"], "actual": [hg, ag],
               "you": None, "engine": None}

        up = user_preds.get(m["id"])
        if up is not None:
            s = fantasy.score(up["pred_home"], up["pred_away"], hg, ag)
            row["you"] = {"pick": [up["pred_home"], up["pred_away"]], **s}
            you_total += s["points"]; you_games += 1
            you_cats[s["category"]] = you_cats.get(s["category"], 0) + 1

        eg = eng_by_id.get(m["id"])
        if eg is not None:
            s = fantasy.score(eg[0], eg[1], hg, ag)
            row["engine"] = {"pick": [eg[0], eg[1]], **s}
            eng_total += s["points"]; eng_games += 1
            eng_cats[s["category"]] = eng_cats.get(s["category"], 0) + 1
        out_rows.append(row)

    out_rows.sort(key=lambda r: (r["kickoff"] or "", r["id"]))
    return {
        "model": _model_metrics(samples),
        "fantasy": {
            "rows": out_rows,
            "you": {"points": you_total, "games": you_games, "by_category": you_cats},
            "engine": {"points": eng_total, "games": eng_games, "by_category": eng_cats},
            "ladder": {k: v[0] for k, v in fantasy.CATEGORIES.items()},
        },
    }


@app.get("/api/scoreboard")
def scoreboard():
    conn = db.connect()
    db.init_db(conn)
    data = _scoreboard(conn)
    conn.close()
    return data


# --- tournament simulation ----------------------------------------------------

def _sim_inputs(conn):
    """Gather the simulator's inputs from the DB: live powers, groups, group-stage games."""
    teams = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM teams")}
    powers = {r["team_id"]: r["power"]
              for r in conn.execute("SELECT team_id, power FROM power_ratings")}
    groups: dict[str, list] = {}
    for tid, t in teams.items():
        if t["group_code"]:
            groups.setdefault(t["group_code"], []).append(tid)
    played, remaining = [], []
    for m in conn.execute("SELECT * FROM matches WHERE stage='group'"):
        h, a = m["home_id"], m["away_id"]
        if h not in powers or a not in powers:
            continue
        if m["status"] == "final" and m["home_goals"] is not None:
            played.append((m["group_code"], h, a, m["home_goals"], m["away_goals"]))
        else:
            remaining.append((m["group_code"], h, a))
    return teams, powers, groups, played, remaining


# Round metadata + the seed label for an R32 slot, for serializing the projected bracket.
_BK_ROUND_NAMES = [("r32", "Round of 32"), ("r16", "Round of 16"),
                   ("qf", "Quarter-finals"), ("sf", "Semi-finals"), ("final", "Final")]
_R32_SLOTS = {mno: (sa, sb) for (mno, sa, sb) in bk.R32}


def _bracket_order():
    """Match numbers per round, top-to-bottom in bracket order (for a clean tree layout)."""
    def leaves(m):
        if m in bk.KO_TREE:
            a, b = bk.KO_TREE[m]
            return leaves(a) + leaves(b)
        return [m]
    parent = {}
    for m, (a, b) in bk.KO_TREE.items():
        parent[a] = parent[b] = m
    order = {"r32": leaves(104)}
    cur = order["r32"]
    for key in ("r16", "qf", "sf", "final"):
        cur = [parent[cur[i]] for i in range(0, len(cur), 2)]
        order[key] = cur
    return order


def _seed_label(slot, assignment):
    """Human seed for an R32 slot: 1A (winner), 2B (runner-up), 3C (best third from group C)."""
    kind, key = slot
    if kind == "W":
        return f"1{key}"
    if kind == "R":
        return f"2{key}"
    return f"3{assignment[key]}"  # ("T", match_no) -> third routed in from its group


def _bracket_payload(proj, teams):
    """Shape project_bracket output into ordered rounds of name/seed/odds for the UI."""
    order = _bracket_order()
    assignment = proj["seeds"]["assignment"]
    rounds = []
    for key, name in _BK_ROUND_NAMES:
        matches = []
        for mno in order[key]:
            m = proj["matches"][mno]
            slots = _R32_SLOTS.get(mno)
            seeds = ([_seed_label(slots[0], assignment), _seed_label(slots[1], assignment)]
                     if slots else ["", ""])
            won_a = m["winner"] == m["a"]
            matches.append({
                "match": mno,
                "a": {"name": teams[m["a"]]["name"], "seed": seeds[0], "win": won_a},
                "b": {"name": teams[m["b"]]["name"], "seed": seeds[1], "win": not won_a},
                "p": round(m["p_adv"] if won_a else 1 - m["p_adv"], 3),
            })
        rounds.append({"key": key, "name": name, "matches": matches})
    return {"champion": teams[proj["champion"]]["name"], "rounds": rounds}


@app.get("/api/simulate")
def simulate_tournament(n: int = 5000):
    """Monte Carlo the rest of the tournament: each team's odds to reach each round + win.

    Plays out the remaining group games and the full knockout bracket `n` times (clamped),
    leakage-free — it reads the live ratings and the games already recorded, nothing else.
    """
    n = max(500, min(int(n), 20000))
    conn = db.connect()
    db.init_db(conn)
    teams, powers, groups, played, remaining = _sim_inputs(conn)
    conn.close()
    # The bracket needs a complete, well-formed field: 12 groups of 4 rated teams.
    if len(groups) != 12 or any(len(v) != 4 for v in groups.values()):
        return JSONResponse(
            {"ok": False, "error": "simulation needs 12 groups of 4 rated teams"},
            status_code=503)
    probs = simulate(powers, groups, played, remaining, DEFAULT_PARAMS, n=n)
    rows = [{"team": teams[tid]["name"], "group": teams[tid]["group_code"],
             "power": powers[tid], **pr} for tid, pr in probs.items()]
    rows.sort(key=lambda r: (-r["champ"], -r["final"], -r["sf"], r["team"]))
    proj = project_bracket(powers, groups, played, remaining, DEFAULT_PARAMS)
    return {"n": n, "played": len(played), "remaining": len(remaining),
            "teams": rows, "bracket": _bracket_payload(proj, teams)}


class PredictionIn(BaseModel):
    match_id: str
    pred_home: int | None = None
    pred_away: int | None = None


@app.post("/api/predictions")
def save_prediction(p: PredictionIn):
    """Upsert (or, with null goals, clear) the user's scoreline pick for a match."""
    conn = db.connect()
    db.init_db(conn)
    if not conn.execute("SELECT 1 FROM matches WHERE id=?", (p.match_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "unknown match")
    if p.pred_home is None or p.pred_away is None:
        db.delete_user_prediction(conn, p.match_id)
        action = "cleared"
    elif p.pred_home < 0 or p.pred_away < 0:
        conn.close()
        raise HTTPException(400, "goals must be >= 0")
    else:
        db.upsert_user_prediction(conn, p.match_id, p.pred_home, p.pred_away, "manual")
        action = "saved"
    conn.commit()
    conn.close()
    return {"ok": True, "match_id": p.match_id, "action": action}


# --- static ------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(WEBAPP / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WC_WEB_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port)
