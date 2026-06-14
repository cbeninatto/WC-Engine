"""Local SQLite data layer. Stdlib only — no server, no external DB driver."""
from __future__ import annotations
import json
import sqlite3
from typing import Any

import config


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with open(config.SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()


# --- reads -------------------------------------------------------------------

def teams_with_form(conn) -> dict[str, dict]:
    teams = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM teams")}
    for f in conn.execute("SELECT * FROM team_form"):
        if f["team_id"] in teams:
            teams[f["team_id"]].update(dict(f))
    return teams


def matches_to_check(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM matches WHERE status != 'final' ORDER BY kickoff"
    )
    return [dict(r) for r in rows]


def all_matches(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM matches ORDER BY kickoff")]


def final_matches(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM matches WHERE status = 'final' ORDER BY kickoff")
    return [dict(r) for r in rows]


def prior_powers(conn) -> dict[str, float]:
    rows = conn.execute("SELECT team_id, power, prior_power FROM power_ratings")
    return {
        r["team_id"]: float(r["prior_power"] if r["prior_power"] is not None else r["power"])
        for r in rows
    }


# --- writes ------------------------------------------------------------------

def upsert_team(conn, id, name, confederation, group_code):
    conn.execute(
        "INSERT INTO teams(id,name,confederation,group_code) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
        "confederation=excluded.confederation, group_code=excluded.group_code",
        (id, name, confederation, group_code),
    )


def upsert_form(conn, team_id, **f):
    conn.execute(
        """INSERT INTO team_form(team_id,played,wins,draws,losses,gf,ga,pass_acc,pressing,sos,notes)
           VALUES(:team_id,:played,:wins,:draws,:losses,:gf,:ga,:pass_acc,:pressing,:sos,:notes)
           ON CONFLICT(team_id) DO UPDATE SET
             played=excluded.played, wins=excluded.wins, draws=excluded.draws,
             losses=excluded.losses, gf=excluded.gf, ga=excluded.ga,
             pass_acc=excluded.pass_acc, pressing=excluded.pressing,
             sos=excluded.sos, notes=excluded.notes, updated_at=datetime('now')""",
        {"team_id": team_id, **f},
    )


def upsert_match(conn, m: dict):
    conn.execute(
        """INSERT INTO matches(id,stage,group_code,kickoff,home_id,away_id,home_goals,away_goals,status,source)
           VALUES(:id,:stage,:group_code,:kickoff,:home_id,:away_id,:home_goals,:away_goals,:status,:source)
           ON CONFLICT(id) DO UPDATE SET
             kickoff=excluded.kickoff,
             home_goals=excluded.home_goals, away_goals=excluded.away_goals,
             status=excluded.status, source=excluded.source, updated_at=datetime('now')""",
        m,
    )


def record_result(conn, match_id, hg, ag, source):
    conn.execute(
        "UPDATE matches SET home_goals=?, away_goals=?, status='final', source=?, "
        "updated_at=datetime('now') WHERE id=?",
        (hg, ag, source, match_id),
    )


def upsert_power(conn, team_id, power, prior_power, wc_games, version):
    conn.execute(
        """INSERT INTO power_ratings(team_id,power,prior_power,wc_games,params_version)
           VALUES(?,?,?,?,?)
           ON CONFLICT(team_id) DO UPDATE SET
             power=excluded.power, wc_games=excluded.wc_games,
             params_version=excluded.params_version, computed_at=datetime('now')""",
        (team_id, power, prior_power, wc_games, version),
    )


def upsert_prediction(conn, match_id, wh, dr, wa, ph, pa, version):
    conn.execute(
        """INSERT INTO predictions(match_id,win_home,draw,win_away,pred_home_goals,pred_away_goals,params_version)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(match_id) DO UPDATE SET
             win_home=excluded.win_home, draw=excluded.draw, win_away=excluded.win_away,
             pred_home_goals=excluded.pred_home_goals, pred_away_goals=excluded.pred_away_goals,
             params_version=excluded.params_version, computed_at=datetime('now')""",
        (match_id, wh, dr, wa, ph, pa, version),
    )


def log_run(conn, agent: str, action: str, payload: dict, status: str = "proposed"):
    conn.execute(
        "INSERT INTO agent_runs(agent,action,payload,status) VALUES(?,?,?,?)",
        (agent, action, json.dumps(payload), status),
    )
