"""Fantasy Premier League (EPL only): player identities, squads and positions.

Sources
* Live current season: ``https://fantasy.premierleague.com/api/bootstrap-static/``
  (public, unauthenticated JSON used by the official site; keep to one request
  per run, see ``Config.host_delay``).
* History: the community archive https://github.com/vaastav/Fantasy-Premier-League
  (``data/<season>/players_raw.csv`` and ``data/<season>/teams.csv`` on
  raw.githubusercontent.com, 2016-17 onwards; MIT licence for the archive).

Licence: FPL data belongs to the Premier League and is exposed for the game
itself; there is no open licence. Treat it as local research data, not for the
redistributable ``share/`` subset.

We keep only identity facts (name, birth date when present, position, club per
season). FPL's own ICT index, prices, ownership, expected stats and bonus
points have no table in ``schema.TABLES`` and are not emitted; see the A2 report
for the table that would hold them.

FPL ``code`` is stable across seasons (the per-season ``id`` is not), so ids are
``fpl:<code>`` for players and ``fpl:<team code>`` for clubs.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import FetchError, fetch_bytes
from matchlens.leaguedata.schema import Club, League, Player, SourceIds

SOURCE = "fpl"
LIVE_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
HISTORY_URL = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}/{name}"
HISTORY_FILES = ("players_raw.csv", "teams.csv")
POSITIONS = {"1": "Goalkeeper", "2": "Defender", "3": "Midfield", "4": "Attack"}  # Transfermarkt vocabulary


@dataclass(frozen=True)
class PlayerContext:
    """Which club a player was registered with in a season (Player rows carry no club)."""

    player_id: str
    name: str
    web_name: str
    birth_date: str | None
    season: str
    team_id: str  # provisional club id "fpl:<team code>"
    team_name: str


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int:
    """Live bootstrap-static plus vaastav history for ``seasons``; EPL only."""
    if League.EPL not in leagues:
        return 0
    raw = cfg.raw_dir(SOURCE)
    new = 0
    live = raw / "bootstrap-static.json"
    new += refresh or not live.exists()
    fetch_bytes(LIVE_URL, live, cfg, refresh=refresh)
    for season in seasons:
        for name in HISTORY_FILES:
            cache = raw / season / name
            existed = cache.exists() and not refresh
            try:
                fetch_bytes(HISTORY_URL.format(season=season, name=name), cache, cfg, refresh=refresh)
            except FetchError:  # season not archived yet (the live file covers the current one)
                break
            new += not existed
    return new


def live_season(bootstrap: dict[str, Any]) -> str:
    """"2026-27" from the first gameweek deadline of a bootstrap-static payload."""
    deadline = bootstrap["events"][0]["deadline_time"]
    year = int(deadline[:4])
    if int(deadline[5:7]) < 7:  # a January/February "first" deadline would belong to the season that began last year
        year -= 1
    return f"{year}-{(year + 1) % 100:02d}"


def _rows(cfg: Config, season: str, name: str) -> list[dict[str, str]]:
    path = cfg.raw_dir(SOURCE) / season / name
    if not path.exists():
        return []
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))


def _clean(v: Any) -> str | None:
    s = "" if v is None else str(v).strip()
    return None if s in ("", "None", "nan") else s


def _birth(v: Any) -> str | None:
    s = _clean(v)
    return s[:10] if s and re.match(r"\d{4}-\d{2}-\d{2}", s) else None


def _season_data(cfg: Config) -> list[tuple[str, list[dict[str, Any]], dict[str, dict[str, Any]]]]:
    """[(season, elements, {team id: team})] oldest first; the live payload wins for its season."""
    out: dict[str, tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]] = {}
    for season in sorted(p.name for p in cfg.raw_dir(SOURCE).glob("20??-??")):
        teams = {t["id"]: t for t in _rows(cfg, season, "teams.csv")}
        elements = _rows(cfg, season, "players_raw.csv")
        if elements:
            out[season] = (elements, teams)
    live = cfg.raw_dir(SOURCE) / "bootstrap-static.json"
    if live.exists():
        boot = json.loads(live.read_text(encoding="utf-8"))
        out[live_season(boot)] = (boot["elements"], {str(t["id"]): t for t in boot["teams"]})
    return [(s, *out[s]) for s in sorted(out)]


def player_contexts(cfg: Config) -> list[PlayerContext]:
    """(player, season, club) facts for ``ids.PlayerResolver``."""
    ctx: list[PlayerContext] = []
    for season, elements, teams in _season_data(cfg):
        for e in elements:
            team = teams.get(str(e.get("team")))
            code = _clean(e.get("code"))
            if not team or not code:
                continue
            ctx.append(PlayerContext(
                player_id=f"{SOURCE}:{code}", name=_full_name(e), web_name=_clean(e.get("web_name")) or "",
                birth_date=_birth(e.get("birth_date")), season=season,
                team_id=f"{SOURCE}:{team['code']}", team_name=str(team["name"])))
    return ctx


def _full_name(e: dict[str, Any]) -> str:
    return " ".join(p for p in (_clean(e.get("first_name")), _clean(e.get("second_name"))) if p)


def normalize(cfg: Config, *, seasons: Sequence[str] | None = None) -> dict[str, list[Any]]:
    """Player rows (one per FPL code, newest facts win) and Club rows (one per team code)."""
    wanted = set(seasons) if seasons else None
    players: dict[str, Player] = {}
    clubs: dict[str, Club] = {}
    for season, elements, teams in _season_data(cfg):
        if wanted is not None and season not in wanted:
            continue
        for t in teams.values():
            code = str(t["code"])
            clubs[code] = Club(team_id=f"{SOURCE}:{code}", name=str(t["name"]), short_name=_clean(t.get("short_name")),
                               country="England", source_ids=SourceIds(fpl=code))
        for e in elements:
            code = _clean(e.get("code"))
            name = _full_name(e)
            if not code or not name:
                continue
            old = players.get(code)
            players[code] = Player(
                player_id=f"{SOURCE}:{code}", name=name,
                birth_date=_birth(e.get("birth_date")) or (old.birth_date if old else None),
                main_position=POSITIONS.get(str(e.get("element_type"))),
                source_ids=SourceIds(fpl=code))
    return {"players": list(players.values()), "clubs": list(clubs.values())}
