"""Understat: shots with xG, per-match PPDA / deep completions, rosters.

Access path (checked October 2026): the site no longer embeds ``JSON.parse('...')``
blobs in its HTML pages. Data comes from two JSON endpoints that answer only when
the request carries ``X-Requested-With: XMLHttpRequest`` (otherwise an HTML 404):

    https://understat.com/getLeagueData/<League>/<year>   teams (+ per-match history), players, dates
    https://understat.com/getMatchData/<match id>         rosters (+ roster ids), shots

Raw files, exactly as received, under ``raw/understat``:

    league_<League>_<year>.json     one per league-season
    match_<id>.json                 one per finished match

Season "2023-24" is Understat year 2023. Match files are immutable once the match
is finished, so ``refresh`` only re-downloads the league files (to pick up new
fixtures and results) and still fetches any match file that is missing.

Conventions worth knowing:
* Understat shot coordinates are 0..1 and already oriented from the shooter's
  point of view (both teams attack towards X=1), so x = X * 105, y = Y * 68.
* Times are UTC (``kickoff_utc``).
* Ids are provisional: ``understat:<team id>`` / ``understat:<player id>``.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import FetchError, fetch_json
from matchlens.leaguedata.schema import (
    UNDERSTAT_LEAGUE,
    Club,
    League,
    LineupEntry,
    Match,
    Player,
    PlayerMatch,
    Shot,
    SourceIds,
    TeamMatchStats,
)

log = logging.getLogger(__name__)

SOURCE = "understat"
BASE = "https://understat.com"
HEADERS = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*"}

_LEAGUE_BY_CODE = {code: league for league, code in UNDERSTAT_LEAGUE.items()}
_COUNTRY = {League.EPL: "England", League.LA_LIGA: "Spain", League.SERIE_A: "Italy",
            League.BUNDESLIGA: "Germany", League.LIGUE_1: "France"}

RESULT = {"Goal": "goal", "SavedShot": "saved", "BlockedShot": "blocked", "MissedShots": "missed",
          "ShotOnPost": "post", "OwnGoal": "own_goal"}
SITUATION = {"OpenPlay": "open_play", "FromCorner": "corner", "SetPiece": "set_piece",
             "DirectFreekick": "direct_free_kick", "Penalty": "penalty"}
BODY_PART = {"RightFoot": "right_foot", "LeftFoot": "left_foot", "Head": "head", "OtherBodyPart": "other"}

PITCH_LENGTH, PITCH_WIDTH = 105.0, 68.0


# ------------------------------------------------------------------ fetching


def season_year(season: str) -> int:
    """'2023-24' -> 2023 (Understat names a season by the year it starts)."""
    return int(season[:4])


def season_label(year: int) -> str:
    return f"{year}-{(year + 1) % 100:02d}"


def league_path(cfg: Config, code: str, year: int):
    return cfg.raw_dir(SOURCE) / f"league_{code}_{year}.json"


def match_path(cfg: Config, match_id: str | int):
    return cfg.raw_dir(SOURCE) / f"match_{match_id}.json"


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int:
    """Download league files and the match file of every finished match.

    ``limit`` caps the match files fetched per league-season (smoke tests).
    A league-season that fails (e.g. a season Understat does not have yet) is
    logged and skipped.
    """
    new = 0
    for league in leagues:
        code = UNDERSTAT_LEAGUE.get(league)
        if code is None:
            continue
        for season in seasons:
            year = season_year(season)
            path = league_path(cfg, code, year)
            existed = path.exists()
            try:
                data = fetch_json(f"{BASE}/getLeagueData/{code}/{year}", path, cfg,
                                  refresh=refresh, headers=HEADERS)
            except (FetchError, json.JSONDecodeError) as exc:
                log.warning("understat %s %s: %s", code, season, exc)
                continue
            new += int(refresh or not existed)
            ids = [str(m["id"]) for m in data.get("dates", []) if m.get("isResult")]
            for mid in ids[:limit] if limit else ids:
                mpath = match_path(cfg, mid)
                if mpath.exists():
                    continue
                try:
                    fetch_json(f"{BASE}/getMatchData/{mid}", mpath, cfg, headers=HEADERS)
                    new += 1
                except (FetchError, json.JSONDecodeError) as exc:
                    log.warning("understat match %s: %s", mid, exc)
            log.info("understat %s %s: %d finished matches", code, season, len(ids))
    return new


# ------------------------------------------------------------------ normalising


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _name(value: str) -> str:
    return html.unescape(value).strip()


def _ratio(d: dict[str, Any] | None) -> float | None:
    """PPDA = opponent passes (att) / our defensive actions (def)."""
    if not d:
        return None
    att, dfn = _f(d.get("att")), _f(d.get("def"))
    return round(att / dfn, 3) if att is not None and dfn else None


def to_metres(x: Any, y: Any) -> tuple[float, float]:
    """Understat 0..1 coordinates (attacking towards 1) -> metres on 105 x 68."""
    return round(float(x) * PITCH_LENGTH, 2), round(float(y) * PITCH_WIDTH, 2)


def scoring_side(shot: dict[str, Any]) -> str | None:
    """'h' / 'a' = the side whose score goes up because of this shot, else None."""
    if shot["result"] == "Goal":
        return shot["h_a"]
    if shot["result"] == "OwnGoal":
        return OWN_GOAL_CREDIT[shot["h_a"]]
    return None


# On an own goal ``h_a`` is the side of the player who put it in (checked against
# final scores: a defender of the away side with h_a="a" gave the home side a goal),
# so the goal counts for the other side.
OWN_GOAL_CREDIT = {"h": "a", "a": "h"}


def _lineup_minutes(roster: dict[str, dict[str, Any]]) -> dict[str, tuple[int, int | None]]:
    """roster row id -> (minute_on, minute_off). Substitution chains are followed:
    a starter lists the sub who replaced him in ``roster_in``, a sub lists the
    player he replaced in ``roster_out``."""
    out: dict[str, tuple[int, int | None]] = {}

    def resolve(rid: str, depth: int = 0) -> tuple[int, int | None]:
        if rid in out:
            return out[rid]
        row = roster[rid]
        played = _i(row.get("time"))
        replaced = str(row.get("roster_out") or "0")
        on = 0
        if row.get("position") == "Sub":
            on = resolve(replaced, depth + 1)[1] or 0 if replaced in roster and depth < 6 else max(0, 90 - played)
        off = on + played if str(row.get("roster_in") or "0") != "0" else None
        out[rid] = (on, off)
        return out[rid]

    for rid in roster:
        resolve(rid)
    return out


def normalize(cfg: Config) -> dict[str, list[Any]]:
    """Rows from the raw files on disk (no network)."""
    rows: dict[str, list[Any]] = {k: [] for k in (
        "matches", "lineups", "shots", "team_match_stats", "player_matches", "players", "clubs")}
    clubs: dict[str, Club] = {}
    players: dict[str, Player] = {}
    seen_matches: set[str] = set()
    for lpath in sorted(cfg.raw_dir(SOURCE).glob("league_*_*.json")):
        m = re.fullmatch(r"league_(.+)_(\d{4})\.json", lpath.name)
        league = _LEAGUE_BY_CODE.get(m.group(1)) if m else None
        if league is None:
            continue
        season = season_label(int(m.group(2)))
        data = json.loads(lpath.read_text(encoding="utf-8"))
        history = {(tid, h["date"]): h for tid, t in data.get("teams", {}).items()
                   for h in t.get("history", [])}
        for item in data.get("dates", []):
            if not item.get("isResult") or str(item["id"]) in seen_matches:
                continue
            seen_matches.add(str(item["id"]))
            _match_rows(cfg, rows, clubs, players, history, league, season, item)
    rows["clubs"] = list(clubs.values())
    rows["players"] = list(players.values())
    return rows


def _match_rows(cfg: Config, rows: dict[str, list[Any]], clubs: dict[str, Club],
                players: dict[str, Player], history: dict[tuple[str, str], dict[str, Any]],
                league: League, season: str, item: dict[str, Any]) -> None:
    mid = str(item["id"])
    stamp = item["datetime"]
    date = stamp[:10]
    hname, aname = _name(item["h"]["title"]), _name(item["a"]["title"])
    hid, aid = f"{SOURCE}:{item['h']['id']}", f"{SOURCE}:{item['a']['id']}"
    key = f"{league.value}:{season}:{date}:{_slug(hname)}-{_slug(aname)}"
    for tid, name, short in ((hid, hname, item["h"].get("short_title")),
                             (aid, aname, item["a"].get("short_title"))):
        clubs.setdefault(tid, Club(team_id=tid, name=name, short_name=short, country=_COUNTRY.get(league),
                                   source_ids=SourceIds(understat=tid.split(":", 1)[1])))
    hg, ag = _i(item["goals"]["h"]), _i(item["goals"]["a"])
    hxg, axg = _f(item["xG"]["h"]), _f(item["xG"]["a"])
    rows["matches"].append(Match(
        match_key=key, league=league, season=season, date=date,
        kickoff_utc=stamp.replace(" ", "T") + "Z", home_id=hid, away_id=aid,
        home_goals=hg, away_goals=ag, home_xg=hxg, away_xg=axg,
        source_ids=SourceIds(understat=mid)))

    mfile = match_path(cfg, mid)
    detail = json.loads(mfile.read_text(encoding="utf-8")) if mfile.exists() else None
    rosters = (detail or {}).get("rosters") or {}
    sums = {"h": [0.0, 0.0], "a": [0.0, 0.0]}  # xg_chain, xg_buildup

    for side, team_id in (("h", hid), ("a", aid)):
        roster = rosters.get(side) or {}
        if roster:
            minutes = _lineup_minutes(roster)
        for rid, r in roster.items():
            pid = f"{SOURCE}:{r['player_id']}"
            played = _i(r.get("time"))
            sums[side][0] += _f(r.get("xGChain")) or 0.0
            sums[side][1] += _f(r.get("xGBuildup")) or 0.0
            players.setdefault(pid, Player(player_id=pid, name=_name(r["player"]),
                                           source_ids=SourceIds(understat=str(r["player_id"]))))
            if played <= 0:
                continue
            rows["player_matches"].append(PlayerMatch(
                match_key=key, player_id=pid, team_id=team_id, minutes=played,
                goals=_i(r.get("goals")), assists=_i(r.get("assists")), shots=_i(r.get("shots")),
                key_passes=_i(r.get("key_passes")), xg=_f(r.get("xG")), xa=_f(r.get("xA")),
                xg_chain=_f(r.get("xGChain")), xg_buildup=_f(r.get("xGBuildup"))))
            on, off = minutes[rid]
            rows["lineups"].append(LineupEntry(
                match_key=key, team_id=team_id, player_id=pid, starter=r.get("position") != "Sub",
                position=r.get("position"), minute_on=on, minute_off=off))

    for side, team_id, tid in (("h", hid, item["h"]["id"]), ("a", aid, item["a"]["id"])):
        h = history.get((str(tid), stamp))
        if h is None:
            continue
        rows["team_match_stats"].append(TeamMatchStats(
            match_key=key, team_id=team_id, is_home=side == "h",
            goals=_i(h.get("scored")), xg=_f(h.get("xG")), npxg=_f(h.get("npxG")),
            ppda=_ratio(h.get("ppda")), ppda_allowed=_ratio(h.get("ppda_allowed")),
            deep=_i(h.get("deep")), deep_allowed=_i(h.get("deep_allowed")),
            xg_chain=round(sums[side][0], 4) if rosters else None,
            xg_buildup=round(sums[side][1], 4) if rosters else None))

    if detail:
        rows["shots"].extend(_shots(detail, key, hid, aid, rosters))


def _shots(detail: dict[str, Any], key: str, hid: str, aid: str,
           rosters: dict[str, dict[str, Any]]) -> list[Shot]:
    raw = [dict(s) for side in ("h", "a") for s in (detail.get("shots") or {}).get(side, [])]
    raw.sort(key=lambda s: (_i(s["minute"]), _i(s["id"])))
    by_name = {side: {_name(r["player"]): f"{SOURCE}:{r['player_id']}" for r in (rosters.get(side) or {}).values()}
               for side in ("h", "a")}
    score = {"h": 0, "a": 0}
    out: list[Shot] = []
    for s in raw:
        side = s["h_a"]
        x, y = to_metres(s["X"], s["Y"])
        assist = s.get("player_assisted")
        last = s.get("lastAction")
        out.append(Shot(
            shot_id=f"{SOURCE}:{s['id']}", match_key=key, team_id=hid if side == "h" else aid,
            player_id=f"{SOURCE}:{s['player_id']}",
            assist_player_id=by_name[side].get(_name(assist)) if assist else None,
            minute=_i(s["minute"]), x=x, y=y, xg=_f(s["xG"]) or 0.0,
            result=RESULT.get(s["result"], "missed"),
            situation=SITUATION.get(s["situation"], "open_play"),
            body_part=BODY_PART.get(s["shotType"], "other"),
            last_action=None if not last or last == "None" else last,
            home_score_before=score["h"], away_score_before=score["a"]))
        credited = scoring_side(s)
        if credited:
            score[credited] += 1
    return out
