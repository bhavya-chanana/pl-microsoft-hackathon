"""Transfermarkt via the dcaribou/transfermarkt-datasets public export.

Source: https://github.com/dcaribou/transfermarkt-datasets (per-table gzipped
CSVs on the project's public bucket, listed in the README). We download the
tables we need once into ``raw/transfermarkt/`` and read them with the
standard library (no DuckDB needed; the prebuilt ``transfermarkt-datasets.duckdb``
holds the same tables).

Licence: the repository is CC0 1.0 (GitHub licence metadata and LICENSE file).
The underlying data is scraped from transfermarkt.com, whose terms of use
restrict scraping/redistribution, so CC0 on the compilation does not remove
the upstream terms. Treat the raw files and the tables derived from them as
local research data: they stay out of the redistributable ``share/`` subset.

Tables used (columns verified against the export header):
    games          game_id, competition_id, season, round, date, home/away_club_id,
                   home/away_club_goals, home/away_club_manager_name, stadium,
                   attendance, referee, home/away_club_formation, home/away_club_name
    game_lineups   game_id, player_id, club_id, player_name, type (starting_lineup |
                   substitutes), position, number, team_captain
    game_events    game_id, minute, type (Goals | Cards | Substitutions | Shootout),
                   club_id, player_id, description, player_in_id, player_assist_id
    players        player_id, name, date_of_birth, country_of_citizenship, height_in_cm,
                   foot, position, sub_position
    player_valuations  player_id, date, market_value_in_eur
    clubs          club_id, name, domestic_competition_id, stadium_name, stadium_seats
    competitions   competition_id, country_name

Conventions of this export
* ``season`` is the starting year (2023 = "2023-24").
* Managers are names only (no ids): the provisional id is ``transfermarkt:<name slug>``.
* A substitution event's ``player_id`` is the player going off, ``player_in_id`` the one
  coming on. For goals and own goals the event's ``club_id`` is the club credited with
  the goal (the own-goal scorer plays for the other side).
* Formations carry variants ("4-3-3 Attacking"); we keep the numeric core ("4-3-3").
* Unused substitutes are kept with ``minute_on = minute_off = 0``.
"""

from __future__ import annotations

import csv
import gzip
import io
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from typing import Any

from matchlens.leaguedata import ids
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import fetch_bytes
from matchlens.leaguedata.schema import (
    SEASONS,
    TRANSFERMARKT_CODE,
    Club,
    League,
    LineupEntry,
    Manager,
    Match,
    MatchEvent,
    Player,
    PlayerValuation,
    SourceIds,
)

SOURCE = "transfermarkt"
BASE_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
TABLES = ("competitions", "clubs", "games", "players", "game_lineups", "game_events", "player_valuations")
CODE_TO_LEAGUE = {code: league for league, code in TRANSFERMARKT_CODE.items()}

_FORMATION = re.compile(r"\d(?:-\d)+")
_STAGES = (  # (pattern on the lower-cased round, stage)
    ("group", "group"),
    ("intermediate", "playoff"),
    ("play-off", "playoff"),
    ("playoff", "playoff"),
    ("last 16", "round_of_16"),
    ("round of 16", "round_of_16"),
    ("quarter", "quarter_final"),
    ("semi", "semi_final"),
    ("final", "final"),
)


# ----------------------------------------------------------------------- fetch


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int:
    """Download the export tables (whole-table files: leagues/seasons/limit only filter in normalize)."""
    if not any(lg in TRANSFERMARKT_CODE for lg in leagues):
        return 0
    new = 0
    for table in TABLES:
        cache = cfg.raw_dir(SOURCE) / f"{table}.csv.gz"
        existed = cache.exists() and not refresh
        fetch_bytes(f"{BASE_URL}/{table}.csv.gz", cache, cfg, refresh=refresh)
        new += 0 if existed else 1
    return new


# ------------------------------------------------------------------- helpers


def season_label(tm_season: str | int) -> str:
    """Transfermarkt season (start year) -> "2023-24"."""
    y = int(tm_season)
    return f"{y}-{(y + 1) % 100:02d}"


def normalize_formation(raw: str | None) -> str | None:
    """"4-3-3 Attacking" -> "4-3-3"; empty/unknown -> None."""
    if not raw:
        return None
    m = _FORMATION.search(raw)
    return m.group(0) if m else None


def cl_stage(round_name: str | None) -> str | None:
    """Champions League round text -> group | playoff | round_of_16 | quarter_final | semi_final | final."""
    if not round_name:
        return None
    low = round_name.lower()
    for needle, stage in _STAGES:
        if needle in low:
            return stage
    return ids.slug(round_name, strip_affixes=False).replace("-", "_") or None


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


def _iso(v: str | None) -> str | None:
    return v[:10] if v else None


def _open(cfg: Config, table: str) -> Iterator[dict[str, str]]:
    path = cfg.raw_dir(SOURCE) / f"{table}.csv.gz"
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":  # stored gzipped (or already unpacked if a proxy decoded it)
        raw = gzip.decompress(raw)
    yield from csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace")))


def _stream(cfg: Config, table: str) -> Iterator[dict[str, str]]:
    """Like ``_open`` but streams gzip (the lineup table is ~700 MB unpacked)."""
    path = cfg.raw_dir(SOURCE) / f"{table}.csv.gz"
    with path.open("rb") as raw:
        magic = raw.read(2)
        raw.seek(0)
        stream = gzip.open(raw, "rt", encoding="utf-8", errors="replace", newline="") if magic == b"\x1f\x8b" \
            else io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        yield from csv.DictReader(stream)


def _pid(player_id: str | None) -> str | None:
    return f"{SOURCE}:{player_id}" if player_id else None


def _event_kind(row: dict[str, str]) -> str | None:
    kind = row["type"]
    parts = [p.strip() for p in row["description"].split(",")]
    first = next((p for p in parts if p), "")
    low = first.lower()
    if kind == "Goals":
        if low.startswith("own-goal") or low.startswith("own goal"):
            return "own_goal"
        return "penalty_goal" if low == "penalty" else "goal"
    if kind == "Cards":
        if "second yellow" in low or "yellow/red" in low:
            return "second_yellow"
        if low.startswith("red"):
            return "red"
        return "yellow"
    if kind == "Substitutions":
        return "sub"
    return None  # Shootout


# ------------------------------------------------------------------- normalize


def normalize(cfg: Config, *, leagues: Sequence[League] | None = None,
              seasons: Sequence[str] | None = None) -> dict[str, list[Any]]:
    """Rows for matches, lineups, match_events, players, player_valuations, clubs, managers."""
    codes = {TRANSFERMARKT_CODE[lg] for lg in (leagues or TRANSFERMARKT_CODE)}
    wanted = set(seasons or SEASONS)

    games = [g for g in _open(cfg, "games")
             if g["competition_id"] in codes and season_label(g["season"]) in wanted
             and g["home_club_goals"] != "" and g["away_club_goals"] != ""]
    competitions = {c["competition_id"]: c for c in _open(cfg, "competitions")}
    clubs_raw = {c["club_id"]: c for c in _open(cfg, "clubs")}

    # One name per club (its latest), so match keys never depend on renames.
    games.sort(key=lambda g: (g["date"], g["game_id"]))
    club_name: dict[str, str] = {}
    for g in games:
        club_name[g["home_club_id"]] = g["home_club_name"]
        club_name[g["away_club_id"]] = g["away_club_name"]

    matches: list[Match] = []
    managers: dict[str, Manager] = {}
    by_game: dict[str, tuple[str, str, str]] = {}  # game_id -> (match_key, home club id, away club id)
    for g in games:
        league = CODE_TO_LEAGUE[g["competition_id"]]
        season = season_label(g["season"])
        key = ids.match_key(league, season, g["date"], club_name[g["home_club_id"]], club_name[g["away_club_id"]])
        by_game[g["game_id"]] = (key, g["home_club_id"], g["away_club_id"])
        mgr: dict[str, str | None] = {}
        for side in ("home", "away"):
            name = (g[f"{side}_club_manager_name"] or "").strip()
            mid = f"{SOURCE}:{ids.person_slug(name)}" if name else None
            mgr[side] = mid
            if mid and mid not in managers:
                managers[mid] = Manager(manager_id=mid, name=name)
        matches.append(Match(
            match_key=key, league=league, season=season, date=g["date"],
            stage=cl_stage(g["round"]) if league == League.UCL else None,
            home_id=f"{SOURCE}:{g['home_club_id']}", away_id=f"{SOURCE}:{g['away_club_id']}",
            home_goals=int(float(g["home_club_goals"])), away_goals=int(float(g["away_club_goals"])),
            home_formation=normalize_formation(g["home_club_formation"]),
            away_formation=normalize_formation(g["away_club_formation"]),
            home_manager_id=mgr["home"], away_manager_id=mgr["away"],
            referee=g["referee"] or None, venue=g["stadium"] or None, attendance=_int(g["attendance"]),
            source_ids=SourceIds(transfermarkt=g["game_id"])))

    # Clubs: every club in the selected games; stadium and country from clubs/competitions.
    clubs: list[Club] = []
    for cid, name in sorted(club_name.items()):
        raw = clubs_raw.get(cid, {})
        comp = competitions.get(raw.get("domestic_competition_id", ""), {})
        alt = (raw.get("name") or "").strip()
        clubs.append(Club(
            team_id=f"{SOURCE}:{cid}", name=name, short_name=alt if alt and alt != name else None,
            country=comp.get("country_name") or None, stadium=raw.get("stadium_name") or None,
            stadium_capacity=_int(raw.get("stadium_seats")), source_ids=SourceIds(transfermarkt=cid)))

    events, sub_off, sub_on, sent_off = _events(cfg, by_game)
    lineups, seen_players = _lineups(cfg, by_game, sub_off, sub_on, sent_off)
    for ev in events:
        for p in (ev.player_id, ev.related_player_id):
            if p:
                seen_players.setdefault(p.split(":", 1)[1], "")
    players = _players(cfg, seen_players)
    valuations = [
        PlayerValuation(player_id=f"{SOURCE}:{r['player_id']}", date=r["date"],
                        market_value_eur=int(float(r["market_value_in_eur"])))
        for r in _open(cfg, "player_valuations")
        if r["player_id"] in seen_players and r["market_value_in_eur"]]

    return {"matches": matches, "lineups": lineups, "match_events": events, "players": players,
            "player_valuations": valuations, "clubs": clubs, "managers": list(managers.values())}


def _events(cfg: Config, by_game: dict[str, tuple[str, str, str]]):
    events: list[MatchEvent] = []
    sub_off: dict[tuple[str, str], int] = {}
    sub_on: dict[tuple[str, str], int] = {}
    sent_off: dict[tuple[str, str], int] = {}
    for r in _stream(cfg, "game_events"):
        game = by_game.get(r["game_id"])
        kind = _event_kind(r) if game else None
        if game is None or kind is None or not r["club_id"]:
            continue
        minute = max(_int(r["minute"]) or 0, 0)
        player, other = r["player_id"], r["player_in_id"] if kind == "sub" else r["player_assist_id"]
        if kind in ("own_goal",):
            other = ""
        events.append(MatchEvent(
            event_id=f"{SOURCE}:{r['game_event_id']}", match_key=game[0], team_id=f"{SOURCE}:{r['club_id']}",
            minute=minute, kind=kind, player_id=_pid(player), related_player_id=_pid(other)))  # type: ignore[arg-type]
        if kind == "sub":
            if player:
                sub_off[(r["game_id"], player)] = minute
            if other:
                sub_on[(r["game_id"], other)] = minute
        elif kind in ("red", "second_yellow") and player:
            sent_off[(r["game_id"], player)] = minute
    return events, sub_off, sub_on, sent_off


def _lineups(cfg: Config, by_game: dict[str, tuple[str, str, str]], sub_off, sub_on, sent_off):
    lineups: list[LineupEntry] = []
    seen: set[tuple[str, str]] = set()
    players: dict[str, str] = {}
    for r in _stream(cfg, "game_lineups"):
        game = by_game.get(r["game_id"])
        if game is None or (r["game_id"], r["player_id"]) in seen:
            continue
        seen.add((r["game_id"], r["player_id"]))
        gid, pid = r["game_id"], r["player_id"]
        starter = r["type"] == "starting_lineup"
        if starter:
            on = 0
        elif (gid, pid) in sub_on:
            on = sub_on[(gid, pid)]
        else:
            on = 0  # unused substitute: zero minutes (minute_off = 0 below)
        off: int | None
        if not starter and (gid, pid) not in sub_on:
            off = 0
        else:
            off = min((m for m in (sub_off.get((gid, pid)), sent_off.get((gid, pid))) if m is not None), default=None)
        players[pid] = r["player_name"]
        lineups.append(LineupEntry(
            match_key=game[0], team_id=f"{SOURCE}:{r['club_id']}", player_id=f"{SOURCE}:{pid}", starter=starter,
            position=r["position"] or None, shirt=_int(r["number"]), minute_on=on, minute_off=off,
            captain=r["team_captain"] == "1"))
    return lineups, players


_FOOT = {"right": "right", "left": "left", "both": "both"}


def _players(cfg: Config, wanted: dict[str, str]) -> list[Player]:
    out: dict[str, Player] = {}
    for r in _open(cfg, "players"):
        pid = r["player_id"]
        if pid not in wanted:
            continue
        name = r["name"] or f"{r['first_name']} {r['last_name']}".strip() or wanted[pid]
        out[pid] = Player(
            player_id=f"{SOURCE}:{pid}", name=name, birth_date=_iso(r["date_of_birth"]),
            nationality=r["country_of_citizenship"] or None, height_cm=_int(r["height_in_cm"]) or None,
            foot=_FOOT.get(r["foot"]),  # type: ignore[arg-type]
            main_position=r["position"] or None, sub_position=r["sub_position"] or None,
            source_ids=SourceIds(transfermarkt=pid))
    for pid, name in wanted.items():  # in a lineup/event but missing from players.csv
        if pid not in out and name:
            out[pid] = Player(player_id=f"{SOURCE}:{pid}", name=name, source_ids=SourceIds(transfermarkt=pid))
    return list(out.values())


def coverage(matches: Sequence[Match]) -> dict[tuple[str, str], tuple[int, float]]:
    """(league, season) -> (matches, share with both formations); a smoke-test helper."""
    n: Counter[tuple[str, str]] = Counter()
    f: Counter[tuple[str, str]] = Counter()
    for m in matches:
        k = (m.league.value, m.season)
        n[k] += 1
        f[k] += bool(m.home_formation and m.away_formation)
    return {k: (n[k], f[k] / n[k]) for k in sorted(n)}
