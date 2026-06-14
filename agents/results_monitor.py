"""Results monitor — the one working runtime agent.

Loop:
  1. Find matches that have kicked off but aren't marked final.
  2. Ask Claude (with the web_search tool) for their latest final scores -> JSON.
  3. For each newly-final match: record the result, fold it into the ratings
     (in-tournament re-rate), log the action, and ping Telegram.

Guardrail: pure result-logging auto-commits (it's just observed fact); anything that
would change model PARAMETERS goes through agent_runs as a 'proposed' row for you to
approve. See CLAUDE.md.

    python agents/results_monitor.py            # one pass
    python agents/results_monitor.py --loop 300 # every 5 min
"""
import sys
import json
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import Anthropic
import config
import lib.db as db
from lib.notify import notify
from engine.rerate import apply_result
from engine.params import DEFAULT_PARAMS

VERSION = 1

PROMPT = """You are a sports-results checker for the 2026 FIFA World Cup.
For each fixture below, search the web and return the FINAL score only if the match
has finished. If it is not finished (scheduled, live, postponed), omit it.

Fixtures (id | home vs away):
{fixtures}

Return ONLY a JSON array, no prose, no markdown fences:
[{{"id": "<id>", "home_goals": <int>, "away_goals": <int>}}]
"""


def fetch_finals(client: Anthropic, pending: list[dict], teams: dict) -> list[dict]:
    lines = [
        f'{m["id"]} | {teams[m["home_id"]]["name"]} vs {teams[m["away_id"]]["name"]}'
        for m in pending
        if m["home_id"] in teams and m["away_id"] in teams
    ]
    if not lines:
        return []
    resp = client.messages.create(
        model=config.AGENT_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT.format(fixtures="\n".join(lines))}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        start, end = text.find("["), text.rfind("]")
        return json.loads(text[start : end + 1]) if start != -1 else []
    except json.JSONDecodeError:
        print("Could not parse model output:\n", text)
        return []


def run_once():
    conn = db.connect()
    db.init_db(conn)  # ensure tables exist
    teams = db.teams_with_form(conn)
    pending = db.matches_to_check(conn)
    if not pending:
        print("No pending matches.")
        return

    client = Anthropic()  # reads ANTHROPIC_API_KEY
    results = fetch_finals(client, pending, teams)
    by_id = {m["id"]: m for m in pending}

    applied = 0
    for r in results:
        m = by_id.get(r["id"])
        if not m:
            continue
        hg, ag = int(r["home_goals"]), int(r["away_goals"])

        # 1) record the observed fact (auto-commit)
        db.record_result(conn, m["id"], hg, ag, source="agent:web_search")

        # 2) in-tournament re-rate of the two teams
        powers = {row["team_id"]: row for row in
                  [dict(x) for x in conn.execute("SELECT * FROM power_ratings")]}
        ph, pa = powers[m["home_id"]]["power"], powers[m["away_id"]]["power"]
        nph, npa = apply_result(ph, pa, hg, ag, DEFAULT_PARAMS)
        db.upsert_power(conn, m["home_id"], round(nph, 1),
                        powers[m["home_id"]]["prior_power"],
                        (powers[m["home_id"]]["wc_games"] or 0) + 1, VERSION)
        db.upsert_power(conn, m["away_id"], round(npa, 1),
                        powers[m["away_id"]]["prior_power"],
                        (powers[m["away_id"]]["wc_games"] or 0) + 1, VERSION)

        # 3) audit + notify
        hn, an = teams[m["home_id"]]["name"], teams[m["away_id"]]["name"]
        db.log_run(conn, "results_monitor", "logged_result",
                   {"match": m["id"], "score": f"{hg}-{ag}",
                    "rerate": {m["home_id"]: round(nph - ph, 2),
                               m["away_id"]: round(npa - pa, 2)}},
                   status="applied")
        notify(f"*{hn} {hg}-{ag} {an}* logged. "
               f"{hn} {nph-ph:+.1f}, {an} {npa-pa:+.1f} power.")
        applied += 1

    conn.commit()
    conn.close()
    print(f"Logged {applied} new result(s) from {len(pending)} pending.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="seconds between passes (0 = run once)")
    args = ap.parse_args()
    if args.loop:
        while True:
            run_once()
            time.sleep(args.loop)
    else:
        run_once()
