"""Deterministic live stats engine: the Interpret stage (BRD §7.3).

Processes every event without any language model (FR-018). It keeps the
corrected event log, live statistics, momentum and control/chaos series, and
emits :class:`Signal` objects for key moments.

Robustness (FR-006):
- duplicate events (same event_id) are ignored;
- out-of-order events and corrections trigger a full rebuild from the event
  log, which is cheap for a single match (a few thousand events);
- after a rebuild, signals whose content changed are re-emitted with an
  incremented revision so every downstream consumer can update.
"""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from matchlens.domain.models import (
    PITCH_LENGTH,
    Event,
    EventType,
    MatchMeta,
    Outcome,
    TrackingFrame,
    minute_label,
)
from matchlens.domain.pitch import to_relative
from matchlens.interpret.config import EngineConfig
from matchlens.interpret.pass_quality import PassQuality, rate_pass
from matchlens.interpret.signals import Signal, SignalType
from matchlens.interpret.stats import PlayerStats, TeamStats

ON_TARGET = (Outcome.SAVED, Outcome.GOAL)


def _swing_towards(then_index: float, index: float, h: float, a: float, band: float,
                   delta_threshold: float) -> str | None:
    """Which side a momentum swing favours ("home"/"away"), or None when it is not a real swing.

    A swing needs a large move that ends on the side now on top, outside the "Even" band,
    and recent events by that side; a move towards zero because the window emptied is decay.
    """
    delta = index - then_index
    if abs(delta) < delta_threshold or abs(index) < band:
        return None
    if (index > 0) != (delta > 0):
        return None
    side_weight = h if index > 0 else a
    if h + a <= 0 or side_weight <= 0:
        return None
    return "home" if index > 0 else "away"


@dataclass
class _Weighted:
    elapsed: float
    team_id: str
    weight: float
    event_id: str
    reason: str


@dataclass
class MomentumPoint:
    elapsed: float
    clock: float
    period: int
    home: float  # weighted attacking pressure in the window
    away: float
    index: float  # -100 (away dominant) .. +100 (home dominant)


@dataclass
class RhythmPoint:
    elapsed: float
    clock: float
    period: int
    turnovers_per_min: float
    fouls_per_min: float
    passes_per_possession: dict[str, float]
    pass_share: dict[str, float]  # share of passes in the window
    state: str  # control | balanced | chaos
    controlling_team: str | None


@dataclass
class _Computed:
    period: int = 0
    clock: float = 0.0
    elapsed: float = 0.0
    status: str = "pre_match"  # pre_match | live | half_time | full_time
    last_event_period: int = 0  # for minutes played: the previous event's period and clock
    last_event_clock: float = 0.0
    duel: tuple[str, str] | None = None  # open contest: (presser id, carrier id)
    period_kickoffs: set[str] = field(default_factory=set)  # event ids of kick-offs that start a half
    possession_team: str | None = None
    possession_since: float = 0.0
    teams: dict[str, TeamStats] = field(default_factory=dict)
    players: dict[str, PlayerStats] = field(default_factory=dict)
    pass_quality: dict[str, PassQuality] = field(default_factory=dict)
    weighted: list[_Weighted] = field(default_factory=list)
    momentum: list[MomentumPoint] = field(default_factory=list)
    rhythm: list[RhythmPoint] = field(default_factory=list)
    last_tick_minute: int = -1
    possession_log: list[tuple[float, str, int]] = field(default_factory=list)  # (elapsed, team, passes)
    pressure_log: dict[str, deque[tuple[float, str]]] = field(default_factory=dict)
    turnover_log: list[tuple[float, str, str]] = field(default_factory=list)  # (elapsed, team, event)
    foul_log: list[float] = field(default_factory=list)
    pass_log: list[tuple[float, str, bool]] = field(default_factory=list)
    last_spell: dict[str, float] = field(default_factory=dict)
    last_shot_by: dict[str, str] = field(default_factory=dict)  # team -> last passer before shot
    last_pass: dict[str, tuple[str, str] | None] = field(default_factory=dict)  # team -> (event, passer)


class MatchEngine:
    def __init__(self, meta: MatchMeta, config: EngineConfig | None = None) -> None:
        self.meta = meta
        self.config = config or EngineConfig()
        self._events: dict[str, Event] = {}
        self._order: list[tuple[int, str]] = []  # (sequence, event_id), sorted
        self._corrections: dict[str, list[Event]] = {}  # target event_id -> corrections
        self._frames: dict[int, TrackingFrame] = {}
        self._signals: dict[str, Signal] = {}
        self._pending: list[Signal] = []
        self._emit_enabled = True
        self._tracking_stats: dict[str, PlayerStats] = {}
        self._badges: set[str] = set()
        self._last_frame: TrackingFrame | None = None
        self.c = self._fresh()

    # ----------------------------------------------------------- public API

    def ingest_event(self, event: Event) -> list[Signal]:
        """Ingest one feed event. Returns new or updated signals."""
        if event.event_id in self._events:
            return []
        self._events[event.event_id] = event
        if event.type == EventType.CORRECTION and event.corrects_event_id:
            self._corrections.setdefault(event.corrects_event_id, []).append(event)
            bisect.insort(self._order, (event.sequence, event.event_id))
            return self._rebuild(reason=event)
        last_seq = self._order[-1][0] if self._order else -1
        bisect.insort(self._order, (event.sequence, event.event_id))
        if event.sequence < last_seq or event.event_id in self._corrections:
            return self._rebuild(reason=None)
        self._apply(self._effective(event))
        return self._drain()

    def ingest_frame(self, frame: TrackingFrame) -> list[Signal]:
        """Ingest one tracking frame (distance, sprints, speed badges)."""
        self._frames[int(frame.elapsed)] = frame
        prev = self._last_frame
        self._last_frame = frame
        th = self.config.thresholds
        for ent in frame.entities:
            if ent.entity_id == "ball" or ent.entity_id not in self.meta.players:
                continue
            ps = self._tracking_stats.setdefault(
                ent.entity_id, PlayerStats(player_id=ent.entity_id, team_id=ent.entity_id.split("-")[0])
            )
            if prev is None or prev.period != frame.period:
                continue
            ps.distance_m += ent.speed
            ps.top_speed_mps = max(ps.top_speed_mps, ent.speed)
            if ent.speed >= th.sprint_mps and not ps.in_sprint:
                ps.sprints += 1
                ps.in_sprint = True
            elif ent.speed < th.sprint_mps * 0.85:
                ps.in_sprint = False
            kmh = ent.speed * 3.6
            key = f"speed:{ent.entity_id}"
            if kmh >= th.speed_badge_kmh and key not in self._badges:
                self._badges.add(key)
                self._signal(
                    SignalType.SPEED_BADGE,
                    key,
                    frame.elapsed,
                    frame.period,
                    frame.clock,
                    team_id=ps.team_id,
                    player_ids=[ent.entity_id],
                    facts={"speed_kmh": round(kmh, 1), "threshold_kmh": th.speed_badge_kmh},
                    priority=4,
                )
            for km in th.distance_badge_km:
                key = f"distance:{km}:{ent.entity_id}"
                if ps.distance_m >= km * 1000 and key not in self._badges:
                    self._badges.add(key)
                    self._signal(
                        SignalType.DISTANCE_BADGE,
                        key,
                        frame.elapsed,
                        frame.period,
                        frame.clock,
                        team_id=ps.team_id,
                        player_ids=[ent.entity_id],
                        facts={"distance_km": km},
                        priority=5,
                    )
        return self._drain()

    # Queries used by the API and the MCP server ---------------------------

    def events(
        self,
        since: float | None = None,
        until: float | None = None,
        team_id: str | None = None,
        player_id: str | None = None,
        types: set[EventType] | None = None,
    ) -> list[Event]:
        out = []
        for _, eid in self._order:
            e = self._events[eid]
            if e.type == EventType.CORRECTION:
                continue
            e = self._effective(e)
            if since is not None and e.elapsed < since:
                continue
            if until is not None and e.elapsed > until:
                continue
            if team_id and e.team_id != team_id:
                continue
            if player_id and player_id not in (e.player_id, e.related_player_id):
                continue
            if types and e.type not in types:
                continue
            out.append(e)
        return out

    def has_event(self, event_id: str) -> bool:
        """True once an event (corrections included) has been ingested."""
        return event_id in self._events

    def event(self, event_id: str) -> Event | None:
        e = self._events.get(event_id)
        return self._effective(e) if e else None

    def is_restart(self, event_id: str) -> bool:
        """True for a kick-off that restarts play after a goal, as opposed to one that starts a half."""
        e = self._events.get(event_id)
        return e is not None and e.type == EventType.KICKOFF and event_id not in self.c.period_kickoffs

    def pass_quality(self, event_id: str) -> PassQuality | None:
        return self.c.pass_quality.get(event_id)

    def signals(self) -> list[Signal]:
        return sorted(self._signals.values(), key=lambda s: (s.elapsed, s.signal_id))

    def player_stats(self, player_id: str) -> dict[str, object]:
        ps = self.c.players.get(player_id) or PlayerStats(player_id=player_id, team_id=player_id.split("-")[0])
        out = ps.as_dict()
        tr = self._tracking_stats.get(player_id)
        if tr:
            out["distance_km"] = round(tr.distance_m / 1000, 2)
            out["top_speed_kmh"] = round(tr.top_speed_mps * 3.6, 1) if tr.top_speed_mps else None
            out["sprints"] = tr.sprints
        else:
            # Unavailable is not zero (FR-045).
            out["distance_km"] = out["top_speed_kmh"] = out["sprints"] = None
        p = self.meta.players[player_id]
        out.update(name=p.name, short_name=p.short_name, shirt=p.shirt, role=p.role, position=p.position.value)
        return out

    def team_stats(self, team_id: str) -> dict[str, object]:
        total = sum(t.possession_s for t in self.c.teams.values())
        out = self.c.teams[team_id].as_dict(total)
        out["shot_map"] = list(self.c.teams[team_id].shot_map)
        out.update(self._half_split(team_id))
        return out

    def _half_split(self, team_id: str) -> dict[str, object]:
        """Possession and field tilt (share of the two sides' final-third passes) per half and overall.

        Both sides' figures need each other, and are rounded so that home + away is exactly 100: the home
        figure is rounded and the away figure is its complement. A half appears only once it has data.
        """
        home, away = self.meta.home.team_id, self.meta.away.team_id
        th, ta = self.c.teams[home], self.c.teams[away]

        def share(h: float, a: float) -> float | None:
            if h + a <= 0:
                return None
            pct = round(h / (h + a) * 100, 1)
            return pct if team_id == home else round(100 - pct, 1)

        periods = sorted(set(th.possession_s_by_period) | set(ta.possession_s_by_period)
                         | set(th.final_third_passes_by_period) | set(ta.final_third_passes_by_period))
        poss, tilt = {}, {}
        for p in periods:
            v = share(th.possession_s_by_period.get(p, 0.0), ta.possession_s_by_period.get(p, 0.0))
            if v is not None:
                poss[str(p)] = v
            v = share(th.final_third_passes_by_period.get(p, 0), ta.final_third_passes_by_period.get(p, 0))
            if v is not None:
                tilt[str(p)] = v
        return {"possession_pct_by_half": poss, "field_tilt_pct": share(th.final_third_passes, ta.final_third_passes),
                "field_tilt_pct_by_half": tilt}

    def score(self) -> dict[str, int]:
        return {tid: t.goals for tid, t in self.c.teams.items()}

    def frames_between(self, start: float, end: float) -> list[TrackingFrame]:
        """Tracking frames from start to end (match seconds), oldest first."""
        return [self._frames[s] for s in range(int(start), int(end) + 1) if s in self._frames]

    def frame_near(self, elapsed: float) -> TrackingFrame | None:
        return self._frame_near(elapsed)

    def feed_events(self) -> list[Event]:
        """Every ingested event as delivered, corrections included, in feed order."""
        return [self._events[eid] for _, eid in self._order]

    def momentum_series(self) -> list[MomentumPoint]:
        return list(self.c.momentum)

    def rhythm_series(self) -> list[RhythmPoint]:
        return list(self.c.rhythm)

    def momentum_now(self) -> float:
        return self.c.momentum[-1].index if self.c.momentum else 0.0

    def state(self) -> dict[str, object]:
        home, away = self.meta.home.team_id, self.meta.away.team_id
        c = self.c
        rhythm = c.rhythm[-1] if c.rhythm else None
        return {
            "match_id": self.meta.match_id,
            "status": c.status,
            "period": c.period,
            "clock": c.clock,
            "elapsed": c.elapsed,
            "minute_label": minute_label(c.period, c.clock) if c.period else "0'",
            "score": {home: c.teams[home].goals, away: c.teams[away].goals},
            "possession_team": c.possession_team,
            "momentum_index": round(self.momentum_now(), 1),
            "rhythm": rhythm.state if rhythm else None,
            "controlling_team": rhythm.controlling_team if rhythm else None,
            "players_on_pitch": {home: c.teams[home].players_on_pitch, away: c.teams[away].players_on_pitch},
        }

    # ------------------------------------------------------------ internals

    def _fresh(self) -> _Computed:
        c = _Computed()
        for team in (self.meta.home, self.meta.away):
            c.teams[team.team_id] = TeamStats(team_id=team.team_id)
            c.pressure_log[team.team_id] = deque()
            c.last_pass[team.team_id] = None
            for pid in team.starting_xi + team.bench:
                c.players[pid] = PlayerStats(player_id=pid, team_id=team.team_id, on_pitch=pid in team.starting_xi)
        return c

    def _effective(self, e: Event) -> Event:
        for corr in self._corrections.get(e.event_id, []):
            fld = str(corr.attrs.get("field", ""))
            if fld in ("player_id", "related_player_id", "team_id"):
                e = e.model_copy(update={fld: corr.attrs.get("value")})
        return e

    def _rebuild(self, reason: Event | None) -> list[Signal]:
        old = dict(self._signals)
        self._signals = {
            k: v for k, v in old.items() if v.type in (SignalType.SPEED_BADGE, SignalType.DISTANCE_BADGE)
        }
        self.c = self._fresh()
        self._emit_enabled = False
        for _, eid in self._order:
            e = self._events[eid]
            if e.type != EventType.CORRECTION:
                self._apply(self._effective(e))
        self._emit_enabled = True
        changed: list[Signal] = []
        for sid, sig in self._signals.items():
            prev = old.get(sid)
            if prev is None:
                changed.append(sig)
            elif prev.model_dump(exclude={"revision"}) != sig.model_dump(exclude={"revision"}):
                sig = sig.model_copy(update={"revision": prev.revision + 1})
                self._signals[sid] = sig
                changed.append(sig)
            else:
                self._signals[sid] = prev
        removed = [sid for sid in old if sid not in self._signals]
        if reason is not None:
            target = self._events.get(reason.corrects_event_id or "")
            corr = Signal(
                signal_id=f"correction:{reason.event_id}",
                type=SignalType.CORRECTION,
                elapsed=reason.elapsed,
                period=reason.period,
                clock=reason.clock,
                minute_label=reason.minute_label,
                team_id=reason.team_id,
                player_ids=[p for p in (str(reason.attrs.get("previous", "")), str(reason.attrs.get("value", ""))) if p],
                evidence_event_ids=[reason.event_id] + ([target.event_id] if target else []),
                facts={
                    "field": str(reason.attrs.get("field", "")),
                    "previous": str(reason.attrs.get("previous", "")),
                    "value": str(reason.attrs.get("value", "")),
                    "corrected_event_type": target.type.value if target else None,
                    "corrected_minute_label": target.minute_label if target else None,
                    "updated_signals": ",".join(s.signal_id for s in changed),
                    "removed_signals": ",".join(removed),
                },
                priority=1,
            )
            self._signals[corr.signal_id] = corr
            changed.append(corr)
        self._pending.clear()
        return sorted(changed, key=lambda s: s.elapsed)

    def _drain(self) -> list[Signal]:
        out, self._pending = self._pending, []
        return out

    def _signal(
        self,
        type_: SignalType,
        key: str,
        elapsed: float,
        period: int,
        clock: float,
        **kw: object,
    ) -> None:
        sid = f"{type_.value}:{key}"
        sig = Signal(
            signal_id=sid,
            type=type_,
            elapsed=elapsed,
            period=period,
            clock=clock,
            minute_label=minute_label(period, clock),
            **kw,  # type: ignore[arg-type]
        )
        self._signals[sid] = sig
        if self._emit_enabled:
            self._pending.append(sig)

    def _rel(self, team_id: str, x: float, y: float) -> tuple[float, float]:
        return to_relative(x, y, team_id == self.meta.home.team_id)

    def _frame_near(self, elapsed: float) -> TrackingFrame | None:
        sec = int(elapsed)
        for s in (sec, sec + 1, sec - 1):
            if s in self._frames:
                return self._frames[s]
        return None

    def _weigh(self, e: Event, team: str, weight: float, reason: str) -> None:
        self.c.weighted.append(_Weighted(e.elapsed, team, weight, e.event_id, reason))

    # ----------------------------------------------------------- apply

    def _apply(self, e: Event) -> None:
        c = self.c
        w = self.config.momentum
        th = self.config.thresholds
        self._tick(e)
        self._credit_minutes(e)
        self._resolve_duel(e)
        c.period, c.clock, c.elapsed = e.period, e.clock, e.elapsed
        team = c.teams.get(e.team_id) if e.team_id else None
        player = c.players.get(e.player_id) if e.player_id else None
        if player:
            player.touches += 1 if e.type not in (EventType.SUBSTITUTION, EventType.CARD) else 0

        match e.type:
            case EventType.KICKOFF:
                if c.status in ("pre_match", "half_time"):
                    c.status = "live"
                    c.period_kickoffs.add(e.event_id)
                    self._signal(SignalType.PERIOD, f"kickoff:{e.period}", e.elapsed, e.period, e.clock,
                                 evidence_event_ids=[e.event_id], facts={"phase": "kickoff", "period": e.period},
                                 priority=3)
                self._set_possession(e.team_id, e.elapsed)
            case EventType.PASS:
                assert team and player and e.start and e.end
                self._set_possession(e.team_id, e.elapsed)
                team.passes += 1
                player.passes += 1
                done = e.outcome == Outcome.SUCCESS
                frame = self._frame_near(e.elapsed)
                opp_ids = set(self.meta.opponent(e.team_id).starting_xi + self.meta.opponent(e.team_id).bench)
                opp_pos = [(en.x, en.y) for en in frame.entities if en.entity_id in opp_ids] if frame else None
                q = rate_pass(
                    (e.start.x, e.start.y),
                    (e.end.x, e.end.y),
                    attacking_right=e.team_id == self.meta.home.team_id,
                    pressured=bool(e.attrs.get("pressured", False)),
                    lofted=e.attrs.get("height") == "high",
                    opponent_positions=opp_pos,
                )
                c.pass_quality[e.event_id] = q
                player.pass_difficulty_sum += q.difficulty
                c.pass_log.append((e.elapsed, e.team_id, done))
                c.possession_log.append((e.elapsed, e.team_id, 1))
                if done:
                    team.passes_completed += 1
                    player.passes_completed += 1
                    c.last_pass[e.team_id] = (e.event_id, e.player_id)  # type: ignore[assignment]
                    receiver = c.players.get(e.related_player_id or "")
                    if receiver:
                        receiver.touches += 1
                    if q.progression_m >= 10:
                        player.progressive_passes += 1
                    ex, ey = self._rel(e.team_id, e.end.x, e.end.y)
                    if ex >= 70:
                        team.final_third_passes += 1
                        by = team.final_third_passes_by_period
                        by[e.period] = by.get(e.period, 0) + 1
                        self._weigh(e, e.team_id, w.final_third_pass, "final_third_pass")
                    if ex >= 88.5 and 13.84 <= ey <= 54.16:
                        team.box_entries += 1
                        self._weigh(e, e.team_id, w.box_entry, "box_entry")
                    if q.difficulty >= th.hard_pass_difficulty:
                        player.hard_passes_completed += 1
                        if ex >= 52.5:
                            self._signal(SignalType.HARD_PASS, e.event_id, e.elapsed, e.period, e.clock,
                                         team_id=e.team_id, player_ids=[e.player_id, e.related_player_id],
                                         evidence_event_ids=[e.event_id],
                                         facts={"difficulty": q.difficulty, "band": q.band,
                                                "distance_m": q.distance_m, "progression_m": q.progression_m,
                                                "lane_opponents": q.lane_opponents, "pressured": q.pressured,
                                                "ball_speed_kmh": e.attrs.get("ball_speed_kmh")},
                                         priority=4)
                else:
                    c.last_pass[e.team_id] = None
            case EventType.CARRY:
                assert player
                player.carries += 1
                self._set_possession(e.team_id, e.elapsed)
            case EventType.PRESSURE:
                assert team and player and e.start
                team.pressures += 1
                player.pressures += 1
                if e.related_player_id in c.players:
                    c.duel = (player.player_id, str(e.related_player_id))
                rx, _ = self._rel(e.team_id, e.start.x, e.start.y)
                if rx >= 52.5:
                    self._pressure(e)
            case EventType.TACKLE | EventType.INTERCEPTION:
                assert team and player and e.start
                if e.type == EventType.TACKLE:
                    team.tackles += 1
                    player.tackles += 1
                else:
                    team.interceptions += 1
                    player.interceptions += 1
                rx, _ = self._rel(e.team_id, e.start.x, e.start.y)
                if rx >= 70:
                    team.high_turnovers += 1
                    self._weigh(e, e.team_id, w.high_turnover, "high_turnover")
                    self._pressure(e)
            case EventType.POSSESSION_CHANGE:
                c.turnover_log.append((e.elapsed, e.team_id or "", e.event_id))
                self._set_possession(e.team_id, e.elapsed)
            case EventType.SHOT:
                assert team and player and e.start
                self._set_possession(e.team_id, e.elapsed)
                xg = float(e.attrs.get("xg", 0.0))
                speed = float(e.attrs.get("shot_speed_kmh", 0.0))
                big = bool(e.attrs.get("big_chance", False))
                team.shots += 1
                player.shots += 1
                team.xg += xg
                player.xg += xg
                player.max_shot_speed_kmh = max(player.max_shot_speed_kmh or 0.0, speed)
                lp = c.last_pass.get(e.team_id)
                if lp:
                    passer = c.players.get(lp[1])
                    if passer:
                        passer.key_passes += 1
                rx, ry = self._rel(e.team_id, e.start.x, e.start.y)
                team.shot_map.append({
                    "event_id": e.event_id, "player_id": e.player_id, "minute_label": e.minute_label,
                    "x": round(rx, 1), "y": round(ry, 1), "xg": xg, "outcome": e.outcome.value,
                    "shot_speed_kmh": speed, "body_part": e.attrs.get("body_part"),
                })
                self._weigh(e, e.team_id, w.shot, "shot")
                if e.outcome in ON_TARGET:
                    team.shots_on_target += 1
                    player.shots_on_target += 1
                    self._weigh(e, e.team_id, w.shot_on_target - w.shot, "shot_on_target")
                if big:
                    team.big_chances += 1
                    self._weigh(e, e.team_id, w.big_chance, "big_chance")
                    if e.outcome != Outcome.GOAL:
                        self._signal(SignalType.BIG_CHANCE, e.event_id, e.elapsed, e.period, e.clock,
                                     team_id=e.team_id, player_ids=[e.player_id],
                                     evidence_event_ids=[e.event_id],
                                     facts={"xg": xg, "outcome": e.outcome.value, "shot_speed_kmh": speed,
                                            "distance_m": e.attrs.get("distance_m")},
                                     priority=2)
                if speed >= th.shot_speed_badge_kmh:
                    self._signal(SignalType.SHOT_SPEED_BADGE, e.event_id, e.elapsed, e.period, e.clock,
                                 team_id=e.team_id, player_ids=[e.player_id], evidence_event_ids=[e.event_id],
                                 facts={"shot_speed_kmh": speed, "threshold_kmh": th.shot_speed_badge_kmh,
                                        "outcome": e.outcome.value},
                                 priority=4)
            case EventType.SAVE:
                assert team and player
                team.saves += 1
                player.saves += 1
                xg = float(e.attrs.get("xg_faced", 0.0))
                if xg >= 0.25:
                    self._signal(SignalType.BIG_SAVE, e.event_id, e.elapsed, e.period, e.clock,
                                 team_id=e.team_id, player_ids=[e.player_id, e.related_player_id],
                                 evidence_event_ids=[e.event_id, str(e.attrs.get("shot_event_id", ""))],
                                 facts={"xg_faced": xg}, priority=2)
            case EventType.GOAL:
                assert team and player
                own_goal = bool(e.attrs.get("own_goal"))
                team.goals += 1
                if not own_goal:  # the scorer is on the other side: no goal to their name
                    player.goals += 1
                self._weigh(e, e.team_id, w.goal, "goal")
                assist = c.players.get(e.related_player_id or "")
                if assist:
                    assist.assists += 1
                home, away = self.meta.home.team_id, self.meta.away.team_id
                self._signal(SignalType.GOAL, e.event_id, e.elapsed, e.period, e.clock,
                             team_id=e.team_id, player_ids=[p for p in (e.player_id, e.related_player_id) if p],
                             evidence_event_ids=[e.event_id, str(e.attrs.get("shot_event_id", ""))],
                             facts={"scorer": e.player_id, "assist": e.related_player_id,
                                    "xg": e.attrs.get("xg"), "shot_speed_kmh": e.attrs.get("shot_speed_kmh"),
                                    "home_score": c.teams[home].goals, "away_score": c.teams[away].goals,
                                    "momentum_index": round(self.momentum_now(), 1), "own_goal": own_goal},
                             priority=1)
                meta_p = self.meta.players[e.player_id]  # type: ignore[index]
                career = meta_p.career_goals + player.goals
                every = th.milestone_goal_every
                if not own_goal and career % every == 0:
                    self._signal(SignalType.MILESTONE, f"goals:{e.player_id}:{career}", e.elapsed, e.period,
                                 e.clock, team_id=e.team_id, player_ids=[e.player_id],
                                 evidence_event_ids=[e.event_id],
                                 facts={"milestone": "career_goals", "value": career}, priority=1)
            case EventType.FOUL:
                assert team and player
                team.fouls += 1
                player.fouls += 1
                c.foul_log.append(e.elapsed)
            case EventType.CARD:
                assert team and player
                red = e.outcome in (Outcome.RED, Outcome.SECOND_YELLOW)
                if e.outcome in (Outcome.YELLOW, Outcome.SECOND_YELLOW):
                    team.yellow_cards += 1
                    player.yellow_cards += 1
                if red:
                    team.red_cards += 1
                    team.players_on_pitch -= 1
                    player.red_card = True
                    player.on_pitch = False
                # Only a dismissal changes how many players are on the pitch; a booking changes the card count.
                card_facts: dict[str, Any] = {"card": e.outcome.value}
                if red:
                    card_facts["players_on_pitch"] = team.players_on_pitch
                else:
                    card_facts["player_yellow_cards"] = player.yellow_cards
                self._signal(SignalType.CARD, e.event_id, e.elapsed, e.period, e.clock, team_id=e.team_id,
                             player_ids=[e.player_id], evidence_event_ids=[e.event_id],
                             facts=card_facts,
                             priority=1 if red else 3)
            case EventType.CORNER:
                assert team
                team.corners += 1
                self._weigh(e, e.team_id, w.corner, "corner")
            case EventType.SUBSTITUTION:
                assert player
                player.on_pitch = False
                on = c.players.get(e.related_player_id or "")
                if on:
                    on.on_pitch = True
                self._signal(SignalType.SUBSTITUTION, e.event_id, e.elapsed, e.period, e.clock,
                             team_id=e.team_id, player_ids=[e.player_id, e.related_player_id],
                             evidence_event_ids=[e.event_id],
                             facts={"off": e.player_id, "on": e.related_player_id}, priority=3)
            case EventType.HALF_TIME | EventType.FULL_TIME:
                self._set_possession(None, e.elapsed)
                c.status = "half_time" if e.type == EventType.HALF_TIME else "full_time"
                home, away = self.meta.home.team_id, self.meta.away.team_id
                self._signal(SignalType.PERIOD, f"{c.status}", e.elapsed, e.period, e.clock,
                             evidence_event_ids=[e.event_id],
                             facts={"phase": c.status, "home_score": c.teams[home].goals,
                                    "away_score": c.teams[away].goals},
                             priority=1)
            case _:
                if e.team_id and e.type in (EventType.FREE_KICK, EventType.THROW_IN, EventType.GOAL_KICK):
                    self._set_possession(e.team_id, e.elapsed)

    def _credit_minutes(self, e: Event) -> None:
        """Playing time: the clock time since the previous event, for everyone on the pitch, while the match is
        live and within one period (the clock restarts for the second half, so nothing is added across it)."""
        c = self.c
        if c.status == "live" and c.last_event_period == e.period and e.clock > c.last_event_clock:
            dt = e.clock - c.last_event_clock
            for p in c.players.values():
                if p.on_pitch:
                    p.seconds_played += dt
        c.last_event_period, c.last_event_clock = e.period, e.clock

    def _resolve_duel(self, e: Event) -> None:
        """A duel opens on a PRESSURE event (the presser against the ball carrier) and is settled by the next
        event: the presser wins it with a TACKLE of their own; a foul by the presser, or anything else (the
        carrier kept or moved the ball), is won by the carrier."""
        c = self.c
        if c.duel is None:
            return
        presser, carrier = c.duel
        c.duel = None
        winner = presser if e.type == EventType.TACKLE and e.player_id == presser else carrier
        for pid in (presser, carrier):
            c.players[pid].duels_total += 1
        c.players[winner].duels_won += 1

    def _set_possession(self, team_id: str | None, elapsed: float) -> None:
        c = self.c
        if c.possession_team and c.possession_team in c.teams:
            held = max(0.0, elapsed - c.possession_since)
            team = c.teams[c.possession_team]
            team.possession_s += held
            team.possession_s_by_period[c.period] = team.possession_s_by_period.get(c.period, 0.0) + held
        c.possession_team = team_id
        c.possession_since = elapsed

    def _pressure(self, e: Event) -> None:
        c = self.c
        th = self.config.thresholds
        team = e.team_id or ""
        log = c.pressure_log[team]
        log.append((e.elapsed, e.event_id))
        while log and log[0][0] < e.elapsed - th.pressure_spell_window_s:
            log.popleft()
        key = f"pressure:{team}"
        if len(log) >= th.pressure_spell_count and e.elapsed - c.last_spell.get(key, -1e9) > 420:
            c.last_spell[key] = e.elapsed
            opp = self.meta.opponent(team).team_id
            turnovers = sum(1 for t, tm, _ in c.turnover_log if t >= e.elapsed - th.pressure_spell_window_s and tm == team)
            self._signal(SignalType.PRESSURE_SPELL, f"{team}:{int(e.elapsed)}", e.elapsed, e.period, e.clock,
                         team_id=team, evidence_event_ids=[eid for _, eid in log],
                         facts={"pressing_actions": len(log), "window_s": th.pressure_spell_window_s,
                                "turnovers_won": turnovers, "opponent": opp,
                                "opponent_pass_accuracy_window": self._pass_accuracy(opp, e.elapsed,
                                                                                     th.pressure_spell_window_s)},
                         priority=2)

    def _pass_accuracy(self, team: str, now: float, window: float) -> float | None:
        rows = [ok for t, tm, ok in self.c.pass_log if tm == team and t >= now - window]
        return round(sum(rows) / len(rows) * 100, 1) if rows else None

    def _tick(self, e: Event) -> None:
        """Once per elapsed minute: sample momentum and rhythm, detect swings."""
        c = self.c
        minute = int(e.elapsed // 60)
        if minute == c.last_tick_minute or e.elapsed < 60:
            return
        c.last_tick_minute = minute
        th = self.config.thresholds
        home, away = self.meta.home.team_id, self.meta.away.team_id
        now = e.elapsed
        window = [w for w in c.weighted if w.elapsed >= now - th.momentum_window_s]
        h = sum(w.weight for w in window if w.team_id == home)
        a = sum(w.weight for w in window if w.team_id == away)
        index = round((h - a) / (h + a + 12.0) * 100, 1)
        point = MomentumPoint(now, e.clock, e.period, round(h, 1), round(a, 1), index)
        c.momentum.append(point)

        # Momentum swing: compare with five samples ago.
        if len(c.momentum) >= 6:
            then = c.momentum[-6]
            delta = index - then.index
            key = "momentum_swing"
            towards_side = _swing_towards(then.index, index, h, a, th.momentum_even_band, th.momentum_swing_delta)
            if towards_side is not None and now - c.last_spell.get(key, -1e9) > 360:
                c.last_spell[key] = now
                towards = home if towards_side == "home" else away
                previous = None
                if abs(then.index) >= th.momentum_even_band:
                    previous = home if then.index > 0 else away
                top = sorted((w for w in window if w.team_id == towards), key=lambda w: -w.weight)[:6]
                reasons: dict[str, int] = {}
                for w in window:
                    if w.team_id == towards:
                        reasons[w.reason] = reasons.get(w.reason, 0) + 1
                self._signal(SignalType.MOMENTUM_SWING, f"{int(now)}", now, e.period, e.clock,
                             team_id=towards, evidence_event_ids=[w.event_id for w in top],
                             facts={"from_index": then.index, "to_index": index, "delta": round(delta, 1),
                                    "strength": round(abs(index)),
                                    **({"previous_leader": previous} if previous else {}),
                                    "window_s": th.momentum_window_s,
                                    "since_minute_label": minute_label(then.period, then.clock),
                                    **{f"count_{k}": v for k, v in sorted(reasons.items())}},
                             priority=2)

        # Rhythm: control vs chaos over the last five minutes.
        win = th.chaos_window_s
        mins = win / 60
        turnovers = [t for t in c.turnover_log if t[0] >= now - win]
        fouls = [t for t in c.foul_log if t >= now - win]
        passes = [p for p in c.pass_log if p[0] >= now - win]
        total_passes = len(passes) or 1
        share = {tid: round(sum(1 for p in passes if p[1] == tid) / total_passes * 100, 1) for tid in (home, away)}
        per_poss: dict[str, float] = {}
        for tid in (home, away):
            seqs = self._possession_lengths(tid, now - win)
            per_poss[tid] = round(sum(seqs) / len(seqs), 1) if seqs else 0.0
        tpm = round(len(turnovers) / mins, 2)
        fpm = round(len(fouls) / mins, 2)
        controlling = max((home, away), key=lambda t: share[t])
        acc = self._pass_accuracy(controlling, now, win) or 0.0
        if tpm >= 2.6 or (tpm >= 2.0 and fpm >= 0.8):
            state = "chaos"
            controlling_team = None
        elif share[controlling] >= 66 and acc >= 88 and per_poss[controlling] >= 6:
            state = "control"
            controlling_team = controlling
        else:
            state = "balanced"
            controlling_team = None
        prev = c.rhythm[-1] if c.rhythm else None
        c.rhythm.append(RhythmPoint(now, e.clock, e.period, tpm, fpm, per_poss, share, state, controlling_team))
        if state != "balanced" and (prev is None or prev.state != state):
            key = f"rhythm:{state}"
            if now - c.last_spell.get(key, -1e9) > 480:
                c.last_spell[key] = now
                evidence = [eid for _, _, eid in turnovers][-8:] if state == "chaos" else []
                self._signal(SignalType.CHAOS_SPELL if state == "chaos" else SignalType.CONTROL_SPELL,
                             f"{int(now)}", now, e.period, e.clock, team_id=controlling_team,
                             evidence_event_ids=evidence,
                             facts={"turnovers_per_min": tpm, "fouls_per_min": fpm,
                                    "pass_share_pct": share[controlling], "pass_accuracy_pct": acc,
                                    "passes_per_possession": per_poss[controlling],
                                    "window_s": win, "dominant_team": controlling},
                             priority=2 if state == "control" else 3)

    def _possession_lengths(self, team: str, since: float) -> list[int]:
        """Lengths (in passes) of possession spells for team since a time."""
        lengths: list[int] = []
        current: str | None = None
        count = 0
        for t, tm, _ in self.c.possession_log:
            if t < since:
                continue
            if tm != current:
                if current == team and count:
                    lengths.append(count)
                current, count = tm, 0
            count += 1
        if current == team and count:
            lengths.append(count)
        return lengths


def pitch_third(x_rel: float) -> str:
    if x_rel < PITCH_LENGTH / 3:
        return "defensive"
    if x_rel < 2 * PITCH_LENGTH / 3:
        return "middle"
    return "attacking"
