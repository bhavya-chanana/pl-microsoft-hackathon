"""Whole-match calibration on a synthetic camera (never real footage): one gantry, a panning and zooming lens with
roll and radial distortion, noisy pitch keypoints. Skipped without ``uv sync --group vision``."""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("scipy")

from matchlens.vision.bundle import (  # noqa: E402
    CameraPrior,
    FrameObs,
    LiveCameraFilter,
    PitchModel,
    SolveOptions,
    associate,
    decompose,
    solve_match,
    to_centred,
    update_venue_prior,
    venue_prior,
)

SIZE = (960, 540)
CENTRE = np.array([52.5, 92.0, 17.0])  # 24 m behind the near touchline, 17 m up
ROLL, K1 = 0.012, -0.06
FPS = 5.0


def true_project(xy: np.ndarray, pan: float, tilt: float, focal: float) -> np.ndarray:
    """Independent z-up forward model: pitch metres -> raw pixels."""
    p, t = pan, tilt
    forward = np.array([np.sin(p) * np.cos(t), -np.cos(p) * np.cos(t), -np.sin(t)])
    right = np.cross([0.0, 0.0, 1.0], forward)
    right /= np.linalg.norm(right)
    down = np.cross(right, forward)
    right, down = (np.cos(ROLL) * right + np.sin(ROLL) * down, -np.sin(ROLL) * right + np.cos(ROLL) * down)
    rel = np.c_[xy, np.zeros(len(xy))] - CENTRE
    x, y, z = rel @ right, rel @ down, rel @ forward
    xn, yn = x / z, y / z
    s = 1 + K1 * (xn**2 + yn**2)
    return np.c_[SIZE[0] / 2 + focal * xn * s, SIZE[1] / 2 + focal * yn * s]


def trajectory(n: int):
    """Look-at point sweeps the pitch; zoom breathes between 0.8 and 1.9 image widths."""
    k = np.arange(n)
    gx = 52.5 + 38 * np.sin(k / 31.0)
    gy = 34 + 14 * np.sin(k / 47.0 + 1.0)
    focal = SIZE[0] * np.exp(np.log(1.25) + 0.42 * np.sin(k / 23.0 + 0.5))
    out = []
    for x, y, f in zip(gx, gy, focal, strict=True):
        v = np.array([x - CENTRE[0], y - CENTRE[1], -CENTRE[2]])
        out.append((np.arctan2(v[0], -v[1]), np.arcsin(-v[2] / np.linalg.norm(v)), f))
    return out


def dlt(src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
    """Normalised direct linear transform, like cv2.findHomography without the robust part."""
    if len(src) < 4:
        return None

    def norm(p):
        c = p.mean(0)
        s = np.sqrt(2) / max(np.sqrt(((p - c) ** 2).sum(1)).mean(), 1e-9)
        return np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1.0]])
    ts, td = norm(src), norm(dst)
    a = (np.c_[src, np.ones(len(src))] @ ts.T)[:, :2]
    b = (np.c_[dst, np.ones(len(dst))] @ td.T)[:, :2]
    rows = []
    for (x, y), (u, v) in zip(a, b, strict=True):
        rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y, -u], [0, 0, 0, x, y, 1, -v * x, -v * y, -v]]
    h = np.linalg.svd(np.array(rows))[2][-1].reshape(3, 3)
    h = np.linalg.inv(td) @ h @ ts
    return h / h[2, 2]


def make_frames(n: int = 150, noise: float = 0.4, kind: str = "points", sparse: tuple[int, ...] = (),
                seed: int = 4) -> tuple[list[FrameObs], list[tuple[float, float, float]]]:
    rng = np.random.default_rng(seed)
    model = PitchModel()
    names = np.array(list(model.landmarks().values()))
    traj = trajectory(n)
    frames = []
    for i, (pan, tilt, focal) in enumerate(traj):
        if kind == "points":
            px = true_project(names, pan, tilt, focal)
            inside = (px[:, 0] > 0) & (px[:, 0] < SIZE[0]) & (px[:, 1] > 0) & (px[:, 1] < SIZE[1])
            xy, img, tan = names[inside], px[inside], None
        else:
            sample = model.points[::7]
            px = true_project(sample, pan, tilt, focal)
            inside = (px[:, 0] > 0) & (px[:, 0] < SIZE[0]) & (px[:, 1] > 0) & (px[:, 1] < SIZE[1])
            xy, img, tan = sample[inside], px[inside], model.tangents[::7][inside]
        if i in sparse:
            keep = rng.permutation(len(xy))[:2]
            xy, img = xy[keep], img[keep]
            tan = None if tan is None else tan[keep]
        img = img + rng.normal(0, noise, img.shape)
        init = dlt(xy, img)
        frames.append(FrameObs(frame=i, t=i / FPS, image_pts=img, pitch_pts=xy, tangents=tan, init_h=init,
                               size=SIZE, shot=0 if i < 100 else 1))
    return frames, traj


def pitch_error(cal, pan, tilt, focal) -> float:
    """Median metres between where the fitted camera sends the true pixels of in-view pitch points and where they are."""
    grid = np.array([[x, y] for x in np.linspace(5, 100, 12) for y in np.linspace(3, 65, 6)])
    px = true_project(grid, pan, tilt, focal)
    ok = (px[:, 0] > 20) & (px[:, 0] < SIZE[0] - 20) & (px[:, 1] > 20) & (px[:, 1] < SIZE[1] - 20)
    if ok.sum() < 4:
        return 0.0
    return float(np.median(np.linalg.norm(cal.image_to_pitch(px[ok]) - grid[ok], axis=1)))


@pytest.fixture(scope="module")
def solved():
    frames, traj = make_frames(sparse=(20, 21, 22, 60))
    cal = solve_match(frames, options=SolveOptions(min_points=6))
    return frames, traj, cal


def test_decompose_recovers_pose():
    pan, tilt, focal = trajectory(1)[0]
    pts = np.array([[x, y] for x in np.linspace(10, 95, 6) for y in np.linspace(5, 60, 5)])
    # No distortion here: project with k1 = 0 through the same independent model.
    global K1
    saved, K1 = K1, 0.0
    try:
        px = true_project(pts, pan, tilt, focal)
    finally:
        K1 = saved
    h = dlt(pts, px)
    d = decompose(h, SIZE)
    assert d is not None
    centre, _, _, roll, f = d
    assert np.allclose(centre, CENTRE, atol=0.3)
    assert abs(roll - ROLL) < 1e-3 and abs(f - focal) / focal < 0.01


def test_shared_camera_recovered(solved):
    _, _, cal = solved
    p = cal.prior
    assert np.linalg.norm(np.array(p.centre) - CENTRE) < 0.5
    assert abs(p.roll - ROLL) < 0.01
    assert abs(p.k1 - K1) < 0.02
    assert p.history == 1 and p.focal_rel is not None and p.pan is not None


def test_every_frame_within_30cm(solved):
    frames, traj, cal = solved
    errors = [pitch_error(c, *traj[i]) for i, c in enumerate(cal.frames) if c.status == "fitted"]
    assert len(errors) >= 120
    assert max(errors) < 0.3
    assert np.median(errors) < 0.12


def test_sparse_frames_are_interpolated_not_dropped(solved):
    frames, traj, cal = solved
    for i in (20, 21, 22, 60):
        c = cal.frames[i]
        assert c.status == "interpolated"
        assert c.homography is not None
        assert pitch_error(c, *traj[i]) < 0.5
    s = cal.summary()
    assert s["calibrated_share"] > 0.9 and s["interpolated"] >= 4


def test_residual_report_in_pixels_and_metres(solved):
    _, _, cal = solved
    fitted = [c for c in cal.frames if c.status == "fitted"]
    assert np.median([c.rmse_px for c in fitted]) < 1.6  # noise sigma is 1 px per axis
    assert np.median([c.rmse_m for c in fitted]) < 0.2


def test_line_points_are_enough():
    frames, traj = make_frames(n=120, kind="lines", noise=0.7)
    cal = solve_match(frames, options=SolveOptions(min_points=20))
    assert np.linalg.norm(np.array(cal.prior.centre) - CENTRE) < 0.5
    errors = [pitch_error(c, *traj[i]) for i, c in enumerate(cal.frames) if c.status == "fitted"]
    assert len(errors) >= 100 and max(errors) < 0.3


def test_smoothing_cuts_jitter_without_losing_the_fit():
    frames, traj = make_frames(n=100, noise=1.2)
    raw = solve_match(frames, options=SolveOptions(min_points=6, process_q=(1e4, 1e4, 1e4)))
    smooth = solve_match(frames, options=SolveOptions(min_points=6))

    def jitter(c):
        pan = np.array([f.pan for f in c.frames])
        return float(np.std(np.diff(pan, 2)))
    assert jitter(smooth) < jitter(raw)
    assert np.linalg.norm(np.array(smooth.prior.centre) - CENTRE) < 4.0


def test_prior_makes_live_filter_work(solved):
    frames, traj, cal = solved
    live = LiveCameraFilter(cal.prior, SolveOptions(min_points=6))
    # Start cold: no starting homography on the first frame, so the filter searches for the pose.
    out = []
    for i, o in enumerate(frames[:100]):
        out.append(live.update(FrameObs(frame=o.frame, t=o.t, image_pts=o.image_pts, pitch_pts=o.pitch_pts,
                                        init_h=None, size=o.size, shot=o.shot)))
    errors = [pitch_error(c, *traj[i]) for i, c in enumerate(out) if c.status == "fitted"]
    assert len(errors) > 80 and np.median(errors) < 0.2 and max(errors) < 0.6


def test_live_filter_needs_a_fitted_prior():
    with pytest.raises(ValueError):
        LiveCameraFilter(CameraPrior())


def test_live_filter_restarts_at_a_cut_and_flags_a_gap(solved):
    frames, traj, cal = solved
    live = LiveCameraFilter(cal.prior, SolveOptions(min_points=6))
    first = live.update(frames[40])
    assert first.status == "fitted"
    empty = FrameObs(frame=41, t=frames[41].t, image_pts=np.zeros((0, 2)), pitch_pts=np.zeros((0, 2)), size=SIZE, shot=0)
    gap = live.update(empty)
    assert gap.status == "interpolated" and np.isfinite(gap.pan)
    late = FrameObs(frame=42, t=frames[41].t + 10, image_pts=np.zeros((0, 2)), pitch_pts=np.zeros((0, 2)), size=SIZE, shot=0)
    assert live.update(late).status == "none"


def test_time_lookup_and_not_main_gaps(solved):
    frames, _, cal = solved
    h = cal.homography_at(frames[10].t)
    assert h is not None and h.shape == (3, 3)
    assert cal.main_at(frames[10].t)
    assert not cal.main_at(frames[-1].t + 30.0)
    assert cal.homography_at(frames[-1].t + 30.0) is None


def test_centred_convention():
    assert np.allclose(to_centred(np.array([[52.5, 34.0], [0.0, 0.0]])), [[0, 0], [-52.5, -34.0]])


def test_associate_matches_lines_within_tolerance():
    model = PitchModel()
    pan, tilt, focal = trajectory(1)[0]
    px = true_project(model.points[::5], pan, tilt, focal)
    ok = (px[:, 0] > 0) & (px[:, 0] < SIZE[0]) & (px[:, 1] > 0) & (px[:, 1] < SIZE[1])
    paint = px[ok]

    def project(xy):
        return true_project(xy, pan, tilt, focal)
    img, pit, tan = associate(paint, project, model, 3.0, SIZE)
    assert len(img) == len(paint)
    assert np.allclose(np.linalg.norm(tan, axis=1), 1.0)


def test_venue_prior_round_trip_and_merge(tmp_path, solved):
    _, _, cal = solved
    path = tmp_path / "venues.json"
    path.write_text(json.dumps({"somewhere": {"name": "Somewhere", "pitch": {"length": 101, "width": 68, "verified": False},
                                               "camera": None, "history": 0}}))
    assert not venue_prior("somewhere", path).has_camera
    assert venue_prior("somewhere", path).pitch_length == 101
    merged = update_venue_prior("somewhere", cal, path)
    assert merged.history == 1 and np.allclose(merged.centre, cal.prior.centre)
    again = update_venue_prior("somewhere", cal, path)
    assert again.history == 2 and np.allclose(again.centre, cal.prior.centre)
    assert venue_prior("somewhere", path).has_camera and venue_prior("somewhere", path).pitch_length == 101


def test_quality_at_gives_status_and_error(solved):
    frames, _, cal = solved
    q = cal.quality_at(frames[10].t)
    assert q["calib_status"] == "fitted" and q["rmse_px"] is not None and q["rmse_m"] is not None
    assert 0.0 < q["inlier_frac"] <= 1.0 and q["n_points"] > 0
    gap = cal.quality_at(frames[20].t)
    assert gap["calib_status"] == "interpolated" and gap["rmse_px"] is None
    assert cal.quality_at(frames[-1].t + 30.0) is None


def test_unsmoothed_fits_are_kept_on_request_and_rts_modes_agree_on_the_camera():
    frames, traj = make_frames(n=90, noise=0.4)
    kept = solve_match(frames, options=SolveOptions(min_points=6, keep_unsmoothed=True))
    assert kept.unsmoothed is not None and len(kept.unsmoothed) == len(kept.frames)
    gaps = solve_match(frames, options=SolveOptions(min_points=6, rts="gaps"))
    assert np.allclose(kept.prior.centre, gaps.prior.centre, atol=0.5)
    # a measured frame keeps its own fit in "gaps" mode, so it equals the unsmoothed value
    i = 40
    assert gaps.frames[i].pan == pytest.approx(kept.unsmoothed[i].pan, abs=1e-3)


def test_fixed_lens_term_is_held():
    frames, _ = make_frames(n=60, noise=0.4)
    cal = solve_match(frames, options=SolveOptions(min_points=6, fix_k1=0.0))
    assert cal.prior.k1 == 0.0


def test_centre_sigma_has_a_floor_for_model_error(solved):
    _, _, cal = solved
    assert all(s >= f for s, f in zip(cal.prior.centre_sigma, (0.5, 0.5, 0.25), strict=True))
