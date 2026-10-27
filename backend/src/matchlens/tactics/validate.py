"""Check a ``TacticIntent`` against the match before it is simulated.

In the spirit of ``simulator.scenarios.check_storyline``: returns every problem as a plain sentence.
They are fed back to the model in a repair turn, so each one names the offending player or step and
says what would fix it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from matchlens.domain.models import MatchMeta, Position
from matchlens.simulator.style import STYLE_KNOBS
from matchlens.tactics.intent import (
    SPOTS,
    ZONES,
    Action,
    BallAction,
    TacticIntent,
    zone_center,
)

MAX_PASS_M = 45.0  # a ground or driven pass longer than this needs long_ball or switch
NEEDS_ZONE = frozenset({Action.RUN_TO, Action.ATTACK_BOX, Action.SCREEN_LANE, Action.HOLD_WIDTH})
NEEDS_TARGET = frozenset({Action.PIN_DEFENDER, Action.PRESS_PLAYER, Action.MAN_MARK})
LONG_ACTIONS = frozenset({BallAction.LONG_BALL, BallAction.SWITCH})
MIN_REST_DEFENCE = 2
_BOX_SPOTS = frozenset(SPOTS)


@dataclass(frozen=True)
class _View:
    """What the check needs from a frozen moment, whether it came from a simulator snapshot or a Situation."""

    team_of: dict[str, str]  # on-pitch player -> team
    pos: dict[str, tuple[float, float]]
    possession: str
    carrier: str | None

    @classmethod
    def of(cls, snap_or_situation: Any) -> _View:
        state = getattr(snap_or_situation, "state", None)
        if state is not None:  # SimSnapshot
            team_of = {p: tid for tid, pids in state.on_pitch.items() for p in pids}
            return cls(team_of, {p: state.positions[p] for p in team_of if p in state.positions},
                       state.possession, state.carrier or None)
        s = snap_or_situation  # Situation
        return cls({p.player_id: p.team_id for p in s.players}, {p.player_id: (p.x, p.y) for p in s.players},
                   s.possession_team_id, s.carrier_id)


def _name(meta: MatchMeta, pid: str) -> str:
    p = meta.players.get(pid)
    return f"{p.short_name} ({pid})" if p else pid


def _is_final_third(zone: str) -> bool:
    return zone.endswith(":final") or zone in _BOX_SPOTS


def check_intent(
    meta: MatchMeta,
    snap_or_situation: Any,
    intent: TacticIntent,
    horizon_s: float,
    context_keys: set[str] | None = None,
) -> list[str]:
    """Everything that would stop the simulator playing the tactic as written. Empty means valid."""
    problems: list[str] = []
    team = intent.team_id
    if team not in (meta.home.team_id, meta.away.team_id):
        return [f"unknown team {team}; use {meta.home.team_id} or {meta.away.team_id}"]
    view = _View.of(snap_or_situation)
    opp = meta.opponent(team).team_id
    in_possession = view.possession == team

    def own(pid: str, where: str) -> bool:
        if pid not in meta.players:
            problems.append(f"{where}: unknown player {pid}")
            return False
        if pid not in view.team_of:
            problems.append(f"{where}: {_name(meta, pid)} is not on the pitch")
            return False
        if view.team_of[pid] != team:
            problems.append(f"{where}: {_name(meta, pid)} plays for {view.team_of[pid]}, not {team}")
            return False
        return True

    def opponent(pid: str, where: str) -> bool:
        if pid not in meta.players:
            problems.append(f"{where}: unknown player {pid}")
            return False
        if pid not in view.team_of:
            problems.append(f"{where}: {_name(meta, pid)} is not on the pitch")
            return False
        if view.team_of[pid] != opp:
            problems.append(f"{where}: {_name(meta, pid)} is a team-mate; the target must be an opponent ({opp})")
            return False
        return True

    def zone_ok(zone: str | None, where: str) -> bool:
        if zone is not None and zone not in ZONES:
            problems.append(f"{where}: unknown zone '{zone}'; use lane:third (e.g. right:att) or a spot "
                            f"({', '.join(sorted(SPOTS))})")
            return False
        return True

    # ------------------------------------------------------------ directives
    windows: dict[str, list[tuple[float, float, int]]] = {}
    for i, d in enumerate(intent.directives, start=1):
        where = f"directive {i} ({d.action.value})"
        known = own(d.player_id, where)
        valid_zone = zone_ok(d.zone, where)
        if d.action in NEEDS_ZONE and d.zone is None:
            problems.append(f"{where}: {d.action.value} needs a zone")
        if d.action in NEEDS_TARGET:
            if d.target_player_id is None:
                problems.append(f"{where}: {d.action.value} needs target_player_id (an opponent)")
            else:
                opponent(d.target_player_id, f"{where} target")
        elif d.target_player_id is not None:
            opponent(d.target_player_id, f"{where} target")
        if not d.end_s > d.start_s:
            problems.append(f"{where}: end_s ({d.end_s}) must be after start_s ({d.start_s})")
        if d.end_s > horizon_s + 1e-9:
            problems.append(f"{where}: ends at {d.end_s}s, after the {horizon_s:g}s horizon")
        if d.start_s >= horizon_s:
            problems.append(f"{where}: starts at {d.start_s}s, after the {horizon_s:g}s horizon")
        if known and meta.players[d.player_id].position == Position.GK:
            if d.action == Action.ATTACK_BOX or (d.action == Action.RUN_TO and d.zone and valid_zone
                                                 and _is_final_third(d.zone)):
                problems.append(f"{where}: the goalkeeper {_name(meta, d.player_id)} cannot go into the final third")
        windows.setdefault(d.player_id, []).append((d.start_s, d.end_s, i))
    for pid, spans in windows.items():
        spans.sort()
        for (s0, e0, i0), (s1, e1, i1) in zip(spans, spans[1:], strict=False):
            if s1 < e0 - 1e-9:
                problems.append(f"directives {i0} and {i1}: {_name(meta, pid)} has overlapping time windows "
                                f"({s0:g}-{e0:g}s and {s1:g}-{e1:g}s); give each player one job at a time")

    # --------------------------------------------------------------- ball plan
    if intent.ball_plan and not in_possession:
        problems.append(f"ball_plan: {team} does not have the ball; use a press plan or leave ball_plan empty")
    shoots = [i for i, b in enumerate(intent.ball_plan) if b.action == BallAction.SHOOT]
    if len(shoots) > 1:
        problems.append("ball_plan: at most one shoot step")
    elif shoots and shoots[0] != len(intent.ball_plan) - 1:
        problems.append("ball_plan: shoot must be the last step")
    holder = view.carrier
    for i, b in enumerate(intent.ball_plan, start=1):
        where = f"ball step {i} ({b.action.value})"
        if b.from_player_id is not None:
            if own(b.from_player_id, f"{where} from_player_id"):
                if i == 1 and view.carrier is not None and in_possession and b.from_player_id != view.carrier:
                    problems.append(f"{where}: the first step must start with the ball carrier "
                                    f"{_name(meta, view.carrier)}, not {_name(meta, b.from_player_id)}")
                holder = b.from_player_id
        zone_ok(b.to_zone, where)
        if b.action == BallAction.SHOOT:
            continue
        receiver_ok = False
        if b.to_player_id is not None:
            receiver_ok = own(b.to_player_id, f"{where} to_player_id")
            if b.to_player_id == holder:
                problems.append(f"{where}: {_name(meta, b.to_player_id)} cannot pass to himself")
                receiver_ok = False
        elif b.to_zone is None and b.action != BallAction.CARRY:
            problems.append(f"{where}: give a to_player_id or a to_zone")
        if (receiver_ok and b.to_player_id and holder and holder in view.pos and b.to_player_id in view.pos
                and b.action not in LONG_ACTIONS and b.action != BallAction.CARRY):
            gap = math.dist(view.pos[holder], view.pos[b.to_player_id])
            if gap > MAX_PASS_M:
                problems.append(f"{where}: {_name(meta, b.to_player_id)} is {gap:.0f} m from "
                                f"{_name(meta, holder)}; use long_ball or switch, or pick a nearer receiver")
        if b.to_player_id is not None and b.action != BallAction.CARRY:
            holder = b.to_player_id

    # ------------------------------------------------------------------ press
    if intent.press is not None:
        if in_possession:
            problems.append(f"press: {team} has the ball; a press plan only applies out of possession")
        if intent.press.cover_shadow_on is not None:
            opponent(intent.press.cover_shadow_on, "press cover_shadow_on")

    # ------------------------------------------------------------- style, rest
    for c in intent.style_changes:
        if c.knob not in STYLE_KNOBS:
            problems.append(f"style change: unknown knob '{c.knob}'; use one of {', '.join(STYLE_KNOBS)}")
        elif not math.isfinite(c.delta):
            problems.append(f"style change: {c.knob} delta must be a finite number")
    if in_possession and intent.rest_defence < MIN_REST_DEFENCE:
        problems.append(f"rest_defence: keep at least {MIN_REST_DEFENCE} players behind the ball while in "
                        f"possession (got {intent.rest_defence})")

    # ----------------------------------------------------------------- cites
    if context_keys is not None:
        unknown = [c for c in intent.cites if c not in context_keys]
        if unknown:
            problems.append(f"cites: {', '.join(unknown)} are not in the context; cite only keys you were given")
    return problems


def zone_point(zone: str, team_id: str, meta: MatchMeta) -> tuple[float, float]:
    """Absolute centre of a zone for a team (the home team attacks towards x=105)."""
    return zone_center(zone, team_id == meta.home.team_id)
