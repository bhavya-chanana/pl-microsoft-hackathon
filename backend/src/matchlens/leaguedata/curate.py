"""Versioned curated dataset: ``tables/`` -> ``curated/v<N>/``.

This is the only input of the reasoner, the knowledge base, the tactician and
the Track C training. ``curate`` reads every build table, then writes

    curated/v<N>/
        tables/<table>.parquet   validated, deduplicated copies (matches get quality columns)
        rejects/<table>.jsonl    rows that failed validation: {"reason", "row"}
        id_links.parquet         the identity links (copy of tables/id_links.parquet)
        identity_report.json     linking stats per entity kind and source
        coverage.json            per league x season: counts, sources, missing-field rates
        splits.json              train/val/test assignment per match_key (80/10/10)
        DATASET_CARD.md          purpose, sources, licences, counts, gaps
        share/                   subset from redistributable sources only + SHARE_LICENSE.md
        manifest.json            every file with sha256, bytes and row count
    curated/LATEST               the newest version number

Quality columns added to ``matches`` (see ``CuratedMatch``): ``has_xg``,
``has_lineup``, ``has_formation``, ``has_events``, ``has_shots``, ``has_odds``,
``has_elo``, ``source_conflict`` (from ``tables/conflicts.json``) and
``sources_present`` (number of providers that identified the match).

Validation: each row is validated against its schema model plus a few sanity
checks (score and coordinate ranges, key/date agreement, children of unknown
matches). Duplicates on the natural key keep the row with the most fields set.

Downstream code reads with ``latest(cfg)`` and ``read_curated(cfg, table)``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from matchlens.leaguedata import build as build_mod
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.schema import TABLES, Match

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

SPLIT_BUCKETS = (("train", 80), ("val", 90), ("test", 100))  # cumulative upper bounds of hash % 100
SPLIT_SCHEME = "sha256(match_key) % 100: <80 train, <90 val, else test"

CHILD_TABLES = ("lineups", "shots", "match_events", "team_match_stats", "player_matches")

# natural keys used to deduplicate each table
NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "matches": ("match_key",),
    "lineups": ("match_key", "team_id", "player_id"),
    "shots": ("match_key", "shot_id"),
    "match_events": ("match_key", "event_id"),
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

PROVIDER_FIELDS = ("understat", "transfermarkt", "fpl", "wikidata", "statsbomb", "football_data")
FUZZY_REVIEW_BELOW = 0.8
FUZZY_REVIEW_LIMIT = 200

# ------------------------------------------------------------------ model


class CuratedMatch(Match):
    """A ``Match`` plus the quality flags added by ``curate``."""

    has_xg: bool = False
    has_lineup: bool = False
    has_formation: bool = False
    has_events: bool = False  # goal / card / substitution timeline
    has_shots: bool = False
    has_odds: bool = False
    has_elo: bool = False
    source_conflict: bool = False
    sources_present: int = 0


QUALITY_COLUMNS: dict[str, str] = {
    "has_xg": "BOOLEAN", "has_lineup": "BOOLEAN", "has_formation": "BOOLEAN", "has_events": "BOOLEAN",
    "has_shots": "BOOLEAN", "has_odds": "BOOLEAN", "has_elo": "BOOLEAN", "source_conflict": "BOOLEAN",
    "sources_present": "BIGINT",
}

CURATED_MODELS: dict[str, type[BaseModel]] = {**TABLES, "matches": CuratedMatch}  # type: ignore[dict-item]


@dataclass
class CurateReport:
    version: int
    path: Path
    counts: dict[str, int] = field(default_factory=dict)  # curated rows per table
    rejected: dict[str, int] = field(default_factory=dict)
    duplicates: dict[str, int] = field(default_factory=dict)
    share_counts: dict[str, int] = field(default_factory=dict)
    splits: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SEASON = re.compile(r"^\d{4}-\d{2}$")


def _valid_date(value: str | None) -> bool:
    if value is None or not _DATE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def split_of(match_key: str) -> str:
    """Deterministic split of a match: depends only on the key, so it is stable across versions."""
    bucket = int.from_bytes(hashlib.sha256(match_key.encode("utf-8")).digest()[:8], "big") % 100
    for name, upper in SPLIT_BUCKETS:
        if bucket < upper:
            return name
    return "test"  # pragma: no cover


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _filled(row: BaseModel) -> int:
    return sum(v is not None for v in row.model_dump().values())


def _error_reason(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"]) or "<row>"
    return f"{loc}: {err['msg']}"


def _in_range(value: float | None, low: float, high: float) -> bool:
    return value is None or low <= value <= high


def _check(table: str, row: Any) -> str | None:
    """Sanity checks beyond the pydantic types; returns the reason a row is rejected."""
    if table == "matches":
        if not _valid_date(row.date):
            return f"date: {row.date!r} is not an ISO date"
        if not _SEASON.match(row.season):
            return f"season: {row.season!r} is not like 2023-24"
        if not row.match_key.startswith(f"{row.league.value}:{row.season}:{row.date}:"):
            return "match_key does not agree with league/season/date"
        if row.home_id == row.away_id:
            return "home_id equals away_id"
        goals = (row.home_goals, row.away_goals, row.home_goals_ht, row.away_goals_ht)
        if any(g is not None and g < 0 for g in goals):
            return "negative goals"
        if not all(_in_range(v, 0, 12) for v in (row.home_xg, row.away_xg)):
            return "xg outside 0..12"
        probs = [row.p_home, row.p_draw, row.p_away]
        if any(p is not None for p in probs):
            if any(p is None or not 0 <= p <= 1 for p in probs) or abs(sum(probs) - 1) > 0.02:
                return "odds probabilities are not a distribution"
    elif table == "shots":
        if not (_in_range(row.x, 0, 105) and _in_range(row.y, 0, 68)):
            return "shot outside the pitch"
        if not 0 <= row.xg <= 1:
            return "shot xg outside 0..1"
        if not 0 <= row.minute <= 130:
            return "shot minute outside 0..130"
    elif table == "match_events":
        if not 0 <= row.minute <= 130:
            return "event minute outside 0..130"
    elif table == "lineups":
        if row.minute_off is not None and row.minute_off < row.minute_on:
            return "minute_off before minute_on"
    elif table == "player_matches":
        if not 0 <= row.minutes <= 150:
            return "minutes outside 0..150"
    elif table == "team_match_stats":
        if row.possession is not None and not 0 <= row.possession <= 1:
            return "possession outside 0..1"
    elif table == "player_valuations":
        if not _valid_date(row.date) or row.market_value_eur < 0:
            return "bad date or negative value"
    elif table == "manager_spells":
        if not _valid_date(row.start) or (row.end is not None and (not _valid_date(row.end) or row.end < row.start)):
            return "bad spell dates"
    return None


# ------------------------------------------------------------------ validation


@dataclass
class _Checked:
    rows: list[Any]
    rejects: list[dict[str, Any]]
    duplicates: int


def _validate_table(table: str, raw: Iterable[dict[str, Any]], known_matches: set[str] | None) -> _Checked:
    model = TABLES[table]
    keys = NATURAL_KEYS[table]
    best: dict[tuple[Any, ...], Any] = {}
    rejects: list[dict[str, Any]] = []
    dups = 0
    for record in raw:
        try:
            row = model.model_validate(record)
        except ValidationError as exc:
            rejects.append({"reason": _error_reason(exc), "row": record})
            continue
        reason = _check(table, row)
        if reason is None and known_matches is not None and table in CHILD_TABLES \
                and row.match_key not in known_matches:  # type: ignore[attr-defined]
            reason = "match_key not in matches"
        if reason is not None:
            rejects.append({"reason": reason, "row": record})
            continue
        key = tuple(getattr(row, k) for k in keys)
        if key in best:
            dups += 1
            if _filled(row) > _filled(best[key]):
                best[key] = row
        else:
            best[key] = row
    return _Checked(rows=list(best.values()), rejects=rejects, duplicates=dups)


def _quality_matches(rows: list[Match], *, lineups: set[str], events: set[str], shots: set[str],
                     conflicted: set[str]) -> list[CuratedMatch]:
    out = []
    for m in rows:
        present = sum(getattr(m.source_ids, f, None) is not None for f in type(m.source_ids).model_fields)
        out.append(CuratedMatch(
            **m.model_dump(),
            has_xg=m.home_xg is not None and m.away_xg is not None,
            has_lineup=m.match_key in lineups,
            has_formation=m.home_formation is not None and m.away_formation is not None,
            has_events=m.match_key in events,
            has_shots=m.match_key in shots,
            has_odds=m.p_home is not None and m.p_draw is not None and m.p_away is not None,
            has_elo=m.home_elo is not None and m.away_elo is not None,
            source_conflict=m.match_key in conflicted,
            sources_present=present,
        ))
    return sorted(out, key=lambda r: r.match_key)


def _sort_rows(table: str, rows: list[Any]) -> list[Any]:
    keys = NATURAL_KEYS[table]
    return sorted(rows, key=lambda r: tuple("" if getattr(r, k) is None else str(getattr(r, k)) for k in keys))


# ------------------------------------------------------------------ writing


def _write_parquet(path: Path, records: Sequence[dict[str, Any]], columns: dict[str, str]) -> None:
    """Write dict records to Parquet with explicit DuckDB column types (nothing is written when empty)."""
    import duckdb

    if not records:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "rows.jsonl"
            with src.open("w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            cols_sql = ", ".join(f"'{n}': '{t}'" for n, t in columns.items())
            con.execute(
                f"COPY (SELECT * FROM read_json('{src.as_posix()}', format='newline_delimited', "
                f"columns={{{cols_sql}}})) TO '{path.as_posix()}' (FORMAT PARQUET)"
            )
    finally:
        con.close()


def _write_rows(path: Path, table: str, rows: Sequence[BaseModel]) -> None:
    columns = build_mod._columns(TABLES[table])  # noqa: SLF001 - one schema -> DuckDB type mapping
    if table == "matches":
        columns = {**columns, **QUALITY_COLUMNS}
    _write_parquet(path, [r.model_dump(mode="json") for r in rows], columns)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _parquet_rows(path: Path) -> int:
    import duckdb

    con = duckdb.connect()
    try:
        return int(con.execute(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0])  # type: ignore[index]
    finally:
        con.close()


# ------------------------------------------------------------------ identity


def _identity_report(links: list[Any], entities: dict[str, set[str]]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, Any]] = {}
    review: list[dict[str, Any]] = []
    for kind in ("club", "player", "manager"):
        kind_links = [link for link in links if link.entity_kind == kind]
        total = len(entities.get(kind, set()))
        sources: dict[str, Any] = {}
        for src in sorted({link.source for link in kind_links}):
            sl = [link for link in kind_links if link.source == src]
            linked = {link.entity_id for link in sl}
            fuzzy_low = [link for link in sl if link.method == "fuzzy" and link.confidence < FUZZY_REVIEW_BELOW]
            sources[src] = {
                "links": len(sl),
                "entities_linked": len(linked),
                "linked_pct": round(100 * len(linked & entities.get(kind, set())) / total, 2) if total else None,
                "mean_confidence": round(sum(link.confidence for link in sl) / len(sl), 4),
                "methods": dict(sorted(Counter(link.method for link in sl).items())),
                "fuzzy_below_0_8": len(fuzzy_low),
            }
            review.extend(
                {"entity_kind": kind, "entity_id": link.entity_id, "source": src, "source_id": link.source_id,
                 "confidence": link.confidence}
                for link in fuzzy_low)
        by_kind[kind] = {"entities": total, "links": len(kind_links), "sources": sources}
    review.sort(key=lambda r: (r["confidence"], r["entity_kind"], r["entity_id"], r["source"], r["source_id"]))
    return {
        "fuzzy_review_threshold": FUZZY_REVIEW_BELOW,
        "kinds": by_kind,
        "fuzzy_review_total": len(review),
        "fuzzy_review": review[:FUZZY_REVIEW_LIMIT],
    }


# ------------------------------------------------------------------ coverage


def expected_matches(league: str, season: str) -> tuple[int | None, bool]:
    """(expected matches, exact?) for a league-season; ``(None, False)`` when unknown."""
    if league in ("EPL", "LA_LIGA", "SERIE_A"):
        return 380, True
    if league == "BUNDESLIGA":
        return 306, True
    if league == "LIGUE_1":
        if season == "2019-20":
            return 279, True  # season ended early (COVID); PSG declared champions after 279 matches
        start = int(season[:4]) if _SEASON.match(season) else 0
        return (306 if start >= 2023 else 380), True  # 18 clubs from 2023-24, 20 before
    if league == "UCL":
        start = int(season[:4]) if _SEASON.match(season) else 0
        if season == "2019-20":
            return 119, False  # single-leg knockouts in Lisbon after COVID
        if start >= 2024:
            return 189, False  # league phase (144) + play-offs + knockouts
        return 125, False  # 96 group + 29 knockout
    return None, False


def _season_of(key: str) -> tuple[str, str] | None:
    parts = key.split(":", 3)
    return (parts[0], parts[1]) if len(parts) >= 3 else None


def _coverage(matches: list[CuratedMatch], tables: dict[str, list[Any]]) -> dict[str, Any]:
    cells: dict[tuple[str, str], list[CuratedMatch]] = defaultdict(list)
    for m in matches:
        cells[(m.league.value, m.season)].append(m)

    rows_by_cell: dict[str, dict[tuple[str, str], int]] = {}
    for table in CHILD_TABLES:
        counter: Counter[tuple[str, str]] = Counter()
        for r in tables.get(table, []):
            cell = _season_of(r.match_key)
            if cell:
                counter[cell] += 1
        rows_by_cell[table] = dict(counter)
    shots_by_source: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for s in tables.get("shots", []):
        cell = _season_of(s.match_key)
        if cell:
            shots_by_source[cell][s.shot_id.split(":", 1)[0] if ":" in s.shot_id else "unknown"] += 1
    possession: dict[str, int] = defaultdict(int)
    for st in tables.get("team_match_stats", []):
        if st.possession is not None:
            possession[st.match_key] += 1

    def rate(items: list[CuratedMatch], pred: Callable[[CuratedMatch], bool]) -> float:
        return round(sum(not pred(m) for m in items) / len(items), 4) if items else 0.0

    out: dict[str, Any] = {}
    for (league, season), items in sorted(cells.items()):
        expected, exact = expected_matches(league, season)
        sources = {f: sum(getattr(m.source_ids, f, None) is not None for m in items) for f in PROVIDER_FIELDS}
        out[f"{league}:{season}"] = {
            "league": league,
            "season": season,
            "matches": len(items),
            "expected_matches": expected,
            "expected_exact": exact,
            "completeness": round(len(items) / expected, 4) if expected else None,
            "sources": {k: v for k, v in sources.items() if v},
            "missing_rate": {
                "xg": rate(items, lambda m: m.has_xg),
                "formations": rate(items, lambda m: m.has_formation),
                "lineups": rate(items, lambda m: m.has_lineup),
                "managers": rate(items, lambda m: m.home_manager_id is not None and m.away_manager_id is not None),
                "odds": rate(items, lambda m: m.has_odds),
                "possession": rate(items, lambda m: possession.get(m.match_key, 0) >= 2),
                "elo": rate(items, lambda m: m.has_elo),
                "events": rate(items, lambda m: m.has_events),
                "shots": rate(items, lambda m: m.has_shots),
            },
            "rows": {t: rows_by_cell[t].get((league, season), 0) for t in CHILD_TABLES},
            "shots_by_source": dict(sorted(shots_by_source.get((league, season), Counter()).items())),
            "source_conflicts": sum(m.source_conflict for m in items),
        }
    return {"expected_notes": (
        "EPL/La Liga/Serie A 380; Bundesliga 306; Ligue 1 380 (2019-20: 279 after early end, 2023-24 on: 306); "
        "UCL counts are indicative only (format changed in 2024-25)."), "cells": out}


# ------------------------------------------------------------------ share

# Provenance we can prove: shot/event ids carry their source ("statsbomb:123"); person and club rows
# carry Wikidata ids; spells, honours and links have a ``source`` column.
SHARE_SOURCES = frozenset({"wikidata", "openfootball", "statsbomb", "wikipedia"})
SHARE_MATCH_COLUMNS = {
    "match_key": "VARCHAR", "league": "VARCHAR", "season": "VARCHAR", "date": "VARCHAR", "stage": "VARCHAR",
    "home_id": "VARCHAR", "away_id": "VARCHAR", "home_goals": "BIGINT", "away_goals": "BIGINT",
    "home_goals_ht": "BIGINT", "away_goals_ht": "BIGINT", "statsbomb_id": "VARCHAR", "openfootball_id": "VARCHAR",
}
SHARE_CLUB_COLUMNS = {"team_id": "VARCHAR", "name": "VARCHAR", "country": "VARCHAR", "founded": "BIGINT",
                      "wikidata_id": "VARCHAR"}
SHARE_PLAYER_COLUMNS = {"player_id": "VARCHAR", "name": "VARCHAR", "birth_date": "VARCHAR",
                        "nationality": "VARCHAR", "wikidata_id": "VARCHAR"}
SHARE_MANAGER_COLUMNS = {"manager_id": "VARCHAR", "name": "VARCHAR", "birth_date": "VARCHAR",
                         "nationality": "VARCHAR", "wikidata_id": "VARCHAR"}


def _src_id(row: Any, name: str) -> str | None:
    ids = getattr(row, "source_ids", None)
    return getattr(ids, name, None) if ids is not None else None


def _record(row: BaseModel, columns: Iterable[str]) -> dict[str, Any]:
    dumped = row.model_dump(mode="json")
    return {c: dumped.get(c) for c in columns}


def _prefix(value: str) -> str:
    return value.split(":", 1)[0] if ":" in value else ""


def _write_share(root: Path, tables: dict[str, list[Any]], links: list[Any]) -> tuple[dict[str, int], list[str]]:
    """Write ``root`` with the redistributable subset; returns (row counts, excluded-table notes)."""
    share_tables = root / "tables"
    counts: dict[str, int] = {}
    notes: list[str] = []

    def put(name: str, records: list[dict[str, Any]], columns: dict[str, str]) -> None:
        counts[name] = len(records)
        _write_parquet(share_tables / f"{name}.parquet", records, columns)

    matches = []
    for m in tables.get("matches", []):
        sb, of = _src_id(m, "statsbomb"), _src_id(m, "openfootball")
        if sb is None and of is None:
            continue
        rec = _record(m, SHARE_MATCH_COLUMNS)
        rec["statsbomb_id"], rec["openfootball_id"] = sb, of
        matches.append(rec)
    put("matches", matches, SHARE_MATCH_COLUMNS)

    shot_cols = {n: t for n, t in build_mod._columns(TABLES["shots"]).items()}  # noqa: SLF001
    put("shots", [r.model_dump(mode="json") for r in tables.get("shots", []) if _prefix(r.shot_id) == "statsbomb"],
        shot_cols)
    event_cols = dict(build_mod._columns(TABLES["match_events"]))  # noqa: SLF001
    put("match_events", [r.model_dump(mode="json") for r in tables.get("match_events", [])
                         if _prefix(r.event_id) == "statsbomb"], event_cols)

    for name, id_field, cols in (("clubs", "team_id", SHARE_CLUB_COLUMNS), ("players", "player_id", SHARE_PLAYER_COLUMNS),
                                 ("managers", "manager_id", SHARE_MANAGER_COLUMNS)):
        recs = []
        for r in tables.get(name, []):
            wid = _src_id(r, "wikidata")
            if wid is None:
                continue
            rec = _record(r, [c for c in cols if c != "wikidata_id"])
            rec["wikidata_id"] = wid
            recs.append(rec)
        put(name, recs, cols)

    for name in ("manager_spells", "honours"):
        put(name, [r.model_dump(mode="json") for r in tables.get(name, []) if r.source in SHARE_SOURCES],
            dict(build_mod._columns(TABLES[name])))  # noqa: SLF001
    put("text_docs", [r.model_dump(mode="json") for r in tables.get("text_docs", [])
                      if r.licence.upper().startswith(("CC BY-SA", "CC0", "CC-BY-SA"))],
        dict(build_mod._columns(TABLES["text_docs"])))  # noqa: SLF001
    put("id_links", [r.model_dump(mode="json") for r in links if r.source in SHARE_SOURCES],
        dict(build_mod._columns(TABLES["id_links"])))  # noqa: SLF001

    for name, why in (
        ("lineups", "no per-row provenance (Transfermarkt/Understat-derived)"),
        ("team_match_stats", "Understat / football-data derived fields, no per-row provenance"),
        ("player_matches", "Understat-derived, no per-row provenance"),
        ("player_valuations", "Transfermarkt-derived, not on the redistributable list"),
    ):
        notes.append(f"{name}: excluded - {why}")
    return counts, notes


SHARE_LICENSE = """# Share subset - licence and attribution

This folder holds only the part of the Matchlens league dataset that is built from
redistributable sources. Everything else stays local (see `../DATASET_CARD.md`).

| Content | Source | Licence | Attribution |
| --- | --- | --- | --- |
| `clubs`, `players`, `managers` (name, birth date, nationality, country, founded, Wikidata id) | Wikidata | CC0 1.0 | none required; please credit Wikidata |
| `text_docs` | Wikipedia | CC BY-SA 4.0 | keep `url` and `licence`; derived text must be shared under CC BY-SA 4.0 |
| `matches` (identity, date, score) of matches identified by openfootball | openfootball | CC0 / public domain | none required |
| `matches`, `shots`, `match_events` of matches identified by StatsBomb | StatsBomb open data | StatsBomb data user agreement | "Data provided by StatsBomb" with the StatsBomb logo wherever the data is shown |
| `manager_spells`, `honours`, `id_links` | rows whose `source` is one of the above | as above | as above |

Excluded because they are not redistributable or cannot be traced per row:
Understat (no licence; local use only), FPL (unofficial API), football-data.co.uk (free with
attribution, kept local), ClubElo, Transfermarkt-derived columns, and every table without per-row
provenance. Match scores are limited to matches that openfootball or StatsBomb identify; xG, odds,
Elo, formations and lineups are never shared.

Because `text_docs` is CC BY-SA 4.0, a redistribution of this folder as a whole must carry that
licence for those rows. Nothing here is warranted to be accurate or complete.
"""

# ------------------------------------------------------------------ dataset card

SOURCE_LICENCES: dict[str, tuple[str, str, bool]] = {
    # source -> (licence, attribution / terms, redistributable in share/)
    "understat": ("No licence published", "Local research use only; never redistributed", False),
    "transfermarkt": ("CC0 per the dataset repository (verify before redistributing)",
                      "Attribution appreciated; not in share/ until verified", False),
    "football_data": ("Free to use", "Attribution to football-data.co.uk required; not redistributed", False),
    "clubelo": ("Free API, no explicit licence", "Credit clubelo.com; local only", False),
    "fpl": ("Unofficial API, no licence", "Local use only; do not redistribute", False),
    "statsbomb": ("StatsBomb open data user agreement", "Attribute StatsBomb and show their logo", True),
    "wikidata": ("CC0 1.0", "None required", True),
    "wikipedia": ("CC BY-SA 4.0", "Attribute, link the article, share alike", True),
    "openfootball": ("CC0 / public domain", "None required", True),
}


def _raw_info(cfg: Config) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for src in SOURCE_LICENCES:
        folder = cfg.raw_dir(src)
        files = [p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")] if folder.is_dir() else []
        newest = max((p.stat().st_mtime for p in files), default=None)
        out[src] = {
            "files": len(files),
            "last_fetched": datetime.fromtimestamp(newest, UTC).date().isoformat() if newest else None,
        }
    return out


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _dataset_card(version: int, created: str, report: CurateReport, coverage: dict[str, Any],
                  raw: dict[str, dict[str, Any]], identity: dict[str, Any], share_notes: list[str]) -> str:
    source_rows = [
        (src, lic, terms, raw[src]["files"], raw[src]["last_fetched"] or "never", "yes" if share else "no")
        for src, (lic, terms, share) in SOURCE_LICENCES.items()
    ]
    count_rows = [(t, report.counts.get(t, 0), report.rejected.get(t, 0), report.duplicates.get(t, 0))
                  for t in TABLES]
    cells = coverage["cells"]
    cov_rows = [
        (f"{c['league']} {c['season']}", c["matches"], c["expected_matches"] or "?",
         "" if c["completeness"] is None else f"{c['completeness']:.0%}",
         f"{c['missing_rate']['xg']:.0%}", f"{c['missing_rate']['formations']:.0%}",
         f"{c['missing_rate']['lineups']:.0%}", f"{c['missing_rate']['odds']:.0%}", c["source_conflicts"])
        for c in cells.values()
    ]
    id_lines = []
    for kind, info in identity["kinds"].items():
        for src, s in info["sources"].items():
            id_lines.append((kind, src, s["links"], s["linked_pct"], s["mean_confidence"], s["fuzzy_below_0_8"]))
    return f"""# Matchlens league dataset - curated v{version}

Created {created}. Built by `python -m matchlens.leaguedata curate` from the Parquet tables in `tables/`.

## Purpose

One validated, versioned snapshot of five seasons of top-flight club football (Premier League, La Liga,
Serie A, optionally Bundesliga, Ligue 1 and the Champions League) with canonical club, player and manager
ids. It feeds the match reasoner, the runtime knowledge base, the tactician and the Track C training
(deterministic train/val/test split in `splits.json`).

## Sources, licences and attribution

{_md_table(["source", "licence", "attribution / terms", "raw files", "last fetched", "in share/"], source_rows)}

Only the rows of the "in share/" sources leave this machine; see `share/SHARE_LICENSE.md`.

## Row counts

{_md_table(["table", "rows", "rejected", "duplicates dropped"], count_rows)}

Rejected rows are in `rejects/<table>.jsonl` with the reason.

## Coverage (share of matches missing the field)

{_md_table(["league season", "matches", "expected", "complete", "no xG", "no formation", "no lineup", "no odds", "conflicts"], cov_rows) if cov_rows else "No matches."}

`coverage.json` has the per-source counts and the remaining missing-field rates.

## Identity links

{_md_table(["kind", "source", "links", "linked %", "mean confidence", "fuzzy < 0.8"], id_lines) if id_lines else "No links."}

Fuzzy links below {FUZZY_REVIEW_BELOW} are listed in `identity_report.json` for review; fix them in `overrides.json`.

## Quality flags (`matches`)

`has_xg`, `has_lineup`, `has_formation`, `has_events`, `has_shots`, `has_odds`, `has_elo`,
`source_conflict` (providers disagreed on the score, see `tables/conflicts.json`), `sources_present`
(providers that identified the match).

## Known gaps

* No free tracking data: positions of all players over time are not available.
* No pass-level events for 2019-2026; event data is shots, goals, cards and substitutions.
* The Champions League has no Understat xG; shots, xG and PPDA exist only for the five domestic leagues.
* Possession, formations and lineups depend on Transfermarkt coverage and are missing for some matches.
* Share subset: {"; ".join(share_notes)}.
* Understat and FPL are unofficial scrapes and may change or disappear.

## Intended use and limits

For research, prototyping and model training on this machine. Not an official data product: values are
as reported by the providers and reconciled by fixed precedence rules (see `leaguedata/build.py`). xG is the
providers' model, not ours. Do not publish anything outside `share/`, and do not use the data to make
betting decisions.
"""


# ------------------------------------------------------------------ versions


def _versions(cfg: Config) -> list[int]:
    if not cfg.curated_dir.is_dir():
        return []
    return sorted(int(p.name[1:]) for p in cfg.curated_dir.iterdir()
                  if p.is_dir() and re.fullmatch(r"v\d+", p.name))


def latest(cfg: Config) -> Path:
    """Folder of the newest curated version (``LATEST`` pointer, else the highest ``v<N>``)."""
    pointer = cfg.curated_dir / "LATEST"
    if pointer.exists():
        text = pointer.read_text(encoding="utf-8").strip().lstrip("vV")
        if text.isdigit() and (cfg.curated_dir / f"v{int(text)}").is_dir():
            return cfg.curated_dir / f"v{int(text)}"
    versions = _versions(cfg)
    if not versions:
        raise FileNotFoundError(f"no curated dataset in {cfg.curated_dir}; run: python -m matchlens.leaguedata curate")
    return cfg.curated_dir / f"v{versions[-1]}"


def _version_dir(cfg: Config, version: int | None) -> Path:
    if version is None:
        return latest(cfg)
    path = cfg.curated_dir / f"v{version}"
    if not path.is_dir():
        raise FileNotFoundError(f"curated version v{version} not found in {cfg.curated_dir}")
    return path


def load_curated(cfg: Config, table: str, version: int | None = None) -> list[dict[str, Any]]:
    """Rows of a curated table as plain dicts ([] when the table is empty)."""
    if table not in TABLES:
        raise KeyError(f"unknown table {table!r}")
    path = _version_dir(cfg, version) / "tables" / f"{table}.parquet"
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


def read_curated(cfg: Config, table: str, model: type[M] | None = None, version: int | None = None) -> list[M]:
    """Curated rows validated into models: ``CuratedMatch`` for ``matches``, else the table's schema model."""
    chosen = model or CURATED_MODELS[table]
    return [chosen.model_validate(r) for r in load_curated(cfg, table, version)]  # type: ignore[return-value]


# ------------------------------------------------------------------ curate


def _manifest(root: Path, created: str, version: int) -> dict[str, Any]:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        entry: dict[str, Any] = {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                                 "sha256": _sha256(path)}
        if path.suffix == ".parquet":
            entry["rows"] = _parquet_rows(path)
        elif path.suffix == ".jsonl":
            with path.open("rb") as fh:
                entry["rows"] = sum(1 for _ in fh)
        files.append(entry)
    return {"version": version, "created": created, "files": files}


def curate(cfg: Config, *, version: int | None = None) -> CurateReport:
    """Validate and version the build tables into ``curated/v<N>/`` (N = next free unless given)."""
    existing = _versions(cfg)
    number = version if version is not None else (existing[-1] + 1 if existing else 1)
    if number in existing:
        raise FileExistsError(f"curated version v{number} already exists in {cfg.curated_dir}")
    final = cfg.curated_dir / f"v{number}"
    work = cfg.curated_dir / f".v{number}.partial"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    report = CurateReport(version=number, path=final)
    created = datetime.now(UTC).replace(microsecond=0).isoformat()

    try:
        conflicts = build_mod.conflicts(cfg)
        conflicted = {c["match_key"] for c in conflicts}

        checked: dict[str, _Checked] = {}
        checked["matches"] = _validate_table("matches", build_mod.load_table(cfg, "matches"), None)
        known = {m.match_key for m in checked["matches"].rows}
        for table in TABLES:
            if table != "matches":
                checked[table] = _validate_table(table, build_mod.load_table(cfg, table), known)

        curated: dict[str, list[Any]] = {t: _sort_rows(t, c.rows) for t, c in checked.items()}
        curated["matches"] = _quality_matches(
            curated["matches"],
            lineups={r.match_key for r in curated["lineups"]},
            events={r.match_key for r in curated["match_events"]},
            shots={r.match_key for r in curated["shots"]},
            conflicted=conflicted)

        for table, c in checked.items():
            report.counts[table] = len(curated[table])
            report.rejected[table] = len(c.rejects)
            report.duplicates[table] = c.duplicates
            if c.rejects:
                rej = work / "rejects" / f"{table}.jsonl"
                rej.parent.mkdir(parents=True, exist_ok=True)
                with rej.open("w", encoding="utf-8") as fh:
                    for r in c.rejects:
                        fh.write(json.dumps(r, ensure_ascii=False, default=str, sort_keys=True) + "\n")
                report.warnings.append(f"{table}: {len(c.rejects)} rows rejected")
            _write_rows(work / "tables" / f"{table}.parquet", table, curated[table])
        if curated["id_links"]:
            shutil.copy2(work / "tables" / "id_links.parquet", work / "id_links.parquet")

        entities = {
            "club": {c.team_id for c in curated["clubs"]},
            "player": {p.player_id for p in curated["players"]},
            "manager": {m.manager_id for m in curated["managers"]},
        }
        identity = _identity_report(curated["id_links"], entities)
        _write_json(work / "identity_report.json", identity)

        coverage = _coverage(curated["matches"], curated)
        _write_json(work / "coverage.json", coverage)

        assignment = {m.match_key: split_of(m.match_key) for m in curated["matches"]}
        report.splits = dict(sorted(Counter(assignment.values()).items()))
        _write_json(work / "splits.json", {"scheme": SPLIT_SCHEME, "counts": report.splits,
                                           "splits": dict(sorted(assignment.items()))})

        report.share_counts, share_notes = _write_share(work / "share", curated, curated["id_links"])
        (work / "share").mkdir(parents=True, exist_ok=True)  # exists only when something was redistributable
        (work / "share" / "SHARE_LICENSE.md").write_text(
            SHARE_LICENSE + "\n## Not shared\n\n" + "\n".join(f"* {n}" for n in share_notes) + "\n", encoding="utf-8")

        (work / "DATASET_CARD.md").write_text(
            _dataset_card(number, created, report, coverage, _raw_info(cfg), identity, share_notes), encoding="utf-8")
        _write_json(work / "manifest.json", _manifest(work, created, number))

        work.replace(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    (cfg.curated_dir / "LATEST").write_text(f"{number}\n", encoding="utf-8")
    log.info("curated v%d written to %s", number, final)
    return report


# ---------------------------------------------------------------------- CLI


def main(cfg: Config, args: Any) -> int:
    """Entry point used by ``python -m matchlens.leaguedata curate`` (optional ``args.version``)."""
    try:
        report = curate(cfg, version=getattr(args, "version", None))
    except FileExistsError as exc:
        print(str(exc))
        return 1
    print(f"curated v{report.version} -> {report.path}")
    print(f"{'table':20s} {'rows':>9s} {'rejected':>9s} {'dups':>7s}")
    for table, n in report.counts.items():
        print(f"{table:20s} {n:9d} {report.rejected.get(table, 0):9d} {report.duplicates.get(table, 0):7d}")
    print("splits: " + ", ".join(f"{k}={v}" for k, v in report.splits.items()))
    for w in report.warnings:
        print(f"warning: {w}")
    return 0


__all__ = [
    "CuratedMatch", "CurateReport", "curate", "expected_matches", "latest", "load_curated", "main",
    "read_curated", "split_of",
]
