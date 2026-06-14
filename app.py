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

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402  (loads .env)
import lib.db as db  # noqa: E402

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
    today = date.today().isoformat()

    matches = []
    for m in conn.execute("SELECT * FROM matches ORDER BY kickoff, id"):
        h, a = teams.get(m["home_id"]), teams.get(m["away_id"])
        if not h or not a:
            continue
        p = preds.get(m["id"], {})
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
    # Serverless (Vercel): delegate the long web-search loop to GitHub Actions.
    # Local/Actions: run it directly, then recompute inline.
    if os.environ.get("GITHUB_DISPATCH_TOKEN"):
        return JSONResponse(_trigger_workflow())
    res = _run(["agents/results_monitor.py"], timeout=300)
    if res["ok"] and "Logged 0" not in res["output"]:
        from scripts.predict import recompute as _recompute
        _recompute()  # refresh preds off new ratings
    return JSONResponse(res)


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


# --- static ------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(WEBAPP / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WC_WEB_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port)
