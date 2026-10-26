"""Fork API: snapshot a simulator, resume it, and roll the future forward."""

from __future__ import annotations

import pytest

from matchlens.domain.models import EventType
from matchlens.domain.pitch import ANCHORS
from matchlens.simulator.engine import MatchSimulator
from matchlens.simulator.fork import RolloutTrace, rollout_storyline, snapshot_from_frame

HORIZON = 12.0


def valid_terminal(terminal: str) -> bool:
    return terminal in {"goal", "shot", "turnover", "horizon"} or terminal.startswith("restart:")


@pytest.fixture(scope="module")
def mid_match_snapshot():
    """A simulator paused five minutes into a neutral-storyline match."""
    sim = MatchSimulator(seed=5, storyline=rollout_storyline())
    sim.run_until(300)
    return sim.snapshot()


def test_exact_resume_matches_the_uninterrupted_run():
    sim = MatchSimulator(seed=9, storyline=rollout_storyline())
    sim.run_until(420)
    snap = sim.snapshot()
    straight = sim.rollout(30)  # the original keeps going...
    forked = MatchSimulator.resume(snap, id_prefix="").rollout(30)  # ...and so does the restored copy
    assert straight.terminal == forked.terminal
    assert [f.model_dump() for f in straight.frames] == [f.model_dump() for f in forked.frames]
    assert [e.model_dump() for e in straight.events] == [e.model_dump() for e in forked.events]


def test_a_snapshot_can_seed_many_rollouts(mid_match_snapshot):
    a = MatchSimulator.resume(mid_match_snapshot).rollout(HORIZON)
    b = MatchSimulator.resume(mid_match_snapshot).rollout(HORIZON)  # the first rollout must not leak into the snapshot
    assert [e.model_dump() for e in a.events] == [e.model_dump() for e in b.events]
    c = MatchSimulator.resume(mid_match_snapshot, seed=1).rollout(HORIZON)
    d = MatchSimulator.resume(mid_match_snapshot, seed=2).rollout(HORIZON)
    assert [f.model_dump() for f in c.frames] != [f.model_dump() for f in d.frames]


def test_rollout_events_use_the_id_prefix(mid_match_snapshot):
    live_ids = {e.event_id for e in MatchSimulator.resume(mid_match_snapshot, id_prefix="").rollout(HORIZON).events}
    forked = MatchSimulator.resume(mid_match_snapshot, id_prefix="r1-").rollout(HORIZON)
    assert forked.events and all(e.event_id.startswith("r1-") for e in forked.events)
    assert live_ids.isdisjoint(e.event_id for e in forked.events)


@pytest.mark.parametrize("dt", [1.0, 0.2])
def test_horizon_is_respected(mid_match_snapshot, dt):
    start = mid_match_snapshot.elapsed
    for seed in range(8):
        trace = MatchSimulator.resume(mid_match_snapshot, seed=seed, frame_dt=dt).rollout(HORIZON)
        assert valid_terminal(trace.terminal)
        assert all(f.elapsed <= start + HORIZON + 1e-6 for f in trace.frames)
        assert all(f.elapsed > start for f in trace.frames)


def test_frames_are_5hz_with_plausible_speeds(mid_match_snapshot):
    for seed in range(10):
        trace = MatchSimulator.resume(mid_match_snapshot, seed=seed, frame_dt=0.2).rollout(HORIZON)
        times = [mid_match_snapshot.last_frame.elapsed] + [f.elapsed for f in trace.frames]
        gaps = [round(b - a, 1) for a, b in zip(times[1:], times[2:], strict=False)]
        assert gaps and all(g == 0.2 for g in gaps), gaps
        top = max(en.speed for f in trace.frames for en in f.entities if en.entity_id != "ball")
        assert top <= 11.0, top


def test_a_rollout_stops_at_the_first_shot_or_turnover(mid_match_snapshot):
    seen: set[str] = set()
    for seed in range(30):
        trace = MatchSimulator.resume(mid_match_snapshot, seed=seed).rollout(60)
        seen.add(trace.terminal.split(":")[0])
        shots = [e for e in trace.events if e.type == EventType.SHOT]
        assert len(shots) <= 1
        if trace.terminal in ("goal", "shot"):
            assert len(shots) == 1
        if trace.terminal == "goal":
            assert any(e.type == EventType.GOAL for e in trace.events)
            assert sum(trace.score.values()) == 1
        if trace.terminal == "turnover":
            assert trace.events[-1].type == EventType.POSSESSION_CHANGE
    assert "turnover" in seen


def demo_state_at(match, index: int):
    """(frame, possession, carrier, score, sent_off, booked, recent events) of the demo match after `index` frames."""
    frame = match.tracking[index]
    past = [e for e in match.events if e.elapsed <= frame.elapsed]
    holder = next(e for e in reversed(past) if e.type in (EventType.POSSESSION_CHANGE, EventType.KICKOFF))
    score = {match.meta.home.team_id: 0, match.meta.away.team_id: 0}
    for e in past:
        if e.type == EventType.GOAL:
            score[e.team_id] += 1
    cards = [e for e in past if e.type == EventType.CARD]
    sent_off = {e.player_id for e in cards if e.outcome.value != "yellow"}
    booked = {e.player_id for e in cards if e.outcome.value == "yellow"}
    return frame, holder.team_id, holder.player_id, score, sent_off, booked, past[-20:]


@pytest.mark.parametrize("index", [200, 1800, 4000])
def test_rollout_from_a_demo_frame(demo_match, index):
    frame, team, carrier, score, sent_off, booked, recent = demo_state_at(demo_match, index)
    snap = snapshot_from_frame(
        demo_match.meta, frame, possession=team, carrier=None, score=score,
        sent_off=sent_off, booked=booked, recent_events=recent,
    )
    assert snap.elapsed == frame.elapsed
    assert snap.state.carrier in snap.state.on_pitch[team]
    on_frame = {e.entity_id for e in frame.entities} - {"ball"}
    assert {p for pids in snap.state.on_pitch.values() for p in pids} == on_frame
    assert set(snap.state.roles) == on_frame
    terminals = set()
    for seed in range(20):
        trace = MatchSimulator.resume(snap, seed=seed, frame_dt=0.2).rollout(10)
        assert isinstance(trace, RolloutTrace)
        assert valid_terminal(trace.terminal), trace.terminal
        assert trace.frames or trace.terminal != "horizon"
        assert all(frame.elapsed < f.elapsed <= frame.elapsed + 10 + 1e-6 for f in trace.frames)
        terminals.add(trace.terminal.split(":")[0])
    assert len(terminals) >= 2  # not stuck in one outcome


def test_snapshot_from_frame_assigns_unknown_roles(demo_match):
    frame, team, _, score, sent_off, booked, recent = demo_state_at(demo_match, 800)
    players = {
        pid: p.model_copy(update={"role": "SW" if p.position.value != "GK" else "?"})
        for pid, p in demo_match.meta.players.items()
    }
    meta = demo_match.meta.model_copy(update={"players": players})
    snap = snapshot_from_frame(
        meta, frame, possession=team, carrier=None, score=score, sent_off=sent_off, booked=booked, recent_events=recent
    )
    roles = snap.state.roles
    assert all(r in ANCHORS for r in roles.values())
    for pids in snap.state.on_pitch.values():
        assert sum(roles[p] == "GK" for p in pids) == 1
    assert MatchSimulator.resume(snap, seed=3).rollout(5).terminal


def test_sent_off_players_stay_off_the_forked_pitch(demo_match):
    frame, team, _, score, sent_off, booked, recent = demo_state_at(demo_match, 800)
    extra = next(p for p in demo_match.meta.away.starting_xi if p != "HBC-01")
    snap = snapshot_from_frame(
        demo_match.meta, frame, possession=team, carrier=None, score=score, sent_off={extra}, booked=booked,
        recent_events=recent,
    )
    assert extra not in snap.state.on_pitch["HBC"] and extra not in snap.state.positions
    trace = MatchSimulator.resume(snap, seed=1).rollout(8)
    assert all(extra not in {en.entity_id for en in f.entities} for f in trace.frames)
