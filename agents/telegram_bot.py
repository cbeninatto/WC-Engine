"""Telegram inbound control bot — drive the engine from your phone.

The missing half of lib/notify.py: that file only *pushes* to Telegram; this one *listens*.
Long-polls getUpdates and dispatches commands. Locked to TELEGRAM_CHAT_ID — it ignores
everyone else, so only you can run anything.

    python agents/telegram_bot.py        # runs until Ctrl-C

Needs an always-on host (home server / small VPS). A scheduled GitHub Action can't hold a
long-poll open — use the results-monitor workflow for autonomous cadence, this for control.

Commands:
    /help                 list commands
    /status               counts: finals / pending / overdue / proposals
    /results              run the results monitor (fetch finals), then recompute
    /predict              recompute ratings + predictions
    /top                  top 10 power ratings
    /standings            all 12 group tables
    /pending              matches that have kicked off but aren't final
    /proposals            agent actions awaiting approval
    /approve <id>         approve a proposed agent_runs row
    /reject  <id>         reject a proposed agent_runs row
"""
from __future__ import annotations

import os
import sys
import subprocess
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402  (loads .env)
import lib.db as db  # noqa: E402

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API = f"https://api.telegram.org/bot{TOKEN}"


# --- telegram io -------------------------------------------------------------

def send(chat_id, text: str) -> None:
    try:
        httpx.post(f"{API}/sendMessage",
                   json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                   timeout=20)
    except Exception as e:
        print(f"[send failed: {e}]")


def get_updates(offset: int | None) -> list[dict]:
    r = httpx.get(f"{API}/getUpdates",
                  params={"offset": offset, "timeout": 30}, timeout=40)
    return r.json().get("result", [])


# --- engine actions ----------------------------------------------------------

def _run(args: list[str], timeout: int) -> str:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run([sys.executable, *args], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    return ((p.stdout or "") + (("\n" + p.stderr) if p.returncode else "")).strip()


def cmd_status() -> str:
    conn = db.connect()
    today = date.today().isoformat()
    finals = conn.execute("SELECT count(*) FROM matches WHERE status='final'").fetchone()[0]
    pending = conn.execute("SELECT count(*) FROM matches WHERE status!='final'").fetchone()[0]
    overdue = conn.execute("SELECT count(*) FROM matches WHERE status!='final' "
                           "AND date(kickoff)<=date(?)", (today,)).fetchone()[0]
    props = conn.execute("SELECT count(*) FROM agent_runs WHERE status='proposed'").fetchone()[0]
    conn.close()
    return (f"*WC Engine* — {today}\n"
            f"Results in: *{finals}*\nPending: *{pending}*\n"
            f"Overdue: *{overdue}*\nProposals: *{props}*")


def cmd_top() -> str:
    conn = db.connect()
    rows = conn.execute(
        "SELECT t.name, p.power, p.prior_power FROM power_ratings p "
        "JOIN teams t ON t.id=p.team_id ORDER BY p.power DESC LIMIT 10").fetchall()
    conn.close()
    lines = [f"{i+1:2d}. {r['name']:<16} {r['power']:5.1f}  (prior {r['prior_power']:.1f})"
             for i, r in enumerate(rows)]
    return "*Top 10 power ratings*\n```\n" + "\n".join(lines) + "\n```"


def cmd_standings() -> str:
    conn = db.connect()
    teams = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM teams")}
    tbl = {tid: {"name": t["name"], "g": t["group_code"], "P": 0, "W": 0, "D": 0,
                 "L": 0, "GF": 0, "GA": 0, "Pts": 0} for tid, t in teams.items()}
    for m in conn.execute("SELECT * FROM matches WHERE status='final'"):
        if m["home_id"] not in tbl or m["away_id"] not in tbl or m["home_goals"] is None:
            continue
        for tid, gf, ga in ((m["home_id"], m["home_goals"], m["away_goals"]),
                            (m["away_id"], m["away_goals"], m["home_goals"])):
            row = tbl[tid]
            row["P"] += 1; row["GF"] += gf; row["GA"] += ga
            res = "W" if gf > ga else ("D" if gf == ga else "L")
            row[res] += 1; row["Pts"] += {"W": 3, "D": 1, "L": 0}[res]
    conn.close()
    groups: dict[str, list] = {}
    for row in tbl.values():
        groups.setdefault(row["g"], []).append(row)
    out = []
    for g in sorted(k for k in groups if k):
        rows = sorted(groups[g], key=lambda r: (-r["Pts"], -(r["GF"] - r["GA"]), -r["GF"]))
        body = "\n".join(f"  {r['name'][:14]:<14} {r['P']} {r['Pts']:>2}pt "
                         f"{r['GF']-r['GA']:+d}" for r in rows)
        out.append(f"*Group {g}*\n```\n{body}\n```")
    return "\n".join(out)


def cmd_pending() -> str:
    conn = db.connect()
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT m.id, m.kickoff, m.group_code, th.name h, ta.name a, "
        "(date(m.kickoff)<=date(?)) overdue FROM matches m "
        "JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id "
        "WHERE m.status!='final' ORDER BY m.kickoff LIMIT 15", (today,)).fetchall()
    conn.close()
    if not rows:
        return "No pending matches."
    lines = [f"{'⏳' if r['overdue'] else '•'} {r['kickoff']} {r['h']} vs {r['a']} ({r['group_code']})"
             for r in rows]
    return "*Next up* (⏳ = overdue)\n" + "\n".join(lines)


def cmd_proposals() -> str:
    conn = db.connect()
    rows = conn.execute("SELECT id, agent, action, payload FROM agent_runs "
                        "WHERE status='proposed' ORDER BY created_at DESC").fetchall()
    conn.close()
    if not rows:
        return "No proposals awaiting approval. ✅"
    lines = [f"`{r['id']}` {r['agent']} · {r['action']}\n   {(r['payload'] or '')[:120]}"
             for r in rows]
    return "*Awaiting approval* (/approve <id> · /reject <id>)\n" + "\n".join(lines)


def cmd_decide(arg: str, approve: bool) -> str:
    if not arg.strip().isdigit():
        return "Usage: /approve <id>  (numeric id from /proposals)"
    run_id = int(arg.strip())
    status = "applied" if approve else "rejected"
    conn = db.connect()
    cur = conn.execute("UPDATE agent_runs SET status=? WHERE id=? AND status='proposed'",
                       (status, run_id))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return f"Proposal `{run_id}` → *{status}*." if n else f"No pending proposal `{run_id}`."


HELP = ("*WC Engine bot*\n"
        "/status — counts\n/results — fetch new finals + recompute\n"
        "/predict — recompute\n/top — top 10 ratings\n/standings — group tables\n"
        "/pending — upcoming/overdue\n/proposals — awaiting approval\n"
        "/approve <id> · /reject <id>")


def handle(chat_id, text: str) -> None:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]   # strip @botname in groups
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/help", "/start"):
        send(chat_id, HELP)
    elif cmd == "/status":
        send(chat_id, cmd_status())
    elif cmd == "/results":
        send(chat_id, "Fetching results…")
        out = _run(["agents/results_monitor.py"], timeout=300)
        last = out.splitlines()[-1] if out else "(no output)"
        if "Logged" in out and "Logged 0" not in last:
            _run(["scripts/predict.py"], timeout=120)
        send(chat_id, f"`{last}`\n\n" + cmd_status())
    elif cmd == "/predict":
        out = _run(["scripts/predict.py"], timeout=120)
        send(chat_id, "Recomputed.\n```\n" + "\n".join(out.splitlines()[:6]) + "\n```")
    elif cmd == "/top":
        send(chat_id, cmd_top())
    elif cmd == "/standings":
        send(chat_id, cmd_standings())
    elif cmd == "/pending":
        send(chat_id, cmd_pending())
    elif cmd == "/proposals":
        send(chat_id, cmd_proposals())
    elif cmd == "/approve":
        send(chat_id, cmd_decide(arg, approve=True))
    elif cmd == "/reject":
        send(chat_id, cmd_decide(arg, approve=False))
    else:
        send(chat_id, "Unknown command. /help")


def main() -> None:
    if not TOKEN or not CHAT_ID:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (.env) first.")
    allowed = str(CHAT_ID)
    db.init_db(db.connect())  # ensure tables exist

    # Skip any backlog so a restart doesn't replay old commands.
    backlog = get_updates(None)
    offset = (backlog[-1]["update_id"] + 1) if backlog else None

    print(f"Bot online, locked to chat {allowed}. Polling…")
    send(allowed, "🤖 WC Engine bot online. /help")

    while True:
        try:
            for u in get_updates(offset):
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                chat_id = msg["chat"]["id"]
                if str(chat_id) != allowed:          # guardrail: only the owner
                    print(f"ignored message from {chat_id}")
                    continue
                print(f"> {msg['text']}")
                handle(chat_id, msg["text"])
        except KeyboardInterrupt:
            print("\nbye"); break
        except Exception as e:                        # never die on a transient error
            print(f"[loop error: {e}]")


if __name__ == "__main__":
    main()
