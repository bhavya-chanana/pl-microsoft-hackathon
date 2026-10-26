"""Simulator matches as a training source (offline; the smoke source).

Samples every pass and carry start plus random open-play frames. The tracking is 1 Hz, so a sample takes
the nearest frame with its 4 earlier frames as history; labels come from the simulator's events.
"""

from __future__ import annotations

import bisect
import random
from pathlib import Path
from typing import Iterator

from matchlens.domain.models import EventType, Outcome, SyntheticMatch
from matchlens.simulator.engine import generate_match
from matchlens.tacticsml.contract import TFrame, from_tracking
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.labels import EventStream, LEvent
from matchlens.tacticsml.train.sources._common import HISTORY, Item, build_from_iter

SOURCE = "synthetic"
DEFAULT_SEEDS = tuple(range(100, 120))
_RESTARTS = {EventType.THROW_IN: "throw_in", EventType.GOAL_KICK: "goal_kick", EventType.CORNER: "corner",
             EventType.FREE_KICK: "free_kick"}
_GAINS = (EventType.POSSESSION_CHANGE, EventType.INTERCEPTION, EventType.TACKLE, EventType.SAVE,
          EventType.KICKOFF, *_RESTARTS)
_STOPS = (EventType.FOUL, EventType.GOAL, EventType.HALF_TIME, EventType.FULL_TIME)
_SET_PIECES = ("corner", "free_kick", "throw_in", "goal_kick")
OPEN_PLAY_WINDOW = 8.0  # seconds since the last action for a frame to count as open play


def to_levents(match: SyntheticMatch) -> list[LEvent]:
    out: list[LEvent] = []
    for e in match.events:
        base = dict(t=e.elapsed, team_id=e.team_id or "", player_id=e.player_id, period=e.period)
        if e.team_id is None:
            continue
        if e.type == EventType.PASS:
            ok = e.outcome == Outcome.SUCCESS
            out.append(LEvent(kind="pass", receiver_id=e.related_player_id if ok else None,
                              outcome="success" if ok else "fail", **base))
        elif e.type == EventType.CARRY:
            out.append(LEvent(kind="carry", outcome="success", **base))
        elif e.type == EventType.SHOT:
            out.append(LEvent(kind="shot", outcome="goal" if e.outcome == Outcome.GOAL else "miss", **base))
        elif e.type == EventType.GOAL:
            out.append(LEvent(kind="goal", outcome="goal", **base))
        elif e.type in _GAINS:
            # a tackle / interception / save / restart gives the ball to the event's team
            out.append(LEvent(kind="gain", **base))
    return out


def _stop_times(match: SyntheticMatch) -> list[tuple[int, float, bool]]:
    """(period, t, is_action) of every event that says whether the ball is in play."""
    rows = []
    for e in match.events:
        if e.type in (EventType.PASS, EventType.CARRY, EventType.SHOT, EventType.POSSESSION_CHANGE,
                      EventType.INTERCEPTION, EventType.TACKLE):
            rows.append((e.period, e.elapsed, True))
        elif e.type in _STOPS:
            rows.append((e.period, e.elapsed, False))
    return rows


def iter_match(match: SyntheticMatch, random_hz: float = 1.0, seed: int = 0) -> Iterator[Item]:
    meta = match.meta
    home, away = meta.home.team_id, meta.away.team_id
    events = to_levents(match)
    last_t = {}
    for fr in match.tracking:
        last_t[fr.period] = fr.elapsed
    stream = EventStream(events, last_t)
    frames = match.tracking
    at = {round(fr.elapsed): i for i, fr in enumerate(frames)}  # tracking is 1 Hz on integer seconds
    rng = random.Random(seed)
    stops = _stop_times(match)
    stop_keys = [(p, t) for p, t, _ in stops]
    base = {"source": SOURCE, "match_id": f"sim-{match.seed}"}

    def frame_index(t: float, period: int) -> int | None:
        i = at.get(round(t))
        return i if i is not None and frames[i].period == period else None

    def tframe(i: int, team: str, carrier: str | None, set_piece: str | None = None) -> TFrame | None:
        lo = i
        while lo > 0 and i - lo < HISTORY and frames[lo - 1].period == frames[i].period:
            lo -= 1
        if len([e for e in frames[i].entities if e.entity_id != "ball"]) < 14:
            return None
        fr = from_tracking(frames[lo: i + 1], meta, team, carrier)
        return fr.model_copy(update={"set_piece": set_piece}) if set_piece else fr

    def target(i: int, team: str) -> TFrame | None:
        j = i + int(schema.TARGET_DT)
        if j >= len(frames) or frames[j].period != frames[i].period:
            return None
        return from_tracking(frames[j: j + 1], meta, team)

    def item(i: int, t: float, team: str, carrier: str | None, kind: int, ev: LEvent | None,
             set_piece: str | None = None) -> Item | None:
        fr = tframe(i, team, carrier, set_piece)
        if fr is None:
            return None
        lab = stream.labels_at(t, frames[i].period, team, ev)
        return fr, lab, dict(base, elapsed=frames[i].elapsed, period=frames[i].period, kind=kind,
                             target=target(i, team), knows_set_piece=True)

    sp_map = {(e.period, e.elapsed, e.player_id): e.attrs["set_piece"] for e in match.events
              if e.type == EventType.PASS and e.attrs.get("set_piece") in _SET_PIECES}
    for le in events:
        if le.kind not in ("pass", "carry"):
            continue
        i = frame_index(le.t, le.period)
        if i is None:
            continue
        sp = sp_map.get((le.period, le.t, le.player_id))
        it = item(i, le.t, le.team_id, le.player_id, schema.KIND_PASS if le.kind == "pass" else schema.KIND_CARRY,
                  le, sp)
        if it is not None:
            yield it

    p = min(1.0, random_hz)
    for i, fr in enumerate(frames):
        if rng.random() >= p:
            continue
        t = fr.elapsed
        team = stream.possession_at(t, fr.period)
        k = bisect.bisect_right(stop_keys, (fr.period, t)) - 1
        if team is None or k < 0 or stops[k][0] != fr.period or not stops[k][2] or t - stops[k][1] > OPEN_PLAY_WINDOW:
            continue
        it = item(i, t, team, None, schema.KIND_OPEN, None)
        if it is not None:
            yield it


def iter_samples(seeds: tuple[int, ...] | list[int] | None = None, limit: int | None = None,
                 random_hz: float = 1.0) -> Iterator[Item]:
    """Samples of ``limit`` simulator matches (seeds ``DEFAULT_SEEDS`` unless given)."""
    seeds = tuple(seeds) if seeds is not None else DEFAULT_SEEDS
    if limit is not None:
        seeds = seeds[:limit]
    for seed in seeds:
        match = generate_match(seed)
        yield from iter_match(match, random_hz, seed)


def build_shards(seeds=None, limit: int | None = None, root: Path | None = None, random_hz: float = 1.0,
                 progress: bool = False) -> list[schema.ShardEntry]:
    return build_from_iter(SOURCE, iter_samples(seeds, limit, random_hz), root, "simulator", progress)
