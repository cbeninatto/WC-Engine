"""Data layer. Dual-backend: local SQLite by default, Postgres (Supabase) when
DATABASE_URL is set.

The rest of the codebase is written against SQLite's `?` / `:name` placeholder style and
expects timestamps as ISO strings. This module makes the Postgres backend behave
identically: it translates placeholders to psycopg style and coerces datetimes back to
strings, so seed/predict/agents/app code need no changes — only the connection differs.
"""
from __future__ import annotations
import os
import re
import json
import sqlite3
from datetime import datetime, date

import config

_NAMED = re.compile(r":([a-zA-Z_]\w*)")


def _translate(sql: str) -> str:
    """SQLite placeholders -> psycopg: ':name' -> '%(name)s', '?' -> '%s'."""
    return _NAMED.sub(r"%(\1)s", sql).replace("?", "%s")


def _coerce(row: dict) -> dict:
    """Postgres rows: datetimes/dates -> ISO strings, matching the SQLite backend."""
    return {k: (v.isoformat() if isinstance(v, (datetime, date)) else v)
            for k, v in row.items()}


class _Result:
    """Minimal cursor-like wrapper so the Postgres path matches sqlite3.Cursor usage."""
    def __init__(self, rows: list, rowcount: int):
        self._rows = rows
        self.rowcount = rowcount

    def __iter__(self):
        return iter(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class Connection:
    """Unifies sqlite3 and psycopg behind the `.execute(sql, params)` API the app uses."""
    def __init__(self, backend: str, raw):
        self.backend = backend
        self.raw = raw

    def execute(self, sql: str, params=()):
        if self.backend == "postgres":
            cur = self.raw.cursor()
            cur.execute(_translate(sql), params)
            rows = [_coerce(r) for r in cur.fetchall()] if cur.description else []
            res = _Result(rows, cur.rowcount)
            cur.close()
            return res
        return self.raw.execute(sql, params)

    def commit(self):
        self.raw.commit()

    def close(self):
        self.raw.close()


def connect() -> Connection:
    """Postgres when DATABASE_URL is set, else the local SQLite file."""
    url = os.environ.get("DATABASE_URL")
    if url:
        import psycopg
        from psycopg.rows import dict_row
        # prepare_threshold=None disables server-side prepared statements, which the
        # Supabase transaction pooler (PgBouncer) does not support.
        return Connection("postgres",
                          psycopg.connect(url, row_factory=dict_row, prepare_threshold=None))
    raw = sqlite3.connect(config.DB_PATH)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Connection("sqlite", raw)


def init_db(conn: Connection) -> None:
    if conn.backend == "postgres":
        sql = open(config.SCHEMA_PG_PATH, encoding="utf-8").read()
        for stmt in (s.strip() for s in sql.split(";")):
            if stmt:
                conn.raw.execute(stmt)
        conn.raw.commit()
    else:
        with open(config.SCHEMA_PATH, encoding="utf-8") as f:
            conn.raw.executescript(f.read())
        conn.raw.commit()


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
             sos=excluded.sos, notes=excluded.notes, updated_at=CURRENT_TIMESTAMP""",
        {"team_id": team_id, **f},
    )


def upsert_match(conn, m: dict):
    conn.execute(
        """INSERT INTO matches(id,stage,group_code,kickoff,home_id,away_id,home_goals,away_goals,status,source)
           VALUES(:id,:stage,:group_code,:kickoff,:home_id,:away_id,:home_goals,:away_goals,:status,:source)
           ON CONFLICT(id) DO UPDATE SET
             kickoff=excluded.kickoff,
             home_goals=excluded.home_goals, away_goals=excluded.away_goals,
             status=excluded.status, source=excluded.source, updated_at=CURRENT_TIMESTAMP""",
        m,
    )


def record_result(conn, match_id, hg, ag, source):
    conn.execute(
        "UPDATE matches SET home_goals=?, away_goals=?, status='final', source=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (hg, ag, source, match_id),
    )


def upsert_power(conn, team_id, power, prior_power, wc_games, version):
    conn.execute(
        """INSERT INTO power_ratings(team_id,power,prior_power,wc_games,params_version)
           VALUES(?,?,?,?,?)
           ON CONFLICT(team_id) DO UPDATE SET
             power=excluded.power, wc_games=excluded.wc_games,
             params_version=excluded.params_version, computed_at=CURRENT_TIMESTAMP""",
        (team_id, power, prior_power, wc_games, version),
    )


def upsert_prediction(conn, match_id, wh, dr, wa, ph, pa, version):
    conn.execute(
        """INSERT INTO predictions(match_id,win_home,draw,win_away,pred_home_goals,pred_away_goals,params_version)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(match_id) DO UPDATE SET
             win_home=excluded.win_home, draw=excluded.draw, win_away=excluded.win_away,
             pred_home_goals=excluded.pred_home_goals, pred_away_goals=excluded.pred_away_goals,
             params_version=excluded.params_version, computed_at=CURRENT_TIMESTAMP""",
        (match_id, wh, dr, wa, ph, pa, version),
    )


def log_run(conn, agent: str, action: str, payload: dict, status: str = "proposed"):
    conn.execute(
        "INSERT INTO agent_runs(agent,action,payload,status) VALUES(?,?,?,?)",
        (agent, action, json.dumps(payload), status),
    )
