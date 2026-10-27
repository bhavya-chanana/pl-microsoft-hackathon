"""Baseline tactical model: pitch control, expected threat, scoring and proposals (no network)."""

from __future__ import annotations

import time

import numpy as np
import pytest

from matchlens.domain.pitch import expected_goals
from matchlens.tacticsml import registry
from matchlens.tacticsml.baseline import BaselineModel, _xg
from matchlens.tacticsml.contract import Intent, TBall, TFrame, TPlayer, from_tracking
from matchlens.tacticsml.pitch_control import GRID_NX, GRID_NY, pitch_control
from matchlens.tacticsml.xt import XtGrid

A, B = "A", "B"


def player(pid: str, team: str, x: float, y: float, gk: bool = False) -> TPlayer:
    return TPlayer(player_id=pid, team_id=team, x=x, y=y, is_gk=gk)


def make_frame(carrier_xy: tuple[float, float], opponents: list[tuple[float, float]] | None = None,
               mates: list[tuple[float, float]] | None = None) -> TFrame:
    opponents = opponents if opponents is not None else [(80.0, 25.0), (80.0, 43.0)]
    mates = mates if mates is not None else [(30.0, 10.0)]
    players = [player("a0", A, *carrier_xy)]
    players += [player(f"a{i + 1}", A, *xy) for i, xy in enumerate(mates)]
    players += [player(f"b{i}", B, *xy) for i, xy in enumerate(opponents)]
    return TFrame(players=players, ball=TBall(x=carrier_xy[0], y=carrier_xy[1]), carrier_id="a0",
                  possession_team_id=A, attacking_right={A: True, B: False})


def demo_frame(demo_match, possession: str | None = None, idx: int = 300) -> TFrame:
    team = possession or demo_match.meta.home.team_id
    return from_tracking(demo_match.tracking[idx - 4: idx + 1], demo_match.meta, team)


@pytest.fixture(scope="module")
def model() -> BaselineModel:
    return BaselineModel()


# ---- pitch control --------------------------------------------------------------------------------------------


def test_pitch_control_is_a_probability_grid(demo_match):
    frame = demo_frame(demo_match)
    gx, gy, control = pitch_control(frame)
    assert control.shape == (GRID_NY, GRID_NX) == (21, 32)
    assert len(gx) == 32 and len(gy) == 21
    assert control.min() >= 0.0 and control.max() <= 1.0
    assert 0.0 < control.mean() < 1.0  # the opponent's share is 1 - control by construction


def test_pitch_control_follows_the_players():
    left = make_frame((20.0, 34.0), opponents=[(90.0, 34.0)], mates=[(30.0, 34.0)])
    _, _, control = pitch_control(left)
    assert control[:, :8].mean() > 0.9  # our half of the pitch belongs to us
    assert control[:, -6:].mean() < 0.2  # the opponent's own end belongs to them


def test_pitch_control_is_fast(demo_match):
    frame = demo_frame(demo_match)
    pitch_control(frame)
    t0 = time.perf_counter()
    for _ in range(20):
        pitch_control(frame)
    assert (time.perf_counter() - t0) / 20 < 0.005


# ---- expected threat ------------------------------------------------------------------------------------------


def test_xt_grid_loads_and_rises_towards_goal():
    grid = XtGrid.load()
    assert grid.values.shape == (12, 16)
    assert grid.values.min() >= 0.0
    assert grid.values[:, 12:].mean() > grid.values[:, :4].mean()
    assert grid.value(100.0, 34.0, True) > grid.value(5.0, 34.0, True)
    assert grid.value(5.0, 34.0, False) == pytest.approx(grid.value(100.0, 34.0, True))
    assert "matches_used" in grid.provenance


def test_vectorised_xg_matches_the_domain_model():
    pts = [(95.0, 34.0), (88.0, 20.0), (60.0, 34.0), (100.0, 5.0)]
    got = _xg(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]), True)
    for g, (x, y) in zip(got, pts, strict=True):
        assert g == pytest.approx(expected_goals(x, y), abs=1e-3)


# ---- scoring --------------------------------------------------------------------------------------------------


def test_lone_attacker_near_goal_shoots_more_than_at_halfway(model):
    near = model.score(make_frame((100.0, 34.0)))
    mid = model.score(make_frame((52.5, 34.0)))
    assert near.shot_prob > 0.2
    assert near.shot_prob > 10 * mid.shot_prob
    assert near.goal_prob > mid.goal_prob


def test_defenders_on_the_carrier_raise_turnover_prob(model):
    free = model.score(make_frame((50.0, 34.0), opponents=[(80.0, 20.0), (80.0, 48.0)]))
    pressed = model.score(make_frame((50.0, 34.0), opponents=[(51.5, 34.0), (50.0, 35.5)]))
    assert pressed.turnover_prob > free.turnover_prob + 0.2
    assert pressed.value <= free.value


def test_receiver_probs_sum_to_one_over_teammates(model, demo_match):
    frame = demo_frame(demo_match)
    result = model.score(frame)
    mates = {p.player_id for p in frame.players if p.team_id == frame.possession_team_id} - {frame.carrier_id}
    assert set(result.receiver_probs) == mates
    assert sum(result.receiver_probs.values()) == pytest.approx(1.0, abs=1e-3)
    assert all(0.0 <= v <= 1.0 for v in result.receiver_probs.values())
    assert result.model_version == "baseline-pc-xt/1" == model.version
    assert len(result.pitch_control) == 21 and len(result.pitch_control[0]) == 32
    assert len(result.embedding) == 17


def test_a_blocked_lane_lowers_the_receiver_probability(model):
    open_lane = model.score(make_frame((50.0, 34.0), opponents=[(80.0, 10.0)], mates=[(70.0, 34.0)]))
    blocked = model.score(make_frame((50.0, 34.0), opponents=[(60.0, 34.0), (80.0, 10.0)],
                                     mates=[(70.0, 34.0), (30.0, 34.0)]))
    assert open_lane.receiver_probs["a1"] == pytest.approx(1.0)
    assert blocked.receiver_probs["a1"] < blocked.receiver_probs["a2"]


def test_intent_moves_are_applied_and_xt_delta_is_reported(model):
    frame = make_frame((70.0, 34.0), opponents=[(90.0, 20.0), (90.0, 48.0)], mates=[(50.0, 34.0)])
    plain = model.score(frame)
    assert plain.xt_delta == pytest.approx(0.0)
    moved = model.score(frame, Intent(moves={"a0": (95.0, 34.0)}))
    assert moved.shot_prob > plain.shot_prob
    assert moved.xt_delta == pytest.approx(moved.value - plain.value, abs=1e-4)
    # an explicit reference changes the baseline of the delta
    assert model.score(frame, reference=frame).xt_delta == pytest.approx(0.0)


def test_score_batch_and_similar(model, demo_match):
    frames = [demo_frame(demo_match, idx=i) for i in (200, 300, 400)]
    assert [r.shot_prob for r in model.score_batch(frames)] == [model.score(f).shot_prob for f in frames]
    assert model.similar(frames[0]) == []


def test_score_latency_on_a_demo_frame(model, demo_match):
    frame = demo_frame(demo_match)
    model.score(frame)
    t0 = time.perf_counter()
    for _ in range(10):
        model.score(frame)
    assert (time.perf_counter() - t0) / 10 < 0.025


# ---- proposals ------------------------------------------------------------------------------------------------


def _origin(frame: TFrame) -> dict[str, tuple[float, float]]:
    return {p.player_id: (p.x, p.y) for p in frame.players}


def test_propose_respects_limits_and_improves_the_target(model, demo_match):
    frame = demo_frame(demo_match)
    team = frame.possession_team_id
    start = _origin(frame)
    mates = sorted(p.player_id for p in frame.players if p.team_id == team)
    locked = frozenset(mates[:3])
    proposals = model.propose(frame, team, "shot", k=3, max_move_m=5.0, locked=locked)
    assert 1 <= len(proposals) <= 3
    base = model.score(frame).shot_prob
    for prop in proposals:
        assert prop.moves and not (set(prop.moves) & locked)
        for pid, (x, y) in prop.moves.items():
            assert pid in mates
            assert np.hypot(x - start[pid][0], y - start[pid][1]) <= 5.0 + 0.02
            assert 0 <= x <= 105 and 0 <= y <= 68
        assert prop.gain["objective"] > 0
        assert prop.score.shot_prob > base
        assert prop.score.shot_prob == pytest.approx(model.score(frame, Intent(moves=prop.moves)).shot_prob, abs=1e-3)
    assert len({tuple(sorted(p.moves.items())) for p in proposals}) == len(proposals)
    ranked = [p.gain["objective"] for p in proposals]
    assert ranked == sorted(ranked, reverse=True)


def test_propose_is_deterministic_and_keeps_the_keeper_close(model, demo_match):
    frame = demo_frame(demo_match)
    team = frame.possession_team_id
    one = model.propose(frame, team, "retain", k=2, max_move_m=12.0)
    two = model.propose(frame, team, "retain", k=2, max_move_m=12.0)
    assert [p.moves for p in one] == [p.moves for p in two]
    start = _origin(frame)
    gks = {p.player_id for p in frame.players if p.is_gk}
    for prop in one:
        for pid, (x, y) in prop.moves.items():
            limit = 6.0 if pid in gks else 12.0
            assert np.hypot(x - start[pid][0], y - start[pid][1]) <= limit + 0.02
        assert prop.score.turnover_prob < model.score(frame).turnover_prob


def test_propose_for_the_defending_team_can_press_and_both_teams_reply(model, demo_match):
    frame = demo_frame(demo_match)
    defenders = next(t for t in frame.attacking_right if t != frame.possession_team_id)
    base = model.score(frame).turnover_prob
    pressing = model.propose(frame, defenders, "press", k=2)
    assert pressing and all(p.score.turnover_prob > base for p in pressing)
    own = {p.player_id for p in frame.players if p.team_id == defenders}
    assert all(set(p.moves) <= own for p in pressing)
    both = model.propose(frame, defenders, "press", k=2, both_teams=True)
    assert all(p.gain["objective"] > 0 for p in both)


def test_propose_receiver_target(model, demo_match):
    frame = demo_frame(demo_match)
    team = frame.possession_team_id
    probs = model.score(frame).receiver_probs
    other = min(probs, key=probs.__getitem__)  # the least likely receiver
    proposals = model.propose(frame, team, f"receiver:{other}", k=2)
    base = model.score(frame).receiver_probs[other]
    assert all(p.score.receiver_probs[other] > base for p in proposals)


# ---- registry -------------------------------------------------------------------------------------------------


def test_registry_returns_the_baseline_without_learned_weights(monkeypatch):
    monkeypatch.setenv("MATCHLENS_EVALUATOR", "baseline")
    registry.get_tactical_model.cache_clear()
    try:
        chosen = registry.get_tactical_model()
        assert isinstance(chosen, BaselineModel)
        assert chosen.version == "baseline-pc-xt/1"
    finally:
        registry.get_tactical_model.cache_clear()
