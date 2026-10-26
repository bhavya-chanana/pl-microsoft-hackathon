"""Identity resolution: one canonical id per club, player and manager across sources.

Pure Python (standard library). The resolvers are incremental: feed the
anchor source first (Transfermarkt) and every later ``resolve`` call either
joins an existing identity or creates a new one. Every decision is recorded
as a ``schema.IdLink`` with a confidence and the method that produced it.

Canonical ids
* Club: ``slug(Transfermarkt name)`` when Transfermarkt knows the club, else
  the slug of the first name seen (``Config``/overrides can pin any of them).
* Player / manager: ``person_slug(name)``; a clash is disambiguated with the
  birth year, then a counter.
* Match: ``match_key(league, season, date, home_id, away_id)``.

Overrides (``Config.overrides_path``, JSON, hand-maintained)::

    {"club":    {"understat:83": "manchester-city", "name:Man City": "manchester-city"},
     "player":  {"fpl:154561": "david-raya", "name:Raya": "david-raya"},
     "manager": {"name:Pep": "pep-guardiola"}}

A key is ``"<source>:<source_id>"`` or ``"name:<any spelling>"`` (matched on
the slug, for every source). Overrides always win and produce confidence 1.0.

Confidence scale: exact id / override 1.0, name + club + season 0.95, name +
birth date 0.9, fuzzy (difflib ratio >= 0.9 or a unique name prefix) 0.7, a
record that matched nothing and became its own identity 0.5.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.schema import Club, IdLink, SourceName

# Club-name decorations that differ between providers ("Burnley FC" / "Burnley").
AFFIXES = frozenset({
    "fc", "afc", "cf", "ac", "ssc", "sc", "ss", "us", "as", "cd", "ud", "sd", "rcd", "rc", "fk", "sk",
    "ssd", "asd", "acf", "ogc", "club", "football", "calcio", "futbol", "fussball",
})
_SPECIAL = str.maketrans({"ø": "o", "ß": "ss", "ł": "l", "æ": "ae", "œ": "oe", "đ": "d", "ı": "i", "ð": "d",
                          "þ": "th", "Ø": "o", "Ł": "l", "Æ": "ae", "Œ": "oe", "Đ": "d", "’": "", "'": ""})
FUZZY_RATIO = 0.9

CONF_ID, CONF_NAME_CLUB, CONF_NAME_BIRTH, CONF_FUZZY, CONF_NEW = 1.0, 0.95, 0.9, 0.7, 0.5


def _tokens(name: str) -> list[str]:
    text = unicodedata.normalize("NFKD", name.translate(_SPECIAL))
    text = text.encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return re.findall(r"[a-z0-9]+", text)


def slug(name: str, *, strip_affixes: bool = True) -> str:
    """ASCII, lower case, hyphen separated; club affixes (fc, afc, cf, ac, ssc ...) dropped."""
    tokens = _tokens(name)
    if strip_affixes:
        kept = [t for t in tokens if t not in AFFIXES]
        tokens = kept or tokens  # never reduce a name to nothing
    return "-".join(tokens)


def person_slug(name: str) -> str:
    """``slug`` for people: no affix stripping."""
    return slug(name, strip_affixes=False)


def match_key(league: Any, season: str, date: str, home_id: str, away_id: str) -> str:
    """``"{league}:{season}:{date}:{home_slug}-{away_slug}"`` (ids are re-slugged: idempotent)."""
    league_s = getattr(league, "value", league)
    return f"{league_s}:{season}:{date}:{slug(home_id)}-{slug(away_id)}"


def load_overrides(cfg: Config | None) -> dict[str, dict[str, str]]:
    if cfg is None:
        return {}
    path: Path = cfg.overrides_path
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: dict(v) for k, v in data.items() if isinstance(v, dict)}


def _strip_prefix(source: str, source_id: str | None) -> str | None:
    if source_id is None:
        return None
    sid = str(source_id)
    return sid[len(source) + 1:] if sid.startswith(f"{source}:") else sid


class _Overrides:
    def __init__(self, section: dict[str, str] | None):
        self.by_id: dict[str, str] = {}
        self.by_name: dict[str, str] = {}
        for key, value in (section or {}).items():
            if key.startswith("name:"):
                self.by_name[slug(key[5:])] = value
                self.by_name[person_slug(key[5:])] = value
            else:
                self.by_id[key] = value

    def lookup(self, source: str, source_id: str | None, names: Iterable[str]) -> str | None:
        if source_id is not None and f"{source}:{source_id}" in self.by_id:
            return self.by_id[f"{source}:{source_id}"]
        for n in names:
            for s in (slug(n), person_slug(n)):
                if s and s in self.by_name:
                    return self.by_name[s]
        return None


# --------------------------------------------------------------------- clubs

# Spellings other providers use -> the name Transfermarkt uses (only applied when that club is known).
BUILTIN_CLUB_ALIASES: dict[str, str] = {
    "Man City": "Manchester City", "Man United": "Manchester United", "Man Utd": "Manchester United",
    "Spurs": "Tottenham Hotspur", "Tottenham": "Tottenham Hotspur", "Wolves": "Wolverhampton Wanderers",
    "Wolverhampton": "Wolverhampton Wanderers", "Newcastle": "Newcastle United",
    "Nott'm Forest": "Nottingham Forest", "Nottingham": "Nottingham Forest",
    "Brighton": "Brighton & Hove Albion", "West Brom": "West Bromwich Albion", "West Ham": "West Ham United",
    "Leeds": "Leeds United", "Leicester": "Leicester City", "Norwich": "Norwich City",
    "Sheffield Utd": "Sheffield United", "Luton": "Luton Town", "Ipswich": "Ipswich Town",
    "Ath Madrid": "Atlético de Madrid", "Atletico Madrid": "Atlético de Madrid",
    "Ath Bilbao": "Athletic Bilbao", "Athletic Club": "Athletic Bilbao", "Betis": "Real Betis Balompié",
    "Real Betis": "Real Betis Balompié", "Sociedad": "Real Sociedad", "Celta": "Celta de Vigo",
    "Alaves": "Deportivo Alavés", "Vallecano": "Rayo Vallecano", "Espanol": "RCD Espanyol Barcelona",
    "Espanyol": "RCD Espanyol Barcelona", "Osasuna": "CA Osasuna",
    "Inter": "Inter Milan", "Internazionale": "Inter Milan", "Milan": "AC Milan", "Roma": "Associazione Sportiva Roma", "AS Roma": "Associazione Sportiva Roma",
    "Verona": "Hellas Verona", "Spal": "SPAL", "SPAL Ferrara": "SPAL", "Lazio": "Società Sportiva Lazio S.p.A.", "SS Lazio": "Società Sportiva Lazio S.p.A.",
    "Atalanta": "Atalanta BC", "Genoa": "Genoa CFC", "Bologna": "Bologna Football Club 1909", "Como": "Como 1907",
    "Salernitana": "US Salernitana 1919", "Monza": "AC Monza", "Verona FC": "Hellas Verona", "Parma": "Parma Calcio 1913",
    "Bayern": "Bayern Munich", "Bayern München": "Bayern Munich", "M'gladbach": "Borussia Mönchengladbach",
    "Leverkusen": "Bayer 04 Leverkusen", "Bayer Leverkusen": "Bayer 04 Leverkusen",
    "Dortmund": "Borussia Dortmund", "Ein Frankfurt": "Eintracht Frankfurt", "Stuttgart": "VfB Stuttgart",
    "Mainz": "1.FSV Mainz 05", "Mainz 05": "1.FSV Mainz 05", "Werder Bremen": "SV Werder Bremen", "Wolfsburg": "VfL Wolfsburg",
    "Bochum": "VfL Bochum", "Darmstadt": "SV Darmstadt 98", "Union Berlin": "1.FC Union Berlin", "Heidenheim": "1. Fußballclub Heidenheim 1846",
    "Greuther Furth": "SpVgg Greuther Fürth", "Paderborn": "SC Paderborn 07", "Fortuna Dusseldorf": "Fortuna Düsseldorf", "FC Koln": "1.FC Köln", "Koln": "1.FC Köln", "Hertha": "Hertha BSC",
    "Hoffenheim": "TSG 1899 Hoffenheim", "Freiburg": "SC Freiburg", "Leipzig": "RB Leipzig",
    "Paris SG": "Paris Saint-Germain", "PSG": "Paris Saint-Germain", "St Etienne": "AS Saint-Étienne",
    "Rennes": "Stade Rennais FC", "Reims": "Stade Reims", "Brest": "Stade Brestois 29", "Strasbourg": "RC Strasbourg Alsace",
    "Montpellier": "Montpellier HSC", "Troyes": "ESTAC Troyes", "Auxerre": "AJ Auxerre", "Angers": "Angers SCO", "Nimes": "Nîmes Olympique",
    "Bordeaux": "FC Girondins Bordeaux", "Dijon": "Dijon FCO", "Lille": "LOSC Lille", "Nice": "OGC Nice",
    "Marseille": "Olympique Marseille", "Lyon": "Olympique Lyon", "Monaco": "AS Monaco",
    "Sporting": "Sporting CP", "Sporting Lisbon": "Sporting CP", "Benfica": "SL Benfica",
    "Porto": "FC Porto", "Ajax": "Ajax Amsterdam", "PSV": "PSV Eindhoven", "Celtic": "Celtic FC",
    "Galatasaray": "Galatasaray SK", "Shakhtar": "Shakhtar Donetsk",
}


class ClubResolver:
    """Maps provider club names/ids to canonical team ids and records the links."""

    def __init__(self, cfg: Config | None = None, overrides: dict[str, Any] | None = None):
        ov = overrides if overrides is not None else load_overrides(cfg)
        self._ov = _Overrides(ov.get("club") if ov else None)
        self._known: set[str] = set()  # canonical ids
        self._alias: dict[str, str] = {}  # name slug -> canonical
        self._ids: dict[tuple[str, str], str] = {}  # (source, id) -> canonical (from Club.source_ids)
        self._pending: list[tuple[str, Club]] = []
        self._memo: dict[tuple[str, str], tuple[str, float, str]] = {}

    # --- registration
    def add_clubs(self, source: str, clubs: Iterable[Club]) -> None:
        self._pending.extend((source, c) for c in clubs)

    def add_alias(self, canonical: str, name: str) -> None:
        self._known.add(canonical)
        self._alias[slug(name)] = canonical

    def _flush(self) -> None:
        if not self._pending:
            return
        rows, self._pending = self._pending, []
        for source, club in rows:
            if source == "transfermarkt":  # anchor first
                self._register(source, club)
        for raw, target in BUILTIN_CLUB_ALIASES.items():
            if slug(target) in self._known:
                self._alias.setdefault(slug(raw), slug(target))
        for source, club in rows:
            if source != "transfermarkt":
                self._register(source, club)

    def _register(self, source: str, club: Club) -> None:
        """Anchor rows (Transfermarkt) define canonical ids; other rows only register when they
        carry a Transfermarkt id or an override. Everything else is decided later by ``resolve``."""
        own_id = _strip_prefix(source, club.source_ids.model_dump().get(source) or club.team_id)
        names = [n for n in (club.name, club.short_name) if n]
        canonical = self._ov.lookup(source, own_id, names)
        if canonical is None and source == "transfermarkt":
            canonical = slug(club.name)
        elif canonical is None:
            tm = club.source_ids.transfermarkt
            if tm and ("transfermarkt", tm) in self._ids:
                canonical = self._ids[("transfermarkt", tm)]
        if canonical is None:
            return
        self._known.add(canonical)
        for n in names:
            self._alias.setdefault(slug(n), canonical)
        if own_id is not None:
            self._ids[(source, own_id)] = canonical
        for provider, pid in club.source_ids.model_dump().items():
            if pid:
                self._ids.setdefault((provider, str(pid)), canonical)

    # --- resolution
    def resolve(self, source: str, source_id: str | None = None, name: str | None = None) -> str:
        self._flush()
        sid = _strip_prefix(source, source_id)
        if sid is not None and (source, sid) in self._memo:
            return self._memo[(source, sid)][0]
        team, conf, method = self._decide(source, sid, name)
        self._known.add(team)
        if sid is not None:
            self._memo[(source, sid)] = (team, conf, method)
        elif name:
            self._memo[(source, f"name:{slug(name)}")] = (team, conf, method)
        return team

    def _decide(self, source: str, sid: str | None, name: str | None) -> tuple[str, float, str]:
        names = [name] if name else []
        if (team := self._ov.lookup(source, sid, names)) is not None:
            return team, CONF_ID, "override"
        if sid is not None and (source, sid) in self._ids:
            return self._ids[(source, sid)], CONF_ID, "exact_id"
        if not name:
            if sid is None:
                raise ValueError("resolve() needs a source_id or a name")
            return f"{source}-{sid}", CONF_NEW, "exact_id"
        s = slug(name)
        if s in self._known:
            return s, CONF_NAME_CLUB, "name_club_season"
        if s in self._alias:
            return self._alias[s], CONF_NAME_CLUB, "name_club_season"
        prefix = [k for k in self._known if k.startswith(s + "-") or s.startswith(k + "-")]
        if len(prefix) == 1:
            return prefix[0], CONF_FUZZY, "fuzzy"
        best = max(self._known, key=lambda k: SequenceMatcher(None, s, k).ratio(), default=None)
        if best is not None and SequenceMatcher(None, s, best).ratio() >= FUZZY_RATIO:
            return best, CONF_FUZZY, "fuzzy"
        return s, CONF_NEW, "exact_id"

    def links(self) -> list[IdLink]:
        out = []
        for (source, key), (team, conf, method) in sorted(self._memo.items()):
            out.append(IdLink(entity_kind="club", entity_id=team, source=_source(source),
                              source_id=key, confidence=conf, method=method))  # type: ignore[arg-type]
        return out


def _source(name: str) -> SourceName:
    return name  # type: ignore[return-value]


# ------------------------------------------------------------------- persons


class _Cluster:
    __slots__ = ("pid", "names", "births", "ctx", "teams", "ids")

    def __init__(self, pid: str):
        self.pid = pid
        self.names: set[str] = set()
        self.births: set[str] = set()
        self.ctx: set[tuple[str, str]] = set()  # (team, season)
        self.teams: set[str] = set()
        self.ids: dict[str, set[str]] = {}  # source -> source ids


class _PersonResolver:
    kind = "player"

    def __init__(self, cfg: Config | None = None, overrides: dict[str, Any] | None = None,
                 clubs: ClubResolver | None = None):
        ov = overrides if overrides is not None else load_overrides(cfg)
        self._ov = _Overrides(ov.get(self.kind) if ov else None)
        self._clubs = clubs
        self._clusters: dict[str, _Cluster] = {}
        self._by_name: dict[str, list[_Cluster]] = {}
        self._by_ctx: dict[tuple[str, str], list[_Cluster]] = {}
        self._by_team: dict[str, list[_Cluster]] = {}
        self._by_birth: dict[str, list[_Cluster]] = {}
        self._by_id: dict[tuple[str, str], _Cluster] = {}
        self._memo: dict[tuple[str, str], tuple[str, float, str]] = {}

    # --- public
    def resolve(self, source: str, source_id: str, name: str, team_id: str | None = None,
                season: str | None = None, birth_date: str | None = None, *,
                aliases: Sequence[str] = (), extra_ids: dict[str, str] | None = None) -> str:
        sid = _strip_prefix(source, str(source_id))
        assert sid is not None
        if (source, sid) in self._memo:
            return self._memo[(source, sid)][0]
        team = self._canon_team(team_id)
        birth = birth_date[:10] if birth_date else None
        names = {person_slug(n) for n in (name, *aliases) if n}
        cluster, conf, method = self._match(source, sid, name, names, team, season, birth, extra_ids or {})
        if cluster is None:
            cluster = self._new_cluster(name, birth)
            conf, method = CONF_NEW, "exact_id"
        self._absorb(cluster, source, sid, names, team, season, birth, extra_ids or {})
        self._memo[(source, sid)] = (cluster.pid, conf, method)
        return cluster.pid

    def links(self) -> list[IdLink]:
        return [IdLink(entity_kind=self.kind, entity_id=pid, source=_source(src), source_id=sid,  # type: ignore[arg-type]
                       confidence=conf, method=method)  # type: ignore[arg-type]
                for (src, sid), (pid, conf, method) in sorted(self._memo.items())]

    # --- internals
    def _canon_team(self, team_id: str | None) -> str | None:
        if not team_id:
            return None
        if self._clubs is not None and ":" in team_id:
            src, _, rest = team_id.partition(":")
            return self._clubs.resolve(src, rest)
        return team_id

    @staticmethod
    def _compatible(names: set[str], cluster: _Cluster) -> bool:
        """Same spelling, or every token of the shorter name appears in the longer one."""
        if names & cluster.names:
            return True
        for a in names:
            ta = set(a.split("-"))
            for b in cluster.names:
                tb = set(b.split("-"))
                small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
                if small and small <= big and (len(small) >= 2 or min(len(a), len(b)) >= 4):
                    return True
        return False

    @staticmethod
    def _free(cluster: _Cluster, source: str, sid: str) -> bool:
        """A source contributes at most one id per identity (two 'Danilo's stay apart)."""
        return not (cluster.ids.get(source) and sid not in cluster.ids[source])

    def _match(self, source: str, sid: str, name: str, names: set[str], team: str | None,
               season: str | None, birth: str | None, extra: dict[str, str]
               ) -> tuple[_Cluster | None, float, str]:
        override = self._ov.lookup(source, sid, [name])
        if override is not None:
            return self._clusters.get(override) or self._make(override), CONF_ID, "override"
        for provider, pid in {**extra, source: sid}.items():
            if (c := self._by_id.get((provider, str(pid)))) is not None:
                return c, CONF_ID, "exact_id"

        def usable(cs: Iterable[_Cluster]) -> list[_Cluster]:
            return [c for c in cs if self._free(c, source, sid) and self._compatible(names, c)
                    and not (birth and c.births and birth not in c.births)]

        # 1. name + club + season
        if team:
            block = self._by_ctx.get((team, season)) if season else self._by_team.get(team)
            cands = usable(block or [])
            exact = [c for c in cands if names & c.names]
            pick = exact or cands
            if len(pick) > 1 and birth:
                pick = [c for c in pick if birth in c.births] or pick
            if len(pick) == 1:
                return pick[0], CONF_NAME_CLUB, "name_club_season"
        # 2. name + birth date
        if birth:
            pick = usable(self._by_birth.get(birth, []))
            exact = [c for c in pick if names & c.names]
            pick = exact or pick
            if len(pick) == 1:
                return pick[0], CONF_NAME_BIRTH, "name_birth"
        # 3. fuzzy within the club block, or among players with the same birth date
        pool: list[_Cluster] = []
        if team:
            pool += self._by_team.get(team, [])
        if birth:
            pool += self._by_birth.get(birth, [])
        scored = []
        seen = set()
        for c in pool:
            if id(c) in seen or not self._free(c, source, sid) or (birth and c.births and birth not in c.births):
                continue
            seen.add(id(c))
            ratio = max((SequenceMatcher(None, n, m).ratio() for n in names for m in c.names), default=0.0)
            if ratio >= FUZZY_RATIO:
                scored.append((ratio, c))
        scored.sort(key=lambda t: -t[0])
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1], CONF_FUZZY, "fuzzy"
        return None, 0.0, ""

    def _make(self, pid: str) -> _Cluster:
        c = self._clusters[pid] = _Cluster(pid)
        return c

    def _new_cluster(self, name: str, birth: str | None) -> _Cluster:
        base = person_slug(name) or "unknown"
        pid = base
        if pid in self._clusters and birth:
            pid = f"{base}-{birth[:4]}"
        n = 2
        while pid in self._clusters:
            pid = f"{base}-{n}"
            n += 1
        return self._make(pid)

    def _absorb(self, c: _Cluster, source: str, sid: str, names: set[str], team: str | None,
                season: str | None, birth: str | None, extra: dict[str, str]) -> None:
        for n in names - c.names:
            c.names.add(n)
            self._by_name.setdefault(n, []).append(c)
        if birth and birth not in c.births:
            c.births.add(birth)
            self._by_birth.setdefault(birth, []).append(c)
        if team:
            if team not in c.teams:
                c.teams.add(team)
                self._by_team.setdefault(team, []).append(c)
            if season and (team, season) not in c.ctx:
                c.ctx.add((team, season))
                self._by_ctx.setdefault((team, season), []).append(c)
        for provider, pid in {**extra, source: sid}.items():
            c.ids.setdefault(provider, set()).add(str(pid))
            self._by_id[(provider, str(pid))] = c


class PlayerResolver(_PersonResolver):
    """Links Understat / Transfermarkt / FPL / StatsBomb / Wikidata players.

    Order of evidence: override, shared provider id, (name, club, season),
    (name, birth date), fuzzy name (ratio >= 0.9) within the club or birth date.
    """

    kind = "player"


class ManagerResolver(_PersonResolver):
    """Same evidence as players; ``team_id`` is the club managed in ``season``."""

    kind = "manager"
