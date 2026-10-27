"""Execute a ``TacticIntent`` inside the simulator.

The engine asks an :class:`IntentController` four questions, all behind
``if self._intent is not None`` so a plain match never reaches this module:

* where should this player be right now (``override``, ``is_sprinting``)?
* which teammate should make the next off-ball run (``directed_run``)?
* what is the ball carrier trying to do next (``active_step``, ``consume``)?
* who should the next pass favour (``pass_bias``)?

The intent says what to try; the simulator still decides what happens. A
directive moves a player, a ball-plan step tilts a decision, and the usual
pressure, tackle and pass-success rolls play out on top.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matchlens.domain.models import MatchMeta
from matchlens.domain.pitch import ANCHORS, to_absolute, to_relative
from matchlens.simulator.style import SimStyle
from matchlens.tactics.intent import (
    Action,
    BallAction,
    BallStep,
    PressTrigger,
    RoleDirective,
    TacticIntent,
    zone_center,
)

if TYPE_CHECKING:
    from matchlens.simulator.engine import _State

Vec = tuple[float, float]

# Directives where the player sprints (the rest drift at anchor pace).
_SPRINTS = frozenset(
    {Action.RUN_TO, Action.OVERLAP, Action.UNDERLAP, Action.ATTACK_BOX, Action.DROP_DEEP,
     Action.PIN_DEFENDER, Action.PRESS_PLAYER, Action.MAN_MARK}
)
# Directives that are an off-ball run towards a place.
_RUNS = frozenset({Action.RUN_TO, Action.OVERLAP, Action.UNDERLAP, Action.ATTACK_BOX})

PLANNED_RECEIVER_BIAS = 2.5  # added to the pass score (which is exponentiated) of the planned receiver
PLANNED_ZONE_BIAS = 2.0  # at the centre of the planned zone, fading to nothing 15 m away
COVER_SHADOW_PENALTY = 1.5
REST_DEFENCE_GAP = 14.0  # metres the rest defence holds behind the ball


class IntentController:
    def __init__(
        self,
        intent: TacticIntent,
        meta: MatchMeta,
        attacking_right: dict[str, bool],
        start_elapsed: float,
    ) -> None:
        self.intent = intent
        self.team = intent.team_id
        self.meta = meta
        self.start = start_elapsed
        self._right = attacking_right
        self._step_idx = 0
        self._by_player: dict[str, list[RoleDirective]] = {}
        for d in intent.directives:
            self._by_player.setdefault(d.player_id, []).append(d)
        team = meta.team(intent.team_id)
        self._ours = frozenset(team.starting_xi + team.bench)
        self._rest: set[str] | None = None
        self._styled: tuple[SimStyle, SimStyle] | None = None  # (base style, with the press plan applied)

    # ------------------------------------------------------------------ style

    def adjust_style(self, team: str, style: SimStyle) -> SimStyle:
        """The press plan sets the team's press_intensity. (style_changes are the caller's to apply with
        SimStyle.with_delta before building the simulator.)"""
        press = self.intent.press
        if team != self.team or press is None or press.trigger == PressTrigger.NONE:
            return style
        if self._styled is None or self._styled[0] is not style:
            self._styled = (style, style.model_copy(update={"press_intensity": press.intensity}))
        return self._styled[1]

    # -------------------------------------------------------------- directives

    def _active(self, pid: str, elapsed: float) -> RoleDirective | None:
        t = elapsed - self.start
        for d in self._by_player.get(pid, ()):
            if d.start_s <= t <= d.end_s:
                return d
        return None

    def is_sprinting(self, pid: str, elapsed: float) -> bool:
        d = self._active(pid, elapsed)
        return d is not None and d.action in _SPRINTS

    def _rest_defenders(self, s: _State) -> set[str]:
        """The ``rest_defence`` deepest outfield players: they stay behind the ball while the team attacks."""
        if self._rest is None:
            pids = [p for p in s.on_pitch[self.team] if s.roles[p] != "GK"]
            pids.sort(key=lambda p: ANCHORS[s.roles[p]][0])
            self._rest = set(pids[: self.intent.rest_defence])
        return self._rest

    def override(self, pid: str, s: _State, base: Vec) -> Vec | None:
        """Where a directive (or the rest defence) sends this player instead of his formation anchor."""
        if pid not in self._ours:
            return None
        team = self.team
        right = self._right[team]
        d = self._active(pid, s.elapsed)
        if d is not None:
            point = self._directive_point(d, s, base, right)
            if point is not None:
                return point
        if s.possession == team and pid in self._rest_defenders(s):
            bx, _ = to_relative(*s.ball, right)
            x, y = to_relative(*base, right)
            return to_absolute(max(1.0, min(x, bx - REST_DEFENCE_GAP)), y, right)
        return None

    def _directive_point(self, d: RoleDirective, s: _State, base: Vec, right: bool) -> Vec | None:
        action = d.action
        zone = zone_center(d.zone, right) if d.zone else None
        bx, by = to_relative(*s.ball, right)
        x, y = to_relative(*base, right)
        if action in (Action.RUN_TO, Action.SCREEN_LANE):
            return zone
        if action == Action.ATTACK_BOX:
            return zone or zone_center("box", right)
        if action == Action.HOLD_WIDTH:
            return zone or to_absolute(x, 62.0 if y >= 34 else 6.0, right)
        if action == Action.DROP_DEEP:
            return zone or to_absolute(max(1.0, x - 12.0), y, right)
        if action in (Action.OVERLAP, Action.UNDERLAP):
            carrier = s.positions.get(s.carrier, s.ball)
            cx, cy = to_relative(*carrier, right)
            side = 1.0 if cy >= 34 else -1.0  # which touchline the carrier is nearer
            outside = action == Action.OVERLAP
            ty = cy + side * (8.0 if outside else -8.0)
            return zone or to_absolute(min(cx + 12.0, 100.0), min(max(ty, 2.0), 66.0), right)
        target = s.positions.get(d.target_player_id or "")
        if target is None:
            return zone
        tx, ty = target
        if action == Action.PRESS_PLAYER:
            return (tx, ty)
        if action == Action.MAN_MARK:
            rx, ry = to_relative(tx, ty, right)
            return to_absolute(max(1.0, rx - 1.5), ry, right)  # goal-side of him
        if action == Action.PIN_DEFENDER:
            rx, ry = to_relative(tx, ty, right)
            return to_absolute(min(rx + 3.0, 104.0), ry, right)  # on his shoulder, towards goal
        return None

    def directed_run(self, team: str, runners: list[str], s: _State) -> tuple[str, Vec] | None:
        """A teammate the intent wants running now and who has not arrived yet, with where he runs to."""
        if team != self.team:
            return None
        right = self._right[team]
        for pid in runners:
            d = self._active(pid, s.elapsed)
            if d is None or d.action not in _RUNS:
                continue
            point = self._directive_point(d, s, s.positions[pid], right)
            if point is not None and abs(point[0] - s.positions[pid][0]) + abs(point[1] - s.positions[pid][1]) > 4.0:
                return pid, point
        return None

    # --------------------------------------------------------------- ball plan

    def active_step(self, team: str, carrier: str) -> BallStep | None:
        """The ball-plan step the carrier should attempt now, if his team is the one the intent is for."""
        if team != self.team or self._step_idx >= len(self.intent.ball_plan):
            return None
        step = self.intent.ball_plan[self._step_idx]
        if step.from_player_id is not None and step.from_player_id != carrier:
            return None
        return step

    def consume(self) -> None:
        """The current step has been attempted: move on to the next one."""
        self._step_idx += 1

    def pass_bias(self, team: str, carrier: str, receiver: str, receiver_pos: Vec) -> float:
        """Score added to a pass option: towards the planned receiver or zone, away from the shadowed man."""
        press = self.intent.press
        if team != self.team:
            if press is not None and press.cover_shadow_on == receiver:
                return -COVER_SHADOW_PENALTY
            return 0.0
        step = self.active_step(team, carrier)
        if step is None:
            return 0.0
        bias = 0.0
        if step.to_player_id == receiver:
            bias += PLANNED_RECEIVER_BIAS
        zone = step.to_zone or ("cutback_spot" if step.action == BallAction.CUTBACK else None)
        if zone is not None:
            zx, zy = zone_center(zone, self._right[team])
            gap = ((receiver_pos[0] - zx) ** 2 + (receiver_pos[1] - zy) ** 2) ** 0.5
            bias += PLANNED_ZONE_BIAS * max(0.0, 1.0 - gap / 15.0)
        return bias
