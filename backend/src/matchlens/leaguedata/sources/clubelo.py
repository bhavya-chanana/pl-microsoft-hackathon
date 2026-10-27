"""ClubElo: club strength ratings.

    http://api.clubelo.com/<YYYY-MM-DD>     CSV: Rank,Club,Country,Level,Elo,From,To

One snapshot per Monday of each season window (1 August to 30 June, never in
the future), saved as ``raw/clubelo/<date>.csv``. ClubElo names clubs without
spaces ("ManCity"); ids are provisional ``clubelo:<ClubElo name>``.

``normalize(cfg)`` returns Club rows only (name and country code).

For ``build.py`` to fill ``Match.home_elo`` / ``away_elo`` this module exposes

    elo_lookup(cfg) -> EloTable        a dict[(clubelo club name, snapshot date), float]
    elo_at(table, club_name, date)     Elo at the latest snapshot on or before ``date``
                                       (None when the club has no snapshot that early)

Matches are rarely played on a Monday, hence ``elo_at``.
"""

from __future__ import annotations

import csv
import io
import logging
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import FetchError, fetch_bytes
from matchlens.leaguedata.schema import Club, League, SourceIds

log = logging.getLogger(__name__)

SOURCE = "clubelo"
BASE = "http://api.clubelo.com"


def season_mondays(season: str, today: date | None = None) -> list[date]:
    """Every Monday from 1 August of the season's first year to 30 June of the next."""
    year = int(season[:4])
    day, end = date(year, 8, 1), min(date(year + 1, 6, 30), today or date.today())
    day += timedelta(days=(7 - day.weekday()) % 7)  # first Monday on or after 1 August
    out = []
    while day <= end:
        out.append(day)
        day += timedelta(days=7)
    return out


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int:
    """Download the Monday snapshots of ``seasons`` (ClubElo is global: ``leagues`` is unused).

    ``limit`` caps the snapshots fetched per season.
    """
    new = failures = 0
    for season in seasons:
        for day in season_mondays(season)[:limit] if limit else season_mondays(season):
            path = cfg.raw_dir(SOURCE) / f"{day.isoformat()}.csv"
            existed = path.exists()
            try:
                body = fetch_bytes(f"{BASE}/{day.isoformat()}", path, cfg, refresh=refresh)
            except FetchError as exc:
                log.warning("clubelo %s: %s", day, exc)
                failures += 1
                if failures >= 2:  # the API is down (it often answers 502): stop instead of retrying 300 times
                    raise FetchError(f"clubelo unavailable after {failures} consecutive failures") from exc
                continue
            failures = 0
            if not body.startswith(b"Rank"):
                path.unlink(missing_ok=True)
                log.warning("clubelo %s: unexpected body", day)
                continue
            new += int(refresh or not existed)
    return new


def _snapshots(cfg: Config):
    """Yield (snapshot date as ISO text, csv rows) for every file on disk."""
    for path in sorted(cfg.raw_dir(SOURCE).glob("????-??-??.csv")):
        text = path.read_text(encoding="utf-8", errors="replace").lstrip("﻿")
        yield path.stem, list(csv.DictReader(io.StringIO(text)))


def normalize(cfg: Config) -> dict[str, list[Any]]:
    """Club rows (name, country code) for every club seen in any snapshot."""
    clubs: dict[str, Club] = {}
    for _, rows in _snapshots(cfg):
        for row in rows:
            name = (row.get("Club") or "").strip()
            if name and name not in clubs:
                tid = f"{SOURCE}:{name}"
                clubs[name] = Club(team_id=tid, name=name, country=(row.get("Country") or "").strip() or None,
                                   source_ids=SourceIds())
    return {"clubs": list(clubs.values())}


class EloTable(dict[tuple[str, str], float]):
    """``(club, snapshot date) -> Elo`` with a nearest-earlier-snapshot lookup."""

    _index: dict[str, tuple[list[str], list[float]]] | None = None

    def at(self, club: str, day: str) -> float | None:
        if self._index is None:
            grouped: dict[str, list[tuple[str, float]]] = {}
            for (name, d), elo in self.items():
                grouped.setdefault(name, []).append((d, elo))
            self._index = {n: ([d for d, _ in sorted(v)], [e for _, e in sorted(v)])
                           for n, v in grouped.items()}
        dates, elos = self._index.get(club, ([], []))
        i = bisect_right(dates, day)
        return elos[i - 1] if i else None


def elo_lookup(cfg: Config) -> EloTable:
    """Mapping (ClubElo club name, snapshot date 'YYYY-MM-DD') -> Elo."""
    table = EloTable()
    for stamp, rows in _snapshots(cfg):
        for row in rows:
            try:
                table[(row["Club"].strip(), stamp)] = float(row["Elo"])
            except (KeyError, TypeError, ValueError):
                continue
    return table


def elo_at(table: EloTable, club: str, day: str) -> float | None:
    return table.at(club, day)
