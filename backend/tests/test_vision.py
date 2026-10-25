"""Video auto-eventing on synthetic arrays (never real footage). Skipped without ``uv sync --group vision``."""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("scipy")

from matchlens.vision.pitch import PitchProjector  # noqa: E402
from matchlens.vision.teams import LEFT, OTHER, RIGHT, shirt_colour, split_teams  # noqa: E402
from matchlens.vision.tracks import fill_gaps, from_rows, per_second, project, smooth, speeds  # noqa: E402

FPS = 25.0


@pytest.fixture(scope="module")
def projector():
    """A 40 x 60 rectified view whose raw clip is shifted 2 px right, at 0.5 px per metre."""
    rows, cols = np.mgrid[0:40, 0:60].astype(np.float32)
    homography = np.diag([0.5, 0.5, 1.0])
    return PitchProjector(cols + 2, rows, homography)


def test_pixels_to_pitch(projector):
    out = projector.to_pitch(np.array([[12.0, 10.0], [2.0, 0.0], [500.0, 500.0], [np.nan, np.nan]]))
    assert np.allclose(out[:2], [[20.0, 20.0], [0.0, 0.0]])
    assert np.isnan(out[2:]).all()  # off the view, and unseen


def test_fixed_point_maps_match_float_maps(projector):
    rows, cols = np.mgrid[0:40, 0:60].astype(np.float32)
    map1, map2 = cv2.convertMaps(cols + 2, rows, cv2.CV_16SC2)
    fixed = PitchProjector.from_fixed_maps(map1, map2, np.diag([0.5, 0.5, 1.0]))
    assert np.allclose(fixed.to_pitch(np.array([[30.0, 21.0]])), projector.to_pitch(np.array([[30.0, 21.0]])))


def test_tracks_feet_on_the_pitch(projector):
    # Track 7 stands still; track 3 walks right one pixel (two metres) a frame, with a gap.
    rows = [[f, 7, 10, 4, 4, 6] for f in range(5)] + [[f, 3, 2 + f, 10, 0, 10] for f in (0, 1, 3, 4)]
    tracks = from_rows(np.array(rows, dtype=float), FPS)
    assert tracks.ids == [3, 7] and tracks.frames == 5
    pitch = project(tracks, projector)
    assert np.allclose(pitch[:, 1], [20.0, 20.0])  # feet at (12, 10) px
    assert np.isnan(pitch[2, 0]).all()
    filled = fill_gaps(pitch)
    assert np.allclose(filled[:, 0, 0], [0, 2, 4, 6, 8]) and np.allclose(filled[:, 0, 1], 40)


def test_speeds_and_seconds():
    # One player jogging along the halfway line at 5 m/s for four seconds, then gone.
    t = np.arange(int(4 * FPS)) / FPS
    positions = np.full((len(t) + 10, 1, 2), np.nan)
    positions[: len(t), 0] = np.c_[30 + 5 * t, np.full_like(t, 34)]
    assert np.allclose(np.nanmedian(speeds(positions, FPS)), 5.0, atol=0.01)
    at, speed = per_second(positions, FPS)
    assert at.shape == (5, 1, 2) and np.allclose(speed[2:4, 0], 5.0, atol=0.01)
    assert np.isnan(smooth(positions)[-1]).all()
    teleport = positions.copy()
    teleport[50:, 0, 0] += 40  # a projection glitch is not a sprint
    assert np.nanmax(speeds(teleport, FPS)) <= 12


def lab(bgr):
    return cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2LAB)[0, 0].astype(float)


def test_shirt_colour_ignores_grass():
    crop = np.zeros((60, 24, 3), np.uint8)
    crop[:] = (40, 140, 40)  # grass
    crop[12:30, 6:18] = (200, 60, 20)  # a blue shirt
    assert np.allclose(shirt_colour(crop), lab((200, 60, 20)), atol=1)
    assert shirt_colour(np.full((60, 24, 3), (40, 140, 40), np.uint8)) is None


def test_teams_by_kit():
    rng = np.random.default_rng(1)
    blue, white, red, black, yellow = (lab(c) for c in ((200, 60, 20), (235, 235, 235), (30, 30, 220), (20, 20, 20),
                                                        (0, 230, 230)))
    colours = np.array([blue] * 10 + [white] * 10 + [red, black, yellow]) + rng.normal(0, 3, (23, 3))
    median_x = np.r_[rng.uniform(65, 90, 10), rng.uniform(15, 40, 10), 100.0, 4.0, 50.0]
    split = split_teams(colours, median_x)
    assert (split.labels[:10] == RIGHT).all() and (split.labels[10:20] == LEFT).all()  # white starts on the left
    assert split.labels[20] == RIGHT and split.labels[21] == LEFT and split.labels[22] == OTHER
    assert split.keepers == {20, 21}
    assert split.kits[0] > "#d0d0d0" and split.kits[1].startswith("#1")


def path(frames, keys):
    """Positions (frames, 2) through ``{frame: (x, y)}`` keyframes, held before and after."""
    f = sorted(keys)
    return np.c_[np.interp(np.arange(frames), f, [keys[k][0] for k in f]),
                 np.interp(np.arange(frames), f, [keys[k][1] for k in f])]


def test_events_from_tracking():
    from matchlens.domain.models import EventType as T
    from matchlens.domain.models import Outcome
    from matchlens.vision.events import detect

    n = 200
    # Kick-off, a carry, a pass to a team-mate, then a pass that an opponent cuts out.
    left_a = path(n, {10: (52.5, 34), 60: (60.5, 34)})
    left_b = path(n, {0: (75, 34)})
    right_a = path(n, {0: (90, 20)})
    keeper = path(n, {0: (103, 34)})
    ball = path(n, {10: (52.5, 34), 60: (60.5, 34), 75: (75, 34), 110: (75, 34), 125: (90, 20)})
    in_play = np.arange(n) >= 10
    teams = np.array([LEFT, LEFT, RIGHT, RIGHT])
    found = detect(np.stack([left_a, left_b, right_a, keeper], axis=1), ball, in_play, teams, frozenset({3}), FPS)
    kinds = [(d.type, d.track) for d in found]
    assert kinds == [(T.KICKOFF, 0), (T.CARRY, 0), (T.PASS, 0), (T.PASS, 1), (T.INTERCEPTION, 2),
                     (T.POSSESSION_CHANGE, 2)]
    kickoff, carry, completed, misplaced, *_ = found
    assert kickoff.frame == 10 and carry.attrs["duration_s"] >= 1.5
    assert completed.outcome == Outcome.SUCCESS and completed.other == 1 and completed.end == (75.0, 34.0)
    assert misplaced.outcome == Outcome.FAIL and found[4].team == RIGHT


def test_shot_and_goal():
    from matchlens.domain.models import EventType as T
    from matchlens.domain.models import Outcome
    from matchlens.vision.events import detect

    n = 120
    # Struck from 15 m, called dead on the line, then the other team kicks off.
    striker = path(n, {0: (90, 34)})
    taker = path(n, {0: (52.5, 34)})
    ball = path(n, {10: (90, 34), 25: (105.5, 35), 60: (105.5, 35), 61: (52.5, 34)})
    in_play = (np.arange(n) < 24) | (np.arange(n) > 61)
    found = detect(np.stack([striker, taker], axis=1), ball, in_play, np.array([LEFT, RIGHT]), frozenset(), FPS)
    shot = next(d for d in found if d.type == T.SHOT)
    assert shot.outcome == Outcome.GOAL and 0.05 < shot.attrs["xg"] < 0.5 and shot.attrs["shot_speed_kmh"] > 60
    assert [d.type for d in found if d.type in (T.GOAL, T.KICKOFF)] == [T.GOAL, T.KICKOFF]


def test_restarts_named_by_place():
    from matchlens.domain.models import EventType as T
    from matchlens.vision.events import _restart

    assert _restart(LEFT, (52.5, 33.0)) == (T.KICKOFF, LEFT)
    assert _restart(LEFT, (40.0, 67.5)) == (T.THROW_IN, LEFT)
    assert _restart(LEFT, (0.5, 67.0)) == (T.CORNER, RIGHT)  # whoever touched it first
    assert _restart(LEFT, (101.0, 36.0)) == (T.GOAL_KICK, RIGHT)
    assert _restart(RIGHT, (30.0, 20.0)) == (T.FREE_KICK, RIGHT)


def test_scoring_against_labels():
    from matchlens.domain.models import EventType as T
    from matchlens.vision.evaluate import Labelled, score
    from matchlens.vision.events import Detected

    found = [Detected(T.PASS, 100, 0, LEFT), Detected(T.PASS, 300, 0, LEFT), Detected(T.PASS, 500, 2, RIGHT),
             Detected(T.CARRY, 260, 1, LEFT, attrs={"duration_s": 2.0})]
    labels = [Labelled(110, T.PASS, LEFT), Labelled(500, T.PASS, LEFT), Labelled(212, T.CARRY, LEFT)]
    by_type = {s.type: s for s in score(found, labels, FPS)}
    assert (by_type[T.PASS].matched, by_type[T.PASS].recall, round(by_type[T.PASS].precision, 2)) == (1, 0.5, 0.33)
    assert by_type[T.CARRY].matched == 1  # compared at the start of the run
