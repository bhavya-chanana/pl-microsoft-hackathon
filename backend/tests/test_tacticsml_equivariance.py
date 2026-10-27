"""D2 symmetry, canonicalisation and graph construction of tacticsml.features."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from matchlens.tacticsml import features as F
from matchlens.tacticsml.contract import from_tracking


def frame_at(match, idx: int, possession: str, n: int = 5):
    return from_tracking(match.tracking[idx - n + 1: idx + 1], match.meta, possession)


@pytest.fixture(scope="module")
def home_frame(demo_match):
    return frame_at(demo_match, 300, demo_match.meta.home.team_id)


@pytest.fixture(scope="module")
def away_frame(demo_match):
    return frame_at(demo_match, 300, demo_match.meta.away.team_id)


def same(a: F.GraphSample, b: F.GraphSample, tol=1e-9) -> bool:
    return (np.abs(a.pos - b.pos).max() < tol and np.abs(a.vel - b.vel).max() < tol
            and a.group == b.group and a.node_ids == b.node_ids
            and np.array_equal(a.node_feat, b.node_feat) and np.array_equal(a.edge_feat, b.edge_feat))


def test_group_closure_and_inverse():
    for a, b in itertools.product(F.D2, F.D2):
        assert F.compose(a, b) in F.D2
        assert F.compose(a, b) == F.compose(b, a)
    for g in F.D2:
        assert F.compose(g, F.invert(g)) == "e"
        assert F.compose("e", g) == g
    xy = np.random.default_rng(0).normal(size=(4, 7, 2))
    for g in F.D2:
        once, _ = F.apply_d2(g, xy)
        back, _ = F.apply_d2(F.invert(g), once)
        assert np.allclose(back, xy, atol=1e-12)


def test_apply_then_invert_roundtrip(home_frame):
    s = F.build_sample(home_frame)
    for g in F.D2:
        assert same(F.augment(F.augment(s, g), F.invert(g)), s)


def test_distances_and_features_invariant(home_frame):
    s = F.build_sample(home_frame, profile_keys=("pace",))
    d0 = np.linalg.norm(s.pos[:, :, None] - s.pos[:, None], axis=-1)
    for g in F.D2:
        a = F.augment(s, g)
        d = np.linalg.norm(a.pos[:, :, None] - a.pos[:, None], axis=-1)
        assert np.abs(d - d0).max() < 1e-9
        assert np.array_equal(a.node_feat, s.node_feat)
        assert np.array_equal(a.edge_feat, s.edge_feat)
        # velocity speeds are preserved too
        assert np.abs(np.linalg.norm(a.vel, axis=-1) - np.linalg.norm(s.vel, axis=-1)).max() < 1e-9
    # edge distance feature matches the geometry at the last step
    assert np.allclose(s.edge_feat[..., F.E_DISTANCE][s.node_mask > 0][:, s.node_mask > 0],
                       d0[-1][s.node_mask > 0][:, s.node_mask > 0] / 10.0)


def test_goal_anchors_keep_identity(home_frame):
    s = F.build_sample(home_frame)
    a = F.augment(s, "fx")
    assert a.node_ids[F.GOAL_TARGET] == "goal_target"
    assert np.allclose(s.pos[0, F.GOAL_TARGET], (F.HALF_L, 0.0))
    assert np.allclose(a.pos[0, F.GOAL_TARGET], (-F.HALF_L, 0.0))


@pytest.mark.parametrize("which", ["home", "away"])
def test_canonicalisation_possession_attacks_right(demo_match, home_frame, away_frame, which):
    frame = home_frame if which == "home" else away_frame
    poss = frame.possession_team_id
    c = F.canonicalize(frame)
    assert c.attacking_right[poss] is True
    assert all(not v for t, v in c.attacking_right.items() if t != poss)
    # the possession team's goalkeeper defends x < 0, the opponent's keeper x > 0
    gks = {p.team_id: p for p in c.players if p.is_gk}
    assert gks[poss].x < 0 < gks[next(t for t in gks if t != poss)].x
    s = F.build_sample(frame)
    assert s.pos[-1, 0, 0] < 0  # slot 0 is the possession GK
    assert s.pos[-1, F.N_TEAM, 0] > 0
    assert s.flipped_x == (not frame.attacking_right[poss])
    # helpers invert each other
    xy = np.array([[10.0, 20.0]])
    for right in (True, False):
        assert np.allclose(F.from_canonical_xy(F.to_canonical_xy(xy, right), right), xy)


def test_build_sample_shapes(demo_match, home_frame):
    s = F.build_sample(home_frame, profile_keys=("pace", "height_cm"))
    assert s.pos.shape == (5, 25, 2) and s.vel.shape == (5, 25, 2)
    assert s.node_feat.shape == (25, 7 + 4) and s.edge_feat.shape == (25, 25, 5)
    assert s.node_mask[:22].sum() == 22 and s.node_mask[22:].sum() == 3
    real = [n for n in s.node_ids if n not in ("pad", "ball", "goal_own", "goal_target")]
    assert len(real) == 22 and len(set(real)) == 22
    assert s.node_ids[F.BALL] == "ball"
    assert s.node_feat[:11, F.F_POSSESSION].all() and not s.node_feat[11:22, F.F_POSSESSION].any()
    assert s.node_feat[s.carrier_index, F.F_IS_CARRIER] == 1 and s.node_feat[:, F.F_IS_CARRIER].sum() == 1
    assert s.node_feat[:, F.F_IS_GK].sum() == 2
    assert s.vel_mask[:23].min() == 1  # all players and the ball have history velocities
    assert F.stack([s, s])["pos"].shape == (2, 5, 25, 2)


def test_single_frame_repeats_and_unknown_velocity(demo_match):
    s = F.build_sample(frame_at(demo_match, 300, demo_match.meta.home.team_id, n=1))
    assert s.pos.shape == (5, 25, 2)
    assert np.allclose(s.pos[0], s.pos[-1])
    assert s.vel_mask[:22].sum() == 0


def test_padding_when_player_sent_off(home_frame):
    victim = next(p for p in home_frame.players if p.team_id != home_frame.possession_team_id and not p.is_gk)
    dropped = home_frame.model_copy(update={
        "players": [p for p in home_frame.players if p.player_id != victim.player_id],
        "history": [h.model_copy(update={"players": [p for p in h.players if p.player_id != victim.player_id]})
                    for h in home_frame.history]})
    s = F.build_sample(dropped)
    assert s.node_ids.count("pad") == 1
    i = s.node_ids.index("pad")
    assert 11 <= i < 22 and s.node_mask[i] == 0 and s.node_feat[i].sum() == 0
    assert s.edge_feat[i].sum() == 0 and s.edge_feat[:, i].sum() == 0
    assert s.node_mask[:22].sum() == 21
    b = F.stack([s, F.build_sample(home_frame)])
    assert b["node_mask"].shape == (2, 25) and b["node_mask"][0].sum() == 24


def test_observed_ids_mask(home_frame):
    keep = frozenset(p.player_id for p in home_frame.players[:15])
    s = F.build_sample(home_frame, observed_ids=keep)
    real = [i for i, n in enumerate(s.node_ids[:22]) if n != "pad"]
    assert sum(s.node_feat[i, F.F_OBSERVED] for i in real) == 15
