"""football-data.co.uk: results, referees, match statistics and closing odds.

    https://www.football-data.co.uk/mmz4281/<yy><yy+1>/<code>.csv      e.g. 2324/E0.csv

(the site redirects ``www`` to the bare domain; the redirect is followed).
One CSV per league-season, saved as ``raw/football_data/<code>_<yyyy>.csv``.

The files are messy: latin-1 with a byte-order mark in some seasons, dates as
``dd/mm/yyyy`` or ``dd/mm/yy``, trailing empty columns and blank rows, and
columns that appear or vanish between seasons. Everything is read defensively.

Closing odds become probabilities by taking 1/odds and normalising so the three
outcomes sum to 1 (removing the bookmaker's margin). Bookmaker order: Pinnacle
closing (PSC*), market average closing (AvgC*), Bet365 closing (B365C*).

Ids are provisional: ``football_data:<team name as spelled in the CSV>``; the
spelling differs from Understat ("Man City" vs "Manchester City"), so
``build.py`` resolves clubs through its alias table.
"""

from __future__ import annotations

import codecs
import csv
import io
import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import FetchError, fetch_bytes
from matchlens.leaguedata.schema import (
    FOOTBALL_DATA_CODE,
    Club,
    League,
    Match,
    SourceIds,
    TeamMatchStats,
)

log = logging.getLogger(__name__)

SOURCE = "football_data"
BASE = "https://www.football-data.co.uk/mmz4281"

_LEAGUE_BY_CODE = {code: league for league, code in FOOTBALL_DATA_CODE.items()}
_COUNTRY = {League.EPL: "England", League.LA_LIGA: "Spain", League.SERIE_A: "Italy",
            League.BUNDESLIGA: "Germany", League.LIGUE_1: "France"}
# (home, draw, away) odds columns, best source first.
ODDS_COLUMNS = (("PSCH", "PSCD", "PSCA"), ("AvgCH", "AvgCD", "AvgCA"), ("B365CH", "B365CD", "B365CA"))


def season_code(season: str) -> str:
    """'2023-24' -> '2324'."""
    return f"{season[2:4]}{season[5:7]}"


def season_label(code: str) -> str:
    """'2324' -> '2023-24'."""
    return f"20{code[:2]}-{code[2:]}"


def raw_path(cfg: Config, code: str, season: str) -> Path:
    return cfg.raw_dir(SOURCE) / f"{code}_{season[:4]}.csv"


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int:
    """Download one CSV per league-season (``limit`` is ignored: files are small)."""
    new = 0
    for league in leagues:
        code = FOOTBALL_DATA_CODE.get(league)
        if code is None:
            continue
        for season in seasons:
            path = raw_path(cfg, code, season)
            existed = path.exists()
            try:
                body = fetch_bytes(f"{BASE}/{season_code(season)}/{code}.csv", path, cfg, refresh=refresh)
            except FetchError as exc:
                log.warning("football-data %s %s: %s", code, season, exc)
                continue
            if b"HomeTeam" not in body[:2000]:  # an HTML error page served with status 200
                path.unlink(missing_ok=True)
                log.warning("football-data %s %s: not a results CSV", code, season)
                continue
            new += int(refresh or not existed)
    return new


# ------------------------------------------------------------------ normalising


def decode(body: bytes) -> str:
    """UTF-8 (with or without BOM) when valid, otherwise latin-1."""
    if body.startswith(codecs.BOM_UTF8):
        body = body[len(codecs.BOM_UTF8):]
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")


def parse_date(value: str) -> str | None:
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _odds(row: dict[str, str], names: tuple[str, str, str]) -> tuple[float, float, float] | None:
    try:
        vals = tuple(float(row[n]) for n in names)
    except (KeyError, TypeError, ValueError):
        return None
    return vals if all(v > 1.0 for v in vals) else None  # type: ignore[return-value]


def implied_probabilities(row: dict[str, str]) -> tuple[float, float, float] | None:
    """De-vigged (home, draw, away) probabilities from the first complete closing-odds triple."""
    for names in ODDS_COLUMNS:
        odds = _odds(row, names)
        if odds is not None:
            inv = [1.0 / o for o in odds]
            total = sum(inv)
            return tuple(round(v / total, 6) for v in inv)  # type: ignore[return-value]
    return None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def read_rows(path: Path) -> list[dict[str, str]]:
    """Parsed CSV rows with blank header cells and blank rows dropped."""
    reader = csv.DictReader(io.StringIO(decode(path.read_bytes())))
    rows = []
    for raw in reader:
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k and k.strip()}
        if row.get("HomeTeam") and row.get("AwayTeam"):
            rows.append(row)
    return rows


def normalize(cfg: Config) -> dict[str, list[Any]]:
    """Rows from the raw CSVs on disk (no network)."""
    matches: list[Match] = []
    stats: list[TeamMatchStats] = []
    clubs: dict[str, Club] = {}
    for path in sorted(cfg.raw_dir(SOURCE).glob("*_*.csv")):
        m = re.fullmatch(r"(\w+)_(\d{4})\.csv", path.name)
        league = _LEAGUE_BY_CODE.get(m.group(1)) if m else None
        if league is None:
            continue
        year = int(m.group(2))
        season = f"{year}-{(year + 1) % 100:02d}"
        for row in read_rows(path):
            iso = parse_date(row.get("Date", ""))
            hg, ag = _int(row.get("FTHG")), _int(row.get("FTAG"))
            if iso is None or hg is None or ag is None:
                continue  # fixture not played yet
            home, away = row["HomeTeam"], row["AwayTeam"]
            hid, aid = f"{SOURCE}:{home}", f"{SOURCE}:{away}"
            key = f"{league.value}:{season}:{iso}:{_slug(home)}-{_slug(away)}"
            for tid, name in ((hid, home), (aid, away)):
                clubs.setdefault(tid, Club(team_id=tid, name=name, country=_COUNTRY.get(league),
                                           source_ids=SourceIds(football_data=name)))
            probs = implied_probabilities(row)
            matches.append(Match(
                match_key=key, league=league, season=season, date=iso,
                home_id=hid, away_id=aid, home_goals=hg, away_goals=ag,
                home_goals_ht=_int(row.get("HTHG")), away_goals_ht=_int(row.get("HTAG")),
                referee=row.get("Referee") or None,
                p_home=probs[0] if probs else None, p_draw=probs[1] if probs else None,
                p_away=probs[2] if probs else None,
                source_ids=SourceIds(football_data=f"{iso}:{home}-{away}")))
            for side, tid, goals in (("H", hid, hg), ("A", aid, ag)):
                stats.append(TeamMatchStats(
                    match_key=key, team_id=tid, is_home=side == "H", goals=goals,
                    shots=_int(row.get(f"{side}S")), shots_on_target=_int(row.get(f"{side}ST")),
                    corners=_int(row.get(f"{side}C")), fouls=_int(row.get(f"{side}F")),
                    yellows=_int(row.get(f"{side}Y")), reds=_int(row.get(f"{side}R"))))
    return {"matches": matches, "team_match_stats": stats, "clubs": list(clubs.values())}

