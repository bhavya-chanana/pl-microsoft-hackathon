"""The video-to-match-clock aligner on synthetic anchors: a known map, noisy minute-level event times, one wrong anchor."""

from __future__ import annotations

import math

import numpy as np

from matchlens.vision.align import HALF_S, Anchor, align, minute_to_seconds

# Truth: period 1 starts at video 120 s; the broadcast runs 0.2% fast. Half-time lasts 15 minutes of video.
K1 = 120.0
SLOPE = 1.002


def video_of(match_s: float, period: int) -> float:
    if period == 1:
        return K1 + match_s / SLOPE
    return K1 + HALF_S / SLOPE + 900.0 + 120.0 + (match_s - HALF_S) / SLOPE  # 2nd kick-off after break + added time


def make_anchors(wrong: tuple[float, ...] = (), seed: int = 2) -> list[Anchor]:
    rng = np.random.default_rng(seed)
    out = [Anchor.kickoff(1, video_of(0.0, 1) + rng.normal(0, 0.5)), Anchor.kickoff(2, video_of(HALF_S, 2) + rng.normal(0, 0.5))]
    for minute, kind in ((12, "goal"), (23, "card"), (38, "goal")):
        true = minute * 60 - rng.uniform(0, 60)  # the event is somewhere inside that minute
        out.append(Anchor(video_of(true, 1), minute_to_seconds(minute), kind, 1))
    for minute, kind in ((52, "card"), (67, "goal"), (81, "goal")):
        true = minute * 60 - rng.uniform(0, 60)
        out.append(Anchor(video_of(true, 2), minute_to_seconds(minute), kind, 2))
    for off in wrong:
        out.append(Anchor(video_of(minute_to_seconds(30), 1) + off, minute_to_seconds(30), "goal", 1, label="wrong"))
    return out


def test_minute_scale():
    assert minute_to_seconds(1) == 30.0 and minute_to_seconds(23) == 22 * 60 + 30
    assert minute_to_seconds(45, 3) == 45 * 60 + 2 * 60 + 30


def test_recovers_known_map():
    cm = align(make_anchors())
    for match_s, period in ((0.0, 1), (900.0, 1), (2400.0, 1), (HALF_S, 2), (3600.0, 2), (5000.0, 2)):
        assert abs(cm.to_match(video_of(match_s, period)) - match_s) < 15.0
        assert abs(cm.to_video(match_s, period) - video_of(match_s, period)) < 15.0
    assert cm.report.outliers == 0 and cm.report.rms_s < 15.0
    assert all(abs(s - SLOPE) < 0.006 for s in cm.report.slopes.values())


def test_one_wrong_anchor_is_an_outlier():
    for off in (400.0, -250.0, 90.0):
        anchors = make_anchors(wrong=(off,))
        cm = align(anchors)
        bad = [r for r in cm.report.residuals if r.anchor.label == "wrong"]
        assert len(bad) == 1 and not bad[0].inlier
        assert cm.report.outliers == 1
        for match_s in (0.0, 1500.0, 2600.0):
            assert abs(cm.to_match(video_of(match_s, 1)) - match_s) < 4.0


def test_wrong_kickoff_loses_to_the_agreeing_events():
    anchors = make_anchors()
    anchors[0] = Anchor.kickoff(1, anchors[0].video_s + 300.0)  # a mislabelled kick-off, 5 minutes off
    cm = align(anchors)
    assert not cm.report.residuals[0].inlier
    assert abs(cm.to_match(video_of(1500.0, 1)) - 1500.0) < 25.0  # events are only known to the minute


def test_half_time_break_has_no_match_time():
    cm = align(make_anchors())
    mid = video_of(HALF_S, 1) + 600.0
    assert math.isnan(cm.to_match(mid)) and cm.period_at(mid) == 0
    assert cm.period_at(video_of(1000.0, 1)) == 1 and cm.period_at(video_of(4000.0, 2)) == 2
    arr = cm.to_match(np.array([video_of(100.0, 1), mid]))
    assert np.isfinite(arr[0]) and np.isnan(arr[1])


def test_single_anchor_per_half_and_kickoff_only():
    cm = align([Anchor.kickoff(1, 300.0), Anchor.kickoff(2, 3900.0)])
    assert abs(cm.to_match(300.0 + 1000.0) - 1000.0) < 1e-6
    assert abs(cm.to_match(3900.0 + 60.0) - (HALF_S + 60.0)) < 1e-6
    assert abs(cm.to_video(HALF_S + 60.0) - 3960.0) < 1e-6


def test_edited_clip_with_a_jump_gets_two_pieces():
    anchors = [Anchor.kickoff(1, 100.0), Anchor(430.0, minute_to_seconds(6), "goal", 1),
               # the recording skips ten minutes here
               Anchor(1000.0, minute_to_seconds(20), "goal", 1), Anchor(1480.0, minute_to_seconds(28), "goal", 1)]
    cm = align(anchors)
    assert len(cm.pieces) == 2
    assert abs(cm.to_match(1200.0) - (minute_to_seconds(20) + 200.0)) < 10.0
    assert abs(cm.to_match(200.0) - 100.0) < 10.0


def test_report_serialises():
    cm = align(make_anchors(wrong=(400.0,)))
    d = cm.to_dict()
    assert d["report"]["outliers"] == 1 and len(d["report"]["anchors"]) == 9


def test_clock_readings_from_the_screen():
    assert Anchor.from_clock(10.0, "14:41").match_s == 881.0
    assert Anchor.from_clock(10.0, 90).match_s == 90.0
    assert Anchor.from_clock(10.0, "45:30", period=2).half == 2


def test_a_run_of_clock_readings_survives_misreads_and_a_variable_frame_rate_offset():
    rng = np.random.default_rng(5)
    video = np.arange(0.0, 580.0, 4.0)
    anchors = [Anchor.from_clock(float(v), float(np.floor(v + 881.4 + rng.normal(0, 0.2))), 1) for v in video]
    anchors += [Anchor.from_clock(float(rng.uniform(0, 580)), float(rng.uniform(800, 1500)), 1) for _ in range(8)]
    cm = align(anchors)
    assert len(cm.pieces) == 1 and cm.report.outliers == 8
    assert abs(cm.to_match(300.0) - 1181.4) < 1.0
    assert abs(cm.pieces[0].slope - 1.0) < 0.002
    assert cm.report.rms_s < 1.0


def test_goal_minutes_agree_with_the_clock_map():
    clock = [Anchor.from_clock(float(v), float(v + 881.4), 1) for v in range(0, 580, 10)]
    goals = [Anchor.from_minute(106.0, 17, kind="goal", period=1), Anchor.from_minute(376.0, 21, kind="goal", period=1)]
    cm = align(clock + goals)
    assert cm.report.outliers == 0 and len(cm.pieces) == 1
