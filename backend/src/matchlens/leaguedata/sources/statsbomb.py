"""StatsBomb Open Data: full event streams (and 360 frames where published).

Source: https://github.com/statsbomb/open-data, read through
raw.githubusercontent.com (see also ``simulator/reference.py`` for the earlier
aggregate-only use of the same files).

User agreement: StatsBomb Open Data may be used and published for free only if
you credit StatsBomb and, when publishing analysis, show their logo / state
"Data provided by StatsBomb". Their licence also forbids selling the data and
requires that you do not claim it as your own. Anything derived from these
files that leaves this repository (the ``share/`` subset, the demo, the docs)
must carry that attribution:

    Data provided by StatsBomb (https://github.com/statsbomb/open-data).

Coverage we pull (men's competitions only): Premier League 2015/16 (competition
2, season 27; the only English top-flight season with events), La Liga
(competition 11, every available season), Champions League (16, every season),
Serie A (12), plus Bundesliga (9) and Ligue 1 (7) when requested. 360 frames
exist for a few competitions (e.g. La Liga 2020/21) and are fetched when the
match file says ``match_status_360 == "available"``.

Raw layout under ``raw/statsbomb``: ``competitions.json``,
``matches/<competition>/<season>.json``, ``events/<match>.json``,
``lineups/<match>.json``, ``three-sixty/<match>.json``.

``fetch(..., limit=N)`` limits the events/lineups/360 downloads to the first N
available matches (by date) of every competition-season; the match lists are
always complete. The ``seasons`` argument does not apply (StatsBomb seasons
rarely coincide with ``schema.SEASONS``); everything available is fetched.

Coordinates: StatsBomb pitches are 120 x 80 yards with the acting team always
attacking towards x = 120; we scale to 105 x 68 m (attacking towards x = 105).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from matchlens.leaguedata import ids
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import fetch_json
from matchlens.leaguedata.schema import (
    Club,
    League,
    LineupEntry,
    Manager,
    Match,
    Player,
    Shot,
    SourceIds,
)

SOURCE = "statsbomb"
BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
COMPETITION_ID = {League.EPL: 2, League.LA_LIGA: 11, League.SERIE_A: 12, League.BUNDESLIGA: 9,
                  League.LIGUE_1: 7, League.UCL: 16}
LEAGUE_OF = {cid: lg for lg, cid in COMPETITION_ID.items()}
ONLY_SEASON_IDS: dict[League, set[int]] = {League.EPL: {27}}

PITCH_L, PITCH_W = 105.0, 68.0
SX, SY = PITCH_L / 120.0, PITCH_W / 80.0

_RESULT = {"Goal": "goal", "Saved": "saved", "Saved Off Target": "saved", "Saved to Post": "saved",
           "Blocked": "blocked", "Off T": "missed", "Wayward": "missed", "Post": "post"}
_BODY = {"Right Foot": "right_foot", "Left Foot": "left_foot", "Head": "head"}


# ----------------------------------------------------------------------- fetch


def _raw(cfg: Config, *parts: str) -> Path:
    return cfg.raw_dir(SOURCE).joinpath(*parts)


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int:
    """Competitions, match lists, events, lineups and (where available) 360 frames."""
    wanted = {COMPETITION_ID[lg]: lg for lg in leagues if lg in COMPETITION_ID}
    if not wanted:
        return 0
    new = 0

    def get(rel: str) -> Any:
        nonlocal new
        cache = _raw(cfg, *rel.split("/"))
        existed = cache.exists() and not refresh
        data = fetch_json(f"{BASE_URL}/{rel}", cache, cfg, refresh=refresh)
        new += not existed
        return data

    competitions = get("competitions.json")
    for comp in competitions:
        cid, sid = comp["competition_id"], comp["season_id"]
        league = wanted.get(cid)
        if league is None or comp.get("competition_gender") != "male" or comp.get("competition_youth"):
            continue
        if league in ONLY_SEASON_IDS and sid not in ONLY_SEASON_IDS[league]:
            continue
        matches = get(f"matches/{cid}/{sid}.json")
        playable = sorted((m for m in matches if m.get("match_status") == "available"),
                          key=lambda m: (m["match_date"], m["match_id"]))
        for m in playable[:limit]:
            mid = m["match_id"]
            get(f"events/{mid}.json")
            get(f"lineups/{mid}.json")
            if m.get("match_status_360") == "available":
                get(f"three-sixty/{mid}.json")
    return new


# ------------------------------------------------------------------- raw access


def _load(path: Path) -> Any | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def match_list(cfg: Config, competition_id: int, season_id: int) -> list[dict[str, Any]]:
    return _load(_raw(cfg, "matches", str(competition_id), f"{season_id}.json")) or []


def iter_match_events(cfg: Config, match_id: int) -> Iterator[dict[str, Any]]:
    """Raw StatsBomb events of one match, in file order (empty if not downloaded)."""
    yield from _load(_raw(cfg, "events", f"{match_id}.json")) or []


def iter_events(cfg: Config, competition_id: int, season_id: int) -> Iterator[dict[str, Any]]:
    """Raw events of every downloaded match of a competition-season, in date order.

    Each event is the untouched StatsBomb dict plus one added key, ``match_id``.
    """
    for m in sorted(match_list(cfg, competition_id, season_id), key=lambda m: (m["match_date"], m["match_id"])):
        for event in iter_match_events(cfg, m["match_id"]):
            event["match_id"] = m["match_id"]
            yield event


def iter_three_sixty(cfg: Config, match_id: int) -> Iterator[dict[str, Any]]:
    """Raw 360 frames of one match ({event_uuid, visible_area, freeze_frame}); empty if unavailable."""
    yield from _load(_raw(cfg, "three-sixty", f"{match_id}.json")) or []


# ------------------------------------------------------------------ conversions


def season_label(name: str) -> str:
    """"2015/2016" -> "2015-16"; a single year stays as is."""
    if "/" in name:
        a, b = name.split("/", 1)
        return f"{a}-{b[-2:]}"
    return name


def to_metres(x: float, y: float) -> tuple[float, float]:
    """StatsBomb yards (120 x 80, attacking towards x=120) -> metres (105 x 68, attacking towards x=105)."""
    return round(x * SX, 2), round(y * SY, 2)


def formation_text(code: int | str | None) -> str | None:
    """StatsBomb numeric formation 4231 -> "4-2-3-1"."""
    return "-".join(str(code)) if code else None


def _minute(clock: str | None) -> int | None:
    return int(clock.split(":", 1)[0]) if clock else None


def _situation(shot: dict[str, Any], play_pattern: str) -> str:
    kind = shot.get("type", {}).get("name", "Open Play")
    if kind == "Penalty":
        return "penalty"
    if kind == "Free Kick":
        return "direct_free_kick"
    if kind == "Corner":
        return "corner"
    return {"From Corner": "corner", "From Free Kick": "set_piece", "From Counter": "fast_break"}.get(
        play_pattern, "open_play")


def _last_action(shot: dict[str, Any], key_pass: dict[str, Any] | None, previous: list[dict[str, Any]]) -> str | None:
    if shot.get("follows_dribble"):
        return "TakeOn"
    if key_pass is not None:
        p = key_pass.get("pass", {})
        if p.get("cross"):
            return "Cross"
        if p.get("through_ball"):
            return "Throughball"
        return "Pass"
    if any(e["type"]["name"] in ("Shot", "Goalkeeper") for e in previous[-2:]):
        return "Rebound"
    return None


def shots_from_events(match_key: str, events: list[dict[str, Any]], home_team_id: int) -> list[Shot]:
    """Shot rows for one match (penalty-shootout shots excluded)."""
    by_id = {e["id"]: e for e in events}
    score = {True: 0, False: 0}  # keyed by "is home"
    out: list[Shot] = []
    for i, e in enumerate(events):
        kind = e["type"]["name"]
        if e.get("period", 1) >= 5:
            continue
        is_home = e["team"]["id"] == home_team_id
        if kind == "Own Goal Against":  # the event belongs to the side that conceded
            score[not is_home] += 1
            continue
        if kind != "Shot":
            continue
        s = e["shot"]
        outcome = s["outcome"]["name"]
        key_pass = by_id.get(s.get("key_pass_id", ""))
        x, y = to_metres(*e["location"][:2])
        out.append(Shot(
            shot_id=f"{SOURCE}:{e['id']}", match_key=match_key, team_id=f"{SOURCE}:{e['team']['id']}",
            player_id=f"{SOURCE}:{e['player']['id']}",
            assist_player_id=f"{SOURCE}:{key_pass['player']['id']}" if key_pass and "player" in key_pass else None,
            minute=e["minute"], x=x, y=y, xg=float(s.get("statsbomb_xg", 0.0)),
            result=_RESULT.get(outcome, "missed"),  # type: ignore[arg-type]
            situation=_situation(s, e.get("play_pattern", {}).get("name", "")),  # type: ignore[arg-type]
            body_part=_BODY.get(s.get("body_part", {}).get("name", ""), "other"),  # type: ignore[arg-type]
            last_action=_last_action(s, key_pass, events[max(0, i - 2):i]),
            home_score_before=score[True], away_score_before=score[False]))
        if outcome == "Goal":
            score[is_home] += 1
    return out


def lineup_rows(match_key: str, lineups: list[dict[str, Any]]) -> tuple[list[LineupEntry], list[Player]]:
    rows: list[LineupEntry] = []
    players: list[Player] = []
    for team in lineups:
        team_id = f"{SOURCE}:{team['team_id']}"
        for p in team["lineup"]:
            positions = p.get("positions") or []
            starter = bool(positions) and positions[0].get("start_reason") == "Starting XI"
            on = _minute(positions[0].get("from")) if positions else 0
            off = 0 if not positions else (_minute(positions[-1].get("to")) if positions[-1].get("to") else None)
            rows.append(LineupEntry(
                match_key=match_key, team_id=team_id, player_id=f"{SOURCE}:{p['player_id']}", starter=starter,
                position=positions[0]["position"] if positions else None, shirt=p.get("jersey_number"),
                minute_on=on or 0, minute_off=off))
            players.append(Player(
                player_id=f"{SOURCE}:{p['player_id']}", name=p["player_name"],
                nationality=(p.get("country") or {}).get("name"),
                source_ids=SourceIds(statsbomb=str(p["player_id"]))))
    return rows, players


# ------------------------------------------------------------------- normalize


def _starting_formations(events: list[dict[str, Any]], home_team_id: int) -> tuple[str | None, str | None]:
    home = away = None
    for e in events:
        if e["type"]["name"] == "Starting XI":
            f = formation_text(e.get("tactics", {}).get("formation"))
            if e["team"]["id"] == home_team_id:
                home = f
            else:
                away = f
    return home, away


def normalize(cfg: Config) -> dict[str, list[Any]]:
    """Match, Shot, LineupEntry, Player, Club and Manager rows for every downloaded match."""
    from matchlens.leaguedata.sources.transfermarkt import cl_stage

    matches: list[Match] = []
    shots: list[Shot] = []
    lineups: list[LineupEntry] = []
    players: dict[str, Player] = {}
    clubs: dict[str, Club] = {}
    managers: dict[str, Manager] = {}

    for path in sorted(_raw(cfg, "matches").glob("*/*.json")):
        for m in _load(path) or []:
            league = LEAGUE_OF.get(m["competition"]["competition_id"])
            if league is None or m.get("match_status") != "available":
                continue
            home, away = m["home_team"], m["away_team"]
            season = season_label(m["season"]["season_name"])
            key = ids.match_key(league, season, m["match_date"], home["home_team_name"], away["away_team_name"])
            mgr: dict[str, str | None] = {"home": None, "away": None}
            for side, team in (("home", home), ("away", away)):
                tid = team[f"{side}_team_id"]
                prior = clubs.get(str(tid))
                stadium = (m.get("stadium") or {}).get("name") if side == "home" else None
                clubs[str(tid)] = Club(
                    team_id=f"{SOURCE}:{tid}", name=team[f"{side}_team_name"],
                    country=(team.get("country") or {}).get("name"),
                    stadium=stadium or (prior.stadium if prior else None), source_ids=SourceIds(statsbomb=str(tid)))
                for boss in team.get("managers") or []:
                    mid = f"{SOURCE}:{boss['id']}"
                    managers.setdefault(mid, Manager(
                        manager_id=mid, name=boss["name"], nationality=(boss.get("country") or {}).get("name"),
                        birth_date=boss.get("dob"), source_ids=SourceIds(statsbomb=str(boss["id"]))))
                    mgr[side] = mgr[side] or mid
            events = _load(_raw(cfg, "events", f"{m['match_id']}.json"))
            home_f, away_f = _starting_formations(events, home["home_team_id"]) if events else (None, None)
            matches.append(Match(
                match_key=key, league=league, season=season, date=m["match_date"],
                stage=cl_stage((m.get("competition_stage") or {}).get("name")) if league == League.UCL else None,
                home_id=f"{SOURCE}:{home['home_team_id']}", away_id=f"{SOURCE}:{away['away_team_id']}",
                home_goals=m["home_score"], away_goals=m["away_score"], home_formation=home_f, away_formation=away_f,
                home_manager_id=mgr["home"], away_manager_id=mgr["away"],
                referee=(m.get("referee") or {}).get("name"), venue=(m.get("stadium") or {}).get("name"),
                source_ids=SourceIds(statsbomb=str(m["match_id"]))))
            if events:
                shots.extend(shots_from_events(key, events, home["home_team_id"]))
            lineup = _load(_raw(cfg, "lineups", f"{m['match_id']}.json"))
            if lineup:
                rows, ps = lineup_rows(key, lineup)
                lineups.extend(rows)
                for p in ps:
                    players.setdefault(p.player_id, p)

    return {"matches": matches, "shots": shots, "lineups": lineups, "players": list(players.values()),
            "clubs": list(clubs.values()), "managers": list(managers.values())}


__all__ = ["fetch", "normalize", "iter_events", "iter_three_sixty", "iter_match_events", "match_list",
           "to_metres", "formation_text", "season_label", "shots_from_events", "lineup_rows"]
