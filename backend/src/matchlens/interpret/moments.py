"""Moment replays and telestrator drawings (no language model).

For one signal, this returns the tracking frames around it, the events that
explain it, and drawings on the pitch: the pass chain behind a goal, the zone a
press squeezed, the run behind a speed badge. Every coordinate comes from an
ingested event or tracking frame, so a drawing is as trustworthy as the
fact-checked text next to it. Agents decide what to say; they never draw.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH, Event, EventType, TrackingFrame
from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.signals import Signal, SignalType

# Replay window around the moment, in match seconds.
LEAD_S = 12.0
TAIL_S = 3.0
MAX_LEAD_S = 18.0
CHAIN_MAX = 6
CHAIN_LOOKBACK_S = 25.0

DrawingKind = Literal["lane", "carry", "shot", "ring", "zone", "path", "marker"]

SHOT_TYPES = {SignalType.GOAL, SignalType.BIG_CHANCE, SignalType.BIG_SAVE, SignalType.SHOT_SPEED_BADGE}
# Events that end the attacking side's possession when walking back from a shot.
BREAKS = {EventType.TACKLE, EventType.INTERCEPTION, EventType.POSSESSION_CHANGE, EventType.SAVE,
          EventType.FOUL, EventType.KICKOFF}


class XY(BaseModel):
    x: float
    y: float


class Drawing(BaseModel):
    """One telestrator stroke, in pitch metres (105 x 68, origin top left)."""

    kind: DrawingKind
    points: list[XY]
    at_s: float = Field(description="Seconds after the replay starts when the stroke is drawn")
    team_id: str | None = None
    player_id: str | None = None
    label: str | None = None
    radius: float | None = None
    emphasis: Literal["primary", "secondary"] = "secondary"
    event_id: str | None = None


def _xy(x: float, y: float) -> XY:
    return XY(x=round(min(max(x, 0.0), PITCH_LENGTH), 2), y=round(min(max(y, 0.0), PITCH_WIDTH), 2))


def _position(frame: TrackingFrame | None, entity_id: str | None) -> XY | None:
    if frame is None or entity_id is None:
        return None
    for ent in frame.entities:
        if ent.entity_id == entity_id:
            return _xy(ent.x, ent.y)
    return None


def _slim(e: Event, engine: MatchEngine) -> dict[str, Any]:
    out: dict[str, Any] = {
        "event_id": e.event_id, "type": e.type.value, "elapsed": e.elapsed, "minute_label": e.minute_label,
        "team_id": e.team_id, "player_id": e.player_id, "related_player_id": e.related_player_id,
        "outcome": e.outcome.value,
        "start": e.start.model_dump() if e.start else None, "end": e.end.model_dump() if e.end else None,
    }
    q = engine.pass_quality(e.event_id)
    if q:
        out["pass_difficulty"] = q.difficulty
    return out


def _possession_chain(engine: MatchEngine, shot: Event) -> list[Event]:
    """The attacking side's passes and carries that led to a shot, oldest first."""
    before = engine.events(since=shot.elapsed - CHAIN_LOOKBACK_S, until=shot.elapsed)
    chain: list[Event] = []
    for e in reversed(before):
        if e.event_id == shot.event_id or e.elapsed > shot.elapsed:
            continue
        if e.type in BREAKS or (e.team_id and e.team_id != shot.team_id and e.type != EventType.PRESSURE):
            break
        if e.team_id != shot.team_id or e.type not in (EventType.PASS, EventType.CARRY):
            continue
        if e.type == EventType.PASS and e.outcome.value != "success":
            break
        chain.append(e)
        if len(chain) >= CHAIN_MAX:
            break
    return list(reversed(chain))


def event_strokes(e: Event, start: float, engine: MatchEngine, primary: bool = False) -> list[Drawing]:
    if not e.start:
        return []
    at = round(max(0.0, e.elapsed - start), 2)
    emphasis: Literal["primary", "secondary"] = "primary" if primary else "secondary"
    if e.type == EventType.PASS and e.end:
        q = engine.pass_quality(e.event_id)
        label = f"{q.difficulty:.0f}" if q and primary else None
        return [Drawing(kind="lane", points=[_xy(e.start.x, e.start.y), _xy(e.end.x, e.end.y)], at_s=at,
                        team_id=e.team_id, player_id=e.player_id, label=label, emphasis=emphasis,
                        event_id=e.event_id)]
    if e.type == EventType.CARRY and e.end:
        return [Drawing(kind="carry", points=[_xy(e.start.x, e.start.y), _xy(e.end.x, e.end.y)], at_s=at,
                        team_id=e.team_id, player_id=e.player_id, emphasis=emphasis, event_id=e.event_id)]
    return [Drawing(kind="marker", points=[_xy(e.start.x, e.start.y)], at_s=at, team_id=e.team_id,
                    player_id=e.player_id, emphasis=emphasis, event_id=e.event_id)]


def _shot_moment(engine: MatchEngine, signal: Signal) -> tuple[float, float, list[Event], list[Drawing]]:
    events = [engine.event(eid) for eid in signal.evidence_event_ids if eid]
    shots = [e for e in events if e is not None and e.type == EventType.SHOT]
    shot = shots[0] if shots else next((e for e in events if e is not None), None)
    if shot is None:
        return signal.elapsed - LEAD_S, signal.elapsed + TAIL_S, [], []
    chain = _possession_chain(engine, shot)
    first = chain[0].elapsed if chain else shot.elapsed
    start = max(shot.elapsed - MAX_LEAD_S, min(first - 2.0, shot.elapsed - LEAD_S))
    end = shot.elapsed + TAIL_S
    drawings = [d for e in chain for d in event_strokes(e, start, engine)]
    at = round(shot.elapsed - start, 2)
    if shot.start:
        target = shot.end or _xy(PITCH_LENGTH if shot.start.x > PITCH_LENGTH / 2 else 0.0, PITCH_WIDTH / 2)
        speed = shot.attrs.get("shot_speed_kmh")
        drawings.append(Drawing(kind="shot", points=[_xy(shot.start.x, shot.start.y), _xy(target.x, target.y)],
                                at_s=at, team_id=shot.team_id, player_id=shot.player_id, emphasis="primary",
                                label=f"{speed} km/h" if speed else None, event_id=shot.event_id))
        drawings.append(Drawing(kind="ring", points=[_xy(shot.start.x, shot.start.y)], at_s=at, radius=2.6,
                                team_id=shot.team_id, player_id=shot.player_id, emphasis="primary",
                                label=_short(engine, shot.player_id), event_id=shot.event_id))
    return start, end, [*chain, shot], drawings


_event_strokes = event_strokes  # former private name


def _short(engine: MatchEngine, player_id: str | None) -> str | None:
    p = engine.meta.players.get(player_id or "")
    return p.short_name if p else None


def _spread(n: int, start: float, end: float) -> list[float]:
    """Evenly spaced draw times for strokes that summarise a longer spell."""
    span = max(1.0, (end - start) * 0.75)
    return [round(1.0 + span * i / max(1, n - 1), 2) if n > 1 else 1.0 for i in range(n)]


def _spell_moment(engine: MatchEngine, signal: Signal) -> tuple[float, float, list[Event], list[Drawing]]:
    start, end = signal.elapsed - LEAD_S, signal.elapsed + TAIL_S
    events = [e for e in (engine.event(eid) for eid in signal.evidence_event_ids if eid) if e and e.start]
    drawings: list[Drawing] = []
    frame = engine.frame_near(signal.elapsed)
    if signal.type == SignalType.PRESSURE_SPELL and events:
        xs = [e.start.x for e in events if e.start]
        ys = [e.start.y for e in events if e.start]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        spread = sum(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in zip(xs, ys, strict=True)) / len(xs)
        drawings.append(Drawing(kind="zone", points=[_xy(cx, cy)], radius=round(min(18.0, max(7.0, spread * 1.3)), 1),
                                at_s=0.5, team_id=signal.team_id, emphasis="primary",
                                label=f"{len(events)}"))
        for t, e in zip(_spread(len(events), start, end), events, strict=True):
            drawings.append(Drawing(kind="marker", points=[_xy(e.start.x, e.start.y)], at_s=t,  # type: ignore[union-attr]
                                    team_id=e.team_id, player_id=e.player_id, event_id=e.event_id))
        top = [pid for pid, _ in Counter(e.player_id for e in events if e.player_id).most_common(2)]
        for pid in top:
            at = _position(frame, pid)
            if at:
                drawings.append(Drawing(kind="ring", points=[at], radius=2.4, at_s=round(end - start - 4, 2),
                                        team_id=signal.team_id, player_id=pid, label=_short(engine, pid),
                                        emphasis="primary"))
    elif signal.type == SignalType.CONTROL_SPELL and signal.team_id:
        passes = [e for e in engine.events(since=signal.elapsed - 90, until=signal.elapsed, team_id=signal.team_id,
                                           types={EventType.PASS}) if e.outcome.value == "success"][-10:]
        events = passes
        for t, e in zip(_spread(len(passes), start, end), passes, strict=True):
            drawings += [d.model_copy(update={"at_s": t}) for d in event_strokes(e, start, engine)]
    else:
        # Chaos, momentum swings and anything else spread over minutes: replay the sequence of
        # evidence events, joined in order, so the eye follows the ball changing hands.
        times = _spread(len(events), start, end)
        for t, e in zip(times, events, strict=True):
            drawings.append(Drawing(kind="marker", points=[_xy(e.start.x, e.start.y)], at_s=t,  # type: ignore[union-attr]
                                    team_id=e.team_id, player_id=e.player_id, event_id=e.event_id,
                                    emphasis="primary" if signal.type == SignalType.MOMENTUM_SWING else "secondary"))
        if len(events) > 1:
            drawings.append(Drawing(kind="path", points=[_xy(e.start.x, e.start.y) for e in events],  # type: ignore[union-attr]
                                    at_s=times[0], team_id=signal.team_id))
    return start, end, events, drawings


def _player_moment(engine: MatchEngine, signal: Signal) -> tuple[float, float, list[Event], list[Drawing]]:
    start, end = signal.elapsed - LEAD_S, signal.elapsed + TAIL_S
    pid = signal.player_ids[0] if signal.player_ids else None
    events = [e for e in (engine.event(eid) for eid in signal.evidence_event_ids if eid) if e]
    drawings: list[Drawing] = []
    if signal.type == SignalType.HARD_PASS and events:
        e = events[0]
        drawings += event_strokes(e, start, engine, primary=True)
        if e.start:
            drawings.append(Drawing(kind="ring", points=[_xy(e.start.x, e.start.y)], radius=2.4,
                                    at_s=round(max(0.0, e.elapsed - start - 0.5), 2), team_id=e.team_id,
                                    player_id=e.player_id, label=_short(engine, e.player_id), emphasis="primary"))
        if e.end and e.related_player_id:
            drawings.append(Drawing(kind="ring", points=[_xy(e.end.x, e.end.y)], radius=2.0,
                                    at_s=round(e.elapsed - start + 1.0, 2), team_id=e.team_id,
                                    player_id=e.related_player_id, label=_short(engine, e.related_player_id)))
        return start, end, events, drawings
    if signal.type in (SignalType.SPEED_BADGE, SignalType.DISTANCE_BADGE) and pid:
        frames = engine.frames_between(signal.elapsed - 8, signal.elapsed)
        path = [p for p in (_position(f, pid) for f in frames) if p]
        if len(path) > 1:
            drawings.append(Drawing(kind="path", points=path, at_s=round(max(0.0, frames[0].elapsed - start), 2),
                                    team_id=signal.team_id, player_id=pid, emphasis="primary"))
        at = path[-1] if path else _position(engine.frame_near(signal.elapsed), pid)
        if at:
            value = signal.facts.get("speed_kmh") or signal.facts.get("distance_km")
            unit = "km/h" if "speed_kmh" in signal.facts else "km"
            drawings.append(Drawing(kind="ring", points=[at], radius=2.6, at_s=round(signal.elapsed - start, 2),
                                    team_id=signal.team_id, player_id=pid, emphasis="primary",
                                    label=f"{value} {unit}" if value is not None else _short(engine, pid)))
        return start, end, events, drawings
    # Cards, substitutions, milestones: circle the player where it happened.
    e = events[0] if events else None
    at = _xy(e.start.x, e.start.y) if e and e.start else _position(engine.frame_near(signal.elapsed), pid)
    if at:
        drawings.append(Drawing(kind="ring", points=[at], radius=2.6, at_s=round(signal.elapsed - start, 2),
                                team_id=signal.team_id, player_id=pid, label=_short(engine, pid),
                                emphasis="primary", event_id=e.event_id if e else None))
    return start, end, events, drawings


def moment(engine: MatchEngine, signal: Signal) -> dict[str, Any]:
    """Frames, events and drawings to replay one signal on the pitch."""
    if signal.type in SHOT_TYPES:
        start, end, events, drawings = _shot_moment(engine, signal)
    elif signal.type in (SignalType.PRESSURE_SPELL, SignalType.CHAOS_SPELL, SignalType.CONTROL_SPELL,
                         SignalType.MOMENTUM_SWING):
        start, end, events, drawings = _spell_moment(engine, signal)
    else:
        start, end, events, drawings = _player_moment(engine, signal)
    start = max(0.0, start)
    frames = engine.frames_between(start, end)
    return {
        "insight_id": signal.signal_id,
        "signal_type": signal.type.value,
        "minute_label": signal.minute_label,
        "team_id": signal.team_id,
        "player_ids": signal.player_ids,
        "start_elapsed": round(start, 2),
        "end_elapsed": round(end, 2),
        "focus_elapsed": round(signal.elapsed, 2),
        "frames": [f.model_dump(mode="json") for f in frames],
        "events": [_slim(e, engine) for e in events],
        "drawings": [d.model_dump(mode="json") for d in sorted(drawings, key=lambda d: d.at_s)],
    }
