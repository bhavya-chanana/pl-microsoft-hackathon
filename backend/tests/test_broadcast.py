"""Broadcast calibration on synthetic frames: a textured pitch filmed by a known panning camera
(never real footage). Skipped without ``uv sync --group vision``."""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("scipy")

from matchlens.vision.camera import CameraTracker, FrameLines, fit, plausible, project, to_pitch  # noqa: E402
from matchlens.vision.lines import distance_map  # noqa: E402
from matchlens.vision.markings import landmarks, model_points, polylines  # noqa: E402
from matchlens.vision.views import classify, look  # noqa: E402

SIZE = (960, 540)
PX_PER_M = 20
EXTENT = (-12.0, -20.0, 117.0, 80.0)  # x0, y0, x1, y1 of the textured ground, metres
CAMERA = np.array([52.5, 98.0, 20.0])  # 30 m behind the near touchline, 20 m up


def camera(pan: float = 0.0, tilt: float = 18.0, focal: float = 950.0) -> np.ndarray:
    """Pitch metres -> pixels for the main camera turned ``pan`` degrees right and ``tilt`` down."""
    p, t = np.radians(pan), np.radians(tilt)
    forward = np.array([np.sin(p) * np.cos(t), -np.cos(p) * np.cos(t), -np.sin(t)])
    right = np.cross([0.0, 0.0, 1.0], forward)
    right /= np.linalg.norm(right)
    down = np.cross(right, forward)
    r = np.array([right, down, forward])
    k = np.array([[focal, 0, SIZE[0] / 2], [0, focal, SIZE[1] / 2], [0, 0, 1]])
    return k @ np.c_[r[:, 0], r[:, 1], -r @ CAMERA]


@pytest.fixture(scope="module")
def ground() -> np.ndarray:
    return make_ground()


def make_ground() -> np.ndarray:
    """The pitch as seen from straight above: striped, noisy grass, white lines, stands behind."""
    rng = np.random.default_rng(3)
    x0, y0, x1, y1 = EXTENT
    w, h = int((x1 - x0) * PX_PER_M), int((y1 - y0) * PX_PER_M)
    xs = (np.arange(w) / PX_PER_M + x0)[None, :].repeat(h, 0)
    stripe = (np.floor(xs / 5.25) % 2 == 0)
    img = np.where(stripe[..., None], [52, 140, 58], [44, 122, 50]).astype(np.float32)
    img += rng.normal(0, 7, (h, w, 1))
    stands = int((-6 - y0) * PX_PER_M)
    img[:stands] = rng.integers(40, 200, (stands // 8 + 1, w // 8 + 1, 3)).repeat(8, 0).repeat(8, 1)[:stands, :w]
    img = np.clip(img, 0, 255).astype(np.uint8)
    for line in polylines(0.1):
        pts = ((line - [x0, y0]) * PX_PER_M).round().astype(np.int32)
        cv2.polylines(img, [pts], False, (235, 235, 235), 3, cv2.LINE_AA)
    return img


def render(ground: np.ndarray, h: np.ndarray, players: int = 14, seed: int = 0) -> np.ndarray:
    x0, y0 = EXTENT[:2]
    to_ground = np.array([[1 / PX_PER_M, 0, x0], [0, 1 / PX_PER_M, y0], [0, 0, 1]])
    # Rendered three times over and shrunk, as a lens would blur it: a plain warp drops thin far lines.
    big = cv2.warpPerspective(ground, np.diag([3.0, 3.0, 1.0]) @ h @ to_ground, (SIZE[0] * 3, SIZE[1] * 3),
                              flags=cv2.INTER_LINEAR, borderValue=(90, 90, 90))
    frame = cv2.resize(big, SIZE, interpolation=cv2.INTER_AREA)
    rng = np.random.default_rng(seed)
    feet, _ = project(h, np.c_[rng.uniform(5, 100, players), rng.uniform(5, 63, players)])
    for i, (x, y) in enumerate(feet):
        colour = (30, 30, 200) if i % 2 else (240, 240, 240)
        cv2.rectangle(frame, (int(x - 5), int(y - 28)), (int(x + 5), int(y)), colour, -1)
    return frame


def reprojection_px(h: np.ndarray, truth: np.ndarray) -> float:
    """Mean pixel distance between where two calibrations put the landmarks in view."""
    pts = np.array(list(landmarks().values()))
    a, _ = project(h, pts)
    b, _ = project(truth, pts)
    seen = (b[:, 0] >= 0) & (b[:, 0] < SIZE[0]) & (b[:, 1] >= 0) & (b[:, 1] < SIZE[1])
    return float(np.linalg.norm(a[seen] - b[seen], axis=1).mean())


def test_landmarks_lie_on_the_markings():
    model = model_points(0.05)
    for name, point in landmarks().items():
        if "spot" in name:
            continue  # spots are painted dots, not lines
        assert np.min(np.linalg.norm(model - point, axis=1)) < 0.05, name


def test_camera_model_looks_at_the_pitch():
    h = camera()
    centre, _ = project(h, np.array([[52.5, 34.0]]))
    assert abs(centre[0, 0] - SIZE[0] / 2) < 1 and 0 < centre[0, 1] < SIZE[1]
    near, _ = project(h, np.array([[52.5, 68.0]]))
    assert near[0, 1] > centre[0, 1]  # the near touchline is lower in the frame
    assert np.allclose(to_pitch(h, centre), [[52.5, 34.0]], atol=1e-6)


def test_squashed_or_mirrored_cameras_are_not_plausible():
    h = camera(pan=5)
    shape = (SIZE[1], SIZE[0])
    assert plausible(h, shape)
    # The whole pitch squashed onto one line of the frame: every marking would sit on that line.
    assert not plausible(np.c_[h[:, 0], h[:, 0] * 1.01, h[:, 2]], shape)
    # The pitch flipped end to end, as if filmed from the far side.
    assert not plausible(h @ np.array([[-1.0, 0, 105], [0, 1, 0], [0, 0, 1]]), shape)
    assert not plausible(camera(pan=5, focal=40), shape)  # far too wide for the main camera


def test_lines_found_where_the_markings_are(ground):
    h = camera(pan=-12)
    frame = FrameLines(render(ground, h))
    assert frame.field.mean() / 255 > 0.6
    marked = np.zeros(frame.lines.shape, np.uint8)
    for line in polylines(0.1):
        pts, _ = project(h, line)
        cv2.polylines(marked, [pts.round().astype(np.int32)], False, 255, 1)
    near_marking = distance_map(marked)[frame.lines > 0] <= 3
    assert near_marking.mean() > 0.9  # what was found is paint
    found = frame.dist[marked > 0] <= 3
    assert found.mean() > 0.7  # and most of the paint in view was found


def test_fit_recovers_a_nudged_camera(ground):
    truth = camera(pan=8, focal=1000)
    frame = FrameLines(render(ground, truth))
    guess = camera(pan=9.2, tilt=17.4, focal=950)
    assert reprojection_px(guess, truth) > 15
    cal = fit(guess, frame, "anchor")
    assert cal is not None and cal.score > 0.8
    assert reprojection_px(cal.homography, truth) < 1.5


def test_tracker_follows_a_pan_and_zoom(ground):
    tracker = CameraTracker()
    errors = []
    for i in range(40):
        truth = camera(pan=-15 + 0.5 * i, tilt=18 + 0.02 * i, focal=950 + 3 * i)
        anchor = camera(pan=-14, tilt=18.3, focal=930) if i == 0 else None
        cal = tracker.step(render(ground, truth, seed=i), main=True, anchor=anchor)
        assert cal is not None, i
        errors.append(reprojection_px(cal.homography, truth))
    # Worst once the penalty area leaves the shot: the centre circle and halfway line pin down less.
    assert np.mean(errors) < 1.5 and max(errors) < 3.5
    assert len(tracker.keyframes.items) >= 2


def test_relocates_after_a_cut(ground):
    tracker = CameraTracker()
    for i in range(30):
        anchor = camera(pan=-10) if i == 0 else None
        tracker.step(render(ground, camera(pan=-10 + i), seed=i), main=True, anchor=anchor)
    assert tracker.step(np.full((SIZE[1], SIZE[0], 3), 120, np.uint8), main=False) is None
    truth = camera(pan=6.5, focal=980)
    cal = tracker.step(render(ground, truth, seed=99), main=True, cut=True)
    assert cal is not None and cal.how == "relocated"
    assert reprojection_px(cal.homography, truth) < 2.0


def test_main_camera_frames_and_cuts(ground):
    rng = np.random.default_rng(1)
    wide = render(ground, camera())
    closeup = wide.copy()
    cv2.ellipse(closeup, (480, 330), (200, 320), 0, 0, 360, (40, 40, 180), -1)  # a player filling the frame
    crowd = rng.integers(0, 255, (SIZE[1] // 10, SIZE[0] // 10, 3), dtype=np.uint8).repeat(10, 0).repeat(10, 1)
    assert look(wide).main and not look(closeup).main and not look(crowd).main

    frames = [render(ground, camera(pan=p)) for p in range(10)] + [crowd] * 8 + [render(ground, camera(pan=5))] * 8
    frames[4] = closeup  # one frame of a player crossing the lens does not split the shot
    views = classify(frames)
    assert views.cuts == [10, 18]
    assert views.main.tolist() == [True] * 10 + [False] * 8 + [True] * 8


def test_clip_end_to_end(tmp_path, ground):
    """A clip with a cut away and back: sorted, calibrated from one labelled frame, checked on others."""
    from matchlens.vision.broadcast import Labelled, calibrate, errors, frames

    shots = [camera(pan=-12 + 0.5 * i) for i in range(16)] + [None] * 8 + [camera(pan=4 + 0.5 * i) for i in range(12)]
    crowd = np.random.default_rng(2).integers(0, 255, (54, 96, 3), dtype=np.uint8).repeat(10, 0).repeat(10, 1)
    clip = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 25, SIZE)
    for i, h in enumerate(shots):
        writer.write(render(ground, h, seed=i) if h is not None else crowd)
    writer.release()

    views = classify(frames(clip, SIZE[0]))
    assert views.main.tolist() == [h is not None for h in shots]
    results = calibrate(clip, views, {2: shots[2]}, factor=1.0)
    assert all((r is not None) == (h is not None) for r, h in zip(results, shots, strict=True))
    assert results[24].how == "relocated"
    names = list(landmarks())
    for i in (10, 30):
        pixels, _ = project(shots[i], np.array([landmarks()[n] for n in names]))
        seen = (pixels[:, 0] > 0) & (pixels[:, 0] < SIZE[0]) & (pixels[:, 1] > 0) & (pixels[:, 1] < SIZE[1])
        lab = Labelled(i, True, [n for n, s in zip(names, seen, strict=True) if s], pixels[seen],
                       np.array([landmarks()[n] for n, s in zip(names, seen, strict=True) if s]))
        metres, px = errors(results[i].homography, lab)
        assert np.median(px) < 2.0 and np.median(metres) < 0.5, i
