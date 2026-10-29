"""A trimmed, typed view of one FotMob match for the match page, read from the cached file only.

The shadow watcher saves FotMob's whole ``matchDetails`` response each minute under
``backend/data/local/fotmob/<fotmob_id>.json`` (about 220 KB). This module never makes a request: it reads that file
(``matchlens.local.fotmob.load_details``) and keeps what a page can draw. Left out on purpose: news, betting odds, the
poll, highlights, the ticker, SEO and navigation.

    view = load_view("fotmob:6275371")      # None when no file is cached
    view = build_view(details, "fotmob:6275371")   # from a response already in memory (the unit tests)

Shape (every section is ``None`` when FotMob has not published it for the match, for example no xG tier)::

    {match_id, fotmob_id, fetched_at,
     header:  {home, away: {id, name, score, colour, text_colour, red_cards}, score_str,
               status: {state: scheduled|live|half_time|extra_time|finished|cancelled, label, minute, added,
                        clock, period},
               competition, round, kickoff_utc, venue: {name, city, country, capacity, surface}, referee,
               attendance, weather: {temperature_c, description, wind_kph, wind_dir, humidity, rain_mm, cloud_cover,
               icon_code}},
     penalties: in header: {home, away} after a shoot-out (else null); status.text is FotMob's own ("Pen 6 - 5").
     shootout:[{order, team_side, player: {id,name}, scored, score: [home, away]}] or null; its kicks are in neither
               events nor shotmap (FotMob's shot map lists them with period "PenaltyShootout"),
     events:  [{type: goal|card|sub|half, minute, added, team: home|away|None, ...}]   oldest first,
     stats:   {all, first_half, second_half: [{title, key, rows: [{title, key, home, away, home_text, away_text,
               home_total, away_total, format, highlighted}]}]},
     momentum:[{minute, value}],               value > 0 favours the home side, < 0 the away side (FotMob's own scale)
     shotmap: [{id, team, player_id, player, minute, added, period, x, y, xg, xgot, outcome, event_type, situation,
               shot_type, on_target, blocked, own_goal}],
     lineups: {home, away: {formation, rating, coach, starters, subs, unavailable}},
     attacking_zones: {home, away: {total, first_half, second_half: {left, center, right}}},
     h2h:     {summary: {home_wins, draws, away_wins}, last: [5 finished matches, newest first]},
     team_form: {home, away: [last matches, newest first]}}

Shot coordinates. FotMob draws every shot on one 105 x 68 pitch as if the shooting side attacked the goal at
``x = 105`` (the penalty spot is ``(94, 34)``, and the shots of both teams cluster at ``x`` 66 to 105 whatever the
side), with ``y`` across the pitch and ``y = 34`` on the centre line. That is a per-shot normalisation, not the
real direction of play. We convert to our centre-origin metres (the frame convention: origin at the centre spot,
105 x 68, the home side attacking ``+x``):

    home shot:  x_m = x - 52.5,  y_m = y - 34       (attacks +x, so the shot sits near x = +40 to +52.5)
    away shot:  x_m = 52.5 - x,  y_m = 34 - y       (a half-turn: the away side attacks -x)

The half-turn is the same one ``matchlens.local.fotmob`` applies to away shots (``105 - x, 68 - y``) shifted to the
centre. The sign of ``y`` follows from that rotation; it cannot be checked against FotMob's own picture, so a
left/right flip on the page would be the one thing to look at.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from matchlens.local import fotmob

log = logging.getLogger(__name__)

PITCH_L, PITCH_W = 105.0, 68.0
H2H_LAST = 5
POSITIONS = {0: "GK", 1: "DEF", 2: "MID", 3: "FWD"}
SHOT_OUTCOMES = {"Goal": "goal", "AttemptSaved": "saved", "Miss": "off_target", "Post": "post"}
PLAYER_EVENTS = {"goal": "goals", "assist": "assists", "yellowCard": "yellow_cards", "redCard": "red_cards",
                 "yellowRedCard": "red_cards", "ownGoal": "own_goals"}
CARDS = {"Yellow": "yellow", "Red": "red", "YellowRed": "second_yellow"}
_CACHE: dict[tuple[str, int], dict[str, Any]] = {}  # (path, mtime_ns) -> view; reparsing 220 KB each poll is waste


# ------------------------------------------------------------------ small helpers


def _num(v: Any) -> float | int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int | float):
        return v
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return None if n is None else int(n)


def _round(v: Any, n: int = 3) -> float | None:
    x = _num(v)
    return None if x is None else round(float(x), n)


def _snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _snake_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {_snake(str(k)): _snake_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_snake_keys(v) for v in obj]
    return obj


def _section(name: str, fn: Callable[[], Any]) -> Any:
    """One malformed section is dropped (``None``), not allowed to take the whole view down."""
    try:
        return fn()
    except Exception:
        log.exception("fotmob view: section %r failed", name)
        return None


def _side(is_home: Any) -> str | None:
    return None if is_home is None else ("home" if is_home else "away")


def parse_match_id(match_id: str) -> int | None:
    """``fotmob:6275371`` or ``6275371`` to the FotMob id; anything else (a video match) is ``None``."""
    m = re.fullmatch(r"(?:fotmob:)?(\d+)", match_id.strip())
    return int(m.group(1)) if m else None


def shot_xy(x: Any, y: Any, home: bool) -> tuple[float, float] | tuple[None, None]:
    """FotMob's per-shot pitch (attacks ``x = 105``, 105 x 68) to centre-origin metres, home attacking ``+x``."""
    fx, fy = _num(x), _num(y)
    if fx is None or fy is None:
        return None, None
    if home:
        return round(fx - PITCH_L / 2, 2), round(fy - PITCH_W / 2, 2)
    return round(PITCH_L / 2 - fx, 2), round(PITCH_W / 2 - fy, 2)


# ------------------------------------------------------------------ sections


def _teams(details: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    general = details.get("general") or {}
    teams = (details.get("header") or {}).get("teams") or []
    home = dict(general.get("homeTeam") or (teams[0] if teams else {}))
    away = dict(general.get("awayTeam") or (teams[1] if len(teams) > 1 else {}))
    return home, away


def _live_minute(short: Any) -> int | None:
    m = re.search(r"\d+", str(short or ""))
    return int(m.group()) if m else None


def _state(status: dict[str, Any]) -> str:
    halfs = status.get("halfs") or {}
    if status.get("cancelled"):
        return "cancelled"
    if status.get("finished"):
        return "finished"
    if not status.get("started"):
        return "scheduled"
    if halfs.get("firstExtraHalfStarted") or halfs.get("secondExtraHalfStarted"):
        return "extra_time"
    if halfs.get("firstHalfEnded") and not halfs.get("secondHalfStarted"):
        return "half_time"
    return "live"


def _period(status: dict[str, Any]) -> int | None:
    halfs = status.get("halfs") or {}
    if not status.get("started"):
        return None
    if halfs.get("secondExtraHalfStarted"):
        return 4
    if halfs.get("firstExtraHalfStarted"):
        return 3
    if halfs.get("secondHalfStarted"):
        return 2
    return 1


def build_header(details: dict[str, Any]) -> dict[str, Any]:
    general = details.get("general") or {}
    head = details.get("header") or {}
    status = head.get("status") or {}
    facts = ((details.get("content") or {}).get("matchFacts")) or {}
    info = facts.get("infoBox") or {}
    home, away = _teams(details)
    scores = {t.get("id"): t.get("score") for t in head.get("teams") or []}
    colours = general.get("teamColors") or {}
    light, dark = colours.get("lightMode") or {}, colours.get("darkMode") or {}
    font = colours.get("fontLightMode") or {}
    live = status.get("liveTime") or {}
    reason = status.get("reason") or {}
    stadium = info.get("Stadium") or {}
    tournament = info.get("Tournament") or {}
    referee = info.get("Referee") or {}
    weather = (details.get("content") or {}).get("weather") or {}

    def team(raw: dict[str, Any], key: str, reds: Any) -> dict[str, Any]:
        return {"id": _int(raw.get("id")), "name": raw.get("name"), "score": _int(scores.get(raw.get("id"))),
                "colour": light.get(key), "colour_dark": dark.get(key), "text_colour": font.get(key),
                "red_cards": _int(reds) or 0}

    state = _state(status)
    pens = reason.get("penalties") if isinstance(reason.get("penalties"), list) and len(reason["penalties"]) == 2 else None
    label = (reason.get("short") if state == "finished" else None) or re.sub(r"[^\w:+ ’']", "", str(live.get("short") or "")) or None
    return {
        "home": team(home, "home", status.get("numberOfHomeRedCards")),
        "away": team(away, "away", status.get("numberOfAwayRedCards")),
        "score_str": status.get("scoreStr"),
        "penalties": {"home": _int(pens[0]), "away": _int(pens[1])} if pens else None,
        "status": {"state": state, "label": label, "text": reason.get("long") or None, "minute": _live_minute(live.get("short")) if state != "finished" else None,
                   "added": _int(live.get("addedTime")), "clock": live.get("long") or None, "period": _period(status)},
        "competition": tournament.get("leagueName") or general.get("leagueName"),
        "round": tournament.get("roundName") or general.get("leagueRoundName"),
        "kickoff_utc": (info.get("Match Date") or {}).get("utcTime") or general.get("matchTimeUTCDate") or status.get("utcTime"),
        "venue": {"name": stadium.get("name"), "city": stadium.get("city"), "country": stadium.get("country"),
                  "capacity": _int(stadium.get("capacity")), "surface": stadium.get("surface")} if stadium else None,
        "referee": {"name": referee.get("text"), "country": referee.get("country")} if referee.get("text") else None,
        "attendance": _int(info.get("Attendance")),
        "weather": {"temperature_c": _num(weather.get("temperature")), "description": weather.get("description"),
                    "wind_kph": _num(weather.get("windSpeed")), "wind_dir": weather.get("windDirectionCardinal"),
                    "humidity": _num(weather.get("relativeHumidity")), "rain_mm": _num(weather.get("precipitation")),
                    "cloud_cover": _num(weather.get("cloudCover")), "icon_code": _int(weather.get("iconCode"))}
        if weather else None,
    }


def _person(p: Any) -> dict[str, Any] | None:
    if not isinstance(p, dict) or not p.get("name"):
        return None
    return {"id": _int(p.get("id")), "name": p["name"]}


def build_events(details: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (((details.get("content") or {}).get("matchFacts") or {}).get("events") or {}).get("events") or []
    out: list[dict[str, Any]] = []
    for e in raw:
        if e.get("isPenaltyShootoutEvent") or e.get("type") == "PenaltyShootout":
            continue  # the shoot-out is not part of the match: see ``build_shootout``
        kind = e.get("type")
        base = {"minute": _int(e.get("time")), "added": _int(e.get("overloadTime")),
                "team": _side(e.get("isHome")) if kind_has_team(kind) else None}
        if kind == "Goal":
            shot = e.get("shotmapEvent") or {}
            score = e.get("newScore") or []
            assist = e.get("assistStr")
            out.append({**base, "type": "goal", "player": _person(e.get("player") or {"id": e.get("playerId"), "name": e.get("fullName")}),
                        "assist": {"id": _int(e.get("assistPlayerId")), "name": e.get("assistInput") or re.sub(r"^assist by\s+", "", str(assist), flags=re.I)} if assist else None,
                        "penalty": e.get("goalDescriptionKey") == "penalty", "own_goal": bool(e.get("ownGoal")),
                        "score": [_int(s) for s in score] if len(score) == 2 else None, "xg": _round(shot.get("expectedGoals"))})
        elif kind == "Card":
            out.append({**base, "type": "card", "player": _person(e.get("player")), "card": CARDS.get(str(e.get("card")), str(e.get("card")).lower()),
                        "reason": e.get("cardDescription") or e.get("cardReason")})
        elif kind == "Substitution":
            swap = e.get("swap") or []  # [player on, player off]
            out.append({**base, "type": "sub", "player_in": _person(swap[0]) if swap else None,
                        "player_out": _person(swap[1]) if len(swap) > 1 else None, "injury": bool(e.get("injuredPlayerOut"))})
        elif kind == "Half":
            out.append({**base, "type": "half", "label": e.get("halfStrShort")})
    out.reverse()  # FotMob lists the newest first (and puts half-time after the stoppage-time goals); the page wants the match in order
    return out


def kind_has_team(kind: Any) -> bool:
    return kind in ("Goal", "Card", "Substitution")


def build_shootout(details: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The penalty shoot-out, kick by kick, in order; ``None`` when the match had none. The kicks are kept out of
    ``events`` and ``shotmap``: they are not shots of the match and their xG (0.79 each) is not the match's."""
    raw = (((details.get("content") or {}).get("matchFacts") or {}).get("events") or {}).get("penaltyShootoutEvents") or []
    out = []
    for i, e in enumerate(raw):
        score = e.get("penShootoutScore") or []
        out.append({"order": i + 1, "team_side": _side(e.get("isHome")), "scored": e.get("type") == "Goal",
                    "player": _person(e.get("player") or {"id": e.get("playerId"), "name": e.get("nameStr")}),
                    "score": [_int(x) for x in score] if len(score) == 2 else None})
    return out or None


def build_stats(details: dict[str, Any]) -> dict[str, list[dict[str, Any]]] | None:
    periods = (((details.get("content") or {}).get("stats")) or {}).get("Periods") or {}
    if not periods:
        return None

    def rows(group: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for s in group.get("stats") or []:
            if s.get("type") == "title":
                continue
            vals, raw = s.get("stats") or [None, None], s.get("rawStats") or [None, None]
            raw = [r if isinstance(r, dict) else {} for r in raw] + [{}, {}]
            out.append({"title": s.get("title"), "key": s.get("key"),
                        "home": _num(raw[0].get("value")), "away": _num(raw[1].get("value")),
                        "home_text": None if vals[0] is None else str(vals[0]),
                        "away_text": None if len(vals) < 2 or vals[1] is None else str(vals[1]),
                        "home_total": _num(raw[0].get("total")), "away_total": _num(raw[1].get("total")),
                        "format": s.get("format"), "highlighted": s.get("highlighted")})
        return out

    out = {}
    for src, dst in (("All", "all"), ("FirstHalf", "first_half"), ("SecondHalf", "second_half")):
        groups = (periods.get(src) or {}).get("stats") or []
        out[dst] = [{"title": g.get("title"), "key": g.get("key"), "rows": rows(g)} for g in groups]
    return out


def build_momentum(details: dict[str, Any]) -> list[dict[str, Any]] | None:
    main = ((details.get("content") or {}).get("momentum") or {}).get("main") or {}
    data = main.get("data")
    if not data:
        return None
    return [{"minute": _num(m.get("minute")), "value": _num(m.get("value"))} for m in data if m.get("minute") is not None]


def build_shotmap(details: dict[str, Any]) -> list[dict[str, Any]] | None:
    shots = (((details.get("content") or {}).get("shotmap")) or {}).get("shots")
    if shots is None:
        return None
    home, away = _teams(details)
    out = []
    for s in shots:
        if s.get("period") == "PenaltyShootout":
            continue  # shoot-out kicks are neither shots nor xG of the match
        team_id = s.get("teamId")
        is_home = team_id == home.get("id")
        if not is_home and team_id != away.get("id"):
            continue
        x, y = shot_xy(s.get("x"), s.get("y"), is_home)
        kind = str(s.get("eventType") or "")
        outcome = SHOT_OUTCOMES.get(kind, _snake(kind))
        if outcome == "saved" and s.get("isBlocked"):
            outcome = "blocked"
        out.append({"id": s.get("id"), "team": "home" if is_home else "away", "player_id": _int(s.get("playerId")),
                    "player": s.get("fullName") or s.get("playerName"), "minute": _int(s.get("min")),
                    "added": _int(s.get("minAdded")), "period": s.get("period"), "x": x, "y": y,
                    "xg": _round(s.get("expectedGoals")), "xgot": _round(s.get("expectedGoalsOnTarget")),
                    "outcome": outcome, "event_type": kind or None, "situation": s.get("situation"),
                    "shot_type": s.get("shotType"), "on_target": s.get("isOnTarget"), "blocked": bool(s.get("isBlocked")),
                    "own_goal": bool(s.get("isOwnGoal"))})
    out.sort(key=lambda r: ((r["minute"] if r["minute"] is not None else 999), r["added"] or 0))
    return out


def _player(p: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    perf = p.get("performance") or {}
    counts = {v: 0 for v in dict.fromkeys(PLAYER_EVENTS.values())}
    for e in perf.get("events") or []:
        key = PLAYER_EVENTS.get(e.get("type"))
        if key:
            counts[key] += 1
    top = {}
    for group in (stats or {}).get("stats") or []:
        if group.get("key") == "top_stats":
            top = group.get("stats") or {}
    precise = ((top.get("FotMob rating") or {}).get("stat") or {}).get("value")
    sub = [{"type": e.get("type"), "minute": _int(e.get("time"))} for e in perf.get("substitutionEvents") or []]
    layout = p.get("horizontalLayout") or {}
    return {"id": _int(p.get("id")), "name": p.get("name"), "shirt": _int(p.get("shirtNumber")),
            "position": POSITIONS.get(_int(p.get("usualPlayingPositionId")) if p.get("usualPlayingPositionId") is not None else -1),
            "position_id": _int(p.get("positionId")), "age": _int(p.get("age")), "country": p.get("countryCode"),
            "rating": _round(precise if precise is not None else perf.get("rating"), 2), **counts,
            "subbed": sub or None,
            "layout": {"x": _round(layout.get("x")), "y": _round(layout.get("y"))} if layout else None,
            "xg": _round(((top.get("Expected goals (xG)") or {}).get("stat") or {}).get("value"), 2),
            "xa": _round(((top.get("Expected assists (xA)") or {}).get("stat") or {}).get("value"), 2)}


def build_lineups(details: dict[str, Any]) -> dict[str, Any] | None:
    content = details.get("content") or {}
    lineup = content.get("lineup") or {}
    if not lineup or not (lineup.get("homeTeam") or lineup.get("awayTeam")):
        return None
    pstats = content.get("playerStats") or {}

    def team(raw: dict[str, Any]) -> dict[str, Any]:
        coach = raw.get("coach") or {}
        return {"id": _int(raw.get("id")), "name": raw.get("name"), "formation": raw.get("formation"),
                "rating": _round(raw.get("rating"), 2),
                "coach": {"id": _int(coach.get("id")), "name": coach.get("name"), "country": coach.get("countryCode")} if coach.get("name") else None,
                "starters": [_player(p, pstats.get(str(p.get("id")))) for p in raw.get("starters") or []],
                "subs": [_player(p, pstats.get(str(p.get("id")))) for p in raw.get("subs") or []],
                "unavailable": [{"id": _int(p.get("id")), "name": p.get("name"),
                                 "reason": (p.get("unavailability") or {}).get("type"),
                                 "expected_return": (p.get("unavailability") or {}).get("expectedReturn")}
                                for p in raw.get("unavailable") or []]}

    return {"home": team(lineup.get("homeTeam") or {}), "away": team(lineup.get("awayTeam") or {}), "source": lineup.get("lineupType")}


def build_attacking_zones(details: dict[str, Any]) -> dict[str, Any] | None:
    zones = (details.get("content") or {}).get("attackingZones")
    return _snake_keys(zones) if zones else None


def _score_pair(text: Any) -> tuple[int | None, int | None]:
    m = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str(text or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def build_h2h(details: dict[str, Any]) -> dict[str, Any] | None:
    h2h = (details.get("content") or {}).get("h2h") or {}
    if not h2h:
        return None
    home, _ = _teams(details)
    summary = h2h.get("summary") or []
    played = [m for m in h2h.get("matches") or [] if (m.get("status") or {}).get("finished") and not (m.get("status") or {}).get("cancelled")]
    played.sort(key=lambda m: (m.get("status") or {}).get("utcTime") or "", reverse=True)
    last = []
    for m in played[:H2H_LAST]:
        status, h, a = m.get("status") or {}, m.get("home") or {}, m.get("away") or {}
        hg, ag = _score_pair(status.get("scoreStr"))
        mine_home = _int(h.get("id")) == home.get("id")  # is this fixture's home side the home side of that match
        for_us, against = (hg, ag) if mine_home else (ag, hg)
        result = None if for_us is None or against is None else ("W" if for_us > against else "L" if for_us < against else "D")
        last.append({"date": status.get("utcTime"), "competition": (m.get("league") or {}).get("name"),
                     "home": {"id": _int(h.get("id")), "name": h.get("name")}, "away": {"id": _int(a.get("id")), "name": a.get("name")},
                     "home_goals": hg, "away_goals": ag, "score": status.get("scoreStr"), "result_for_home": result})
    return {"summary": {"home_wins": _int(summary[0]), "draws": _int(summary[1]), "away_wins": _int(summary[2])}
            if len(summary) == 3 else None, "last": last}


def build_team_form(details: dict[str, Any]) -> dict[str, list[dict[str, Any]]] | None:
    form = (((details.get("content") or {}).get("matchFacts")) or {}).get("teamForm")
    if not form or len(form) < 2:
        return None

    def one(m: dict[str, Any]) -> dict[str, Any]:
        tip, h, a = m.get("tooltipText") or {}, m.get("home") or {}, m.get("away") or {}
        ours_home = bool(h.get("isOurTeam"))
        hg, ag = _int(tip.get("homeScore")), _int(tip.get("awayScore"))
        opp = a if ours_home else h
        return {"result": m.get("resultString"), "date": (m.get("date") or {}).get("utcTime") or tip.get("utcTime"),
                "opponent": {"id": _int(opp.get("id")), "name": opp.get("name")}, "venue": "H" if ours_home else "A",
                "goals_for": hg if ours_home else ag, "goals_against": ag if ours_home else hg, "score": m.get("score")}

    return {"home": [one(m) for m in form[0] or []], "away": [one(m) for m in form[1] or []]}


# ------------------------------------------------------------------ entry points


def build_view(details: dict[str, Any], match_id: str, fetched_at: str | None = None) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "fotmob_id": parse_match_id(match_id),
        "fetched_at": fetched_at,
        "header": _section("header", lambda: build_header(details)),
        "events": _section("events", lambda: build_events(details)),
        "shootout": _section("shootout", lambda: build_shootout(details)),
        "stats": _section("stats", lambda: build_stats(details)),
        "momentum": _section("momentum", lambda: build_momentum(details)),
        "shotmap": _section("shotmap", lambda: build_shotmap(details)),
        "lineups": _section("lineups", lambda: build_lineups(details)),
        "attacking_zones": _section("attacking_zones", lambda: build_attacking_zones(details)),
        "h2h": _section("h2h", lambda: build_h2h(details)),
        "team_form": _section("team_form", lambda: build_team_form(details)),
    }


def load_view(match_id: str, cache_dir: Path | None = None) -> dict[str, Any] | None:
    """The view of ``fotmob:<id>`` from the cached file; ``None`` when the id is not FotMob's or nothing is cached."""
    fid = parse_match_id(match_id)
    if fid is None:
        return None
    path = (cache_dir or fotmob.CACHE_DIR) / f"{fid}.json"
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    key = (str(path), stat.st_mtime_ns)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    details = fotmob.load_details(path=path)
    if not details:
        return None
    view = build_view(details, f"fotmob:{fid}", datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"))
    for stale in [k for k in _CACHE if k[0] == key[0]]:
        del _CACHE[stale]
    _CACHE[key] = view
    return view
