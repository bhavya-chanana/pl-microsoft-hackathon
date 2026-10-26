"""Fork the simulator: freeze a moment, then replay "what if" from it.

``MatchSimulator.snapshot()`` freezes a running simulator and
``snapshot_from_frame()`` builds the same thing from any tracking frame (the live
session, real data). ``MatchSimulator.resume()`` turns a snapshot back into a
simulator, with optional styles, a tactic intent and 5 Hz frames, and
``rollout()`` plays it forward for a few seconds, stopping early at the first
shot, goal, turnover or dead ball.

Only this module touches the engine's private state, and the engine imports it
lazily, so the two can reference each other without a cycle.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from matchlens.domain.models import (
    PITCH_LENGTH,
    Event,
    MatchMeta,
    Position,
    TrackingFrame,
)
from matchlens.domain.pitch import ANCHORS, distance, to_absolute
from matchlens.simulator.storyline import Phase, Storyline
from matchlens.simulator.style import SimStyle

if TYPE_CHECKING:
    from matchlens.simulator.engine import MatchSimulator
    from matchlens.simulator.intent_exec import IntentController

Vec = tuple[float, float]


def rollout_storyline() -> Storyline:
    """Neutral storyline for forks: no beats, no momentum, and any shot may score."""
    return Storyline(
        title="Rollout",
        summary="Unscripted play from a frozen moment.",
        phases=[Phase(start_minute=0, end_minute=96, home_dominance=0.0)],
        beats=[],
        scripted_goals_only=False,
    )


@dataclass(frozen=True)
class SimSnapshot:
    """Everything needed to continue a simulation. The mutable parts are private deep copies,
    and ``resume`` copies them again, so one snapshot can seed any number of rollouts."""

    meta: MatchMeta
    state: Any  # engine._State
    rng_state: tuple[Any, ...]
    seq: int  # last event sequence number used
    wander: dict[str, Vec]
    pending_corrections: tuple[tuple[float, Event, str], ...]
    last_frame: TrackingFrame | None
    elapsed: float
    seed: int = 0
    counter_until: dict[str, float] = field(default_factory=dict)


@dataclass
class RolloutTrace:
    """What one rollout produced."""

    frames: list[TrackingFrame]
    events: list[Event]
    # goal | shot | turnover | restart:<kind> | horizon
    terminal: str
    end_elapsed: float = 0.0
    score: dict[str, int] = field(default_factory=dict)
    possession: str = ""


# --------------------------------------------------------------------- snapshot


def take_snapshot(sim: MatchSimulator) -> SimSnapshot:
    return SimSnapshot(
        meta=sim.meta,
        state=copy.deepcopy(sim.s),
        rng_state=sim.rng.getstate(),
        seq=sim._seq,
        wander=dict(sim._wander),
        pending_corrections=tuple(copy.deepcopy(sim._pending_corrections)),
        last_frame=sim.tracking[-1] if sim.tracking else None,
        elapsed=sim.s.elapsed,
        seed=sim.seed,
        counter_until=dict(sim._counter_until),
    )


def restore(
    snap: SimSnapshot,
    *,
    seed: int | None,
    styles: dict[str, SimStyle] | None,
    intent: IntentController | None,
    frame_dt: float,
    id_prefix: str,
) -> MatchSimulator:
    from matchlens.simulator.engine import MatchSimulator

    sim = MatchSimulator(
        seed=snap.seed if seed is None else seed,
        meta=snap.meta,
        storyline=rollout_storyline(),
        styles=styles,
        intent=intent,
        frame_dt=frame_dt,
        id_prefix=id_prefix,
    )
    sim.s = copy.deepcopy(snap.state)
    if seed is None:
        sim.rng.setstate(snap.rng_state)
    # else: the constructor already seeded a fresh stream (and drew the wander phases from it).
    sim._wander = dict(snap.wander)
    sim._seq = snap.seq
    sim._pending_corrections = copy.deepcopy(list(snap.pending_corrections))
    sim._counter_until = dict(snap.counter_until)
    if snap.last_frame is not None:
        sim.tracking = [snap.last_frame]
        sim._last_frame_t = snap.last_frame.elapsed
    if frame_dt != 1.0:
        # Snap the first new frame onto the frame_dt grid, just after the frozen moment.
        sim.s.next_frame = round((math.floor(sim.s.elapsed / frame_dt + 1e-9) + 1) * frame_dt, 6)
    return sim


def run_rollout(sim: MatchSimulator, horizon_s: float) -> RolloutTrace:
    from matchlens.simulator.engine import _Stop

    s = sim.s
    sim._stop_at = s.elapsed + horizon_s
    sim._pending_terminal = None
    n_frames, n_events = len(sim.tracking), len(sim.events)
    terminal = "horizon"
    try:
        while s.elapsed < sim._stop_at - 1e-9:
            if not s.carrier or s.carrier not in s.on_pitch.get(s.possession, []):
                s.carrier, _ = sim._nearest(s.possession, s.ball)
            sim._step()
    except _Stop as stop:
        terminal = stop.terminal
    finally:
        sim._stop_at = None
        sim._pending_terminal = None
    return RolloutTrace(
        frames=sim.tracking[n_frames:],
        events=sim.events[n_events:],
        terminal=terminal,
        end_elapsed=s.elapsed,
        score=dict(s.score),
        possession=s.possession,
    )


# ------------------------------------------------------------ from a raw frame


def _assign_roles(meta: MatchMeta, pids: list[str], pos: dict[str, Vec], right: bool) -> dict[str, str]:
    """Formation role (an ANCHORS key) for each player: the meta role when the simulator has an anchor for
    it, otherwise the closest unused anchor to where the player stands."""
    roles: dict[str, str] = {}
    pending: list[str] = []
    for pid in pids:
        role = meta.players[pid].role
        if role in ANCHORS:
            roles[pid] = role
        else:
            pending.append(pid)
    # Goalkeepers first: they must hold the GK anchor, and nobody else may.
    for pid in list(pending):
        if meta.players[pid].position == Position.GK:
            roles[pid] = "GK"
            pending.remove(pid)
    used = set(roles.values())
    generic = {"CB", "CM", "AM"}  # bench roles several players may share
    free = [r for r in ANCHORS if r != "GK" and (r in generic or r not in used)]
    # Greedy: repeatedly give the globally closest (player, anchor) pair its match.
    while pending:
        best: tuple[float, str, str] | None = None
        for pid in pending:
            for role in free:
                d = distance(pos[pid], to_absolute(*ANCHORS[role], right))
                if best is None or d < best[0]:
                    best = (d, pid, role)
        assert best is not None
        _, pid, role = best
        roles[pid] = role
        pending.remove(pid)
        if role not in generic:
            free.remove(role)
        if not free:
            free = [r for r in ANCHORS if r != "GK"]
    return roles


def snapshot_from_frame(
    meta: MatchMeta,
    frame: TrackingFrame,
    *,
    possession: str,
    carrier: str | None,
    score: dict[str, int],
    sent_off: set[str],
    booked: set[str],
    recent_events: list[Event],
    styles: dict[str, SimStyle] | None = None,
    seed: int = 0,
) -> SimSnapshot:
    """Build a simulator state from any tracking frame (the live session, real data).

    Players are those present in the frame. ``carrier`` defaults to the possession team's player nearest the
    ball. ``recent_events`` only seeds the event sequence counter. ``styles`` is accepted for symmetry with
    the live match; pass the styles to ``MatchSimulator.resume`` to use them.
    """
    from matchlens.simulator.engine import MatchSimulator, _State

    ball = next(((e.x, e.y) for e in frame.entities if e.entity_id == "ball"), (PITCH_LENGTH / 2, 34.0))
    positions: dict[str, Vec] = {
        e.entity_id: (e.x, e.y) for e in frame.entities if e.entity_id != "ball" and e.entity_id in meta.players
    }
    sim = MatchSimulator(seed=seed, meta=meta, storyline=rollout_storyline(), styles=styles)
    home, away = meta.home, meta.away
    on_pitch: dict[str, list[str]] = {}
    roles: dict[str, str] = {}
    for team in (home, away):
        ordered = [p for p in team.starting_xi + team.bench if p in positions and p not in sent_off]
        on_pitch[team.team_id] = ordered
        roles.update(_assign_roles(meta, ordered, positions, sim._attacks_right(team.team_id)))
    if possession not in on_pitch:
        raise ValueError(f"unknown possession team: {possession}")
    if carrier is None or carrier not in on_pitch[possession]:
        carrier = min(on_pitch[possession], key=lambda p: distance(positions[p], ball))
    state = _State(
        period=frame.period,
        clock=frame.clock,
        elapsed=frame.elapsed,
        possession=possession,
        carrier=carrier,
        ball=ball,
        score={home.team_id: score.get(home.team_id, 0), away.team_id: score.get(away.team_id, 0)},
        on_pitch=on_pitch,
        roles=roles,
        booked=set(booked),
        sent_off=set(sent_off),
        positions={p: positions[p] for pids in on_pitch.values() for p in pids},
        targets={},
        next_frame=frame.elapsed + 1.0,
    )
    return SimSnapshot(
        meta=meta,
        state=state,
        rng_state=sim.rng.getstate(),
        seq=max((e.sequence for e in recent_events), default=0),
        wander=dict(sim._wander),
        pending_corrections=(),
        last_frame=frame,
        elapsed=frame.elapsed,
        seed=seed,
    )
