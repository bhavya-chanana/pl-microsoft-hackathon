"""Style knobs and tactic intents change how the simulator plays, without touching the default match."""

from __future__ import annotations

import pytest

from matchlens.domain.models import EventType
from matchlens.domain.pitch import distance
from matchlens.simulator.engine import MatchSimulator, generate_match
from matchlens.simulator.fork import rollout_storyline
from matchlens.simulator.intent_exec import IntentController
from matchlens.simulator.style import NEUTRAL, SimStyle
from matchlens.tactics.intent import (
    Action,
    BallAction,
    BallStep,
    Mechanism,
    RoleDirective,
    TacticIntent,
    zone_center,
)
from tests.test_sim_golden import GOLDEN, match_digest

SEEDS = range(20)


@pytest.fixture(scope="module")
def snapshots():
    """Paused moments from a neutral match, with different teams on the ball."""
    out = []
    for elapsed in (300, 900, 1500, 2100):
        sim = MatchSimulator(seed=5, storyline=rollout_storyline())
        sim.run_until(elapsed)
        out.append(sim.snapshot())
    return out


def attacking_right(snap) -> dict[str, bool]:
    return {snap.meta.home.team_id: True, snap.meta.away.team_id: False}


def roll(snap, seeds=SEEDS, horizon=15, **kwargs):
    return [MatchSimulator.resume(snap, seed=k, **kwargs).rollout(horizon) for k in seeds]


def make_intent(team_id: str, **overrides) -> TacticIntent:
    fields = dict(
        team_id=team_id, title="test", idea="test", mechanism=Mechanism.OVERLOAD, directives=[], ball_plan=[],
        press=None, style_changes=[], rest_defence=0, cites=[],
    )
    return TacticIntent(**{**fields, **overrides})


def test_neutral_styles_leave_the_match_unchanged():
    styles = {"NBR": NEUTRAL, "HBC": SimStyle()}
    assert match_digest(MatchSimulator(seed=3, styles=styles).run()) == GOLDEN[3]
    assert match_digest(generate_match(seed=3)) == GOLDEN[3]


def test_pressing_harder_creates_more_pressure(snapshots):
    def pressures(intensity: float) -> int:
        total = 0
        for snap in snapshots[:2]:
            defender = snap.meta.opponent(snap.state.possession).team_id
            styles = {defender: SimStyle(press_intensity=intensity)}
            total += sum(
                e.type == EventType.PRESSURE for t in roll(snap, styles=styles) for e in t.events
            )
        return total

    low, high = pressures(0.5), pressures(1.8)
    assert high > 2 * low, (low, high)


def test_directness_plays_more_long_balls(snapshots):
    def long_share(directness: float) -> float:
        long = total = 0
        for snap in snapshots:
            team = snap.state.possession
            for trace in roll(snap, styles={team: SimStyle(directness=directness)}):
                for e in trace.events:
                    if e.type == EventType.PASS and e.team_id == team and e.start and e.end:
                        total += 1
                        long += distance((e.start.x, e.start.y), (e.end.x, e.end.y)) > 30
        return long / total

    assert long_share(2.2) > long_share(0.5) + 0.03


def test_shot_appetite_and_verticality_are_wired(snapshots):
    snap = snapshots[0]
    team = snap.state.possession
    for knob, value in (("shot_appetite", 2.5), ("verticality", 2.0), ("crossing", 2.5), ("tempo", 1.6)):
        traces = roll(snap, styles={team: SimStyle(**{knob: value})})
        assert all(t.terminal for t in traces)  # runs cleanly with the knob at its limit
    # Tempo shortens the wait for a pass to be received, so more happens in the same 15 s.
    slow = sum(len(t.events) for t in roll(snap, styles={team: SimStyle(tempo=0.6)}))
    fast = sum(len(t.events) for t in roll(snap, styles={team: SimStyle(tempo=1.6)}))
    assert fast >= slow


def test_a_high_block_pushes_the_defence_up(snapshots):
    snap = snapshots[0]
    defender = snap.meta.opponent(snap.state.possession).team_id
    right = attacking_right(snap)[defender]

    def mean_depth(style: SimStyle) -> float:
        xs = []
        for trace in roll(snap, seeds=range(6), horizon=10, styles={defender: style}):
            for f in trace.frames[-5:]:
                for en in f.entities:
                    if en.entity_id in snap.state.on_pitch[defender] and snap.state.roles[en.entity_id] != "GK":
                        xs.append(en.x if right else 105 - en.x)
        return sum(xs) / len(xs)

    assert mean_depth(SimStyle(block_height=12.0, line_height=6.0)) > mean_depth(SimStyle(block_height=-12.0)) + 5


def test_run_to_directive_moves_the_player_into_the_zone(snapshots):
    snap = snapshots[1]
    team = snap.state.possession
    zone = "centre:final"
    target = zone_center(zone, attacking_right(snap)[team])
    runner = next(
        p for p in snap.state.on_pitch[team]
        if p != snap.state.carrier and snap.state.roles[p] != "GK"
        and 20 < distance(snap.state.positions[p], target) < 45
    )
    directive = RoleDirective(player_id=runner, action=Action.RUN_TO, zone=zone, target_player_id=None, start_s=0, end_s=8)
    eligible = arrived = 0
    for seed in SEEDS:
        controller = IntentController(make_intent(team, directives=[directive]), snap.meta, attacking_right(snap), snap.elapsed)
        trace = MatchSimulator.resume(snap, seed=seed, intent=controller, frame_dt=0.2).rollout(8)
        spots = [(en.x, en.y) for f in trace.frames for en in f.entities if en.entity_id == runner]
        if len(spots) >= 30:  # at least 6 s of play before any early stop
            # He may be asked for the ball first and arrive late, so judge his closest approach.
            eligible += 1
            arrived += min(distance(p, target) for p in spots) < 5
    assert eligible >= 5 and arrived >= 0.7 * eligible, (arrived, eligible)
    # Without the directive he does not make the run.
    plain = roll(snap, seeds=[0], horizon=8, frame_dt=0.2)[0]
    ends = [(en.x, en.y) for en in plain.frames[-1].entities if en.entity_id == runner]
    assert distance(ends[0], target) > 15


def test_a_ball_plan_raises_the_planned_receivers_share(snapshots):
    gains = 0
    for snap in snapshots:
        team, carrier = snap.state.possession, snap.state.carrier
        mates = [p for p in snap.state.on_pitch[team] if p != carrier and snap.state.roles[p] != "GK"]
        # A midfield option about 20 m away: plausible but never the obvious choice.
        planned = min(mates, key=lambda p: abs(distance(snap.state.positions[p], snap.state.positions[carrier]) - 20))
        step = BallStep(action=BallAction.PASS, from_player_id=carrier, to_player_id=planned, to_zone=None)

        def first_pass_share(intent_for) -> int:
            hits = 0
            for seed in range(40):
                controller = intent_for()
                trace = MatchSimulator.resume(snap, seed=seed, intent=controller).rollout(10)
                first = next((e for e in trace.events if e.type == EventType.PASS and e.player_id == carrier), None)
                hits += first is not None and first.related_player_id == planned
            return hits

        base = first_pass_share(lambda: None)
        planned_hits = first_pass_share(
            lambda: IntentController(make_intent(team, ball_plan=[step]), snap.meta, attacking_right(snap), snap.elapsed)
        )
        gains += planned_hits - base
    assert gains >= 20, gains
