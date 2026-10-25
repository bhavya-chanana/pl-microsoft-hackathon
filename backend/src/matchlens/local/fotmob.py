"""FotMob feed adapter: real matches, live or finished, through the whole pipeline.

It runs one real match through the engine, agents and fan features, and is the
event-level data clock for the live companion and the shadow predictions
(``matchlens.predict.shadow``).

FotMob's web app polls ``/api/data/matchDetails?matchId=<id>`` every 10 s
during a live match. That JSON has lineups, the timeline (goals, cards,
substitutions, halves), a shot map with xG and per-team and per-player
totals, but no passes and no positions. So a FotMob run has no tracking: the
2D pitch stays empty and distance and speed are unavailable.

Mapping to the Matchlens schema:

- teams and players come from the lineup (ids ``<CODE>-<fotmob id>``);
- every shot-map shot becomes a ``shot`` (FotMob normalises shots to attack
  x = 105; away shots are mirrored because our away side attacks x = 0), and a
  saved shot also a ``save`` for the keeper;
- goals, cards, substitutions and the halves map one to one; kick-offs are
  added for both halves;
- a scorer, assister or substitute changed between polls becomes a
  ``correction`` (an event FotMob removes, such as a goal ruled out by VAR,
  is logged and kept, since the engine cannot retract events).

FotMob's terms forbid scraping and bulk retrieval. Keep usage polite: polled
no faster than every 30 s by default, responses cached under
``backend/data/local/`` (gitignored), off unless ``MATCHLENS_FOTMOB=1``, and
the raw responses are never committed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from matchlens.domain.models import (
    PITCH_LENGTH,
    PITCH_WIDTH,
    Event,
    EventType,
    MatchMeta,
    Outcome,
    Player,
    Point,
    Position,
    Side,
    Team,
)
from matchlens.ingest.adapters import FeedItem, ReplayControl, paced

log = logging.getLogger(__name__)

API = "https://www.fotmob.com/api/data/matchDetails?matchId={id}"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "local" / "fotmob"
MIN_POLL_S = 10.0  # FotMob's own web app polls at this rate; never go faster
DEFAULT_POLL_S = 30.0
H1, H2 = 2700, 5400  # clock at the end of regulation in each half

POSITIONS = {0: Position.GK, 1: Position.DEF, 2: Position.MID, 3: Position.FWD}
SHOT_OUTCOMES = {"Goal": Outcome.GOAL, "AttemptSaved": Outcome.SAVED, "Miss": Outcome.OFF_TARGET,
                 "Post": Outcome.OFF_TARGET}
CARDS = {"Yellow": Outcome.YELLOW, "YellowRed": Outcome.SECOND_YELLOW, "Red": Outcome.RED}
BODY_PARTS = {"RightFoot": "right_foot", "LeftFoot": "left_foot", "Header": "head"}
PERIODS = {"FirstHalf": 1, "SecondHalf": 2}
# Feed order within the same moment: half-time before the second-half kick-off, a shot before
# its save and goal.
RANK = {EventType.HALF_TIME: 0, EventType.KICKOFF: 1, EventType.SHOT: 2, EventType.SAVE: 3, EventType.GOAL: 4,
        EventType.CARD: 5, EventType.SUBSTITUTION: 6, EventType.FULL_TIME: 7}


def enabled() -> bool:
    return os.getenv("MATCHLENS_FOTMOB") == "1"


# ------------------------------------------------------------------ fetching


def fetch_details(match_id: int, cache_dir: Path = CACHE_DIR) -> dict[str, Any]:
    """One request for one match; the response is cached locally for offline replays."""
    req = urllib.request.Request(API.format(id=int(match_id)), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        details = json.load(resp)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{int(match_id)}.json").write_text(json.dumps(details))
    return details


def load_details(match_id: int | None = None, path: Path | None = None,
                 cache_dir: Path = CACHE_DIR) -> dict[str, Any] | None:
    """A saved response, from an explicit path or the local cache."""
    target = path or (cache_dir / f"{int(match_id)}.json" if match_id is not None else None)
    if target and target.exists():
        return json.loads(target.read_text())
    return None


# ------------------------------------------------------------------ parsing


@dataclass
class Parsed:
    meta: MatchMeta
    events: list[Event]
    started: bool
    finished: bool
    stats: dict[str, Any] = field(default_factory=dict)


def _code(name: str, taken: str = "") -> str:
    letters = re.sub(r"[^A-Za-z]", "", name).upper() or "TEAM"
    code = letters[:3].ljust(3, "X")
    if code == taken:
        words = [w for w in re.split(r"\W+", name) if w]
        code = ("".join(w[0] for w in words).upper() + letters)[:3] if len(words) > 1 else letters[-3:]
        if code == taken:
            code = code[:2] + "2"
    return code


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _player(raw: dict[str, Any], team_id: str) -> Player:
    name = str(raw.get("name") or f"{raw.get('firstName', '')} {raw.get('lastName', '')}".strip() or raw["id"])
    if raw.get("positionId") == 11:
        position = Position.GK
    else:
        position = POSITIONS.get(_int(raw.get("usualPlayingPositionId"), 2), Position.MID)
    return Player(
        player_id=f"{team_id}-{raw['id']}",
        name=name,
        short_name=str(raw.get("lastName") or name.split()[-1]),
        shirt=_int(raw.get("shirtNumber")),
        position=position,
        role=position.value,
        nationality=str(raw.get("countryName") or ""),
    )


def build_meta(details: dict[str, Any]) -> MatchMeta:
    general, content = details["general"], details["content"]
    lineup = content.get("lineup") or {}
    colours = (general.get("teamColors") or {}).get("lightMode") or {}
    home_code = _code(general["homeTeam"]["name"])
    codes = {"home": home_code, "away": _code(general["awayTeam"]["name"], taken=home_code)}
    players: dict[str, Player] = {}
    teams: dict[str, Team] = {}
    for side in ("home", "away"):
        raw_team = lineup.get(f"{side}Team") or {}
        team_id = codes[side]
        starters = [_player(p, team_id) for p in raw_team.get("starters") or []]
        bench = [_player(p, team_id) for p in raw_team.get("subs") or []]
        players.update({p.player_id: p for p in starters + bench})
        teams[side] = Team(
            team_id=team_id,
            name=general[f"{side}Team"]["name"],
            short_name=general[f"{side}Team"]["name"][:12],
            side=Side(side),
            colour=colours.get(side) or ("#2563eb" if side == "home" else "#dc2626"),
            formation=str(raw_team.get("formation") or ""),
            starting_xi=[p.player_id for p in starters],
            bench=[p.player_id for p in bench],
        )
    info = (content.get("matchFacts") or {}).get("infoBox") or {}
    return MatchMeta(
        match_id=f"fm-{general['matchId']}",
        competition=str(general.get("leagueName") or ""),
        venue=str((info.get("Stadium") or {}).get("name") or ""),
        kickoff_utc=str(general.get("matchTimeUTCDate") or ""),
        home=teams["home"],
        away=teams["away"],
        players=players,
    )


def _stamp(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y %H:%M:%S") if value else None
    except ValueError:
        return None


def _half_extra(halfs: dict[str, Any], start_key: str, end_key: str, regulation: int) -> int | None:
    start, end = _stamp(halfs.get(start_key)), _stamp(halfs.get(end_key))
    return max(0, int((end - start).total_seconds()) - regulation) if start and end else None


class _Clock:
    """FotMob minutes to our clock and elapsed seconds."""

    def __init__(self, details: dict[str, Any]) -> None:
        halfs = (details.get("header") or {}).get("status", {}).get("halfs") or {}
        announced = {e.get("time"): _int(e.get("minutesAddedInput")) * 60
                     for e in _events(details) if e.get("type") == "AddedTime"}
        # Stoppage time: from the real half timestamps, else as announced, and never shorter
        # than the latest event FotMob placed in it.
        latest = {1: 0, 2: 0}
        for e in _events(details):
            if e.get("overloadTime") and e.get("time") in (45, 90):
                half = 1 if e["time"] == 45 else 2
                latest[half] = max(latest[half], _int(e["overloadTime"]) * 60)
        for s in ((details.get("content") or {}).get("shotmap") or {}).get("shots") or []:
            period = PERIODS.get(s.get("period"))
            if period and s.get("minAdded"):
                latest[period] = max(latest[period], _int(s["minAdded"]) * 60)
        h1 = _half_extra(halfs, "firstHalfStarted", "firstHalfEnded", H1)
        h2 = _half_extra(halfs, "secondHalfStarted", "secondHalfEnded", H1)
        self.h1_extra = max(h1 if h1 is not None else announced.get(45, 0), latest[1])
        self.h2_extra = max(h2 if h2 is not None else announced.get(90, 0), latest[2])

    @staticmethod
    def clock(period: int, minute: int, added: int | None) -> float:
        if added:
            return (H1 if period == 1 else H2) + (added - 1) * 60 + 30
        return (max(minute, 1) - 1) * 60 + 30

    def elapsed(self, period: int, clock: float) -> float:
        return clock if period == 1 else clock + self.h1_extra


def _events(details: dict[str, Any]) -> list[dict[str, Any]]:
    facts = (details.get("content") or {}).get("matchFacts") or {}
    return list((facts.get("events") or {}).get("events") or [])


def _point(x: float | None, y: float | None, away: bool) -> Point | None:
    if x is None or y is None:
        return None
    if away:
        x, y = PITCH_LENGTH - x, PITCH_WIDTH - y
    return Point(x=min(max(x, 0.0), PITCH_LENGTH), y=min(max(y, 0.0), PITCH_WIDTH))


def _event_period(raw: dict[str, Any]) -> int | None:
    minute = _int(raw.get("time"))
    if minute <= 45:
        return 1
    if minute <= 90:
        return 2
    return None  # extra time and shoot-outs are out of scope


class _Builder:
    def __init__(self, details: dict[str, Any], meta: MatchMeta) -> None:
        self.details, self.meta = details, meta
        self.match_id = meta.match_id
        self.clock = _Clock(details)
        general = details["general"]
        self.team_of = {general["homeTeam"]["id"]: meta.home, general["awayTeam"]["id"]: meta.away}
        self.events: list[Event] = []

    def pid(self, team: Team, fotmob_id: Any) -> str | None:
        if fotmob_id in (None, ""):
            return None
        candidate = f"{team.team_id}-{fotmob_id}"
        return candidate if candidate in self.meta.players else None

    def any_pid(self, fotmob_id: Any) -> str | None:
        return self.pid(self.meta.home, fotmob_id) or self.pid(self.meta.away, fotmob_id)

    def add(self, event_id: str, type_: EventType, period: int, clock: float, **fields: Any) -> Event:
        elapsed = self.clock.elapsed(period, clock)
        e = Event(event_id=f"fm-{event_id}", match_id=self.match_id, sequence=0, period=period, clock=clock,
                  elapsed=elapsed, type=type_, **fields)
        self.events.append(e)
        return e

    def shots(self) -> dict[str, Event]:
        by_id: dict[str, Event] = {}
        for s in (self.details["content"].get("shotmap") or {}).get("shots") or []:
            period = PERIODS.get(s.get("period"))
            team = self.team_of.get(s.get("teamId"))
            if period is None or team is None or s.get("isOwnGoal"):
                continue
            shooter = self.pid(team, s.get("playerId"))
            start = _point(s.get("x"), s.get("y"), away=team.side == Side.AWAY)
            if shooter is None or start is None:
                continue
            outcome = (Outcome.BLOCKED if s.get("isBlocked")
                       else SHOT_OUTCOMES.get(s.get("eventType"), Outcome.OFF_TARGET))
            if outcome == Outcome.BLOCKED:
                end = _point(s.get("blockedX"), s.get("blockedY"), away=team.side == Side.AWAY)
            else:
                end = _point(PITCH_LENGTH, s.get("goalCrossedY"), away=team.side == Side.AWAY)
            xg = round(float(s.get("expectedGoals") or 0.0), 4)
            goal_x = PITCH_LENGTH if team.side == Side.HOME else 0.0
            attrs: dict[str, float | int | str | bool] = {
                "xg": xg,
                "big_chance": xg >= 0.3,  # the simulator's rule; FotMob has no per-shot flag
                "distance_m": round(math.hypot(goal_x - start.x, PITCH_WIDTH / 2 - start.y), 1),
                "body_part": BODY_PARTS.get(s.get("shotType"), "other"),
                "situation": re.sub(r"(?<!^)(?=[A-Z])", "_", str(s.get("situation") or "")).lower(),
                "source": "fotmob",
            }
            if s.get("expectedGoalsOnTarget"):
                attrs["xgot"] = round(float(s["expectedGoalsOnTarget"]), 4)
            clock = _Clock.clock(period, _int(s.get("min")), s.get("minAdded"))
            shot = self.add(f"shot-{s['id']}", EventType.SHOT, period, clock, team_id=team.team_id,
                            player_id=shooter, start=start, end=end, outcome=outcome, attrs=attrs)
            by_id[str(s["id"])] = shot
            keeper = self.pid(self.meta.opponent(team.team_id), s.get("keeperId"))
            if outcome == Outcome.SAVED and keeper:
                self.add(f"save-{s['id']}", EventType.SAVE, period, clock,
                         team_id=self.meta.opponent(team.team_id).team_id, player_id=keeper,
                         related_player_id=shooter, attrs={"xg_faced": xg, "shot_event_id": shot.event_id})
        return by_id

    def timeline(self, shots: dict[str, Event]) -> None:
        for raw in _events(self.details):
            kind, period = raw.get("type"), _event_period(raw)
            if period is None or raw.get("isPenaltyShootoutEvent"):
                continue
            clock = _Clock.clock(period, _int(raw.get("time")), raw.get("overloadTime") or None)
            if kind == "Goal":
                self._goal(raw, period, clock, shots)
            elif kind == "Card":
                team = self.meta.home if raw.get("isHome") else self.meta.away
                player = self.pid(team, raw.get("playerId"))
                outcome = CARDS.get(str(raw.get("card")))
                if player and outcome:
                    self.add(f"card-{raw.get('eventId') or player + str(clock)}", EventType.CARD, period, clock,
                             team_id=team.team_id, player_id=player, outcome=outcome)
            elif kind == "Substitution":
                self._substitution(raw, period, clock)
            elif kind == "Half":
                # The whistle goes just before the stoppage runs out, so 3 minutes reads 45+3'.
                if raw.get("halfStrShort") == "HT":
                    self.add("half-time", EventType.HALF_TIME, 1, H1 + self.clock.h1_extra - 1)
                elif raw.get("halfStrShort") == "FT":
                    self.add("full-time", EventType.FULL_TIME, 2, H2 + self.clock.h2_extra - 1)

    def _goal(self, raw: dict[str, Any], period: int, clock: float, shots: dict[str, Event]) -> None:
        before = (_int(raw.get("homeScore")), _int(raw.get("awayScore")))
        after = raw.get("newScore")
        if after and (_int(after[0]), _int(after[1])) != before:
            team = self.meta.home if _int(after[0]) > before[0] else self.meta.away
        else:  # no score change given: the event's side (the scoring side, own goals included)
            team = self.meta.home if raw.get("isHome") else self.meta.away
        scorer = self.any_pid(raw.get("playerId"))
        if scorer is None:
            return
        shot_raw = raw.get("shotmapEvent") or {}
        shot = shots.get(str(shot_raw.get("id")))
        attrs: dict[str, float | int | str | bool] = {"source": "fotmob"}
        if shot:
            attrs.update(shot_event_id=shot.event_id, xg=shot.attrs.get("xg", 0.0))
            clock = shot.clock
        if raw.get("ownGoal"):
            attrs["own_goal"] = True
        self.add(f"goal-{raw.get('eventId') or scorer + str(clock)}", EventType.GOAL, period, clock + 1,
                 team_id=team.team_id, player_id=scorer, related_player_id=self.any_pid(raw.get("assistPlayerId")),
                 attrs=attrs)

    def _substitution(self, raw: dict[str, Any], period: int, clock: float) -> None:
        team = self.meta.home if raw.get("isHome") else self.meta.away
        swap = [self.pid(team, s.get("id")) for s in raw.get("swap") or []]
        if len(swap) != 2 or None in swap:
            return
        on, off = swap
        # FotMob lists the player coming on first; trust the lineup when it says otherwise.
        if off in team.bench and on not in team.bench:
            on, off = off, on
        self.add(f"sub-{raw.get('time')}-{off}", EventType.SUBSTITUTION, period, clock,
                 team_id=team.team_id, player_id=off, related_player_id=on)


def parse(details: dict[str, Any], meta: MatchMeta | None = None) -> Parsed:
    """Map one matchDetails response to the Matchlens schema. Deterministic: the same
    response always gives the same event ids, so polls can be diffed."""
    meta = meta or build_meta(details)
    status = (details.get("header") or {}).get("status") or {}
    started, finished = bool(status.get("started")), bool(status.get("finished"))
    b = _Builder(details, meta)
    if started:
        b.add("kickoff-1", EventType.KICKOFF, 1, 0.0)
    b.timeline(b.shots())
    second_half = any(e.period == 2 for e in b.events) or bool(
        (status.get("halfs") or {}).get("secondHalfStarted"))
    if started and second_half:
        b.add("kickoff-2", EventType.KICKOFF, 2, float(H1))
    events = sorted(b.events, key=lambda e: (e.elapsed, RANK.get(e.type, 9), e.event_id))
    # Sequence follows match time, so an event FotMob adds late slots into place (the engine rebuilds).
    events = [e.model_copy(update={"sequence": int(e.elapsed * 10) * 10 + RANK.get(e.type, 9)}) for e in events]
    return Parsed(meta=meta, events=events, started=started, finished=finished, stats=provider_stats(details, meta))


def provider_stats(details: dict[str, Any], meta: MatchMeta) -> dict[str, Any]:
    """FotMob's own totals and ratings (from Opta), kept beside the engine's, never mixed in."""
    content = details.get("content") or {}
    teams: dict[str, dict[str, Any]] = {}
    for period, block in ((content.get("stats") or {}).get("Periods") or {}).items():
        rows: dict[str, Any] = {}
        for group in block.get("stats") or []:
            for s in group.get("stats") or []:
                raw = s.get("rawStats")
                if s.get("type") == "title" or not s.get("key") or not isinstance(raw, list) or len(raw) != 2:
                    continue
                if any(not isinstance(r, dict) or r.get("value") is None for r in raw):
                    continue
                rows[s["key"]] = {t.team_id: r for t, r in zip((meta.home, meta.away), raw, strict=True)}
        teams[period] = rows
    ratings: dict[str, float] = {}
    for raw in (content.get("playerStats") or {}).values():
        team = meta.home if f"{meta.home.team_id}-{raw.get('id')}" in meta.players else meta.away
        top = next((g for g in raw.get("stats") or [] if g.get("title") == "Top stats"), {})
        rating = ((top.get("stats") or {}).get("FotMob rating") or {}).get("stat", {}).get("value")
        if rating is not None:
            ratings[f"{team.team_id}-{raw.get('id')}"] = float(rating)
    momentum = ((content.get("momentum") or {}).get("main") or {}).get("data") or []
    return {"source": "FotMob (Opta data)", "teams": teams, "ratings": ratings,
            "momentum": momentum}


# ------------------------------------------------------------------ adapter


class FotMobAdapter:
    """Feeds one FotMob match into a session: a finished match as a timed replay, or a live
    match by polling and emitting what changed since the last poll."""

    def __init__(
        self,
        details: dict[str, Any],
        *,
        control: ReplayControl,
        fetch: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        poll_s: float = DEFAULT_POLL_S,
    ) -> None:
        self.latest = parse(details)
        self.control = control
        self._fetch = fetch
        self.poll_s = max(poll_s, MIN_POLL_S)
        self.live = fetch is not None and not self.latest.finished

    def meta(self) -> MatchMeta:
        return self.latest.meta

    def provider_stats(self) -> dict[str, Any]:
        return self.latest.stats

    async def stream(self) -> AsyncIterator[FeedItem]:
        if not self.live:
            async for item in paced(self.latest.events, self.control):
                yield item
            return
        emitted: dict[str, Event] = {}
        corrections = 0
        while True:
            await self.control.wait_if_paused()
            changes, corrections = diff(emitted, self.latest.events, corrections)
            for item in changes:
                yield item
            if self.latest.finished:
                return
            await asyncio.sleep(self.poll_s)
            try:
                self.latest = parse(await self._fetch(), self.latest.meta)  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 - a failed poll must not end the match
                log.warning("fotmob poll failed: %s", exc)


CORRECTABLE = ("player_id", "related_player_id", "team_id")


def diff(emitted: dict[str, Event], events: list[Event], corrections: int) -> tuple[list[Event], int]:
    """New events, plus a correction for each changed player or team on an event already sent.
    Updates ``emitted`` in place."""
    out: list[Event] = []
    seen = set()
    for e in events:
        seen.add(e.event_id)
        prev = emitted.get(e.event_id)
        if prev is None:
            emitted[e.event_id] = e
            out.append(e)
            continue
        for fld in CORRECTABLE:
            old, new = getattr(prev, fld), getattr(e, fld)
            if old != new and new is not None:
                corrections += 1
                out.append(Event(
                    event_id=f"fm-correction-{corrections}", match_id=e.match_id,
                    sequence=max(x.sequence for x in emitted.values()) + corrections,
                    period=e.period, clock=e.clock, elapsed=e.elapsed, type=EventType.CORRECTION,
                    team_id=e.team_id, corrects_event_id=e.event_id,
                    attrs={"field": fld, "previous": old or "", "value": new},
                ))
        emitted[e.event_id] = e
    for gone in sorted(set(emitted) - seen):
        log.warning("fotmob no longer lists %s; kept, since events cannot be retracted", gone)
    return out, corrections


async def open_adapter(
    match_id: int | None = None,
    *,
    path: Path | None = None,
    live: bool = False,
    control: ReplayControl,
    poll_s: float = DEFAULT_POLL_S,
) -> FotMobAdapter:
    """Build an adapter from a saved response (path or cache) or one fresh request."""
    details = None if live else load_details(match_id, path)
    if details is None:
        if match_id is None:
            raise FileNotFoundError(f"no saved FotMob response at {path}")
        details = await asyncio.to_thread(fetch_details, match_id)
    if not ((details.get("content") or {}).get("lineup") or {}).get("homeTeam", {}).get("starters"):
        raise ValueError("FotMob has no confirmed lineup for this match yet")

    async def poll() -> dict[str, Any]:
        return await asyncio.to_thread(fetch_details, match_id)  # type: ignore[arg-type]

    return FotMobAdapter(details, control=control, fetch=poll if live and match_id is not None else None,
                         poll_s=poll_s)
