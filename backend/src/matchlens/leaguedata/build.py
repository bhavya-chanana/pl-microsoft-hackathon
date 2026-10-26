"""Build the Parquet tables: normalise every source, link ids, reconcile, dedupe, write.

Pipeline (``build``):

1. ``normalize(cfg)`` of each source (lazy import, a failing source is skipped
   with a warning). Rows carry provisional ``"<source>:<id>"`` ids.
2. Resolve clubs, managers and players to canonical ids with the ``ids.py``
   resolvers (Transfermarkt first, so canonical ids anchor on it).
3. Recompute every match key from canonical club slugs, merge near-duplicate
   dates (providers disagree by a day on late kickoffs) and remap the child
   tables.
4. Reconcile matches field group by field group (precedence below) and record
   disagreements in ``conflicts``.
5. Dedupe every table, fill Elo from ClubElo, write ``<tables_dir>/<table>.parquet``
   with DuckDB (plus ``conflicts.json``).

Match field precedence (first non-null wins):

* scores, half-time scores, odds, referee: football_data, transfermarkt, understat, openfootball, statsbomb
* xG: understat, statsbomb
* formations, managers, venue, attendance: transfermarkt, statsbomb, football_data
* Elo: ClubElo (``clubelo.elo_at``), otherwise whatever a source supplied
* everything else: transfermarkt, understat, football_data, statsbomb, openfootball

Lineups, shots and match events come from one source per match (the highest
precedence that has any) so nothing is double counted; team and player match
stats are merged field by field.
"""

from __future__ import annotations

import importlib
import json
import logging
import tempfile
import types
import typing
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from matchlens.leaguedata import schema
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.schema import TABLES, IdLink, Match, SourceIds
from matchlens.leaguedata.sources import SOURCES

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

KNOWN_SOURCES = frozenset(typing.get_args(schema.SourceName))

# ------------------------------------------------------------------ precedence

DEFAULT_ORDER = ("transfermarkt", "understat", "football_data", "statsbomb", "openfootball", "fpl",
                 "clubelo", "wikidata", "wikipedia", "espn", "fotmob")
RESULT_ORDER = ("football_data", "transfermarkt", "understat", "openfootball", "statsbomb")
XG_ORDER = ("understat", "statsbomb")
LINEUP_ORDER = ("transfermarkt", "statsbomb", "understat", "football_data", "openfootball")
SHOT_ORDER = ("understat", "statsbomb")
EVENT_ORDER = ("transfermarkt", "statsbomb", "understat", "football_data", "openfootball")
STATS_ORDER = ("understat", "football_data", "espn", "fotmob", "statsbomb", "transfermarkt")

MATCH_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("home_goals", "away_goals", "home_goals_ht", "away_goals_ht", "p_home", "p_draw", "p_away", "referee"),
     RESULT_ORDER),
    (("home_xg", "away_xg"), XG_ORDER),
    (("home_formation", "away_formation", "home_manager_id", "away_manager_id", "venue", "attendance"),
     ("transfermarkt", "statsbomb", "football_data")),
    (("home_elo", "away_elo"), ("clubelo",)),
)
SCORE_FIELDS = ("home_goals", "away_goals")
HT_FIELDS = ("home_goals_ht", "away_goals_ht")

# fields of a row that hold a club / person id
CLUB_FIELDS = ("team_id", "home_id", "away_id")
PLAYER_FIELDS = ("player_id", "assist_player_id", "related_player_id")
MANAGER_FIELDS = ("manager_id", "home_manager_id", "away_manager_id")

SORT_KEYS: dict[str, tuple[str, ...]] = {
    "matches": ("match_key",),
    "lineups": ("match_key", "team_id", "player_id"),
    "shots": ("match_key", "minute", "shot_id"),
    "match_events": ("match_key", "minute", "event_id"),
    "team_match_stats": ("match_key", "team_id"),
    "player_matches": ("match_key", "player_id"),
    "players": ("player_id",),
    "player_valuations": ("player_id", "date"),
    "clubs": ("team_id",),
    "managers": ("manager_id",),
    "manager_spells": ("manager_id", "team_id", "start"),
    "honours": ("team_id", "competition", "season"),
    "text_docs": ("entity_kind", "entity_id", "url"),
    "id_links": ("entity_kind", "entity_id", "source", "source_id"),
}


@dataclass
class BuildReport:
    counts: dict[str, int]
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # source -> reason


# ------------------------------------------------------------------ ids helpers


def _split(pid: str | None) -> tuple[str, str] | None:
    """``"understat:81"`` -> ("understat", "81"); None for canonical / unprefixed ids."""
    if not pid or ":" not in pid:
        return None
    src, bare = pid.split(":", 1)
    return (src, bare) if src in KNOWN_SOURCES else None


def _slugify(name: str) -> str:
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


class _FallbackClubs:
    """Used only when ids.py is unavailable: canonical id = slug of the name."""

    def __init__(self) -> None:
        self._links: dict[tuple[str, str], IdLink] = {}

    def add_clubs(self, source: str, clubs: Iterable[Any]) -> None:
        return None

    def resolve(self, source: str, source_id: str | None = None, name: str | None = None) -> str:
        cid = _slugify(name or source_id or "unknown")
        self._links[(source, source_id or cid)] = IdLink(
            entity_kind="club", entity_id=cid, source=source, source_id=source_id or cid,  # type: ignore[arg-type]
            confidence=0.5, method="fuzzy")
        return cid

    def links(self) -> list[IdLink]:
        return list(self._links.values())


class _FallbackPeople(_FallbackClubs):
    kind = "player"

    def resolve(self, source: str, source_id: str | None = None, name: str | None = None, **_: Any) -> str:  # type: ignore[override]
        cid = _slugify(name or source_id or "unknown")
        self._links[(source, source_id or cid)] = IdLink(
            entity_kind=self.kind, entity_id=cid, source=source, source_id=source_id or cid,  # type: ignore[arg-type]
            confidence=0.5, method="fuzzy")
        return cid


class _FallbackManagers(_FallbackPeople):
    kind = "manager"


def _fallback_match_key(league: Any, season: str, date: str, home_id: str, away_id: str) -> str:
    lg = getattr(league, "value", league)
    return f"{lg}:{season}:{date}:{_slugify(home_id)}-{_slugify(away_id)}"


class _Ids:
    """Facade over ``leaguedata.ids`` (or a slug fallback when that module is missing)."""

    def __init__(self, cfg: Config) -> None:
        try:
            ids = importlib.import_module("matchlens.leaguedata.ids")
        except ImportError:
            log.warning("leaguedata.ids not available: falling back to name slugs for ids")
            self.clubs: Any = _FallbackClubs()
            self.players: Any = _FallbackPeople()
            self.managers: Any = _FallbackManagers()
            self.match_key: Callable[..., str] = _fallback_match_key
            return
        overrides = None
        if cfg.overrides_path.exists():
            try:
                overrides = json.loads(cfg.overrides_path.read_text())
            except (OSError, ValueError):
                log.warning("unreadable overrides file %s", cfg.overrides_path)
        self.clubs = ids.ClubResolver(cfg, overrides)
        self.players = ids.PlayerResolver(cfg, overrides, clubs=self.clubs)
        self.managers = ids.ManagerResolver(cfg, overrides)
        self.match_key = ids.match_key


# ------------------------------------------------------------------ loading


def _load_sources(cfg: Config, names: Sequence[str]) -> tuple[dict[str, dict[str, list[BaseModel]]], dict[str, str], dict[str, Any]]:
    tables: dict[str, dict[str, list[BaseModel]]] = {}
    skipped: dict[str, str] = {}
    modules: dict[str, Any] = {}
    for name in names:
        path = SOURCES.get(name)
        if path is None:
            skipped[name] = "unknown source"
            log.warning("unknown source %r skipped", name)
            continue
        try:
            mod = importlib.import_module(path)
            result = mod.normalize(cfg)
        except Exception as exc:  # noqa: BLE001 - one broken source must not stop the build
            skipped[name] = f"{type(exc).__name__}: {exc}"
            log.warning("source %s skipped: %s", name, skipped[name])
            continue
        modules[name] = mod
        tables[name] = {t: list(rows) for t, rows in result.items() if t in TABLES}
    return tables, skipped, modules


def _order(names: Iterable[str]) -> list[str]:
    """Transfermarkt first (canonical ids anchor on it), the rest in SOURCES order."""
    ordered = [n for n in SOURCES if n in set(names)]
    extra = [n for n in names if n not in SOURCES]
    ordered = sorted(ordered, key=lambda n: (n != "transfermarkt", list(SOURCES).index(n)))
    return ordered + extra


# --------------------------------------------------------------- resolution


class _Resolution:
    """provisional id -> canonical id for clubs, players and managers."""

    def __init__(self, ids: _Ids, tables: dict[str, dict[str, list[BaseModel]]], order: list[str]) -> None:
        self.ids = ids
        self.club: dict[str, str] = {}
        self.player: dict[str, str] = {}
        self.manager: dict[str, str] = {}
        self._tables = tables
        self._order = order
        self._run()

    def _names(self, table: str, id_field: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for src in self._order:
            for row in self._tables[src].get(table, []):
                out.setdefault(getattr(row, id_field), row)
        return out

    def _provisional(self, src: str, fields: tuple[str, ...]) -> list[str]:
        seen: dict[str, None] = {}
        for rows in self._tables[src].values():
            for row in rows:
                for f in fields:
                    v = getattr(row, f, None)
                    if isinstance(v, str) and v:
                        seen.setdefault(v, None)
        return list(seen)

    def _run(self) -> None:
        club_rows = self._names("clubs", "team_id")
        for src in self._order:
            self.ids.clubs.add_clubs(src, [r for r in self._tables[src].get("clubs", [])])
        # ids can be referenced by tables of other sources, so look at all of them
        for src in self._order:
            for pid in self._provisional(src, CLUB_FIELDS):
                if pid in self.club:
                    continue
                parts = _split(pid)
                if parts is None:
                    self.club[pid] = pid
                    continue
                row = club_rows.get(pid)
                self.club[pid] = self.ids.clubs.resolve(parts[0], parts[1], getattr(row, "name", None))

        mgr_rows = self._names("managers", "manager_id")
        for src in self._order:
            for pid in self._provisional(src, MANAGER_FIELDS):
                if pid in self.manager:
                    continue
                parts = _split(pid)
                if parts is None:
                    self.manager[pid] = pid
                    continue
                row = mgr_rows.get(pid)
                self.manager[pid] = self.ids.managers.resolve(
                    parts[0], parts[1], getattr(row, "name", None) or parts[1])

        ctx = self._player_context()
        player_rows = self._names("players", "player_id")
        for src in self._order:
            for pid in self._provisional(src, PLAYER_FIELDS):
                if pid in self.player:
                    continue
                parts = _split(pid)
                if parts is None:
                    self.player[pid] = pid
                    continue
                row = player_rows.get(pid)
                team, season = ctx.get(pid, (None, None))
                self.player[pid] = self.ids.players.resolve(
                    parts[0], parts[1], getattr(row, "name", None) or parts[1],
                    team_id=self.club.get(team) if team else None, season=season,
                    birth_date=getattr(row, "birth_date", None))

    def _player_context(self) -> dict[str, tuple[str | None, str | None]]:
        """Provisional player -> (provisional team, season) of the first appearance."""
        ctx: dict[str, tuple[str | None, str | None]] = {}
        for src in self._order:
            for table in ("lineups", "player_matches", "shots"):
                for row in self._tables[src].get(table, []):
                    pid = getattr(row, "player_id", None)
                    if pid and pid not in ctx:
                        parts = row.match_key.split(":")  # type: ignore[attr-defined]
                        ctx[pid] = (getattr(row, "team_id", None), parts[1] if len(parts) > 1 else None)
        return ctx

    def apply(self, row: M) -> M:
        update: dict[str, Any] = {}
        for fields, mapping in ((CLUB_FIELDS, self.club), (PLAYER_FIELDS, self.player), (MANAGER_FIELDS, self.manager)):
            for f in fields:
                v = getattr(row, f, None)
                if isinstance(v, str) and v in mapping:
                    update[f] = mapping[v]
        kind = getattr(row, "entity_kind", None)
        if kind in ("club", "player", "manager") and hasattr(row, "entity_id") and hasattr(row, "text"):
            mapping = {"club": self.club, "player": self.player, "manager": self.manager}[kind]
            if row.entity_id in mapping:  # type: ignore[attr-defined]
                update["entity_id"] = mapping[row.entity_id]  # type: ignore[attr-defined]
        return row.model_copy(update=update) if update else row


# -------------------------------------------------------------- match merge


def _cluster_keys(rows: list[tuple[str, Match]], mk: Callable[..., str], precedence: Callable[[str], int]
                  ) -> dict[tuple[str, str], str]:
    """(source, old match_key) -> final canonical match_key, merging dates one day apart."""
    groups: dict[tuple[str, str, str, str], list[tuple[str, str, Match]]] = defaultdict(list)
    for src, m in rows:
        groups[(m.league.value, m.season, m.home_id, m.away_id)].append((src, m.match_key, m))
    final: dict[tuple[str, str], str] = {}
    for items in groups.values():
        items.sort(key=lambda it: it[2].date)
        clusters: list[list[tuple[str, str, Match]]] = []
        for it in items:
            if clusters:
                first = _date.fromisoformat(clusters[-1][0][2].date)
                if _date.fromisoformat(it[2].date) - first <= timedelta(days=1):
                    clusters[-1].append(it)
                    continue
            clusters.append([it])
        for cluster in clusters:
            best = min(cluster, key=lambda it: (precedence(it[0]), it[2].date))
            key = mk(best[2].league, best[2].season, best[2].date, best[2].home_id, best[2].away_id)
            for src, old, _m in cluster:
                final[(src, old)] = key
    return final


def _precedence(source: str) -> int:
    return DEFAULT_ORDER.index(source) if source in DEFAULT_ORDER else len(DEFAULT_ORDER)


def _pick(by_src: dict[str, Match], fields: tuple[str, ...], order: Sequence[str]) -> dict[str, Any]:
    ranked = sorted(by_src, key=lambda s: order.index(s) if s in order else len(order))
    out: dict[str, Any] = {}
    for f in fields:
        for src in ranked:
            v = getattr(by_src[src], f)
            if v is not None:
                out[f] = v
                break
    return out


def _merge_matches(key: str, by_src: dict[str, Match], conflicts: list[dict[str, Any]]) -> Match:
    base_src = min(by_src, key=lambda s: DEFAULT_ORDER.index(s) if s in DEFAULT_ORDER else 99)
    base = by_src[base_src]
    update: dict[str, Any] = {"match_key": key}
    covered: set[str] = set()
    for fields, order in MATCH_GROUPS:
        update.update(_pick(by_src, fields, order))
        covered.update(fields)
    for f in Match.model_fields:
        if f in covered or f in ("match_key", "source_ids"):
            continue
        for src in sorted(by_src, key=lambda s: DEFAULT_ORDER.index(s) if s in DEFAULT_ORDER else 99):
            v = getattr(by_src[src], f)
            if v is not None:
                update[f] = v
                break
    ids = SourceIds()
    merged_ids: dict[str, str] = {}
    for src in by_src:
        for k, v in by_src[src].source_ids.model_dump().items():
            if v is not None:
                merged_ids.setdefault(k, v)
    if merged_ids:
        ids = SourceIds(**merged_ids)
    update["source_ids"] = ids

    if len(by_src) > 1:
        for label, fields in (("score", SCORE_FIELDS), ("score_ht", HT_FIELDS)):
            values = {s: tuple(getattr(m, f) for f in fields) for s, m in by_src.items()}
            values = {s: v for s, v in values.items() if all(x is not None for x in v)}
            if len(set(values.values())) > 1:
                chosen = tuple(update.get(f) for f in fields)
                conflicts.append({
                    "match_key": key, "field": label,
                    "values": {s: "-".join(map(str, v)) for s, v in sorted(values.items())},
                    "chosen": "-".join(map(str, chosen)),
                })
    return base.model_copy(update=update)


# -------------------------------------------------------------- generic merge


def _merge_fieldwise(rows: list[tuple[str, M]], order: Sequence[str]) -> M:
    ranked = sorted(rows, key=lambda r: order.index(r[0]) if r[0] in order else len(order))
    first = ranked[0][1]
    update: dict[str, Any] = {}
    for f in type(first).model_fields:
        if getattr(first, f) is None:
            for _, row in ranked[1:]:
                v = getattr(row, f)
                if v is not None:
                    update[f] = v
                    break
    return first.model_copy(update=update) if update else first


def _merge_source_ids(rows: list[tuple[str, M]], merged: M) -> M:
    if not hasattr(merged, "source_ids"):
        return merged
    ids: dict[str, str] = {}
    for _, row in rows:
        for k, v in row.source_ids.model_dump().items():  # type: ignore[attr-defined]
            if v is not None:
                ids.setdefault(k, v)
    return merged.model_copy(update={"source_ids": SourceIds(**ids)}) if ids else merged


def _dedupe(rows: list[tuple[str, M]], key: Callable[[M], Any], order: Sequence[str]) -> list[M]:
    groups: dict[Any, list[tuple[str, M]]] = {}
    for src, row in rows:
        groups.setdefault(key(row), []).append((src, row))
    out = []
    for items in groups.values():
        merged = _merge_fieldwise(items, order) if len(items) > 1 else items[0][1]
        out.append(_merge_source_ids(items, merged) if len(items) > 1 else merged)
    return out


def _sort(table: str, rows: list[Any]) -> list[Any]:
    keys = SORT_KEYS[table]
    return sorted(rows, key=lambda r: tuple("" if getattr(r, k) is None else getattr(r, k) for k in keys))


# -------------------------------------------------------------------- build


def build_report(cfg: Config, sources: list[str] | None = None) -> BuildReport:
    names = _order(sources if sources is not None else list(SOURCES))
    tables, skipped, modules = _load_sources(cfg, names)
    order = _order(tables)
    ids = _Ids(cfg)
    res = _Resolution(ids, tables, order)

    # --- matches: canonical keys, date clustering, remap
    match_rows: list[tuple[str, Match]] = []
    originals: dict[tuple[str, str], str] = {}  # (source, provisional key) -> interim canonical key
    for src in order:
        for raw in tables[src].get("matches", []):
            m = res.apply(raw)  # type: ignore[arg-type]
            canon = ids.match_key(m.league, m.season, m.date, m.home_id, m.away_id)
            match_rows.append((src, m.model_copy(update={"match_key": canon})))
            originals[(src, raw.match_key)] = canon  # type: ignore[attr-defined]
    final = _cluster_keys(match_rows, ids.match_key, _precedence)
    key_map: dict[tuple[str, str], str] = {
        (src, raw): final.get((src, canon), canon) for (src, raw), canon in originals.items()
    }

    conflicts: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Match]] = defaultdict(dict)
    for src, m in match_rows:
        grouped[final.get((src, m.match_key), m.match_key)].setdefault(src, m)
    matches = [_merge_matches(k, by_src, conflicts) for k, by_src in grouped.items()]
    # the winning date of a merged cluster must agree with its key
    matches = [m.model_copy(update={"date": m.match_key.split(":")[2]}) for m in matches]

    def remap(src: str, row: M) -> M:
        row = res.apply(row)
        key = getattr(row, "match_key", None)
        if key is not None and (src, key) in key_map:
            row = row.model_copy(update={"match_key": key_map[(src, key)]})
        return row

    def rows_of(table: str) -> list[tuple[str, Any]]:
        return [(src, remap(src, r)) for src in order for r in tables[src].get(table, [])]

    out: dict[str, list[Any]] = {"matches": matches}
    out["lineups"] = _dedupe(
        _by_source_choice(rows_of("lineups"), LINEUP_ORDER),
        lambda r: (r.match_key, r.team_id, r.player_id), LINEUP_ORDER)
    out["shots"] = _dedupe(
        _by_source_choice(rows_of("shots"), SHOT_ORDER), lambda r: (r.match_key, r.shot_id), SHOT_ORDER)
    out["match_events"] = _dedupe(
        _by_source_choice(rows_of("match_events"), EVENT_ORDER), lambda r: (r.match_key, r.event_id), EVENT_ORDER)
    out["team_match_stats"] = _dedupe(
        rows_of("team_match_stats"), lambda r: (r.match_key, r.team_id), STATS_ORDER)
    out["player_matches"] = _dedupe(
        rows_of("player_matches"), lambda r: (r.match_key, r.player_id), STATS_ORDER)
    out["players"] = _dedupe(rows_of("players"), lambda r: r.player_id, DEFAULT_ORDER)
    out["player_valuations"] = _dedupe(
        rows_of("player_valuations"), lambda r: (r.player_id, r.date), DEFAULT_ORDER)
    out["clubs"] = _dedupe(rows_of("clubs"), lambda r: r.team_id, DEFAULT_ORDER)
    out["managers"] = _dedupe(rows_of("managers"), lambda r: r.manager_id, DEFAULT_ORDER)
    out["manager_spells"] = _dedupe(
        rows_of("manager_spells"), lambda r: (r.manager_id, r.team_id, r.start), DEFAULT_ORDER)
    out["honours"] = _dedupe(
        rows_of("honours"), lambda r: (r.team_id, r.competition, r.season), DEFAULT_ORDER)
    out["text_docs"] = _dedupe(
        rows_of("text_docs"), lambda r: (r.entity_kind, r.entity_id, r.url), DEFAULT_ORDER)

    links = [r for _, r in rows_of("id_links")]
    for resolver in (ids.clubs, ids.players, ids.managers):
        links.extend(resolver.links())
    best_links: dict[tuple[str, str, str], IdLink] = {}
    for link in links:
        k = (link.entity_kind, link.source, link.source_id)
        if k not in best_links or link.confidence > best_links[k].confidence:
            best_links[k] = link
    out["id_links"] = list(best_links.values())

    _fill_elo(cfg, out, modules, res, tables, order)

    for table in out:
        out[table] = _sort(table, out[table])
    counts = {t: len(rows) for t, rows in out.items()}
    _write_tables(cfg, out)
    _write_conflicts(cfg, conflicts)
    counts["conflicts"] = len(conflicts)
    for c in conflicts:
        log.warning("conflict %s %s: %s -> %s", c["match_key"], c["field"], c["values"], c["chosen"])
    return BuildReport(counts=counts, conflicts=conflicts, skipped=skipped)


def _by_source_choice(rows: list[tuple[str, M]], order: Sequence[str]) -> list[tuple[str, M]]:
    """Per match keep the rows of the highest-precedence source that has any."""
    by_match: dict[str, dict[str, list[tuple[str, M]]]] = defaultdict(lambda: defaultdict(list))
    for src, row in rows:
        by_match[row.match_key][src].append((src, row))  # type: ignore[attr-defined]
    out: list[tuple[str, M]] = []
    for srcs in by_match.values():
        best = min(srcs, key=lambda s: order.index(s) if s in order else len(order))
        out.extend(srcs[best])
    return out


def build(cfg: Config, sources: list[str] | None = None) -> dict[str, int]:
    """Build every table; returns row counts per table plus ``"conflicts"``."""
    return build_report(cfg, sources).counts


# ------------------------------------------------------------------- Elo


def _fill_elo(cfg: Config, out: dict[str, list[Any]], modules: dict[str, Any], res: _Resolution,
              tables: dict[str, dict[str, list[BaseModel]]], order: list[str]) -> None:
    mod = modules.get("clubelo")
    if mod is None or not hasattr(mod, "elo_lookup"):
        return
    try:
        lookup = mod.elo_lookup(cfg)
        elo_at = getattr(mod, "elo_at", None)
    except Exception as exc:  # noqa: BLE001
        log.warning("ClubElo lookup unavailable: %s", exc)
        return
    if not lookup:
        return
    name_of: dict[str, str] = {}  # canonical team id -> ClubElo name
    for prov, canon in res.club.items():
        parts = _split(prov)
        if parts and parts[0] == "clubelo":
            name_of.setdefault(canon, parts[1])

    def elo(team: str, day: str) -> float | None:
        club = name_of.get(team)
        if club is None:
            return None
        if elo_at is not None:
            return elo_at(lookup, club, day)
        return lookup.get((club, day))

    filled = []
    for m in out["matches"]:
        update: dict[str, Any] = {}
        for f, team in (("home_elo", m.home_id), ("away_elo", m.away_id)):
            v = elo(team, m.date)
            if v is not None:
                update[f] = float(v)
        filled.append(m.model_copy(update=update) if update else m)
    out["matches"] = filled


# ------------------------------------------------------------------- output


def _duck_type(ann: Any) -> str:
    origin = typing.get_origin(ann)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return _duck_type(args[0])
    if origin is typing.Literal:
        return "VARCHAR"
    if ann is bool:
        return "BOOLEAN"
    if ann is int:
        return "BIGINT"
    if ann is float:
        return "DOUBLE"
    if ann is str:
        return "VARCHAR"
    if isinstance(ann, type) and issubclass(ann, StrEnum):
        return "VARCHAR"
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        inner = ", ".join(f'"{n}" {_duck_type(f.annotation)}' for n, f in ann.model_fields.items())
        return f"STRUCT({inner})"
    raise TypeError(f"no DuckDB type for {ann!r}")


def _columns(model: type[BaseModel]) -> dict[str, str]:
    return {n: _duck_type(f.annotation) for n, f in model.model_fields.items()}


def _write_tables(cfg: Config, tables: dict[str, list[BaseModel]]) -> None:
    import duckdb

    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        for name, rows in tables.items():
            target = cfg.tables_dir / f"{name}.parquet"
            if not rows:
                target.unlink(missing_ok=True)
                continue
            columns = _columns(TABLES[name])
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / f"{name}.jsonl"
                with src.open("w", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n")
                cols_sql = ", ".join(f"'{n}': '{t}'" for n, t in columns.items())
                part = target.with_suffix(".parquet.tmp")
                con.execute(
                    f"COPY (SELECT * FROM read_json('{src.as_posix()}', format='newline_delimited', "
                    f"columns={{{cols_sql}}})) TO '{part.as_posix()}' (FORMAT PARQUET)"
                )
                part.replace(target)
    finally:
        con.close()


def _write_conflicts(cfg: Config, conflicts: list[dict[str, Any]]) -> None:
    cfg.tables_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.tables_dir / "conflicts.json"
    path.write_text(json.dumps(conflicts, indent=2, sort_keys=True), encoding="utf-8")


def load_table(cfg: Config, name: str) -> list[dict[str, Any]]:
    """Read ``<tables_dir>/<name>.parquet`` back as dicts ([] when the table is empty/absent)."""
    if name not in TABLES:
        raise KeyError(f"unknown table {name!r}")
    path = cfg.tables_dir / f"{name}.parquet"
    if not path.exists():
        return []
    import duckdb

    con = duckdb.connect()
    try:
        cur = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}')")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def rows(cfg: Config, name: str, model: type[M] | None = None) -> list[M]:
    """Table rows validated into schema models (the table's own model by default)."""
    model = model or TABLES[name]  # type: ignore[assignment]
    return [model.model_validate(r) for r in load_table(cfg, name)]  # type: ignore[union-attr]


def conflicts(cfg: Config) -> list[dict[str, Any]]:
    path = cfg.tables_dir / "conflicts.json"
    return json.loads(path.read_text()) if path.exists() else []


# ---------------------------------------------------------------------- CLI


def main(cfg: Config, args: Any) -> int:
    """Entry point used by ``python -m matchlens.leaguedata build``."""
    raw = getattr(args, "sources", None)
    if isinstance(raw, str):
        raw = [s.strip() for s in raw.split(",") if s.strip()]
    report = build_report(cfg, list(raw) if raw else None)
    for table, n in report.counts.items():
        print(f"{table:20s} {n}")
    for src, why in report.skipped.items():
        print(f"skipped {src}: {why}")
    return 0
