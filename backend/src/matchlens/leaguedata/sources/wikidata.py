"""Wikidata (CC0): clubs, head coaches, honours and current squads via the SPARQL endpoint.

Queries are small and batched (the endpoint is rate limited, sometimes to one request a
minute: a 429 is waited out and the pace is then kept at 61 s). Raw files in
``raw/wikidata``:

* ``clubs_<LEAGUE>.json``   current members of the league (P118 without end time)
* ``honours.json``          league / UCL season winners (P1346 on P3450 season items)
* ``managers_<hash>.json``  head coach statements (P286 with P580/P582) for a batch of clubs
* ``players_<hash>.json``   current squad members (P54 without end time) for a batch of clubs

Ids are provisional (``wikidata:Q123``); the Wikidata item id is also kept in
``source_ids.wikidata``. English Wikipedia article titles (sitelinks) are kept in the raw
data and exposed by :func:`load_raw` for the Wikipedia adapter.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode

from matchlens.leaguedata import http
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.schema import (
    Club,
    Honour,
    League,
    Manager,
    ManagerSpell,
    Player,
    SourceIds,
)

log = logging.getLogger(__name__)

SOURCE = "wikidata"
ENDPOINT = "https://query.wikidata.org/sparql"
HOST = "query.wikidata.org"

# Verified item ids (2026): league items; Serie A and Bundesliga have no English label in the
# endpoint, so competition names are fixed here.
LEAGUE_ITEM = {League.EPL: "Q9448", League.LA_LIGA: "Q324867", League.SERIE_A: "Q15804",
               League.BUNDESLIGA: "Q82595", League.LIGUE_1: "Q13394"}
UCL_ITEM = "Q18756"
COMPETITION_NAME = {"Q9448": "Premier League", "Q324867": "La Liga", "Q15804": "Serie A",
                    "Q82595": "Bundesliga", "Q13394": "Ligue 1", "Q18756": "UEFA Champions League"}
FOOTBALL_CLUB = "Q476028"
FOOTBALLER = "Q937857"

CLUB_BATCH = 20
PLAYER_BATCH = 8
KEY_PLAYERS_PER_CLUB = 25
SQUAD_SINCE = "2019-01-01"  # squad statements older than this without an end date are stale

_PREFIX = "http://www.wikidata.org/entity/"
_slow_until_spacing = False  # set after the first 429: keep 61 s between queries


def qid(uri: str | None) -> str | None:
    if not uri:
        return None
    return uri.rsplit("/", 1)[-1] if uri.startswith(_PREFIX) else uri


def entity_id(q: str) -> str:
    return f"{SOURCE}:{q}"


# ------------------------------------------------------------------ queries

_LABEL = 'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }'
_ARTICLE = "OPTIONAL { ?article schema:about ?{v} ; schema:isPartOf <https://en.wikipedia.org/> }"


def clubs_query(league_item: str) -> str:
    return f"""SELECT ?club ?clubLabel ?founded ?country ?countryLabel ?stadium ?stadiumLabel ?capacity ?article WHERE {{
  ?club p:P118 ?st . ?st ps:P118 wd:{league_item} . FILTER NOT EXISTS {{ ?st pq:P582 ?end }}
  ?club wdt:P31/wdt:P279* wd:{FOOTBALL_CLUB} .
  OPTIONAL {{ ?club wdt:P571 ?founded }}
  OPTIONAL {{ ?club wdt:P17 ?country }}
  OPTIONAL {{ ?club wdt:P115 ?stadium . OPTIONAL {{ ?stadium wdt:P1083 ?capacity }} }}
  {_ARTICLE.replace('{v}', 'club')}
  {_LABEL}
}}"""


def honours_query() -> str:
    comps = " ".join(f"wd:{c}" for c in COMPETITION_NAME)
    return f"""SELECT ?comp ?season ?start ?end ?winner WHERE {{
  VALUES ?comp {{ {comps} }}
  ?season wdt:P3450 ?comp ; wdt:P1346 ?winner .
  OPTIONAL {{ ?season wdt:P580 ?start }}
  OPTIONAL {{ ?season wdt:P582 ?end }}
}}"""


def managers_query(club_ids: Sequence[str]) -> str:
    values = " ".join(f"wd:{c}" for c in club_ids)
    return f"""SELECT ?club ?mgr ?mgrLabel ?start ?end ?birth ?natLabel ?article WHERE {{
  VALUES ?club {{ {values} }}
  ?club p:P286 ?st . ?st ps:P286 ?mgr .
  OPTIONAL {{ ?st pq:P580 ?start }}
  OPTIONAL {{ ?st pq:P582 ?end }}
  OPTIONAL {{ ?mgr wdt:P569 ?birth }}
  OPTIONAL {{ ?mgr wdt:P27 ?nat }}
  {_ARTICLE.replace('{v}', 'mgr')}
  {_LABEL}
}}"""


def players_query(club_ids: Sequence[str]) -> str:
    values = " ".join(f"wd:{c}" for c in club_ids)
    return f"""SELECT ?club ?p ?pLabel ?birth ?height ?natLabel ?sitelinks ?article WHERE {{
  VALUES ?club {{ {values} }}
  ?p p:P54 ?st . ?st ps:P54 ?club ; pq:P580 ?since .
  FILTER NOT EXISTS {{ ?st pq:P582 ?end }}
  FILTER (?since >= "{SQUAD_SINCE}T00:00:00Z"^^xsd:dateTime)
  FILTER NOT EXISTS {{ ?p wdt:P570 ?died }}
  FILTER NOT EXISTS {{ ?p p:P54 ?later . ?later ps:P54 ?other ; pq:P580 ?laterSince .
                      FILTER (?other != ?club && ?laterSince > ?since) }}
  ?p wdt:P106 wd:{FOOTBALLER} ; wikibase:sitelinks ?sitelinks .
  OPTIONAL {{ ?p wdt:P569 ?birth }}
  OPTIONAL {{ ?p wdt:P2048 ?height }}
  OPTIONAL {{ ?p wdt:P27 ?nat }}
  {_ARTICLE.replace('{v}', 'p')}
  {_LABEL}
}}"""


# ------------------------------------------------------------------ fetch


def _run(cfg: Config, sparql: str, cache: Path, *, refresh: bool) -> dict[str, Any]:
    """One SPARQL query, cached. A 429 is waited out (the endpoint may allow one query a minute)."""
    global _slow_until_spacing
    url = f"{ENDPOINT}?{urlencode({'query': sparql, 'format': 'json'})}"
    headers = {"Accept": "application/sparql-results+json"}
    if cache.exists() and not refresh:
        return json.loads(cache.read_bytes())
    local = cfg
    if _slow_until_spacing:
        local = replace(cfg, host_delay={**cfg.host_delay, HOST: 61.0})
    for attempt in range(4):
        try:
            return http.fetch_json(url, cache, local, refresh=refresh, headers=headers, retries=0)
        except http.FetchError as exc:
            if "429" not in str(exc) or attempt == 3:
                raise
            log.warning("wikidata rate limited; waiting 65 s")
            _slow_until_spacing = True
            local = replace(cfg, host_delay={**cfg.host_delay, HOST: 61.0})
            time.sleep(65)
    raise http.FetchError(url)  # pragma: no cover


def _bindings(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return doc.get("results", {}).get("bindings", [])


def _val(b: dict[str, Any], key: str) -> str | None:
    cell = b.get(key)
    return cell.get("value") if cell else None


def _batch_name(prefix: str, ids: Sequence[str]) -> str:
    return f"{prefix}_{hashlib.sha1(','.join(sorted(ids)).encode()).hexdigest()[:10]}.json"


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int:
    """Clubs per league, then honours, managers and squads for the first ``limit`` clubs."""
    raw = cfg.raw_dir(SOURCE)
    before = set(raw.glob("*.json")) if raw.exists() else set()
    club_ids: list[str] = []
    for league in leagues:
        item = LEAGUE_ITEM.get(league)
        if item is None:
            continue
        doc = _run(cfg, clubs_query(item), raw / f"clubs_{league.value}.json", refresh=refresh)
        for b in _bindings(doc):
            q = qid(_val(b, "club"))
            if q and q not in club_ids:
                club_ids.append(q)
    club_ids.sort()
    if limit is not None:
        club_ids = club_ids[:limit]
    if club_ids:
        _run(cfg, honours_query(), raw / "honours.json", refresh=refresh)
        for i in range(0, len(club_ids), CLUB_BATCH):
            ids = club_ids[i:i + CLUB_BATCH]
            _run(cfg, managers_query(ids), raw / _batch_name("managers", ids), refresh=refresh)
        for i in range(0, len(club_ids), PLAYER_BATCH):
            ids = club_ids[i:i + PLAYER_BATCH]
            _run(cfg, players_query(ids), raw / _batch_name("players", ids), refresh=refresh)
    return len(set(raw.glob("*.json")) - before) if raw.exists() else 0


# ------------------------------------------------------------------ parsing


def _date(value: str | None) -> str | None:
    """'1967-06-16T00:00:00Z' -> '1967-06-16' (None for BCE or malformed values)."""
    if not value or not re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return None
    return value[:10]


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


def article_title(url: str | None) -> str | None:
    if not url or "/wiki/" not in url:
        return None
    return unquote(url.split("/wiki/", 1)[1]).replace("_", " ")


def season_label(start: str | None) -> str | None:
    year = _int(start[:4]) if start else None
    return None if year is None else f"{year}-{(year + 1) % 100:02d}"


@dataclass
class Raw:
    """Parsed raw Wikidata results, merged across files."""

    clubs: dict[str, dict[str, Any]] = field(default_factory=dict)
    managers: dict[str, dict[str, Any]] = field(default_factory=dict)
    spells: dict[tuple[str, str, str], str | None] = field(default_factory=dict)
    players: dict[str, dict[str, Any]] = field(default_factory=dict)
    player_club: dict[str, set[str]] = field(default_factory=dict)  # club qid -> player qids
    honours: set[tuple[str, str, str]] = field(default_factory=set)  # (club qid, competition, season)


def _read_files(raw_dir: Path, pattern: str) -> Iterable[dict[str, Any]]:
    for path in sorted(raw_dir.glob(pattern)):
        yield json.loads(path.read_text(encoding="utf-8"))


def parse_clubs(doc: dict[str, Any], clubs: dict[str, dict[str, Any]]) -> None:
    for b in _bindings(doc):
        q = qid(_val(b, "club"))
        if not q:
            continue
        c = clubs.setdefault(q, {"name": _val(b, "clubLabel") or q, "founded": None, "country": None,
                                 "stadium": None, "capacity": None, "article": None})
        founded = _int((_date(_val(b, "founded")) or "")[:4])
        if founded is not None and (c["founded"] is None or founded < c["founded"]):
            c["founded"] = founded
        c["country"] = c["country"] or _val(b, "countryLabel")
        cap = _int(_val(b, "capacity"))
        stadium = _val(b, "stadiumLabel")
        if stadium and (c["stadium"] is None or (cap or 0) > (c["capacity"] or 0)):
            c["stadium"], c["capacity"] = stadium, cap
        c["article"] = c["article"] or article_title(_val(b, "article"))


def parse_managers(doc: dict[str, Any], raw: Raw) -> None:
    for b in _bindings(doc):
        m, club = qid(_val(b, "mgr")), qid(_val(b, "club"))
        if not m or not club:
            continue
        info = raw.managers.setdefault(m, {"name": _val(b, "mgrLabel") or m, "nationality": None,
                                           "birth": None, "article": None})
        info["nationality"] = info["nationality"] or _val(b, "natLabel")
        info["birth"] = info["birth"] or _date(_val(b, "birth"))
        info["article"] = info["article"] or article_title(_val(b, "article"))
        start, end = _date(_val(b, "start")), _date(_val(b, "end"))
        if start is None or (end is not None and end < start):
            continue  # a spell without a start date cannot be placed in time
        key = (m, club, start)
        if key not in raw.spells or (raw.spells[key] is None and end):
            raw.spells[key] = end


def parse_players(doc: dict[str, Any], raw: Raw) -> None:
    for b in _bindings(doc):
        p, club = qid(_val(b, "p")), qid(_val(b, "club"))
        if not p or not club:
            continue
        height = None
        h = _val(b, "height")
        if h:
            try:
                value = float(h)
                height = round(value * 100) if value < 3 else round(value)
            except ValueError:
                height = None
        info = raw.players.setdefault(p, {"name": _val(b, "pLabel") or p, "birth": None, "height": None,
                                          "nationality": None, "sitelinks": 0, "article": None})
        info["birth"] = info["birth"] or _date(_val(b, "birth"))
        info["height"] = info["height"] or height
        info["nationality"] = info["nationality"] or _val(b, "natLabel")
        info["sitelinks"] = max(info["sitelinks"], _int(_val(b, "sitelinks")) or 0)
        info["article"] = info["article"] or article_title(_val(b, "article"))
        raw.player_club.setdefault(club, set()).add(p)


def parse_honours(doc: dict[str, Any], raw: Raw) -> None:
    for b in _bindings(doc):
        comp, winner = qid(_val(b, "comp")), qid(_val(b, "winner"))
        season = season_label(_val(b, "start"))
        if comp in COMPETITION_NAME and winner and season:
            raw.honours.add((winner, COMPETITION_NAME[comp], season))


def load_raw(cfg: Config) -> Raw:
    raw = Raw()
    d = cfg.raw_dir(SOURCE)
    if not d.exists():
        return raw
    for doc in _read_files(d, "clubs_*.json"):
        parse_clubs(doc, raw.clubs)
    for doc in _read_files(d, "managers_*.json"):
        parse_managers(doc, raw)
    for doc in _read_files(d, "players_*.json"):
        parse_players(doc, raw)
    for doc in _read_files(d, "honours.json"):
        parse_honours(doc, raw)
    return raw


def key_players(raw: Raw, club: str, n: int = KEY_PLAYERS_PER_CLUB) -> list[str]:
    """Most notable (most sitelinks) squad members of a club that have an English article."""
    ids = [p for p in raw.player_club.get(club, ()) if raw.players[p]["article"]]
    return sorted(ids, key=lambda p: (-raw.players[p]["sitelinks"], p))[:n]


# ------------------------------------------------------------------ normalize


def normalize(cfg: Config) -> dict[str, list]:
    raw = load_raw(cfg)
    clubs = [Club(team_id=entity_id(q), name=c["name"], country=c["country"], founded=c["founded"],
                  stadium=c["stadium"], stadium_capacity=c["capacity"], source_ids=SourceIds(wikidata=q))
             for q, c in sorted(raw.clubs.items())]
    managers = [Manager(manager_id=entity_id(q), name=m["name"], nationality=m["nationality"],
                        birth_date=m["birth"], source_ids=SourceIds(wikidata=q))
                for q, m in sorted(raw.managers.items())]
    spells = [ManagerSpell(manager_id=entity_id(m), team_id=entity_id(c), start=s, end=e, source="wikidata")
              for (m, c, s), e in sorted(raw.spells.items())]
    # Honours are kept for clubs we know about (the league members that were fetched).
    honours = [Honour(team_id=entity_id(c), competition=comp, season=season, source="wikidata")
               for c, comp, season in sorted(raw.honours) if c in raw.clubs]
    players = [Player(player_id=entity_id(q), name=p["name"], birth_date=p["birth"],
                      nationality=p["nationality"], height_cm=p["height"], source_ids=SourceIds(wikidata=q))
               for q, p in sorted(raw.players.items())]
    return {"clubs": clubs, "managers": managers, "manager_spells": spells, "honours": honours,
            "players": players}


def article_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()_,.'-")
