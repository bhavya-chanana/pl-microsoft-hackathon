"""Round 2: the whole-match calibrator with per-frame fallback, the video clock, Next10 inference over frames and
the moments writer. Everything is synthetic and fictional (no footage, no weights needed except where marked)."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass

import numpy as np
import pytest

from matchlens.next10 import clock as clk
from matchlens.next10.frames import entity, frame_doc
from matchlens.next10.hub import Hub
from matchlens.next10.moments import XG_MIN, fotmob_moments, model_moments, peaks
from matchlens.next10.predict import LivePredictor, Predictor, Possession, prediction_doc, tframe
from matchlens.next10.sink import FrameSink

# --------------------------------------------------------------------------------------- calibrator and chain


@dataclass
class _Frame:
    status: str
    homography: np.ndarray | None

    @property
    def usable(self) -> bool:
        return self.status != "none"


class _Solution:
    """frame_at by video second: fitted before 10 s, none from 10 to 12, interpolated after, absent past 20."""

    def __init__(self, h: np.ndarray) -> None:
        self.h = h

    def frame_at(self, t_s: float):
        if t_s >= 20:
            return None
        if t_s < 10:
            return _Frame("fitted", self.h)
        return _Frame("none", None) if t_s < 12 else _Frame("interpolated", self.h)

    def image_to_pitch_at(self, t_s, px):
        return np.asarray(px, float)


class _Fallback:
    status = None

    def __init__(self, h):
        self.h, self.calls = h, 0
        self.tracker = type("T", (), {"reset": lambda self: None})()

    def step(self, bgr, *, t_s, main, cut):
        self.calls += 1
        self.status = "frame"
        return self.h


def test_bundle_calibrator_reports_status_and_falls_back_where_the_solution_has_nothing():
    from matchlens.next10.chain import BundleCalibrator

    h = np.eye(3)
    fb = _Fallback(2 * np.eye(3))
    cal = BundleCalibrator(_Solution(h), fb)
    img = np.zeros((4, 4, 3), np.uint8)
    got = {t: (cal.step(img, t_s=t, main=True, cut=False), cal.status) for t in (1.0, 11.0, 15.0, 25.0)}
    assert got[1.0][1] == "fitted" and np.array_equal(got[1.0][0], h)
    assert got[11.0][1] == "frame" and got[15.0][1] == "interpolated" and got[25.0][1] == "frame"
    assert fb.calls == 2  # only the "none" frame and the one the solution does not cover
    assert cal.step(img, t_s=1.0, main=False, cut=False) is None and cal.status is None
    solo = BundleCalibrator(_Solution(h))
    assert solo.step(img, t_s=11.0, main=True, cut=False) is None and solo.status == "none"
    assert solo.step(img, t_s=1.0, main=True, cut=False) is not None and solo.image_to_pitch(1.0, np.ones((2, 2))) is not None
    solo.step(img, t_s=11.0, main=True, cut=False)
    assert solo.image_to_pitch(11.0, np.ones((2, 2))) is None


def test_make_calibrator_solves_once_and_reuses_the_saved_solution(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    from matchlens.vision import jobs

    calls = []

    class Sol:
        def __init__(self):
            pass

        def summary(self):
            return {"frames": 3, "calibrated_share": 1.0}

        def to_dict(self):
            return {"fake": True}

        def frame_at(self, t):
            return None

    monkeypatch.setattr(jobs, "whole_match_solver", lambda: lambda video, **kw: calls.append(kw) or Sol())
    monkeypatch.setattr("matchlens.vision.bundle.MatchCalibration.from_dict", classmethod(lambda cls, d: Sol()))
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    cache = tmp_path / "job" / "calibration.json"
    cal, info = jobs.make_calibrator(clip, clip, mode="auto", fps=5.0, venue=None, labels=tmp_path / "none.json",
                                     cache=cache)
    assert len(calls) == 1 and info["mode"] == "bundle" and not info["reused"] and cache.exists()
    assert type(cal).__name__ == "BundleCalibrator" and cal.fallback is not None
    cal2, info2 = jobs.make_calibrator(clip, clip, mode="bundle", fps=5.0, venue=None, labels=tmp_path / "none.json",
                                       cache=cache)
    assert len(calls) == 1 and info2["reused"]  # a resumed job does not solve again
    frame_only, info3 = jobs.make_calibrator(clip, clip, mode="frame", fps=5.0, venue=None, labels=None, cache=cache)
    assert info3["mode"] == "frame" and type(frame_only).__name__ == "TrackerCalibrator"


# ------------------------------------------------------------------------------------------------ the clock

ANCHORS = [{"video_s": 14.0, "clock": "00:03", "period": 1}, {"video_s": 1214.0, "clock": "20:03", "period": 1},
           {"video_s": 4000.0, "clock": "45:10", "period": 2}, {"video_s": 4300.0, "clock": "50:10", "period": 2}]


def _clock(tmp_path):
    path = tmp_path / "anchors.json"
    path.write_text(json.dumps(ANCHORS))
    anchors = clk.load_anchors(path)
    return anchors, clk.build_clock(anchors)


def test_clock_map_gives_match_seconds_and_nothing_in_the_break(tmp_path):
    anchors, cm = _clock(tmp_path)
    assert len(anchors) == 4 and cm is not None
    at = clk.clock_function(cm)
    assert at(14.0)[1] == 1 and at(14.0)[0] == pytest.approx(3.0, abs=1.0)
    assert at(4000.0)[1] == 2 and at(4000.0)[0] == pytest.approx(2710.0, abs=1.0)
    clock, period = at(3500.0)  # half time, between the pieces
    assert (clock, period) == (None, 0)
    assert clk.clock_function(None) is None
    # JSON round trip of the map (it is saved beside the calibration)
    again = clk.ClockMap.from_dict(json.loads(json.dumps(cm.to_dict()))) if hasattr(clk, "ClockMap") else None
    assert again is None or clk.clock_function(again)(14.0)[0] == at(14.0)[0]
    assert clk.finite({"a": math.nan, "b": [math.inf, 1.0]}) == {"a": None, "b": [None, 1.0]}
    with pytest.raises(ValueError):
        (tmp_path / "bad.json").write_text('[{"video_s": 1}]')
        clk.load_anchors(tmp_path / "bad.json")


def test_apply_to_frame_turns_the_second_half_and_back(tmp_path):
    _, cm = _clock(tmp_path)
    at = clk.clock_function(cm)
    doc = frame_doc("m", 4000.0, [entity("p1", "home", 10.0, -5.0), entity("ball", "ball", 1.0, 2.0)], True)
    assert clk.apply_to_frame(doc, at) is True
    assert doc["flipped"] is True and doc["match_clock_s"] is not None
    assert (doc["entities"][0]["x"], doc["entities"][0]["y"]) == (-10.0, 5.0)
    assert clk.apply_to_frame(doc, at) is False  # idempotent
    first = frame_doc("m", 14.0, [entity("p1", "home", 10.0, -5.0)], True)
    first["flipped"] = True  # a stale flip is undone
    assert clk.apply_to_frame(first, at) is True
    assert "flipped" not in first and first["entities"][0]["x"] == -10.0


def test_apply_clock_rewrites_a_stored_match(tmp_path):
    _, cm = _clock(tmp_path)

    class Store:
        def __init__(self):
            self.docs = {"frames": [frame_doc("m", t, [entity("p1", "home", 1.0, 2.0)], True) for t in (14.0, 4000.0)],
                         "predictions": [{"id": "x", "t_s": 14.0, "match_clock_s": None}]}

        async def query(self, container, sql, params=None, partition=None):
            if "MAX" in sql:
                return [max(d["t_s"] for d in self.docs[container])]
            a = next(p["value"] for p in params if p["name"] == "@a")
            b = next(p["value"] for p in params if p["name"] == "@b")
            return [dict(d) for d in self.docs[container] if a <= d["t_s"] < b]

        async def upsert(self, container, docs):
            by = {d["id"]: d for d in docs}
            self.docs[container] = [by.get(d["id"], d) for d in self.docs[container]]
            return len(docs)

    store = Store()
    changed = asyncio.run(clk.apply_clock(store, "m", cm))
    assert changed == {"frames": 2, "predictions": 1}
    late = next(d for d in store.docs["frames"] if d["t_s"] == 4000.0)
    assert late["flipped"] and late["entities"][0]["x"] == -1.0
    assert store.docs["predictions"][0]["match_clock_s"] == pytest.approx(3.0, abs=1.0)


def test_chain_writes_clock_status_and_turns_the_second_half():
    pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    from matchlens.next10.chain import BundleCalibrator, FrameChain
    from tests.test_broadcast import camera
    from tests.test_vision_live import _Detector

    h = camera(pan=0.0, tilt=22.0, focal=700.0)
    rng = np.random.default_rng(2)
    points = np.c_[rng.uniform(15, 90, 8), rng.uniform(12, 56, 8)]
    det = _Detector(h, points)
    image = np.zeros((540, 960, 3), np.uint8)
    image[:] = (50, 140, 58)
    for i, (fx, fy) in enumerate(det.feet):
        image[int(fy - 36):int(fy), int(fx - 9):int(fx + 9)] = (30, 30, 200) if i < 4 else (200, 80, 30)

    def clock(t):
        return (t + 100.0, 2) if t >= 1.0 else (t, 1)

    class Sol:
        def frame_at(self, t):
            return _Frame("fitted" if t < 1.0 else "interpolated", h)

    chain = FrameChain("m", BundleCalibrator(Sol()), detector=lambda: det, fps=5.0, clock=clock)
    docs = []
    for i in range(10):
        docs += [r.doc for r in chain.process(image, i / 5) if r.doc is not None]
    docs += [r.doc for r in chain.flush() if r.doc is not None]
    first, late = docs[0], docs[-1]
    assert first["calib_status"] == "fitted" and first["match_clock_s"] == 0.0 and "flipped" not in first
    assert late["calib_status"] == "interpolated" and late["flipped"] is True and late["match_clock_s"] > 100
    # the same people, turned 180 degrees about the centre spot
    a = sorted((e["x"], e["y"]) for e in first["entities"])
    b = sorted((-e["x"], -e["y"]) for e in late["entities"])
    assert np.allclose(a, b, atol=0.5)


# --------------------------------------------------------------------------------------------- inference


def _frames(n_seconds=8, fps=5, *, home_ball_side=True):
    """A fictional passage of play: 10 home players attack +x with the ball, 9 away defend, at 5 Hz."""
    docs = []
    for k in range(n_seconds * fps):
        t = k / fps
        ents = [entity(f"p{i}", "home", -30 + 3 * i + t, -20 + 4 * (i % 5)) for i in range(10)]
        ents += [entity(f"p{20 + i}", "away", 5 + 4 * i, -18 + 4 * (i % 5)) for i in range(9)]
        ents.append(entity("p99", "ref", 0, 0))
        if not (home_ball_side is False and k % 2):
            ents.append(entity("ball", "ball", -30 + t + 0.3, -19.0))
        docs.append(frame_doc("fict:1", t, ents, True))
    return docs


def test_tframe_uses_absolute_metres_possession_goalkeeper_and_history():
    docs = _frames()
    got = tframe(docs[:26], Possession())
    assert got is not None
    frame, info = got
    assert frame.possession_team_id == "home" and frame.attacking_right == {"home": True, "away": False}
    assert len(frame.players) == 19 and all(0 <= p.x <= 105 and 0 <= p.y <= 68 for p in frame.players)
    assert info["ball_observed"] and info["n_players"] == 19
    assert frame.history == [] or len(frame.history) <= 4
    assert {p.player_id for p in frame.players if p.is_gk} == {"p28"}  # the away player deepest in its own box
    assert frame.ball.x == pytest.approx(52.5 - 30 + 25 / 5 + 0.3, abs=0.01)
    # not calibrated or too few players: nothing to predict
    bad = dict(docs[10], calibrated=False)
    assert tframe([*docs[:10], bad], Possession()) is None
    thin = dict(docs[10], entities=docs[10]["entities"][:3])
    assert tframe([*docs[:10], thin], Possession()) is None
    one_team = dict(docs[10], entities=[e for e in docs[10]["entities"] if e["team"] != "away"])
    assert tframe([*docs[:10], one_team], Possession()) is None  # a failed kit split: out of the model's range


def test_possession_holds_through_a_blind_spell_and_the_ball_falls_back_to_the_centroid():
    docs = _frames(8, home_ball_side=False)  # ball missing on odd frames
    pos = Possession()
    teams = []
    for d in docs[:12]:
        players = [e for e in d["entities"] if e["team"] in ("home", "away")]
        teams.append(pos.update(d, players)[0])
    assert set(teams) == {"home"}
    no_ball = dict(docs[0], t_s=60.0, entities=[e for e in docs[0]["entities"] if e["team"] != "ball"])
    players = [e for e in no_ball["entities"] if e["team"] in ("home", "away")]
    team, carrier, ball, seen = pos.update(no_ball, players)
    assert not seen and carrier is None and team == "home"  # held; 60 s later it is only a guess
    assert ball == pytest.approx((np.mean([p["x"] for p in players]), np.mean([p["y"] for p in players])))


class _Model:
    """Stands in for Next10Model: a canned answer shaped like infer_next10's."""

    version = "stub-v1"

    def __init__(self, shot=None):
        self.shot, self.calls, self.names = shot or {}, 0, []

    def predict_batch(self, frames, team_names=None, observed_ids=None, top_k=3, with_trajectories=True,
                      with_embedding=True):
        outs = []
        for f in frames:
            self.calls += 1
            self.names.append(team_names[len(outs)] if team_names else None)
            t = f.ball.x - 52.5 + 30 - 0.3  # the fictional ball moves 1 m a second
            outs.append({
                "model_version": "stub-v1", "possession_team_id": f.possession_team_id, "team_known": False,
                "probs": {"shot10": self.shot.get(round(t), 0.04), "goal10": 0.01, "concede10": 0.01, "turn5": 0.2,
                          "poss10": 0.6},
                "prob_std": {"shot10": 0.01, "goal10": 0.0, "concede10": 0.0, "turn5": 0.0, "poss10": 0.0},
                "poss10_by_team": {f.possession_team_id: 0.6},
                "shot10_parts": {"direct": 0.04, "via_receiver": 0.05},
                "ball_zone10": {"cols": 16, "rows": 12, "x0": -52.5, "y0": -34.0, "cell_w": 6.5625, "cell_h": 5.6667,
                                "p": [1 / 192] * 192, "top_cells": [], "expected_xy": [1.234567, 2.0]},
                "receivers": [{"player_id": "p1", "p": 0.2, "std": 0.01}], "receiver_lost": 0.1,
                "action_next": {"pass": 0.5, "carry": 0.3, "shot": 0.1, "other": 0.1},
                "embedding": [0.1] * 64,
                "trajectories": {"dt_s": 1.0, "horizon_s": 10, "k": 1, "entities": [
                    {"entity_id": "p1", "modes": [{"p": 1.0, "path": [[0.12345, 1.0]] * 10}]}]},
            })
        return outs


def test_predictor_runs_at_1_hz_and_writes_unique_ids():
    docs = _frames(10)
    for d in docs[25:]:
        d["match_clock_s"] = d["t_s"] + 100.0
    model = _Model()
    p = Predictor("fict:1", home="Westmoor", away="Eastbrook", model=model)
    out = p.run(docs)
    assert [d["t_s"] for d in out] == [float(s) for s in range(10)] and model.calls == 10
    assert len({d["id"] for d in out}) == 10 and out[3]["id"] == "n10:fict:1:3000"
    d = out[7]
    assert d["kind"] == "next10" and d["match_clock_s"] == 107.0 and d["minute"] == 1
    assert d["possession_team_id"] == "home" and set(d["poss10_by_team"]) == {"home", "away"}
    assert d["poss10_by_team"]["away"] == pytest.approx(0.4)
    assert len(d["embedding"]) == 64 and d["t_issued"] and d["model_version"] == "stub-v1"
    assert d["ball_zone10"]["expected_xy"] == [1.23, 2.0] and d["trajectories"]["entities"][0]["modes"][0]["path"][0] == [0.12, 1.0]
    assert model.names[0] == ("Westmoor", "Eastbrook")
    json.dumps(d)  # storable


def test_live_predictor_publishes_and_stores_what_is_due():
    async def go():
        hub = Hub()
        wrote = []

        class Store:
            async def upsert(self, container, docs):
                wrote.append((container, [d["id"] for d in docs]))
                return len(docs)

        sink = FrameSink("fict:1", Store(), hub=hub, flush_s=0.05).start()
        got = []
        async with hub.subscribe("fict:1") as queue:
            live = LivePredictor(Predictor("fict:1", model=_Model()), sink)
            for d in _frames(4):
                await live.feed(d)
                sink.add(d)
            await sink.close()
            while not queue.empty():
                got.append(queue.get_nowait())
        return live, wrote, got

    live, wrote, got = asyncio.run(go())
    assert live.made == 4
    kinds = [k for k, _ in ((m[0], m[1]) if isinstance(m, tuple) else (m["type"], m["doc"]) for m in got)]
    assert kinds.count("next10") + kinds.count("prediction") == 4
    stored = {c: ids for c, ids in wrote}
    assert "predictions" in stored and "frames" in stored


def test_the_real_model_runs_on_the_fictional_passage_when_weights_are_installed():
    from matchlens.tacticsml.infer_next10 import next10_available

    if not next10_available():
        pytest.skip("no Next10 weights installed")
    out = Predictor("fict:1", home="Westmoor", away="Eastbrook").run(_frames(6))
    assert len(out) == 6
    d = out[-1]
    assert 0 <= d["probs"]["shot10"] <= 1 and len(d["embedding"]) == 64
    assert abs(sum(d["ball_zone10"]["p"]) - 1) < 1e-3 and d["trajectories"]["entities"]
    assert len(json.dumps(d)) < 200_000


# ------------------------------------------------------------------------------------------------ moments


def test_fotmob_moments_goals_conceded_and_chances_from_the_shot_map():
    match = {"home": {"name": "Aston"}, "away": {"name": "Brent"}}
    score = {"outcomes": [
        {"team_side": "home", "minute": 12, "t_s": 720, "type": "shot", "xg": 0.05},
        {"team_side": "away", "minute": 23, "t_s": 1380, "type": "goal", "xg": 0.31},
        {"team_side": "home", "minute": 40, "t_s": 2400, "type": "shot", "xg": XG_MIN},
        {"team_side": "home", "minute": 71, "t_s": 4260, "type": "goal", "xg": None},
        {"team_side": "home", "minute": 95, "t_s": 5700, "type": "goal", "xg": 0.79}]}  # a shoot-out kick
    got = fotmob_moments("fotmob:1", score, match, shootout_from=90)
    kinds = [(m["kind"], m["team_side"], m["minute"]) for m in got]
    # the score doc counts minutes from 0 (23 is FotMob's 24th), the moment shows FotMob's; t_s stays the minute's start
    # one moment per goal (the goal conceded, with scorer and xG in it), a chance only for a shot that was not a goal
    assert kinds == [("goal_conceded", "home", 24), ("chance", "home", 41), ("goal_conceded", "away", 72)]
    assert [m["t_s"] for m in got] == [1380, 2400, 4260]
    assert len(fotmob_moments("fotmob:1", score, match)) == 4  # without a shoot-out the late kick would count
    assert all(m["source"] == "fotmob" and "embedding" not in m for m in got)
    assert got[0]["team"] == "Aston" and got[0]["scored_by"] == "away" and got[0]["xg"] == 0.31
    assert len({m["id"] for m in got}) == 3


def test_peaks_are_local_maxima_above_the_threshold_and_apart():
    series = [(t, 0.05) for t in range(0, 60)]
    series[10], series[11], series[12] = (10, 0.30), (11, 0.42), (12, 0.35)
    series[20] = (20, 0.28)  # 9 s from the 0.42 peak: the same chance seen twice
    series[45] = (45, 0.26)
    series[50] = (50, 0.20)  # below the threshold
    idx = peaks(series, threshold=0.25)
    assert [series[i][0] for i in idx] == [11, 45]


def test_model_moments_say_where_they_come_from_and_carry_the_embedding():
    docs = _frames(50)
    model = _Model(shot={5: 0.31, 40: 0.55})
    preds = Predictor("vid:1", model=model).run(docs)
    for p in preds:
        p["match_clock_s"] = None if p["t_s"] < 10 else p["t_s"] + 200.0
    got = model_moments("vid:1", preds, {"home": {"name": "Westmoor"}, "away": {"name": "Eastbrook"}})
    assert [m["t_s"] for m in got] == [5.0, 40.0] and all(m["kind"] == "chance" and m["source"] == "model" for m in got)
    assert got[1]["p_shot10"] == 0.55 and "Model shot-chance peak" in got[1]["label"] and "55%" in got[1]["label"]
    assert got[1]["team"] == "Westmoor" and got[1]["xg"] is None and got[1]["model_version"] == "stub-v1"
    assert len(got[1]["embedding"]) == 64 and got[1]["match_clock_s"] == 240.0 and got[1]["minute"] == 5
    assert got[0]["match_clock_s"] is None
    assert model_moments("vid:1", preds, {}, threshold=0.9) == []
    for p in preds:
        p["embedding"] = [0.1] * 5  # a wrong-length vector would fail the batch upsert: it is left out
    assert all("embedding" not in m for m in model_moments("vid:1", preds, {}))


def test_prediction_doc_is_json_and_size_bounded():
    out = _Model().predict_batch([tframe(_frames(3), Possession())[0]])[0]
    doc = prediction_doc("fict:1", _frames(3)[-1], out, {"n_players": 19})
    assert len(json.dumps(doc)) < 40_000


# -------------------------------------------------------------------------------------------- kits and the pitch


def _lab(rgb):
    import cv2

    return cv2.cvtColor(np.uint8([[rgb[::-1]]]), cv2.COLOR_BGR2LAB)[0, 0].astype(float)


def test_kit_split_finds_the_two_teams_despite_a_black_referee_and_a_white_keeper():
    pytest.importorskip("cv2")
    from matchlens.next10.kits import split_kits
    from matchlens.vision.teams import LEFT, OTHER, RIGHT

    rng = np.random.default_rng(3)
    red, blue = _lab((150, 30, 40)), _lab((30, 80, 170))
    cols = [red + rng.normal(0, 2, 3) for _ in range(9)] + [blue + rng.normal(0, 2, 3) for _ in range(8)]
    cols += [_lab((20, 20, 20)), _lab((20, 22, 20)), _lab((245, 245, 245))]  # referee and assistant, a white shirt
    xs = np.r_[rng.uniform(35, 60, 9), rng.uniform(45, 70, 8), 50, 52, 50.0]
    split = split_kits(np.array(cols), xs)
    assert set(split.labels[:9]) == {LEFT} and set(split.labels[9:17]) == {RIGHT}
    assert list(split.labels[17:]) == [OTHER, OTHER, OTHER]
    with pytest.raises(ValueError):  # one kit only: no split
        split_kits(np.array(cols[:9]), xs[:9])


def test_swapped_home_turns_the_first_half_and_people_off_the_lines_are_not_players():
    pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    from matchlens.next10.chain import FrameChain
    from tests.test_broadcast import camera
    from tests.test_vision_live import _Detector, _FixedCamera

    h = camera(pan=0.0, tilt=22.0, focal=700.0)
    left = np.c_[np.linspace(18, 45, 9), np.linspace(14, 54, 9)]
    right = np.c_[np.linspace(60, 88, 9), np.linspace(14, 54, 9)]
    staff = np.array([[50.0, -1.8]])  # a metre and a half behind the touchline
    det = _Detector(h, np.r_[left, right, staff])
    image = np.zeros((540, 960, 3), np.uint8)
    image[:] = (50, 140, 58)
    for i, (fx, fy) in enumerate(det.feet):
        kit = (30, 30, 200) if i < 9 else (200, 80, 30) if i < 18 else (30, 200, 200)
        image[int(fy - 36):int(fy), int(fx - 9):int(fx + 9)] = kit

    def run(swap, home_kit=None):
        chain = FrameChain("m", _FixedCamera(h), detector=lambda: det, fps=5.0, swap_teams=swap, home_kit=home_kit)
        docs = []
        for i in range(12):
            docs += [r.doc for r in chain.process(image, i / 5) if r.doc is not None]
        return docs + [r.doc for r in chain.flush() if r.doc is not None]

    plain, swapped = run(False)[-1], run(True)[-1]
    assert sum(e["team"] == "ref" for e in plain["entities"]) >= 1  # the person behind the line
    assert all(abs(e["y"]) <= 34.5 for e in plain["entities"] if e["team"] in ("home", "away"))
    assert all(0.5 <= e["team_conf"] <= 1 for e in plain["entities"] if e["team"] in ("home", "away"))
    assert all(e["x"] < 0 for e in plain["entities"] if e["team"] == "home") and "flipped" not in plain
    # the left team is called away when home started on the right, and then the frame is turned: home attacks +x
    assert swapped["flipped"] is True
    assert all(e["x"] < 0 for e in swapped["entities"] if e["team"] == "home")
    assert clk.apply_to_frame(dict(plain, flipped=True, entities=[dict(e) for e in plain["entities"]]),
                              clk.clock_function(None) or (lambda t: (None, 1)), swap=True) is False
    # naming by kit: the blue shirts are home wherever they stand (red is left here, blue right)
    by_kit = run(False, "#1e50aa")[-1]
    assert all(e["x"] > 0 for e in by_kit["entities"] if e["team"] == "home")
    assert all(e["x"] < 0 for e in by_kit["entities"] if e["team"] == "away")
    assert by_kit["entities"] != plain["entities"] and sum(e["team"] == "home" for e in by_kit["entities"]) >= 8


def test_possession_changes_only_after_two_consecutive_confirmations():
    docs = _frames(4)
    pos = Possession()
    players = [e for e in docs[0]["entities"] if e["team"] in ("home", "away")]

    def doc_with_ball_at(team, t):
        near = next(p for p in players if p["team"] == team)
        ents = [dict(e) for e in docs[0]["entities"] if e["team"] != "ball"] + [
            {"id": "ball", "team": "ball", "x": near["x"], "y": near["y"], "observed": True}]
        return dict(docs[0], t_s=t, entities=ents)

    seq = [pos.update(doc_with_ball_at(t, float(i)), players)[0]
           for i, t in enumerate(["away", "away", "home", "away", "home", "home"])]
    # the first sighting sets it; a single contrary second does not flip it; two in a row do
    assert seq == ["away", "away", "away", "away", "away", "home"]


# ------------------------------------------------------------------ a per-frame calibration that drifted is dropped


class _FrameCamera:
    """A per-frame calibrator ("frame" status) that answers with a fixed homography."""

    status = "frame"

    def __init__(self, h: np.ndarray) -> None:
        self.h, self.rejected = h, 0

    def step(self, bgr960, *, t_s, main, cut):
        return self.h if main else None

    def reject(self) -> None:
        self.rejected += 1
        self.status = "none"


def _drift_chain(bad: np.ndarray | None):
    from matchlens.next10.chain import FrameChain
    from tests.test_broadcast import camera
    from tests.test_vision_live import _Detector

    h = camera(pan=0.0, tilt=22.0, focal=700.0)
    rng = np.random.default_rng(3)
    points = np.c_[rng.uniform(15, 90, 14), rng.uniform(10, 58, 14)]
    det = _Detector(h, points)  # the people really stand at ``points`` as seen through h
    image = np.zeros((540, 960, 3), np.uint8)
    image[:] = (50, 140, 58)
    cam = _FrameCamera(h if bad is None else bad)
    chain = FrameChain("m", cam, detector=lambda: det, fps=5.0)
    docs = []
    for i in range(10):
        cam.status = "frame"
        docs += [r.doc for r in chain.process(image, i / 5) if r.doc is not None]
    docs += [r.doc for r in chain.flush() if r.doc is not None]
    return docs, cam, h


def test_a_homography_that_projects_off_the_pitch_is_dropped_not_clamped():
    pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    _, _, h = _drift_chain(None)
    shift = np.eye(3)
    shift[0, 2] = -250.0  # the frame's pitch is 250 m away from where the people are
    docs, cam, _ = _drift_chain(h @ shift)
    assert len(docs) == 10
    assert all(d["calib_status"] == "none" and d["calibrated"] is False and d["entities"] == [] for d in docs)
    assert cam.rejected == 10  # the calibrator is told, so it re-acquires from its keyframes


def test_a_homography_that_collapses_the_players_onto_one_line_is_dropped():
    pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    _, _, h = _drift_chain(None)
    docs, cam, _ = _drift_chain(h @ np.diag([1e4, 1.0, 1.0]))  # every x lands within a centimetre of 0: "on the pitch"
    assert all(d["calib_status"] == "none" and d["entities"] == [] for d in docs) and cam.rejected == len(docs)


def test_a_sound_per_frame_calibration_is_kept():
    pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    docs, cam, _ = _drift_chain(None)
    assert all(d["calib_status"] == "frame" and len(d["entities"]) == 14 for d in docs) and cam.rejected == 0


def test_implausible_needs_enough_people_to_judge():
    from matchlens.next10.chain import implausible

    line = np.c_[np.zeros(8), np.linspace(5, 60, 8)]
    assert implausible(line, np.ones(8, bool)) == "people collapsed onto a line"
    assert implausible(line[:3], np.ones(3, bool)) is None  # three people say nothing
    spread = np.c_[np.linspace(10, 95, 8), np.linspace(5, 60, 8)]
    assert implausible(spread, np.ones(8, bool)) is None
    assert "off the pitch" in implausible(spread, np.array([1, 0, 0, 0, 0, 0, 0, 1], bool))


# ------------------------------------------------------------------ a "ball" that is part of the picture


class _BallDetector:
    """No people; one ball candidate at a given pixel (of a 960-px frame)."""

    def __init__(self, pixel: tuple[float, float]) -> None:
        from tests.test_vision_live import _Tiling

        self.tiling = _Tiling(0, 1, 1)
        self.pixel = pixel

    def __call__(self, image):
        x, y = self.pixel
        return np.zeros((0, 5)), np.array([[x - 4, y - 4, x + 4, y + 4, 0.9]])


def _ball_at(h: np.ndarray, x: float, y: float):
    from matchlens.vision.camera import project

    px, _ = project(h, np.array([[x, y]]))
    return float(px[0, 0]), float(px[0, 1])


def _ball_frames(pitch_xy: tuple[float, float]):
    pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    from matchlens.next10.chain import FrameChain
    from tests.test_broadcast import camera
    from tests.test_vision_live import _FixedCamera

    h = camera(pan=0.0, tilt=22.0, focal=700.0)
    chain = FrameChain("m", _FixedCamera(h), detector=lambda: _BallDetector(_ball_at(h, *pitch_xy)), fps=5.0)
    image = np.zeros((540, 960, 3), np.uint8)
    image[:] = (50, 140, 58)  # grass: the view gate calls it the main camera
    docs = [r.doc for i in range(4) for r in chain.process(image, i / 5) if r.doc is not None]
    return [e for d in docs for e in d["entities"] if e["team"] == "ball"]


def test_a_ball_beyond_the_goal_line_is_dropped_but_one_on_the_pitch_is_kept():
    # corner-origin metres: x = -2 is 2 m behind the goal line (centre-origin -54.5), x = -0.5 within a metre of it
    assert _ball_frames((-2.0, 32.0)) == []
    assert _ball_frames((60.0, 30.0)) != []
    assert _ball_frames((-0.5, 32.0)) != []


def test_a_ball_fixed_in_the_image_while_the_camera_moves_is_part_of_the_picture():
    pytest.importorskip("cv2")
    pytest.importorskip("scipy")
    from matchlens.next10.chain import FrameChain

    chain = FrameChain("m", object())
    pixel, flags = (480.0, 300.0), []
    for i in range(30):  # the camera pans: the same pixel is half a metre further along the pitch every frame
        flags.append(chain._fixture(i / 5, pixel, (20.0 + 0.5 * i, 30.0)))
    assert not any(flags[:15]) and all(flags[20:])  # needs 6 sightings over 3 s and 3 m of camera movement
    resting = FrameChain("m", object())
    assert not any(resting._fixture(i / 5, pixel, (40.0, 30.0)) for i in range(60))  # a real ball at rest, camera still
    other = FrameChain("m", object())
    assert not any(other._fixture(i / 5, (100.0 + 30 * i, 300.0), (20.0 + 0.5 * i, 30.0)) for i in range(30))  # it moves


def test_an_off_pitch_ball_is_never_used_by_the_predictor():
    from matchlens.next10.predict import Possession

    players = [{"id": f"p{i}", "team": "home", "x": 10.0 + i, "y": 0.0} for i in range(8)]
    pos = Possession()
    got, seen = pos.ball_for({"t_s": 6.0, "entities": players + [{"id": "ball", "team": "ball", "x": -55.0, "y": 32.3}]},
                             players)
    assert seen is False and abs(got[0] - 13.5) < 1e-6  # the centroid of the players, not the board behind the goal
    got, seen = pos.ball_for({"t_s": 7.0, "entities": players + [{"id": "ball", "team": "ball", "x": 20.0, "y": 5.0}]},
                             players)
    assert seen is True and got == (20.0, 5.0)


def test_v2_weights_are_the_default_and_v1_stays_selectable(tmp_path, monkeypatch):
    from matchlens.tacticsml import infer_next10 as inf

    v1, v2 = tmp_path / "weights", tmp_path / "weights_v2"
    for d in (v1, v2):
        d.mkdir()
        (d / inf.MANIFEST).write_text("{}")
    monkeypatch.setattr(inf, "DEFAULT_DIR_V1", v1)
    monkeypatch.setattr(inf, "DEFAULT_DIR_V2", v2)
    monkeypatch.delenv(inf.ENV_WEIGHTS, raising=False)
    assert inf.weights_dir() == v2
    monkeypatch.setenv(inf.ENV_WEIGHTS, str(v1))
    assert inf.weights_dir() == v1
    (v2 / inf.MANIFEST).unlink()
    monkeypatch.delenv(inf.ENV_WEIGHTS)
    assert inf.weights_dir() == v1  # no v2 installed: the old set
