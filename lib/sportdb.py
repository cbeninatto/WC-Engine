"""SportDB.dev REST client — fetch football fixtures + results.

Real-data source for agents/ingest.py (REST, not MCP: the ingest job is a headless,
deterministic ETL that needs no LLM in the loop — see the design note in agents/ingest.py).

Auth: set SPORTDB_API_KEY in .env. Sent as the `X-API-Key` header.
Free tier: ~1000 requests. Docs/keys: https://sportdb.dev/

Documented endpoints (base https://api.sportdb.dev):
  GET /api/football/live
  GET /api/{sport}/{country}/{competition}/{season}/fixtures
  GET /api/{sport}/{country}/{competition}/{season}/standings
  GET /api/match/{match_id}

The exact World Cup 2026 {country}/{competition}/{season} slugs and the fixture JSON field
paths are discovered against the live API before ingest commits anything (we don't hardcode
a guessed shape).
"""
from __future__ import annotations

import os
import time
import httpx

BASE = os.environ.get("SPORTDB_BASE_URL", "https://api.sportdb.dev")

# Senior men's FIFA World Cup on Flashscore is the "World Championship" competition under the
# World region (every-4-years seasons: 2026, 2022, 2018 …). IDs are opaque + stable.
WORLD_CUP_PATH = "football/world:8/world-championship:lvUBR5F8"
FINISHED_STAGE = "FINISHED"


def _headers() -> dict:
    key = os.environ.get("SPORTDB_API_KEY")
    if not key:
        raise SystemExit(
            "No SportDB key found. Add to .env:\n"
            "  SPORTDB_API_KEY=...\n"
            "Get one at https://sportdb.dev/")
    return {"X-API-Key": key, "Accept": "application/json"}


def get(path: str, timeout: int = 30, **params) -> dict | list:
    """GET an arbitrary SportDB path (leading slash optional). Returns parsed JSON.

    Raises for HTTP errors so a bad key / wrong slug / exhausted quota stops loudly
    instead of silently recording nothing.
    """
    url = f"{BASE}/{path.lstrip('/')}"
    r = httpx.get(url, headers=_headers(), params=params or None, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fixtures(country: str, competition: str, season: str | int,
             sport: str = "football") -> dict | list:
    """GET /api/{sport}/{country}/{competition}/{season}/fixtures."""
    return get(f"/api/{sport}/{country}/{competition}/{season}/fixtures")


def standings(country: str, competition: str, season: str | int,
              sport: str = "football") -> dict | list:
    return get(f"/api/{sport}/{country}/{competition}/{season}/standings")


def live(sport: str = "football") -> dict | list:
    return get(f"/api/{sport}/live")


def match(match_id: str | int) -> dict | list:
    return get(f"/api/match/{match_id}")


def _to_int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _ft_goals(m: dict) -> tuple[int | None, int | None]:
    """90-minute (regulation) goals as (home, away), or (None, None) if unscored.

    A knockout decided in extra time / penalties collapses to its 90-minute score, so a
    shootout reads as a DRAW — correct for a model that predicts 90-minute W/D/L. The
    `homeScore`/`awayScore` headline is a penalties-inclusive composite (e.g. the 2022 final
    shows 4-3, not the 2-2 it was at full time), so it's used only as a fallback when the
    full-time fields are absent AND no ET/pens occurred (`__REA__`/`__RPA__` empty).
    """
    hg, ag = _to_int(m.get("homeFullTimeScore")), _to_int(m.get("awayFullTimeScore"))
    if hg is None or ag is None:
        went_long = bool(m.get("__REA__")) or bool(m.get("__RPA__"))
        if not went_long:
            hg, ag = _to_int(m.get("homeScore")), _to_int(m.get("awayScore"))
    return hg, ag


def tournament_matches(season: int, start: str | None = None, end: str | None = None,
                       competition_path: str = WORLD_CUP_PATH,
                       max_pages: int = 20, pace: float = 0.4) -> list[dict]:
    """All FINISHED matches in the competition feed for `season`, normalized.

    Returns [{home, away, home_goals, away_goals, date, round, event_id}] with goals as the
    90-minute (regulation) result (see `_ft_goals`). Optional [start, end] (YYYY-MM-DD,
    inclusive) filter by date — used to slice the finals window out of a feed that also
    carries that cycle's qualifiers. Pages until the feed is exhausted; `pace` keeps the
    multi-page pull under the free tier's 3 RPS.
    """
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(pace)
        rows = get(f"/api/flashscore/{competition_path}/{season}/results", page=page)
        if not isinstance(rows, list) or not rows:
            break
        for m in rows:
            if m.get("eventStage") != FINISHED_STAGE:
                continue
            date = (m.get("startDateTimeUtc") or "")[:10]
            if (start and date < start) or (end and date > end):
                continue
            hg, ag = _ft_goals(m)
            if hg is None or ag is None:
                continue
            out.append({
                "home": m.get("homeName", ""), "away": m.get("awayName", ""),
                "home_goals": hg, "away_goals": ag, "date": date,
                "round": m.get("round", ""), "event_id": m.get("eventId"),
            })
    return out


def world_cup_results(season: int = 2026, min_date: str | None = None,
                      competition_path: str = WORLD_CUP_PATH,
                      max_pages: int = 1, pace: float = 0.4) -> list[dict]:
    """Finished senior-World-Cup matches for `season`, normalized for ingest.

    Returns [{home, away, home_goals, away_goals, date, event_id, round}], keeping only
    FINISHED games dated >= `min_date` (default `{season}-06-01`, the finals window). That
    date floor matters: Flashscore files World Cup *qualifiers* under the same season feed
    (it spans the prior autumn), so without it a qualifier could collide with a finals
    fixture by team-pair. Scores arrive as strings and are coerced to int.

    Results are sorted most-recent-first, so the finals (newest) sit on page 1; one page is
    enough in practice. `pace` keeps multi-page pulls under the free tier's 3 RPS.
    """
    if min_date is None:
        min_date = f"{season}-06-01"
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(pace)
        rows = get(f"/api/flashscore/{competition_path}/{season}/results", page=page)
        if not isinstance(rows, list) or not rows:
            break
        for m in rows:
            if m.get("eventStage") != FINISHED_STAGE:
                continue
            date = (m.get("startDateTimeUtc") or "")[:10]
            if date < min_date:
                continue
            hg, ag = _to_int(m.get("homeScore")), _to_int(m.get("awayScore"))
            if hg is None or ag is None:
                continue
            out.append({
                "home": m.get("homeName", ""), "away": m.get("awayName", ""),
                "home_goals": hg, "away_goals": ag, "date": date,
                "event_id": m.get("eventId"), "round": m.get("round", ""),
            })
    return out
