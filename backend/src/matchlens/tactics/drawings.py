"""Telestrator drawings for the Tactics Board (pitch metres, 105 x 68), no language model.

``intent_drawings`` shows what a tactic asks for: runs as arrows to the zone centre, presses as arrows at
the target, screened lanes, the planned ball path. ``rollout_drawings`` shows what a rollout did: the
ball's path, the passes and the shot line. Every point comes from a player position, a named zone or a
simulated event.
"""

from __future__ import annotations

from typing import Any

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH, EventType, MatchMeta
from matchlens.domain.pitch import GOAL_Y, to_absolute, to_relative
from matchlens.interpret.moments import event_strokes
from matchlens.simulator.fork import RolloutTrace
from matchlens.tactics.intent import Action, BallAction, TacticIntent, zone_center
from matchlens.tactics.models import Drawing

Pt = tuple[float, float]
_RUNS = {Action.RUN_TO, Action.ATTACK_BOX, Action.HOLD_WIDTH, Action.DROP_DEEP, Action.OVERLAP, Action.UNDERLAP}
_AT_TARGET = {Action.PRESS_PLAYER, Action.MAN_MARK, Action.PIN_DEFENDER}
MAX_BALL_POINTS = 14


def _pt(x: float, y: float) -> Pt:
    return round(min(max(x, 0.0), PITCH_LENGTH), 2), round(min(max(y, 0.0), PITCH_WIDTH), 2)


def _positions(snap_or_situation: Any) -> tuple[dict[str, Pt], dict[str, str], Pt, str]:
    """(positions, team of each player, ball, possession team) from a snapshot or a Situation."""
    state = getattr(snap_or_situation, "state", None)
    if state is not None:
        teams = {p: tid for tid, pids in state.on_pitch.items() for p in pids}
        return {p: state.positions[p] for p in teams if p in state.positions}, teams, tuple(state.ball), state.possession
    s = snap_or_situation
    return ({p.player_id: (p.x, p.y) for p in s.players}, {p.player_id: p.team_id for p in s.players},
            (s.ball[0], s.ball[1]), s.possession_team_id)


def _right(intent: TacticIntent, snap_or_situation: Any, meta: MatchMeta | None) -> bool:
    meta = meta or getattr(snap_or_situation, "meta", None)
    if meta is not None:
        return intent.team_id == meta.home.team_id
    pos, teams, _, _ = _positions(snap_or_situation)
    mine = [x for p, (x, _) in pos.items() if teams[p] == intent.team_id]
    theirs = [x for p, (x, _) in pos.items() if teams[p] != intent.team_id]
    return sum(mine) / max(1, len(mine)) < sum(theirs) / max(1, len(theirs))


def intent_drawings(intent: TacticIntent, snap_or_situation: Any, meta: MatchMeta | None = None) -> list[Drawing]:
    """Arrows for runs and presses, zones for screened lanes, a path for the ball plan.

    Directives or steps that name someone not on the pitch are skipped (``check_intent`` reports them)."""
    pos, _, ball, _possession = _positions(snap_or_situation)
    right = _right(intent, snap_or_situation, meta)
    team = intent.team_id
    out: list[Drawing] = []

    for d in intent.directives:
        start = pos.get(d.player_id)
        if start is None:
            continue
        zone = zone_center(d.zone, right) if d.zone else None
        label = d.action.value.replace("_", " ")
        if d.action == Action.SCREEN_LANE and zone is not None:
            out.append(Drawing(kind="lane", team_id=team, points=[_pt(*ball), _pt(*zone)], label=label))
            out.append(Drawing(kind="zone", team_id=team, points=[_pt(*zone)], label=label))
        elif d.action in _AT_TARGET:
            target = pos.get(d.target_player_id or "")
            if target is not None:
                out.append(Drawing(kind="arrow", team_id=team, points=[_pt(*start), _pt(*target)], label=label))
        elif d.action in _RUNS:
            end = zone
            if end is None:
                rx, ry = to_relative(*start, right)
                if d.action == Action.DROP_DEEP:
                    end = to_absolute(max(1.0, rx - 12.0), ry, right)
                elif d.action in (Action.OVERLAP, Action.UNDERLAP):
                    bx, by = to_relative(*ball, right)
                    ty = by + (8.0 if (by >= 34) == (d.action == Action.OVERLAP) else -8.0)
                    end = to_absolute(min(bx + 12.0, 100.0), min(max(ty, 2.0), 66.0), right)
            if end is not None:
                kind = "path" if d.action in (Action.OVERLAP, Action.UNDERLAP) else "arrow"
                out.append(Drawing(kind=kind, team_id=team, points=[_pt(*start), _pt(*end)], label=label))
        elif d.action == Action.REST_DEFENCE and zone is not None:
            out.append(Drawing(kind="zone", team_id=team, points=[_pt(*zone)], label=label))

    press = intent.press
    if press is not None and press.cover_shadow_on and press.cover_shadow_on in pos:
        out.append(Drawing(kind="lane", team_id=team, points=[_pt(*ball), _pt(*pos[press.cover_shadow_on])],
                           label="cover shadow"))

    path: list[Pt] = []
    for i, step in enumerate(intent.ball_plan):
        frm = pos.get(step.from_player_id or "") or (path[-1] if path else _pt(*ball))
        if not path:
            path.append(_pt(*frm))
        if step.action == BallAction.SHOOT:
            out.append(Drawing(kind="shot", team_id=team, points=[path[-1], _pt(*to_absolute(105.0, GOAL_Y, right))],
                               label="shoot"))
            continue
        end = pos.get(step.to_player_id or "")
        if end is None and step.to_zone:
            end = zone_center(step.to_zone, right)
        if end is None:
            continue
        path.append(_pt(*end))
        out.append(Drawing(kind="arrow", team_id=team, points=[path[-2], path[-1]],
                           label=f"{i + 1}. {step.action.value.replace('_', ' ')}"))
    return out


class _NoQuality:
    """Stand-in for MatchEngine.pass_quality: rollouts have no pass ratings."""

    @staticmethod
    def pass_quality(_event_id: str) -> None:
        return None


def rollout_drawings(trace: RolloutTrace) -> list[Drawing]:
    """The ball's path, an arrow per completed pass or carry, and a line for each shot."""
    out: list[Drawing] = []
    ball: list[Pt] = []
    for f in trace.frames:
        for e in f.entities:
            if e.entity_id == "ball":
                p = _pt(e.x, e.y)
                if not ball or p != ball[-1]:
                    ball.append(p)
                break
    if len(ball) > MAX_BALL_POINTS:
        step = (len(ball) - 1) / (MAX_BALL_POINTS - 1)
        ball = [ball[round(i * step)] for i in range(MAX_BALL_POINTS)]
    if len(ball) > 1:
        out.append(Drawing(kind="path", team_id=None, points=ball, label="ball"))
    first = trace.events[0].elapsed if trace.events else 0.0
    for e in trace.events:
        if e.type == EventType.SHOT and e.start is not None:
            end = e.end or e.start
            out.append(Drawing(kind="shot", team_id=e.team_id, points=[_pt(e.start.x, e.start.y), _pt(end.x, end.y)],
                               label=f"xG {e.attrs['xg']:.2f}" if "xg" in e.attrs else "shot"))
        elif e.type in (EventType.PASS, EventType.CARRY):
            for s in event_strokes(e, first, _NoQuality()):  # type: ignore[arg-type]
                if len(s.points) == 2:
                    out.append(Drawing(kind="arrow" if s.kind == "lane" else "path", team_id=s.team_id,
                                       points=[(p.x, p.y) for p in s.points]))
    return out
