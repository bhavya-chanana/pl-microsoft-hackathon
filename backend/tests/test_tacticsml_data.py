"""Training data for the tactical models: label rules, the synthetic source, shard format and splits (no network)."""

from __future__ import annotations

import numpy as np
import pytest

from matchlens.simulator.engine import generate_match
from matchlens.tacticsml import features as F
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.labels import (
    RECEIVER_LOST, RECEIVER_PLAYER, RECEIVER_UNKNOWN, EventStream, LEvent, receiver_index,
)
from matchlens.tacticsml.train.sources import statsbomb360, synthetic
from matchlens.tacticsml.train.sources._common import build_from_iter


def ev(t, team, kind, player="p", receiver=None, outcome="none", period=1):
    return LEvent(t=t, team_id=team, player_id=player, kind=kind, receiver_id=receiver, outcome=outcome,
                  period=period)


# ---------------------------------------------------------------- labels

def test_receiver_of_pass_and_carry_and_lost():
    a, b = "A", "B"
    pas = ev(10, a, "pass", "a1", "a2", "success")
    stream = EventStream([pas, ev(13, a, "pass", "a2", None, "fail"), ev(20, a, "carry", "a3"),
                          ev(30, b, "pass", "b1", "b2", "success")], {1: 100})
    lab = stream.labels_at(10, 1, a, pas)
    assert (lab.receiver_id, lab.receiver_state) == ("a2", RECEIVER_PLAYER)
    failed = stream.events[1]
    assert stream.labels_at(13, 1, a, failed).receiver_state == RECEIVER_LOST
    carry = stream.events[2]
    assert stream.labels_at(20, 1, a, carry).receiver_id == "a3"
    # no anchoring event: the next pass decides
    assert stream.labels_at(9, 1, a).receiver_id == "a2"
    # next action belongs to the opponent -> the ball is lost
    assert stream.labels_at(25, 1, a).receiver_state == RECEIVER_LOST
    # nothing within 6 s -> unknown
    assert stream.labels_at(60, 1, b).receiver_state == RECEIVER_UNKNOWN


def test_shot_window_boundary_and_unknown_at_end():
    a = "A"
    stream = EventStream([ev(0, a, "pass", "a1", "a2", "success"), ev(10, a, "shot", "a2", outcome="miss"),
                          ev(10.5, a, "shot", "a2", outcome="miss")], {1: 100})
    assert stream.labels_at(0, 1, a).shot10 == 1  # a shot exactly 10 s later counts
    assert stream.labels_at(0.2, 1, a).shot10 == 1  # the 10 s and 10.5 s shots, window to 10.2: the first one
    only_late = EventStream([ev(10.5, a, "shot", "a2", outcome="miss"), ev(0, a, "pass", "a1", "a2", "success")],
                            {1: 100})
    assert only_late.labels_at(0, 1, a).shot10 == 0  # 10.5 s is outside the window
    # window runs past the end of the data and nothing happened: unknown
    short = EventStream([ev(0, a, "pass", "a1", "a2", "success")], {1: 5})
    assert short.labels_at(0, 1, a).shot10 == -1
    assert short.labels_at(0, 1, a).turn5 == 0  # 5 s window is fully inside the data


def test_goal_and_concede():
    a, b = "A", "B"
    stream = EventStream([ev(0, a, "pass", "a1", "a2", "success"), ev(4, a, "shot", "a2", outcome="goal"),
                          ev(8, b, "pass", "b1", "b2", "success"), ev(15, b, "shot", "b2", outcome="goal")],
                         {1: 100})
    lab = stream.labels_at(0, 1, a)
    assert (lab.shot10, lab.goal10, lab.concede10) == (1, 1, 0)
    lab_b = stream.labels_at(8, 1, b)
    assert (lab_b.shot10, lab_b.goal10, lab_b.concede10) == (1, 1, 0)
    assert stream.labels_at(8, 1, a).concede10 == 1  # seen from A at t=8, B scores at 15 (within 10 s)


def test_turnover_within_five_seconds():
    a, b = "A", "B"
    stream = EventStream([ev(0, a, "pass", "a1", "a2", "success"), ev(3, a, "pass", "a2", None, "fail"),
                          ev(5.0, b, "gain"), ev(5.5, b, "pass", "b1", "b2", "success"),
                          ev(20, b, "pass", "b2", "b3", "success"), ev(30, a, "gain")], {1: 100})
    assert stream.labels_at(0, 1, a).turn5 == 1  # B wins the ball at 5.0 = boundary
    assert stream.labels_at(-0.1, 1, a).turn5 == 0  # the switch at 5.0 is 5.1 s away
    assert stream.labels_at(21, 1, b).turn5 == 0  # A regains at 30
    assert stream.labels_at(26, 1, b).turn5 == 1  # at 30, 4 s later


def test_goal_is_not_a_turnover():
    a, b = "A", "B"
    stream = EventStream([ev(0, a, "pass", "a1", "a2", "success"), ev(2, a, "shot", "a2", outcome="goal"),
                          ev(40, b, "pass", "b1", "b2", "success")], {1: 100})
    assert stream.labels_at(0, 1, a).turn5 == 0


def test_receiver_index_maps_node_order():
    ids = [f"a{i}" for i in range(11)] + [f"b{i}" for i in range(11)]
    from matchlens.tacticsml.train.labels import Labels
    assert receiver_index(Labels("a3", RECEIVER_PLAYER, 0, 0, 0, 0), ids) == 3
    assert receiver_index(Labels("b3", RECEIVER_PLAYER, 0, 0, 0, 0), ids) == -1  # opponent: not a team slot
    assert receiver_index(Labels(None, RECEIVER_LOST, 0, 0, 0, 0), ids) == 11
    assert receiver_index(Labels(None, RECEIVER_UNKNOWN, 0, 0, 0, 0), ids) == -1


# ---------------------------------------------------------------- synthetic source

@pytest.fixture(scope="module")
def short_match():
    return generate_match(7)


@pytest.fixture(scope="module")
def sim_items(short_match):
    items = list(synthetic.iter_match(short_match, random_hz=0.3, seed=1))
    assert items
    return items


def test_synthetic_samples_are_valid_and_labels_match_events(short_match, sim_items):
    frame_kinds = {i[2]["kind"] for i in sim_items}
    assert {schema.KIND_PASS, schema.KIND_OPEN} <= frame_kinds
    by_player = {e.elapsed: e for e in short_match.events if e.type.value == "pass"}
    checked = 0
    for frame, lab, meta in sim_items:
        assert len(frame.players) >= 14 and frame.possession_team_id in frame.attacking_right
        assert lab.shot10 in (-1, 0, 1) and lab.goal10 <= lab.shot10 or lab.shot10 == -1
        if meta["kind"] == schema.KIND_PASS and lab.receiver_state == RECEIVER_PLAYER:
            e = by_player.get(frame.elapsed)
            if e is not None and e.team_id == frame.possession_team_id:
                assert e.outcome.value == "success" and lab.receiver_id == e.related_player_id
                checked += 1
        s = F.build_sample(frame)
        assert s.pos.shape == (5, 25, 2)
        if lab.receiver_state == RECEIVER_PLAYER:
            assert 0 <= receiver_index(lab, s.node_ids) <= 10
    assert checked > 20
    # a goal in the data gives goal10 = 1 for passes in the 10 s before it
    goals = [e for e in short_match.events if e.type.value == "goal"]
    assert goals
    g = goals[0]
    near = [lab for fr, lab, m in sim_items
            if m["kind"] == schema.KIND_PASS and 0 < g.elapsed - fr.elapsed <= 10 and fr.possession_team_id == g.team_id]
    assert near and all(lab.goal10 == 1 and lab.shot10 == 1 for lab in near)


def test_synthetic_set_piece_flag(sim_items):
    assert any(f.set_piece for f, _, _ in sim_items)


# ---------------------------------------------------------------- shards

def test_shard_round_trip(tmp_path, sim_items):
    subset = sim_items[:120]
    entries = build_from_iter("synthetic", iter([(f, lab, dict(m, match_id="m1")) for f, lab, m in subset]),
                              root=tmp_path)
    assert len(entries) == 1 and entries[0].n == 120
    d = schema.read_shards(["synthetic"], entries[0].split, root=tmp_path)
    direct = F.stack([F.build_sample(f) for f, _, _ in subset])
    assert np.allclose(d["pos"], direct["pos"], atol=1e-5) and np.allclose(d["node_feat"], direct["node_feat"])
    assert d["node_ids"].tolist() == direct["node_ids"].tolist()
    assert np.allclose(d["edge_feat"], direct["edge_feat"], atol=1e-2)
    for k in ("receiver_idx", "shot10", "goal10", "concede10", "turn5", "kind", "set_piece", "target_valid"):
        assert d[k].shape == (120,)
    assert d["target_pos"].shape == (120, 25, 2) and d["profile_keys"].shape == (0,)
    assert set(np.unique(d["receiver_idx"])) <= set(range(-1, 12))
    assert (d["source"] == "synthetic").all() and (d["match_id"] == "m1").all()
    index = schema.ShardIndex.load(tmp_path)
    assert index.counts()["synthetic"][entries[0].split] == 120
    with pytest.raises(schema.DataUnavailable):
        schema.read_shards(["synthetic"], "val" if entries[0].split != "val" else "test", root=tmp_path)
    # rewriting the same match replaces the manifest row
    build_from_iter("synthetic", iter([(f, lab, dict(m, match_id="m1")) for f, lab, m in subset[:10]]), root=tmp_path)
    assert schema.ShardIndex.load(tmp_path).counts()["synthetic"][entries[0].split] == 10


def test_split_is_deterministic_and_respects_held_out_sets():
    ids = [f"match-{i}" for i in range(400)]
    a = [schema.assign_split("statsbomb360", m) for m in ids]
    assert a == [schema.assign_split("statsbomb360", m) for m in ids]
    share = {s: a.count(s) / len(a) for s in schema.SPLITS}
    assert 0.7 < share["train"] < 0.9 and 0.04 < share["val"] < 0.17 and 0.04 < share["test"] < 0.17
    assert all(schema.assign_split("sportec", m) == "test" for m in schema.HELD_OUT_SPORTEC)
    assert schema.assign_split("pff", "x", override="test") == "test"
    with pytest.raises(ValueError):
        schema.assign_split("pff", "x", override="bogus")


# ---------------------------------------------------------------- statsbomb 360 conversion (fixtures, no network)

def test_statsbomb360_conversion_from_fixture():
    events = [
        {"id": "e1", "period": 1, "timestamp": "00:00:10.000", "type": {"name": "Pass"},
         "team": {"id": 1}, "possession_team": {"id": 1}, "player": {"id": 7}, "location": [60.0, 40.0],
         "pass": {"recipient": {"id": 8}}},
        {"id": "e2", "period": 1, "timestamp": "00:00:14.000", "type": {"name": "Shot"},
         "team": {"id": 1}, "possession_team": {"id": 1}, "player": {"id": 8}, "location": [100.0, 40.0],
         "shot": {"outcome": {"name": "Goal"}}},
        {"id": "e3", "period": 1, "timestamp": "00:00:40.000", "type": {"name": "Pass"},
         "team": {"id": 2}, "possession_team": {"id": 2}, "player": {"id": 9}, "location": [20.0, 40.0],
         "pass": {"outcome": {"name": "Incomplete"}}},
    ]
    freeze = [{"teammate": i < 8, "actor": i == 0, "keeper": i == 7 or i == 12,
               "location": [55.0 + 3 * i, 20.0 + 2 * i]} for i in range(14)]
    frames = [{"event_uuid": "e1", "visible_area": [], "freeze_frame": freeze}]
    items = list(statsbomb360.iter_match(events, frames, "99", open_fraction=0.0))
    assert len(items) == 1
    frame, lab, meta = items[0]
    assert frame.possession_team_id == "1" and frame.carrier_id == "a01"
    assert all(0 <= p.x <= 105 and 0 <= p.y <= 68 for p in frame.players)
    assert (lab.shot10, lab.goal10, lab.concede10) == (1, 1, 0)
    assert lab.receiver_state == RECEIVER_UNKNOWN and meta["kind"] == schema.KIND_PASS
    s = F.build_sample(frame, observed_ids=frozenset(p.player_id for p in frame.players))
    assert s.node_mask.sum() == 14 + 3
