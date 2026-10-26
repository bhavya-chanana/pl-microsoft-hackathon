"""openfootball results (public domain / CC0): domestic leagues and the Champions League.

* Domestic leagues: ``openfootball/football.json`` ``{season}/{code}.json``
  (en.1, es.1, it.1, de.1, fr.1).
* Champions League: ``openfootball/champions-league`` ``{season}/cl.txt`` (Football.TXT
  format; the JSON repo has no ``cl.json``).

Raw files: ``raw/openfootball/{season}/{code}.json`` and ``raw/openfootball/{season}/cl.txt``.

Ids are provisional (``openfootball:<slug>``); ``build.py`` resolves them through ids.py.
Scores are the 90-minute result. For knockout ties decided in extra time or on penalties the
extra-time score and shoot-out are kept in ``home_goals_aet`` / ``home_pens`` etc. when the
``Match`` schema has those fields (see ``_EXTRA_FIELDS``).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from matchlens.leaguedata import http
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.schema import Club, League, Match, SourceIds

log = logging.getLogger(__name__)

SOURCE = "openfootball"
JSON_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
CL_BASE = "https://raw.githubusercontent.com/openfootball/champions-league/master"

JSON_CODE = {League.EPL: "en.1", League.LA_LIGA: "es.1", League.SERIE_A: "it.1",
             League.BUNDESLIGA: "de.1", League.LIGUE_1: "fr.1"}
CODE_LEAGUE = {code: league for league, code in JSON_CODE.items()}

# Optional Match fields for knockout ties (set only when the lead's schema has them).
_EXTRA_FIELDS = ("home_goals_aet", "away_goals_aet", "home_pens", "away_pens")
_HAS_EXTRA = all(f in Match.model_fields for f in _EXTRA_FIELDS)

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def slug(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")


def team_id(name: str) -> str:
    return f"{SOURCE}:{slug(name)}"


def match_key(league: League, season: str, date: str, home: str, away: str) -> str:
    return f"{league.value}:{season}:{date}:{slug(home)}-{slug(away)}"


# ------------------------------------------------------------------ fetch


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int:
    """Download one file per (league, season); ``limit`` caps the number of files."""
    raw = cfg.raw_dir(SOURCE)
    new = 0
    done = 0
    for season in seasons:
        for league in leagues:
            if limit is not None and done >= limit:
                return new
            if league == League.UCL:
                url, cache = f"{CL_BASE}/{season}/cl.txt", raw / season / "cl.txt"
            elif league in JSON_CODE:
                code = JSON_CODE[league]
                url, cache = f"{JSON_BASE}/{season}/{code}.json", raw / season / f"{code}.json"
            else:
                continue
            existed = cache.exists() and not refresh
            try:
                http.fetch_bytes(url, cache, cfg, refresh=refresh)
            except http.FetchError as exc:
                log.warning("openfootball %s %s: %s", league, season, exc)
                continue
            done += 1
            new += 0 if existed else 1
    return new


# ------------------------------------------------------------------ parsing


def parse_json_matches(doc: dict[str, Any], league: League, season: str) -> list[Match]:
    rows: list[Match] = []
    for m in doc.get("matches", []):
        ft = (m.get("score") or {}).get("ft")
        if not ft or len(ft) != 2:
            continue  # not played yet
        ht = (m.get("score") or {}).get("ht")
        home, away, date = m["team1"], m["team2"], m["date"]
        rows.append(Match(
            match_key=match_key(league, season, date, home, away), league=league, season=season,
            date=date, home_id=team_id(home), away_id=team_id(away),
            home_goals=int(ft[0]), away_goals=int(ft[1]),
            home_goals_ht=int(ht[0]) if ht and len(ht) == 2 else None,
            away_goals_ht=int(ht[1]) if ht and len(ht) == 2 else None,
        ))
    return rows


_SCORE = re.compile(
    r"^(?P<a>\d+)-(?P<b>\d+)"
    r"(?:\s+pen\.\s+(?P<c>\d+)-(?P<d>\d+))?"
    r"(?P<aet>\s+a\.e\.t\.)?"
    r"(?:\s*\((?P<p>\d+)-(?P<q>\d+)(?:,\s*(?P<r>\d+)-(?P<s>\d+))?\))?\s*$")
_LINE = re.compile(r"^\s*(?:(?P<time>\d{1,2}[:.]\d{2})\s+)?(?P<home>.+?)\s+v\s+(?P<away>.+?)\s+(?P<score>\d+-\d+.*)$")
_DATE = re.compile(r"^\s+(?:[A-Z][a-z]{2}\s+)?(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})(?:\s+(?P<year>\d{4}))?\s*$")
_COUNTRY = re.compile(r"\s*\([A-Z]{3}\)\s*$")


def stage_of(header: str) -> str | None:
    """'Group A' / 'League, Matchday 3' -> 'group'; 'Finals, Round of 16' -> 'round_of_16'."""
    key = re.sub(r"[^a-z0-9]+", "", header.split(",")[-1].lower()) if "," in header else \
        re.sub(r"[^a-z0-9]+", "", header.lower())
    head = re.sub(r"[^a-z0-9]+", "", header.split(",")[0].lower())
    if head.startswith(("group", "gruppe", "league")):
        return "group"
    if head.startswith("playoff"):
        return "playoff"
    for needle, name in (("roundof16", "round_of_16"), ("roundof32", "round_of_32"),
                         ("quarterfinal", "quarter_final"), ("semifinal", "semi_final"),
                         ("final", "final")):
        if key.startswith(needle):
            return name
    return None


def parse_cl_text(text: str, season: str) -> list[Match]:
    """Parse Football.TXT. Scores: ``ft (ht)``; ``aet [pen.] ... (ft, ht)`` for extra time."""
    rows: list[Match] = []
    stage: str | None = None
    year = int(season[:4])
    last_month = 0
    date: str | None = None
    for line in text.splitlines():
        if line.startswith("▪"):
            stage = stage_of(line.lstrip("▪").strip())
            continue
        if not line.strip() or line.lstrip().startswith(("#", "=")):
            continue
        dm = _DATE.match(line)
        if dm and " v " not in line:
            mon = _MONTHS.get(dm["mon"])
            if mon is None:
                continue
            if dm["year"]:
                year = int(dm["year"])
            elif last_month and mon < last_month:
                year += 1
            last_month = mon
            date = f"{year:04d}-{mon:02d}-{int(dm['day']):02d}"
            continue
        lm = _LINE.match(line)
        if not lm or date is None:
            continue
        sm = _SCORE.match(lm["score"].strip())
        if not sm:
            log.debug("openfootball: unparsed score %r", lm["score"])
            continue
        a, b = int(sm["a"]), int(sm["b"])
        pens = aet = None
        if sm["c"] is not None:  # "4-2 pen. 1-0 a.e.t. (ft, ht)"
            pens, aet = (a, b), (int(sm["c"]), int(sm["d"]))
        elif sm["aet"]:
            aet = (a, b)
        if aet is not None:
            ft = (int(sm["p"]), int(sm["q"])) if sm["p"] is not None else aet
            ht = (int(sm["r"]), int(sm["s"])) if sm["r"] is not None else None
        else:
            ft = (a, b)
            ht = (int(sm["p"]), int(sm["q"])) if sm["p"] is not None else None
        home = _COUNTRY.sub("", lm["home"]).strip()
        away = _COUNTRY.sub("", lm["away"]).strip()
        extra: dict[str, Any] = {}
        if _HAS_EXTRA:
            extra = {"home_goals_aet": aet[0] if aet else None, "away_goals_aet": aet[1] if aet else None,
                     "home_pens": pens[0] if pens else None, "away_pens": pens[1] if pens else None}
        rows.append(Match(
            match_key=match_key(League.UCL, season, date, home, away), league=League.UCL, season=season,
            date=date, stage=stage, home_id=team_id(home), away_id=team_id(away),
            home_goals=ft[0], away_goals=ft[1],
            home_goals_ht=ht[0] if ht else None, away_goals_ht=ht[1] if ht else None, **extra))
    return rows


# ------------------------------------------------------------------ normalize


def normalize(cfg: Config) -> dict[str, list]:
    raw = cfg.raw_dir(SOURCE)
    matches: dict[str, Match] = {}
    club_names: dict[str, str] = {}
    if raw.exists():
        for path in sorted(raw.glob("*/*")):
            season = path.parent.name
            rows: list[Match] = []
            if path.name == "cl.txt":
                rows = parse_cl_text(path.read_text(encoding="utf-8"), season)
            elif path.suffix == ".json" and path.stem in CODE_LEAGUE:
                rows = parse_json_matches(json.loads(path.read_text(encoding="utf-8")),
                                          CODE_LEAGUE[path.stem], season)
            for row in rows:
                matches.setdefault(row.match_key, row)
            _collect_names(path, club_names)
    clubs = [Club(team_id=tid, name=name, source_ids=SourceIds(openfootball=tid.split(":", 1)[1]))
             for tid, name in sorted(club_names.items())]
    ordered = sorted(matches.values(), key=lambda m: (m.date, m.match_key))
    return {"matches": ordered, "clubs": clubs}


def _collect_names(path: Path, out: dict[str, str]) -> None:
    """Remember the display name behind each provisional team id."""
    if path.name == "cl.txt":
        for line in path.read_text(encoding="utf-8").splitlines():
            lm = _LINE.match(line)
            if lm and _SCORE.match(lm["score"].strip()):
                for raw_name in (lm["home"], lm["away"]):
                    name = _COUNTRY.sub("", raw_name).strip()
                    out.setdefault(team_id(name), name)
    elif path.suffix == ".json" and path.stem in CODE_LEAGUE:
        for m in json.loads(path.read_text(encoding="utf-8")).get("matches", []):
            for name in (m["team1"], m["team2"]):
                out.setdefault(team_id(name), name)
