"""Calibrate a whole match at once: one camera centre, roll and lens, and pan / tilt / focal per frame.

The main broadcast camera sits on one gantry. Its position, its roll and its lens distortion do not change over
a match; only where it points (pan, tilt) and how far it is zoomed (focal length) do. Calibrating every frame as
an 8-parameter homography throws that away. Here the shared part is solved once from every frame's
correspondences, which leaves three numbers per frame:

    shared   camera centre C = (x, y, height) in pitch metres, roll, radial distortion k1 (and k2)
    per frame  pan, tilt, log focal length   (principal point at the image centre, square pixels)

Pipeline (``solve_match``):

1. **Start.** Shared values from the venue prior, or from decomposing the frames' per-frame homographies
   (``FrameObs.init_h``) and taking the median. Per-frame values from those homographies.
2. **Per-frame fit.** Three parameters per frame with the shared values held, robust (soft-L1) loss.
3. **Joint solve.** ``scipy.optimize.least_squares`` over the shared values and the per-frame values of a spread
   of frames (a sparse Jacobian: a frame's points touch only its own three columns and the shared ones), with a
   smoothness residual on the acceleration of (pan, tilt, log focal) between neighbouring frames.
4. **Refit** every frame with the final shared values.
5. **Smooth.** A constant-velocity Kalman filter and Rauch-Tung-Striebel smoother over (pan, tilt, log focal) in
   each shot, weighting each frame by how well its points pin the camera down. Frames with too few points are not
   dropped: the smoother fills them and they are flagged ``interpolated``.

``LiveCameraFilter`` is the same filter run forward only, with the shared values fixed from a venue prior.

Pitch coordinates are those of ``markings.py`` (metres, x 0..105 left to right, y 0 at the far touchline to 68 at the
camera's side). ``to_centred`` moves to the centre-origin convention. The camera world frame is (x, y, z down),
which is right-handed with the camera axes (right, down, forward).

A frame's ``homography`` takes pitch metres to **undistorted** pixels. Raw detections need the lens too: use
``FrameCalibration.image_to_pitch`` / ``pitch_to_image``.

Observations (``FrameObs``) come from outside this module: keypoints (``pitch_pts`` and ``image_pts``), or points
on painted lines with the marking's direction (``tangents``), which makes each a point-to-line residual so a point
may slide along its line. ``observations_from_frame`` builds the second kind from an image and a guess.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix, identity
from scipy.sparse.linalg import splu

from .markings import ARC_HALF_ANGLE, ARC_MEETS_BOX, BOX_DEPTH, BOX_HALF, CIRCLE_R, GOAL_AREA_DEPTH
from .markings import GOAL_AREA_HALF, POST_HALF, SPOT

LENGTH, WIDTH = 105.0, 68.0
REF_WIDTH = 960.0
VENUES = Path(__file__).with_name("venues.json")


# --------------------------------------------------------------------------------------------------------------
# Data


@dataclass
class FrameObs:
    """One main-camera frame's evidence.

    ``image_pts`` (N, 2) are raw pixels of the frame at ``size``; ``pitch_pts`` (N, 2) the pitch metres they
    belong to. With ``tangents`` (N, 2, unit direction of the marking at that pitch point) a point only has to
    lie on the marking's line (one residual, perpendicular to it); without, it has to land on the exact spot (two).
    ``init_h`` is an existing pitch-metres to pixels homography for the frame, used to start the solve.
    ``shot`` is a counter that changes at every cut: smoothing never crosses it.
    """

    frame: int
    t: float
    image_pts: np.ndarray
    pitch_pts: np.ndarray
    tangents: np.ndarray | None = None
    weights: np.ndarray | None = None
    init_h: np.ndarray | None = None
    size: tuple[int, int] | None = None
    shot: int | None = None

    def __post_init__(self) -> None:
        self.image_pts = np.asarray(self.image_pts, np.float64).reshape(-1, 2)
        self.pitch_pts = np.asarray(self.pitch_pts, np.float64).reshape(-1, 2)
        if len(self.image_pts) != len(self.pitch_pts):
            raise ValueError("image_pts and pitch_pts differ in length")
        n = len(self.image_pts)
        if self.tangents is not None:
            self.tangents = np.asarray(self.tangents, np.float64).reshape(-1, 2)
            if len(self.tangents) != n:
                raise ValueError("tangents differ in length from image_pts")
        if self.weights is not None:
            self.weights = np.asarray(self.weights, np.float64).reshape(-1)
            if len(self.weights) != n:
                raise ValueError("weights differ in length from image_pts")
        if self.init_h is not None:
            self.init_h = np.asarray(self.init_h, np.float64).reshape(3, 3)

    @property
    def n_points(self) -> int:
        return len(self.image_pts)


@dataclass
class CameraPrior:
    """What is known about a camera before a frame is seen: the shared values, and ranges of the per-frame ones.

    ``centre`` is (x, y, height) in pitch metres; ``focal_rel`` is focal length over image width; angles in
    radians (``pan`` 0 looks straight across the pitch to the far touchline, positive to the right of that).
    Fields left ``None`` are unknown. ``history`` counts the matches merged into it.
    """

    centre: tuple[float, float, float] | None = None
    centre_sigma: tuple[float, float, float] | None = None
    roll: float | None = None
    k1: float | None = None
    k2: float = 0.0
    focal_rel: tuple[float, float] | None = None
    pan: tuple[float, float] | None = None
    tilt: tuple[float, float] | None = None
    pitch_length: float = LENGTH
    pitch_width: float = WIDTH
    image_size: tuple[int, int] | None = None
    history: int = 0

    @property
    def has_camera(self) -> bool:
        return self.centre is not None and self.roll is not None and self.k1 is not None

    def to_dict(self) -> dict:
        def tup(v):
            return None if v is None else [float(x) for x in v]
        return {"centre": tup(self.centre), "centre_sigma": tup(self.centre_sigma), "roll": self.roll,
                "k1": self.k1, "k2": self.k2, "focal_rel": tup(self.focal_rel), "pan": tup(self.pan),
                "tilt": tup(self.tilt), "pitch_length": self.pitch_length, "pitch_width": self.pitch_width,
                "image_size": None if self.image_size is None else list(self.image_size), "history": self.history}

    @classmethod
    def from_dict(cls, d: dict) -> CameraPrior:
        def tup(v):
            return None if v is None else tuple(float(x) for x in v)
        size = d.get("image_size")
        return cls(centre=tup(d.get("centre")), centre_sigma=tup(d.get("centre_sigma")), roll=d.get("roll"),
                   k1=d.get("k1"), k2=d.get("k2") or 0.0, focal_rel=tup(d.get("focal_rel")), pan=tup(d.get("pan")),
                   tilt=tup(d.get("tilt")), pitch_length=d.get("pitch_length") or LENGTH,
                   pitch_width=d.get("pitch_width") or WIDTH,
                   image_size=None if size is None else (int(size[0]), int(size[1])), history=int(d.get("history", 0)))


@dataclass
class FrameCalibration:
    """One frame's result. ``status``: ``fitted`` (its own points pinned the camera), ``interpolated`` (filled by
    the smoother from neighbours), or ``none`` (nothing usable nearby)."""

    frame: int
    t: float
    status: str
    size: tuple[int, int]
    pan: float = math.nan
    tilt: float = math.nan
    focal: float = math.nan  # pixels, at ``size``
    centre: tuple[float, float, float] = (math.nan,) * 3
    roll: float = 0.0
    k1: float = 0.0
    k2: float = 0.0
    n_points: int = 0
    rmse_px: float = math.nan  # at 960 px width, over inlier points
    rmse_m: float = math.nan
    inlier_frac: float = math.nan
    std: tuple[float, float, float] | None = None  # of (pan, tilt, log focal) after smoothing
    shot: int | None = None

    @property
    def usable(self) -> bool:
        return self.status != "none"

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("frame", "t", "status", "pan", "tilt", "focal", "roll", "k1", "k2",
                                           "n_points", "rmse_px", "rmse_m", "inlier_frac", "shot")}
        d.update(size=list(self.size), centre=list(self.centre), std=None if self.std is None else list(self.std))
        return d

    @classmethod
    def from_dict(cls, d: dict) -> FrameCalibration:
        d = dict(d)
        d["size"], d["centre"] = tuple(d["size"]), tuple(d["centre"])
        d["std"] = None if d.get("std") is None else tuple(d["std"])
        return cls(**d)

    @property
    def homography(self) -> np.ndarray | None:
        """Pitch metres to undistorted pixels (3x3), or None."""
        if not self.usable:
            return None
        return _homography(self.pan, self.tilt, self.focal, self.centre, self.roll, self.size)

    def pitch_to_image(self, pts: np.ndarray) -> np.ndarray:
        """Pitch metres (N, 2) to raw pixels (N, 2), lens included."""
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        px, _ = _project(pts, self.pan, self.tilt, math.log(self.focal),
                         (*self.centre, self.roll, self.k1, self.k2), np.array(self.size) / 2.0)
        return px

    def image_to_pitch(self, pixels: np.ndarray) -> np.ndarray:
        """Raw pixels (N, 2) to pitch metres (N, 2); NaN above the horizon."""
        h = self.homography
        if h is None:
            return np.full((len(np.reshape(pixels, (-1, 2))), 2), np.nan)
        pixels = np.asarray(pixels, np.float64).reshape(-1, 2)
        cx, cy = self.size[0] / 2.0, self.size[1] / 2.0
        xn, yn = _undistort((pixels[:, 0] - cx) / self.focal, (pixels[:, 1] - cy) / self.focal, self.k1, self.k2)
        und = np.c_[xn * self.focal + cx, yn * self.focal + cy]
        p = np.c_[und, np.ones(len(und))] @ np.linalg.inv(h).T
        with np.errstate(divide="ignore", invalid="ignore"):
            return p[:, :2] / p[:, 2:]


@dataclass
class MatchCalibration:
    """The result for every frame given to ``solve_match`` (in time order) and the fitted shared camera."""

    frames: list[FrameCalibration]
    prior: CameraPrior
    info: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._times = np.array([f.t for f in self.frames], np.float64)
        gaps = np.diff(self._times)
        gaps = gaps[(gaps > 0) & (gaps < 1.0)]
        self._tol = 1.5 * float(np.median(gaps)) if len(gaps) else 0.2

    def frame_at(self, t_s: float) -> FrameCalibration | None:
        """The frame nearest ``t_s`` if one lies within about 1.5 frame spacings (else it is not main camera)."""
        if not len(self._times):
            return None
        i = int(np.searchsorted(self._times, t_s))
        best = min((j for j in (i - 1, i) if 0 <= j < len(self._times)), key=lambda j: abs(self._times[j] - t_s))
        return self.frames[best] if abs(self._times[best] - t_s) <= self._tol else None

    def homography_at(self, t_s: float) -> np.ndarray | None:
        """Pitch metres to undistorted pixels at video time ``t_s``; None where not main camera or unusable."""
        f = self.frame_at(t_s)
        return None if f is None else f.homography

    def main_at(self, t_s: float) -> bool:
        return self.frame_at(t_s) is not None

    def image_to_pitch_at(self, t_s: float, pixels: np.ndarray) -> np.ndarray | None:
        f = self.frame_at(t_s)
        return None if f is None or not f.usable else f.image_to_pitch(pixels)

    def to_dict(self) -> dict:
        return {"prior": self.prior.to_dict(), "info": self.info, "frames": [f.to_dict() for f in self.frames]}

    @classmethod
    def from_dict(cls, d: dict) -> MatchCalibration:
        return cls([FrameCalibration.from_dict(f) for f in d["frames"]], CameraPrior.from_dict(d["prior"]),
                   d.get("info", {}))

    def summary(self) -> dict:
        n = len(self.frames)
        by = {s: sum(f.status == s for f in self.frames) for s in ("fitted", "interpolated", "none")}
        px = np.array([f.rmse_px for f in self.frames if f.status == "fitted" and np.isfinite(f.rmse_px)])
        m = np.array([f.rmse_m for f in self.frames if f.status == "fitted" and np.isfinite(f.rmse_m)])
        out = {"frames": n, **by,
               "calibrated_share": (by["fitted"] + by["interpolated"]) / max(n, 1),
               "fitted_share": by["fitted"] / max(n, 1),
               "reproj_px_median": float(np.median(px)) if px.size else None,
               "reproj_px_p90": float(np.percentile(px, 90)) if px.size else None,
               "reproj_m_median": float(np.median(m)) if m.size else None,
               "reproj_m_p90": float(np.percentile(m, 90)) if m.size else None}
        out["camera"] = self.prior.to_dict()
        out.update(self.info)
        return out


@dataclass
class SolveOptions:
    fit_k2: bool = False
    fit_roll: bool = True
    refit_shared: bool = True
    min_points: int = 30  # points a frame needs to count as a measurement
    loss_px: float = 2.0  # soft-L1 scale, pixels at 960 wide
    max_joint_frames: int = 1000
    joint_points: int = 120  # per frame in the joint solve
    max_nfev: int = 60
    tol: float = 1e-10
    max_interp_s: float = 2.0  # a frame with no points is filled only this close to a measured one
    seg_gap_s: float = 1.0  # a longer hole between frames starts a new smoothing segment
    gate_rms_px: float = 3.5  # a frame fitting worse than this (inlier rms) is not a measurement
    gate_inliers: float = 0.5
    accel_sigma: tuple[float, float, float] = (0.5, 0.5, 0.5)  # rad/s^2, rad/s^2, 1/s^2: smoothness in the joint solve
    process_q: tuple[float, float, float] = (0.5, 0.5, 0.3)  # acceleration noise density of the smoother
    meas_inflate: float = 4.0  # neighbouring paint pixels are not independent: inflate the fit's variance
    meas_floor: tuple[float, float, float] = (1e-3, 1e-3, 3e-3)  # std floor on (pan, tilt, log focal)
    info_std: tuple[float, float, float] = (0.01, 0.01, 0.05)  # measurement counts as fitted below these stds
    progress: Callable[[float], None] | None = None


# --------------------------------------------------------------------------------------------------------------
# Camera model


def _camera_axes(pan, tilt, roll):
    """Camera (right, down, forward) unit vectors in the (x, y, z-down) world."""
    sp, cp, st, ct = np.sin(pan), np.cos(pan), np.sin(tilt), np.cos(tilt)
    sr, cr = np.sin(roll), np.cos(roll)
    forward = (sp * ct, -cp * ct, st)
    right = (cr * cp - sr * st * sp, cr * sp + sr * st * cp, sr * ct)
    down = (-sr * cp - cr * st * sp, -sr * sp + cr * st * cp, cr * ct)
    return right, down, forward


def _project(xy, pan, tilt, logf, shared, half):
    """Pitch metres (N, 2) to raw pixels (N, 2) and depth. ``shared`` = (cx, cy, h, roll, k1, k2); the per-frame
    values and ``half`` (half the image size) broadcast against the points."""
    cx, cy, h, roll, k1, k2 = shared
    right, down, fwd = _camera_axes(pan, tilt, roll)
    dx, dy, dz = xy[:, 0] - cx, xy[:, 1] - cy, h
    x = right[0] * dx + right[1] * dy + right[2] * dz
    y = down[0] * dx + down[1] * dy + down[2] * dz
    z = fwd[0] * dx + fwd[1] * dy + fwd[2] * dz
    zc = np.maximum(z, 1e-3)
    xn, yn = x / zc, y / zc
    r2 = xn * xn + yn * yn
    s = 1.0 + k1 * r2 + k2 * r2 * r2
    f = np.exp(logf)
    half = np.asarray(half)
    return np.stack([half[..., 0] + f * xn * s, half[..., 1] + f * yn * s], axis=-1), z


def _undistort(xd, yd, k1, k2, iters: int = 10):
    x, y = xd.copy(), yd.copy()
    for _ in range(iters):
        r2 = x * x + y * y
        s = 1.0 + k1 * r2 + k2 * r2 * r2
        x, y = xd / s, yd / s
    return x, y


def _homography(pan, tilt, focal, centre, roll, size) -> np.ndarray:
    cx, cy, h = centre
    right, down, fwd = _camera_axes(pan, tilt, roll)
    r = np.array([right, down, fwd])
    t = -r @ np.array([cx, cy, -h])
    g = np.c_[r[:, 0], r[:, 1], t]
    k = np.array([[focal, 0, size[0] / 2.0], [0, focal, size[1] / 2.0], [0, 0, 1.0]])
    return k @ g


def decompose(h: np.ndarray, size: tuple[int, int]):
    """From a pitch-to-pixel homography: (centre (x, y, height), pan, tilt, roll, focal px), or None.

    The principal point is taken at the image centre; the focal length is the one that makes the first two columns
    of K^-1 H orthonormal.
    """
    cx, cy = size[0] / 2.0, size[1] / 2.0
    hc = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1.0]]) @ h
    if not np.isfinite(hc).all():
        return None

    def cost(f: float) -> float:
        m = hc / np.array([[f], [f], [1.0]])
        m1, m2 = m[:, 0], m[:, 1]
        a, b = np.linalg.norm(m1), np.linalg.norm(m2)
        lam = (a + b) / 2.0
        if lam < 1e-12:
            return 1e9
        return float((m1 @ m2 / lam**2) ** 2 + ((a - b) / lam) ** 2)

    grid = np.exp(np.linspace(np.log(0.3 * size[0]), np.log(8 * size[0]), 120))
    costs = [cost(f) for f in grid]
    i = int(np.argmin(costs))
    lo, hi = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    for _ in range(40):  # golden section
        a, b = lo + 0.382 * (hi - lo), lo + 0.618 * (hi - lo)
        lo, hi = (lo, b) if cost(a) < cost(b) else (a, hi)
    f = (lo + hi) / 2.0
    m = hc / np.array([[f], [f], [1.0]])
    lam = (np.linalg.norm(m[:, 0]) + np.linalg.norm(m[:, 1])) / 2.0
    m = m / lam
    if m[2, 2] < 0:
        m = -m
    r1, r2, t = m[:, 0], m[:, 1], m[:, 2]
    r = np.c_[r1, r2, np.cross(r1, r2)]
    u, _, vt = np.linalg.svd(r)
    r = u @ vt
    if np.linalg.det(r) < 0:
        return None
    centre_w = -r.T @ t
    if centre_w[2] > -1.0:  # the camera must be above the pitch
        return None
    pan, tilt, roll = _angles(r)
    return (float(centre_w[0]), float(centre_w[1]), float(-centre_w[2])), pan, tilt, roll, float(f)


def _angles(r: np.ndarray) -> tuple[float, float, float]:
    fwd = r[2]
    tilt = float(np.arcsin(np.clip(fwd[2], -1, 1)))
    pan = float(np.arctan2(fwd[0], -fwd[1]))
    cp, sp, st, ct = np.cos(pan), np.sin(pan), np.sin(tilt), np.cos(tilt)
    r0, d0 = np.array([cp, sp, 0.0]), np.array([-st * sp, st * cp, ct])
    roll = float(np.arctan2(r[0] @ d0, r[0] @ r0))
    return pan, tilt, roll


def to_centred(pitch: np.ndarray, length: float = LENGTH, width: float = WIDTH) -> np.ndarray:
    """``markings.py`` pitch metres (corner origin) to metres from the pitch centre (same axis directions)."""
    return np.asarray(pitch, np.float64) - np.array([length / 2.0, width / 2.0])


def from_centred(pitch: np.ndarray, length: float = LENGTH, width: float = WIDTH) -> np.ndarray:
    return np.asarray(pitch, np.float64) + np.array([length / 2.0, width / 2.0])


# --------------------------------------------------------------------------------------------------------------
# Residuals


class _Points:
    """The correspondences of a set of frames, flattened, with the frame each belongs to."""

    def __init__(self, obs: Sequence[FrameObs], sizes: Sequence[tuple[int, int]], max_points: int | None = None):
        self.F = len(obs)
        self.half = np.array(sizes, np.float64).reshape(-1, 2) / 2.0
        self.scale = np.array([s[0] for s in sizes], np.float64) / REF_WIDTH
        pt_f, pt_xy, pt_px, pt_w = [], [], [], []
        ln_f, ln_xy, ln_tan, ln_px, ln_w = [], [], [], [], []
        for i, o in enumerate(obs):
            n = o.n_points
            if n == 0:
                continue
            idx = np.arange(n)
            if max_points is not None and n > max_points:
                idx = np.linspace(0, n - 1, max_points).astype(int)
            w = np.ones(len(idx)) if o.weights is None else o.weights[idx]
            keep = np.isfinite(o.image_pts[idx]).all(1) & np.isfinite(o.pitch_pts[idx]).all(1)
            idx, w = idx[keep], w[keep]
            if o.tangents is None:
                pt_f.append(np.full(len(idx), i)); pt_xy.append(o.pitch_pts[idx]); pt_px.append(o.image_pts[idx])
                pt_w.append(w)
            else:
                tan = o.tangents[idx]
                norm = np.linalg.norm(tan, axis=1)
                ok = norm > 1e-9
                ln_f.append(np.full(ok.sum(), i)); ln_xy.append(o.pitch_pts[idx][ok])
                ln_tan.append(tan[ok] / norm[ok, None]); ln_px.append(o.image_pts[idx][ok]); ln_w.append(w[ok])

        def cat(parts, width):
            return np.concatenate(parts) if parts else np.zeros((0, width) if width else 0)
        self.pt_f = cat(pt_f, 0).astype(int)
        self.pt_xy, self.pt_px, self.pt_w = cat(pt_xy, 2), cat(pt_px, 2), cat(pt_w, 0)
        self.ln_f = cat(ln_f, 0).astype(int)
        self.ln_xy, self.ln_tan, self.ln_px, self.ln_w = cat(ln_xy, 2), cat(ln_tan, 2), cat(ln_px, 2), cat(ln_w, 0)
        self.n_rows = 2 * len(self.pt_f) + len(self.ln_f)
        self.row_frame = np.concatenate([np.repeat(self.pt_f, 2), self.ln_f]).astype(int)

    def residuals(self, pan, tilt, logf, shared) -> np.ndarray:
        """Pixel residuals at 960 wide, [point residuals (x, y interleaved)..., line residuals...]."""
        out = []
        if len(self.pt_f):
            f = self.pt_f
            px, z = _project(self.pt_xy, pan[f], tilt[f], logf[f], shared, self.half[f])
            e = (px - self.pt_px) * (self.pt_w / self.scale[f])[:, None]
            e = np.where(z[:, None] > 0.05, e, 1e3)
            out.append(e.reshape(-1))
        if len(self.ln_f):
            f = self.ln_f
            args = (pan[f], tilt[f], logf[f], shared, self.half[f])
            p0, z = _project(self.ln_xy, *args)
            pa, _ = _project(self.ln_xy + 0.5 * self.ln_tan, *args)
            pb, _ = _project(self.ln_xy - 0.5 * self.ln_tan, *args)
            u = pa - pb
            u = u / np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-9)
            e = p0 - self.ln_px
            r = (u[:, 0] * e[:, 1] - u[:, 1] * e[:, 0]) * self.ln_w / self.scale[f]
            out.append(np.where(z > 0.05, r, 1e3))
        return np.concatenate(out) if out else np.zeros(0)

    def metres(self, pan, tilt, logf, shared) -> np.ndarray:
        """Per correspondence, the pitch-plane error in metres (perpendicular to the marking for line points):
        each pixel is sent back to the pitch under the camera and compared with its marking point."""
        out = []
        cx, cy, h, roll, k1, k2 = shared
        for xy, px, tan, fi in ((self.pt_xy, self.pt_px, None, self.pt_f), (self.ln_xy, self.ln_px, self.ln_tan, self.ln_f)):
            if not len(fi):
                out.append(np.zeros(0))
                continue
            right, down, fwd = _camera_axes(pan[fi], tilt[fi], roll)
            f = np.exp(logf[fi])
            xn, yn = _undistort((px[:, 0] - self.half[fi, 0]) / f, (px[:, 1] - self.half[fi, 1]) / f, k1, k2)
            ray = [right[k] * xn + down[k] * yn + fwd[k] for k in range(3)]
            with np.errstate(divide="ignore", invalid="ignore"):
                s = h / ray[2]  # camera at z = -h; the ray reaches z = 0 where -h + s*ray_z = 0 -> s = h / ray_z
                gx, gy = cx + s * ray[0], cy + s * ray[1]
            err = np.stack([gx - xy[:, 0], gy - xy[:, 1]], 1)
            out.append(np.linalg.norm(err, axis=1) if tan is None
                       else np.abs(err[:, 0] * tan[:, 1] - err[:, 1] * tan[:, 0]))
        return np.concatenate(out)

    def counts(self) -> np.ndarray:
        return np.bincount(self.pt_f, minlength=self.F) + np.bincount(self.ln_f, minlength=self.F)


def _initial_pose(h: np.ndarray, shared: Sequence[float], size: tuple[int, int]) -> tuple[float, float, float] | None:
    """(pan, tilt, log focal) for a camera at the shared centre that looks where ``h`` looks at the frame centre."""
    cx, cy, ht = shared[0], shared[1], shared[2]
    try:
        g = np.linalg.solve(h, [size[0] / 2.0, size[1] / 2.0, 1.0])
    except np.linalg.LinAlgError:
        return None
    if abs(g[2]) < 1e-12:
        return None
    gx, gy = g[0] / g[2], g[1] / g[2]
    v = np.array([gx - cx, gy - cy, ht])
    dist = float(np.linalg.norm(v))
    v /= dist
    pan, tilt = math.atan2(v[0], -v[1]), math.asin(np.clip(v[2], -1, 1))
    e = np.array([math.cos(pan), math.sin(pan)])
    pts = np.array([[gx, gy], [gx + e[0], gy + e[1]]])
    p = np.c_[pts, np.ones(2)] @ h.T
    with np.errstate(divide="ignore", invalid="ignore"):
        px = p[:, :2] / p[:, 2:]
    per_m = float(np.linalg.norm(px[1] - px[0]))
    if not np.isfinite(per_m) or per_m <= 0:
        return None
    return pan, tilt, math.log(per_m * dist)


@dataclass
class _Fit:
    x: np.ndarray  # pan, tilt, log focal
    cov: np.ndarray  # 3x3
    rms: float  # inlier rms, pixels at 960
    inliers: float
    n: int
    ok: bool


def _fit_frame(points: _Points, rows: np.ndarray, x0: np.ndarray, shared, opt: SolveOptions) -> _Fit:
    """Three parameters of one frame (``points`` holds that frame alone), shared values held."""
    n = len(rows)
    if n == 0:
        return _Fit(x0, np.eye(3) * 1e6, math.nan, 0.0, 0, False)

    def res(x: np.ndarray) -> np.ndarray:
        return points.residuals(x[:1], x[1:2], x[2:], shared)

    r = least_squares(res, x0, loss="soft_l1", f_scale=opt.loss_px, x_scale=np.array([0.01, 0.01, 0.05]),
                      max_nfev=40)
    final = res(r.x)
    inl = np.abs(final) < 2.5 * opt.loss_px
    rms = float(np.sqrt(np.mean(final[inl] ** 2))) if inl.any() else math.inf
    jtj = r.jac.T @ r.jac + np.diag([1e-3, 1e-3, 1e-3])
    sigma2 = max(rms, 0.5) ** 2 * opt.meas_inflate
    cov = sigma2 * np.linalg.inv(jtj)
    ok = bool(np.isfinite(r.x).all() and inl.mean() >= opt.gate_inliers and rms <= opt.gate_rms_px)
    return _Fit(r.x, cov, rms, float(inl.mean()), len(final), ok)


def _search_pose(points: _Points, shared, size: tuple[int, int], opt: SolveOptions) -> np.ndarray | None:
    """No starting homography and no neighbour: look for the pose on a coarse grid of look-at points."""
    cx, cy, ht = shared[0], shared[1], shared[2]
    best, best_cost = None, math.inf
    for gx in np.linspace(5, 100, 11):
        for gy in np.linspace(0, 68, 6):
            v = np.array([gx - cx, gy - cy, ht])
            v /= np.linalg.norm(v)
            pan, tilt = math.atan2(v[0], -v[1]), math.asin(v[2])
            for f in size[0] * np.array([0.6, 1.0, 1.6, 2.5, 4.0]):
                x = np.array([pan, tilt, math.log(f)])
                r = points.residuals(x[:1], x[1:2], x[2:], shared)
                c = float(np.sum(2 * opt.loss_px**2 * (np.sqrt(1 + (r / opt.loss_px) ** 2) - 1)))
                if c < best_cost:
                    best, best_cost = x, c
    return best


# --------------------------------------------------------------------------------------------------------------
# Smoothing


class _Kalman:
    """Constant-velocity filter on (pan, tilt, log focal), each channel on its own."""

    def __init__(self, q: Sequence[float], diffuse: float = 10.0) -> None:
        self.q = np.asarray(q, np.float64)
        self.diffuse = diffuse
        self.x: np.ndarray | None = None  # (3, 2): position, velocity
        self.p: np.ndarray | None = None  # (3, 2, 2)
        self.t = 0.0

    def reset(self, t: float, mean: np.ndarray | None = None) -> None:
        self.x = np.zeros((3, 2))
        if mean is not None:
            self.x[:, 0] = mean
        self.p = np.tile(np.eye(2) * self.diffuse, (3, 1, 1))
        self.t = t

    def predict(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        dt = max(t - self.t, 0.0)
        f = np.array([[1.0, dt], [0.0, 1.0]])
        qm = np.array([[dt**3 / 3, dt**2 / 2], [dt**2 / 2, dt]])
        x = self.x @ f.T
        p = f @ self.p @ f.T + self.q[:, None, None] * qm
        return x, p

    def step(self, t: float, z: np.ndarray | None, var: np.ndarray | None):
        """Advance to ``t`` and fold in a measurement (or none). Returns the prior (x, P) and the posterior."""
        xp, pp = self.predict(t)
        x, p = xp, pp
        if z is not None:
            s = p[:, 0, 0] + var
            k = p[:, :, 0] / s[:, None]
            x = xp + k * (z - xp[:, 0])[:, None]
            p = pp - k[:, :, None] * pp[:, 0:1, :]
        self.x, self.p, self.t = x, p, t
        return (xp, pp), (x.copy(), p.copy())


def _rts(times, prior, post, q):
    """Rauch-Tung-Striebel over one segment: smoothed (n, 3) means and (n, 3) variances of the positions."""
    n = len(times)
    xs = [None] * n
    ps = [None] * n
    xs[-1], ps[-1] = post[-1][0], post[-1][1]
    for i in range(n - 2, -1, -1):
        dt = times[i + 1] - times[i]
        f = np.array([[1.0, dt], [0.0, 1.0]])
        xf, pf = post[i]
        xp, pp = prior[i + 1]
        gain = pf @ f.T @ np.linalg.inv(pp + 1e-12 * np.eye(2))
        xs[i] = xf + np.einsum("cij,cj->ci", gain, xs[i + 1] - xp)
        ps[i] = pf + gain @ (ps[i + 1] - pp) @ gain.transpose(0, 2, 1)
    means = np.array([x[:, 0] for x in xs])
    var = np.array([p[:, 0, 0] for p in ps])
    return means, var


# --------------------------------------------------------------------------------------------------------------
# The solve


def _shared_vector(prior: CameraPrior) -> np.ndarray:
    assert prior.centre is not None
    return np.array([*prior.centre, prior.roll or 0.0, prior.k1 or 0.0, prior.k2 or 0.0])


def _segments(times: np.ndarray, shots: list[int | None], gap: float) -> list[range]:
    out, start = [], 0
    for i in range(1, len(times)):
        if times[i] - times[i - 1] > gap or shots[i] != shots[i - 1]:
            out.append(range(start, i))
            start = i
    out.append(range(start, len(times)))
    return out


def _pseudo_obs(o: FrameObs, size: tuple[int, int], length: float, width: float) -> FrameObs | None:
    """Correspondences read off a frame's starting homography: pitch grid points that land in the frame."""
    if o.init_h is None:
        return None
    gx, gy = np.meshgrid(np.linspace(-3, length + 3, 12), np.linspace(-3, width + 3, 8))
    xy = np.c_[gx.ravel(), gy.ravel()]
    p = np.c_[xy, np.ones(len(xy))] @ o.init_h.T
    with np.errstate(divide="ignore", invalid="ignore"):
        px = p[:, :2] / p[:, 2:]
    ok = (p[:, 2] > 1e-9) & (px[:, 0] > 0) & (px[:, 0] < size[0]) & (px[:, 1] > 0) & (px[:, 1] < size[1])
    if ok.sum() < 6:
        return None
    return FrameObs(frame=o.frame, t=o.t, image_pts=px[ok], pitch_pts=xy[ok], init_h=o.init_h, size=size, shot=o.shot)


def _initial_shared(frames: Sequence[FrameObs], sizes, prior: CameraPrior, opt: SolveOptions) -> np.ndarray:
    """Shared values to start from: the prior's, else the best of a few bundles over pseudo-correspondences read
    off the frames' own homographies, each started from a different guess of where the gantry is (a single
    homography is a poor witness of the focal length, so its decomposition alone is not trusted)."""
    if prior.has_camera:
        return _shared_vector(prior)
    cands = [i for i, o in enumerate(frames) if o.init_h is not None and o.n_points >= opt.min_points]
    if len(cands) > 60:
        cands = [cands[i] for i in np.linspace(0, len(cands) - 1, 60).astype(int)]
    pseudo = [(i, _pseudo_obs(frames[i], sizes[i], prior.pitch_length, prior.pitch_width)) for i in cands]
    pseudo = [(i, p) for i, p in pseudo if p is not None]
    if len(pseudo) < 3:
        raise ValueError("no prior camera, and fewer than three frames have a usable starting homography")
    length, width = prior.pitch_length, prior.pitch_width
    starts = [np.array([length / 2, width + d, h, 0.0, 0.0, 0.0]) for d in (12.0, 25.0, 45.0) for h in (10.0, 18.0, 30.0)]
    found = [d for i, _ in pseudo if (d := decompose(frames[i].init_h, sizes[i])) is not None]
    if found:
        c = np.median(np.array([d[0] for d in found]), axis=0)
        starts.append(np.array([*c, float(np.median([d[3] for d in found])), 0.0, 0.0]))
    if prior.centre is not None:
        starts = [np.array([*prior.centre, 0.0, 0.0, 0.0])]
    light = replace(opt, max_nfev=40, joint_points=60, refit_shared=True, progress=None, min_points=6)
    obs = [p for _, p in pseudo][:60]
    psizes = [sizes[i] for i, _ in pseudo][:60]
    single = [_Points([o], [sz]) for o, sz in zip(obs, psizes, strict=True)]

    def refit(shared: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Each frame's pose refitted at ``shared`` from the pose its homography implies; and its rms."""
        poses = np.full((len(obs), 3), np.nan)
        rms = np.full(len(obs), math.inf)
        for k, o in enumerate(obs):
            pose = _initial_pose(o.init_h, shared, psizes[k])
            if pose is None:
                continue
            ft = _fit_frame(single[k], np.arange(single[k].n_rows), np.array(pose), shared, light)
            poses[k], rms[k] = ft.x, ft.rms
        return poses, rms

    fitted = [refit(start) for start in starts]
    # Frames whose own homography is poor (a view of one line, say) would drag every start: use the frames that
    # fit well under most of the starts.
    good = np.mean([r <= max(2.5 * np.median(r[np.isfinite(r)]), 2.0) if np.isfinite(r).any() else np.zeros(len(r), bool)
                    for _, r in fitted], axis=0) >= 0.5
    keep = np.flatnonzero(good)
    if len(keep) < 3:
        raise ValueError("fewer than three frames have a usable starting homography")
    best, best_cost = None, math.inf
    for start, (poses, _) in zip(starts, fitted, strict=True):
        use = [k for k in keep if not np.isnan(poses[k]).any()]
        if len(use) < 3:
            continue
        try:
            shared, _, _, cost = _joint([obs[k] for k in use], [psizes[k] for k in use], poses[use], start,
                                        CameraPrior(), light, np.array([obs[k].t for k in use]),
                                        [obs[k].shot for k in use], smooth=False)
        except Exception:  # noqa: BLE001  (a bad start may fail outright)
            continue
        cost /= len(use)
        if 2.0 < shared[2] < 80.0 and len(use) >= 0.8 * len(keep) and cost < best_cost:
            best, best_cost = shared, cost
    if best is None:
        raise ValueError("could not find a camera centre that explains the starting homographies")
    return best


def _bounds(opt: SolveOptions):
    free = [0, 1, 2] + ([3] if opt.fit_roll else []) + [4] + ([5] if opt.fit_k2 else [])
    lo = np.array([-100.0, -150.0, 3.0, -0.3, -0.5, -0.5])
    hi = np.array([200.0, 250.0, 80.0, 0.3, 0.5, 0.5])
    return free, lo, hi


def _joint(frames, sizes, poses, shared, prior: CameraPrior, opt: SolveOptions, times, shots, smooth: bool = True):
    """Shared values plus per-frame poses of the selected frames, together. Returns shared, poses, std of shared."""
    sel = list(range(len(frames)))
    pts = _Points(frames, sizes, max_points=opt.joint_points)
    free, lo, hi = _bounds(opt)
    m, f = len(free), len(frames)
    x0 = np.concatenate([shared[free], poses[:, 0], poses[:, 1], poses[:, 2]])
    lb = np.concatenate([lo[free], np.full(f, -10.0), np.full(f, -0.2), np.full(f, math.log(100.0))])
    ub = np.concatenate([hi[free], np.full(f, 10.0), np.full(f, 1.55), np.full(f, math.log(60000.0))])
    x0 = np.clip(x0, lb + 1e-9, ub - 1e-9)
    sh_scale = np.array([1.0, 1.0, 1.0, 0.01, 0.01, 0.01])[free]
    x_scale = np.concatenate([sh_scale, np.full(f, 0.01), np.full(f, 0.01), np.full(f, 0.05)])

    # Smoothness: acceleration of each channel between consecutive frames of a segment.
    triples = [(i - 1, i, i + 1) for i in range(1, f - 1)
               if smooth and shots[sel[i - 1]] == shots[sel[i]] == shots[sel[i + 1]]
               and times[sel[i + 1]] - times[sel[i - 1]] < 2 * opt.seg_gap_s]
    prior_rows = []
    if prior.centre_sigma is not None and prior.centre is not None:
        for k in range(3):
            if k in free:
                prior_rows.append((free.index(k), prior.centre[k], prior.centre_sigma[k]))
    for k, sigma in ((4, 0.2), (5, 0.1), (3, 0.1)):
        if k in free:
            prior_rows.append((free.index(k), 0.0, sigma))

    base = pts.n_rows
    n_rows = base + 3 * len(triples) + len(prior_rows)

    def unpack(x):
        s = shared.copy()
        s[free] = x[:m]
        return s, x[m:m + f], x[m + f:m + 2 * f], x[m + 2 * f:]

    def residual(x):
        s, pan, tilt, logf = unpack(x)
        out = np.empty(n_rows)
        out[:base] = pts.residuals(pan, tilt, logf, s)
        o = base
        if triples:
            a, b, c = (np.array(v) for v in zip(*triples, strict=True))
            t = np.array(times)[sel]
            d1, d2 = t[b] - t[a], t[c] - t[b]
            for ch, arr in enumerate((pan, tilt, logf)):
                acc = ((arr[c] - arr[b]) / d2 - (arr[b] - arr[a]) / d1) / (0.5 * (d1 + d2))
                out[o:o + len(triples)] = acc / opt.accel_sigma[ch] * 0.05  # 1 sigma ~ 0.05 px: a gentle pull
                o += len(triples)
        for j, mean, sigma in prior_rows:
            out[o] = (x[j] - mean) / sigma * 2.0
            o += 1
        return out

    rows, cols = [], []
    pr = np.arange(base)
    fr = pts.row_frame
    for j in range(m):
        rows.append(pr); cols.append(np.full(base, j))
    for ch in range(3):
        rows.append(pr); cols.append(m + ch * f + fr)
    o = base
    for ch in range(3):
        for k, tri in enumerate(triples):
            for i in tri:
                rows.append(np.array([o + k])); cols.append(np.array([m + ch * f + i]))
        o += len(triples)
    for j, _, _ in prior_rows:
        rows.append(np.array([o])); cols.append(np.array([j]))
        o += 1
    sparsity = coo_matrix((np.ones(sum(len(r) for r in rows)), (np.concatenate(rows), np.concatenate(cols))),
                          shape=(n_rows, m + 3 * f)).tocsr()
    result = least_squares(residual, x0, jac_sparsity=sparsity, loss="soft_l1", f_scale=opt.loss_px,
                           x_scale=x_scale, bounds=(lb, ub), max_nfev=opt.max_nfev, tr_solver="lsmr", method="trf",
                           ftol=opt.tol, xtol=opt.tol, gtol=opt.tol)
    s, pan, tilt, logf = unpack(result.x)
    # Standard errors of the shared values: the shared block of (J^T J)^-1, via a sparse solve.
    std = np.full(6, math.nan)
    try:
        jac = result.jac.tocsc()
        h = (jac.T @ jac + 1e-9 * identity(jac.shape[1])).tocsc()
        lu = splu(h)
        resid = residual(result.x)
        sigma2 = max(float(np.sqrt(np.mean(resid[:base][np.abs(resid[:base]) < 2.5 * opt.loss_px] ** 2))), 0.5) ** 2
        for j, k in enumerate(free):
            e = np.zeros(h.shape[0])
            e[j] = 1.0
            std[k] = math.sqrt(max(sigma2 * opt.meas_inflate * float(lu.solve(e)[j]), 0.0))
    except Exception:  # noqa: BLE001  (standard errors are a courtesy)
        pass
    return s, np.c_[pan, tilt, logf], std, float(result.cost)


def solve_match(frames: Sequence[FrameObs], prior: CameraPrior | None = None,
                options: SolveOptions | None = None) -> MatchCalibration:
    """Calibrate every frame of a match with one shared camera. See the module docstring."""
    opt = options or SolveOptions()
    prior = prior or CameraPrior()
    order = sorted(range(len(frames)), key=lambda i: (frames[i].t, frames[i].frame))
    obs = [frames[i] for i in order]
    n = len(obs)
    if n == 0:
        return MatchCalibration([], prior)
    sizes = []
    for o in obs:
        size = o.size or prior.image_size
        if size is None:
            raise ValueError("a FrameObs has no size and the prior has no image_size")
        sizes.append((int(size[0]), int(size[1])))
    times = np.array([o.t for o in obs], np.float64)
    shots = [o.shot for o in obs]
    counts = np.array([o.n_points for o in obs])
    report = opt.progress or (lambda _: None)

    shared = _initial_shared(obs, sizes, prior, opt)
    single = [_Points([o], [s]) for o, s in zip(obs, sizes, strict=True)]
    rows = [np.arange(p.n_rows) for p in single]
    poses = np.full((n, 3), np.nan)
    segs = _segments(times, shots, opt.seg_gap_s)

    def start_poses(shared_now):
        for i, o in enumerate(obs):
            if o.init_h is not None and counts[i] > 0:
                p = _initial_pose(o.init_h, shared_now, sizes[i])
                if p is not None:
                    poses[i] = p

    def fit_all(shared_now, only=None) -> list[_Fit | None]:
        fits: list[_Fit | None] = [None] * n
        todo = range(n) if only is None else only
        for k, i in enumerate(todo):
            if counts[i] < opt.min_points or np.isnan(poses[i]).any():
                continue
            fits[i] = _fit_frame(single[i], rows[i], poses[i], shared_now, opt)
            if fits[i].ok:
                poses[i] = fits[i].x
            if k % 500 == 0:
                report(0.05 + 0.4 * k / max(len(todo), 1))
        return fits

    def seed_missing(shared_now, fits):
        """Frames without a starting homography (or whose fit failed) start from the nearest solved frame of
        their segment, swept forward then back; the first of a segment with nothing solved searches a grid."""
        for seg in segs:
            for direction in (1, -1):
                idx = list(seg) if direction == 1 else list(reversed(seg))
                last = None
                for i in idx:
                    good = fits[i] is not None and fits[i].ok
                    if good:
                        last = poses[i].copy()
                        continue
                    if counts[i] < opt.min_points:
                        continue
                    if last is None and np.isnan(poses[i]).any():
                        found = _search_pose(single[i], shared_now, sizes[i], opt)
                        if found is None:
                            continue
                        poses[i] = found
                    elif last is not None:
                        poses[i] = last
                    fits[i] = _fit_frame(single[i], rows[i], poses[i], shared_now, opt)
                    if fits[i].ok:
                        poses[i] = fits[i].x
                        last = poses[i].copy()

    start_poses(shared)
    fits = fit_all(shared)
    good = [i for i in range(n) if fits[i] is not None and fits[i].ok]
    seed_missing(shared, fits)
    good = [i for i in range(n) if fits[i] is not None and fits[i].ok]
    report(0.5)
    std = np.full(6, math.nan)
    if opt.refit_shared and len(good) >= 3:
        pick = good
        if len(pick) > opt.max_joint_frames:
            pick = [good[i] for i in np.linspace(0, len(good) - 1, opt.max_joint_frames).astype(int)]
        if not prior.has_camera:
            # The lens term has false valleys: try a few starting values on a thin spread of frames first.
            thin = [good[i] for i in np.linspace(0, len(good) - 1, min(len(good), 120)).astype(int)]
            best_cost = math.inf
            for k1 in (-0.1, 0.0, 0.1):
                start = shared.copy()
                start[4] = k1
                try:
                    cand, _, _, cost = _joint([obs[i] for i in thin], [sizes[i] for i in thin], poses[thin].copy(),
                                              start, prior, opt, times[thin], [shots[i] for i in thin])
                except Exception:  # noqa: BLE001
                    continue
                if cost < best_cost:
                    best_cost, shared = cost, cand
        shared, _, std, _ = _joint([obs[i] for i in pick], [sizes[i] for i in pick], poses[pick].copy(), shared, prior,
                                   opt, times[pick], [shots[i] for i in pick])
        report(0.7)
        start_poses_keep = poses.copy()
        fits = fit_all(shared)
        for i in range(n):  # frames the second pass could not start fall back to the earlier pose
            if fits[i] is None and not np.isnan(start_poses_keep[i]).any() and counts[i] >= opt.min_points:
                poses[i] = start_poses_keep[i]
        seed_missing(shared, fits)
    report(0.85)

    # Smooth every segment.
    meas = np.full((n, 3), np.nan)
    var = np.full((n, 3), np.nan)
    floor = np.array(opt.meas_floor) ** 2
    for i, ft in enumerate(fits):
        if ft is not None and ft.ok:
            meas[i] = ft.x
            var[i] = np.maximum(np.diag(ft.cov), floor)
    smooth = np.full((n, 3), np.nan)
    sstd = np.full((n, 3), np.nan)
    for seg in segs:
        idx = list(seg)
        valid = [i for i in idx if not np.isnan(meas[i]).any()]
        if not valid:
            continue
        kf = _Kalman(opt.process_q)
        kf.reset(times[idx[0]], meas[valid[0]])
        prior_s, post_s = [], []
        for i in idx:
            z = None if np.isnan(meas[i]).any() else meas[i]
            pr, po = kf.step(times[i], z, None if z is None else var[i])
            prior_s.append(pr)
            post_s.append(po)
        mean, v = _rts(times[idx], prior_s, post_s, opt.process_q)
        smooth[idx] = mean
        sstd[idx] = np.sqrt(np.maximum(v, 0))
    report(0.93)

    cams: list[FrameCalibration] = []
    since = _distance_to_valid(times, segs, meas)
    centre = (float(shared[0]), float(shared[1]), float(shared[2]))
    all_pts = _Points(obs, sizes)
    pan, tilt, logf = smooth[:, 0], smooth[:, 1], smooth[:, 2]
    metres = all_pts.metres(np.nan_to_num(pan), np.nan_to_num(tilt), np.nan_to_num(logf, nan=7.0), shared)
    resid = all_pts.residuals(np.nan_to_num(pan), np.nan_to_num(tilt), np.nan_to_num(logf, nan=7.0), shared)
    # The residual and metre vectors are ordered point rows then line rows; map them back to frames.
    rpx = _per_frame_stats(all_pts, resid, metres, opt)
    informative = np.array([ft is not None and ft.ok and bool(np.all(np.sqrt(np.diag(ft.cov)) < opt.info_std))
                            for ft in fits])
    for i, o in enumerate(obs):
        have = not np.isnan(smooth[i]).any()
        if have and informative[i]:
            status = "fitted"
        elif have and since[i] <= opt.max_interp_s:
            status = "interpolated"
        else:
            status = "none"
        rms_px, rms_m, inl = rpx[i]
        cams.append(FrameCalibration(
            frame=o.frame, t=o.t, status=status, size=sizes[i],
            pan=float(pan[i]) if have else math.nan, tilt=float(tilt[i]) if have else math.nan,
            focal=float(math.exp(logf[i])) if have else math.nan, centre=centre, roll=float(shared[3]),
            k1=float(shared[4]), k2=float(shared[5]), n_points=int(counts[i]),
            rmse_px=rms_px if have else math.nan, rmse_m=rms_m if have else math.nan,
            inlier_frac=inl if have else math.nan,
            std=tuple(float(s) for s in sstd[i]) if have else None, shot=o.shot))
    report(1.0)

    fitted = [c for c in cams if c.status == "fitted"]
    fused = fitted or [c for c in cams if c.usable]
    width = np.median([s[0] for s in sizes])
    fitted_prior = replace(prior)
    fitted_prior.centre = centre
    fitted_prior.centre_sigma = None if np.isnan(std[:3]).any() else tuple(float(s) for s in std[:3])
    fitted_prior.roll, fitted_prior.k1, fitted_prior.k2 = float(shared[3]), float(shared[4]), float(shared[5])
    if fused:
        f_rel = np.array([c.focal / c.size[0] for c in fused])
        fitted_prior.focal_rel = (float(np.percentile(f_rel, 1)), float(np.percentile(f_rel, 99)))
        fitted_prior.pan = (float(np.percentile([c.pan for c in fused], 1)),
                            float(np.percentile([c.pan for c in fused], 99)))
        fitted_prior.tilt = (float(np.percentile([c.tilt for c in fused], 1)),
                             float(np.percentile([c.tilt for c in fused], 99)))
    fitted_prior.image_size = (int(width), int(np.median([s[1] for s in sizes])))
    fitted_prior.history = prior.history + 1
    return MatchCalibration(cams, fitted_prior, {"shared_std": [None if math.isnan(s) else float(s) for s in std]})


def _distance_to_valid(times: np.ndarray, segs: list[range], meas: np.ndarray) -> np.ndarray:
    out = np.full(len(times), math.inf)
    for seg in segs:
        idx = np.array(list(seg))
        ok = idx[~np.isnan(meas[idx]).any(axis=1)]
        if not len(ok):
            continue
        d = np.abs(times[idx][:, None] - times[ok][None, :]).min(axis=1) if len(idx) * len(ok) < 4_000_000 else \
            np.array([np.abs(times[ok] - t).min() for t in times[idx]])
        out[idx] = d
    return out


def _per_frame_stats(points: _Points, resid: np.ndarray, metres: np.ndarray, opt: SolveOptions):
    """Per frame (inlier rms px at 960, inlier rms metres, inlier share)."""
    stats = [(math.nan, math.nan, math.nan)] * points.F
    # residual rows: point rows are 2 per point; reduce to a per-point error in pixels for the rms.
    n_pt = len(points.pt_f)
    pt_err = np.linalg.norm(resid[:2 * n_pt].reshape(-1, 2), axis=1) if n_pt else np.zeros(0)
    ln_err = np.abs(resid[2 * n_pt:])
    err = np.concatenate([pt_err, ln_err])
    frame = np.concatenate([points.pt_f, points.ln_f])
    order = np.argsort(frame, kind="stable")
    bounds = np.searchsorted(frame[order], np.arange(points.F + 1))
    for i in range(points.F):
        sl = order[bounds[i]:bounds[i + 1]]
        if not len(sl):
            continue
        e, m = err[sl], metres[sl]
        inl = e < 2.5 * opt.loss_px
        if not inl.any():
            stats[i] = (float(np.sqrt(np.mean(e**2))), float(np.sqrt(np.nanmean(m**2))), 0.0)
        else:
            stats[i] = (float(np.sqrt(np.mean(e[inl] ** 2))), float(np.sqrt(np.nanmean(m[inl] ** 2))),
                        float(inl.mean()))
    return stats


# --------------------------------------------------------------------------------------------------------------
# Live


class LiveCameraFilter:
    """Causal version of ``solve_match``: the shared camera is fixed from ``prior``; each frame's pan, tilt and
    focal are fitted (starting from the filter's prediction) and folded into a constant-velocity Kalman filter.

    ``update`` returns that frame's calibration from the past and present only. A frame with too few points, or
    one that fits badly, is carried by the prediction (``interpolated``) for up to ``max_interp_s``, then ``none``.
    A cut (``shot`` changes) or a long silence restarts the filter.
    """

    def __init__(self, prior: CameraPrior, options: SolveOptions | None = None) -> None:
        if not prior.has_camera:
            raise ValueError("the prior has no fitted camera: solve a recorded match at this venue first")
        self.prior = prior
        self.opt = options or SolveOptions()
        self.shared = _shared_vector(prior)
        self.kf = _Kalman(self.opt.process_q)
        self._init = False
        self._last_shot: int | None = None
        self._last_t = -math.inf
        self._last_meas = -math.inf

    def reset(self) -> None:
        self._init = False

    def update(self, obs: FrameObs) -> FrameCalibration:
        opt = self.opt
        size = obs.size or self.prior.image_size
        if size is None:
            raise ValueError("FrameObs has no size and the prior has no image_size")
        size = (int(size[0]), int(size[1]))
        t = obs.t
        if self._init and (t - self._last_t > opt.seg_gap_s or obs.shot != self._last_shot or t < self._last_t):
            self._init = False
        pts = _Points([obs], [size])
        start = None
        if self._init:
            start = self.kf.predict(t)[0][:, 0].copy()
        elif obs.init_h is not None:
            start = (np.array(p) if (p := _initial_pose(obs.init_h, self.shared, size)) is not None else None)
        fit = None
        if obs.n_points >= opt.min_points:
            if start is None:
                start = _search_pose(pts, self.shared, size, opt)
            if start is not None:
                fit = _fit_frame(pts, np.arange(pts.n_rows), start, self.shared, opt)
        if not self._init:
            self.kf.reset(t, None if fit is None or not fit.ok else fit.x)
            self._init = fit is not None and fit.ok
            if not self._init:
                return self._result(obs, size, None, "none", pts)
        measured = fit is not None and fit.ok
        var = None if not measured else np.maximum(np.diag(fit.cov), np.array(opt.meas_floor) ** 2)
        _, (x, p) = self.kf.step(t, fit.x if measured else None, var)
        self._last_t, self._last_shot = t, obs.shot
        if measured:
            self._last_meas = t
        std = np.sqrt(np.maximum(p[:, 0, 0], 0))
        if measured and bool(np.all(np.sqrt(np.diag(fit.cov)) < opt.info_std)):
            status = "fitted"
        elif t - self._last_meas <= opt.max_interp_s:
            status = "interpolated"
        else:
            status = "none"
        return self._result(obs, size, (x[:, 0], std), status, pts)

    def _result(self, obs, size, state, status, pts) -> FrameCalibration:
        s = self.shared
        cal = FrameCalibration(frame=obs.frame, t=obs.t, status=status, size=size, centre=(s[0], s[1], s[2]),
                               roll=float(s[3]), k1=float(s[4]), k2=float(s[5]), n_points=obs.n_points, shot=obs.shot)
        if state is None or status == "none":
            return cal
        x, std = state
        cal.pan, cal.tilt, cal.focal, cal.std = float(x[0]), float(x[1]), float(math.exp(x[2])), tuple(float(v) for v in std)
        if obs.n_points:
            r = pts.residuals(x[:1], x[1:2], x[2:], s)
            m = pts.metres(x[:1], x[1:2], x[2:], s)
            stats = _per_frame_stats(pts, r, m, self.opt)[0]
            cal.rmse_px, cal.rmse_m, cal.inlier_frac = stats
        return cal


# --------------------------------------------------------------------------------------------------------------
# Venues


def load_venues(path: Path | None = None) -> dict:
    return json.loads((path or VENUES).read_text())


def venue_prior(venue: str, path: Path | None = None) -> CameraPrior:
    """The prior of a venue (pitch size always; camera fields only once fitted)."""
    entry = load_venues(path)[venue]
    cam = entry.get("camera") or {}
    pitch = entry.get("pitch") or {}
    return CameraPrior.from_dict({**cam, "pitch_length": pitch.get("length"), "pitch_width": pitch.get("width"),
                                  "history": entry.get("history", 0), "image_size": cam.get("image_size")})


def update_venue_prior(venue: str, calibration: MatchCalibration, path: Path | None = None,
                       note: str | None = None) -> CameraPrior:
    """Merge a fitted calibration into the venue's prior and write it back.

    The shared values are a running mean over ``history`` matches (so one new match moves a prior built from many
    only a little); the pan, tilt and focal ranges are the union. Returns the merged prior.
    """
    path = path or VENUES
    venues = load_venues(path)
    entry = venues[venue]
    old = venue_prior(venue, path)
    new = calibration.prior
    if not new.has_camera:
        raise ValueError("the calibration has no fitted camera")
    n = old.history if old.has_camera else 0

    def mean(a, b):
        return float(b) if a is None else float((a * n + b) / (n + 1))

    def union(a, b):
        return tuple(float(v) for v in b) if a is None else (float(min(a[0], b[0])), float(max(a[1], b[1])))

    merged = CameraPrior(
        centre=tuple(mean(old.centre[k] if old.centre else None, new.centre[k]) for k in range(3)),
        centre_sigma=new.centre_sigma if old.centre_sigma is None or n == 0 else
        tuple(float(min(a, b)) for a, b in zip(old.centre_sigma, new.centre_sigma or old.centre_sigma, strict=True)),
        roll=mean(old.roll, new.roll), k1=mean(old.k1, new.k1), k2=mean(old.k2 if old.has_camera else None, new.k2),
        focal_rel=union(old.focal_rel, new.focal_rel), pan=union(old.pan, new.pan), tilt=union(old.tilt, new.tilt),
        pitch_length=old.pitch_length, pitch_width=old.pitch_width, image_size=new.image_size, history=n + 1)
    cam = merged.to_dict()
    for k in ("pitch_length", "pitch_width", "history"):
        cam.pop(k)
    entry["camera"] = cam
    entry["history"] = n + 1
    if note:
        entry.setdefault("notes", []).append(note)
    path.write_text(json.dumps(venues, indent=2) + "\n")
    return merged


def venue_guesses(venue: str, bgr: np.ndarray, top: int = 3, path: Path | None = None) -> list[np.ndarray]:
    """Candidate pitch-to-pixel homographies (at ``bgr``'s size) for the first main-camera frame of a match at
    ``venue``, from its fitted camera: pan, tilt and focal are searched over the prior's ranges and scored on how
    many of the markings in view land on painted lines. ``camera.fit`` should refine and validate each. Empty when
    the venue has no fitted camera yet."""
    from .camera import FrameLines

    prior = venue_prior(venue, path)
    if not prior.has_camera or prior.centre is None:
        return []
    h, w = bgr.shape[:2]
    frame = FrameLines(bgr)
    pan_lo, pan_hi = prior.pan or (-0.8, 0.8)
    tilt_lo, tilt_hi = prior.tilt or (0.1, 0.5)
    f_lo, f_hi = prior.focal_rel or (0.8, 4.0)
    pans = np.arange(pan_lo - 0.05, pan_hi + 0.05, 0.03)
    tilts = np.arange(tilt_lo - 0.03, tilt_hi + 0.03, 0.03)
    focals = w * np.exp(np.linspace(np.log(f_lo * 0.9), np.log(f_hi * 1.1), 6))
    scored: list[tuple[float, np.ndarray]] = []
    for pan in pans:
        for tilt in tilts:
            for f in focals:
                hom = _homography(pan, tilt, f, prior.centre, prior.roll or 0.0, (w, h))
                hom = hom / hom[2, 2]
                pts = frame.visible(hom)
                if len(pts) < 60:
                    continue
                on = frame.distances(hom, pts) <= 3.0 * frame.scale
                scored.append((float(on.mean()) * math.sqrt(len(pts)), hom))
    scored.sort(key=lambda t: -t[0])
    out: list[np.ndarray] = []
    for _, hom in scored:
        if all(np.abs(hom / hom[2, 2] - o).max() > 0.05 * np.abs(o).max() for o in out):
            out.append(hom)
        if len(out) >= top:
            break
    return out


# --------------------------------------------------------------------------------------------------------------
# Pitch model and observations from images (needs OpenCV)


def _segments_for(length: float, width: float):
    out = [((0, 0), (length, 0)), ((0, width), (length, width)), ((0, 0), (0, width)), ((length, 0), (length, width)),
           ((length / 2, 0), (length / 2, width))]
    for x0, d in ((0.0, 1.0), (length, -1.0)):
        for depth, half in ((BOX_DEPTH, BOX_HALF), (GOAL_AREA_DEPTH, GOAL_AREA_HALF)):
            front = x0 + d * depth
            out += [((x0, width / 2 - half), (front, width / 2 - half)),
                    ((x0, width / 2 + half), (front, width / 2 + half)),
                    ((front, width / 2 - half), (front, width / 2 + half))]
    return out


class PitchModel:
    """The markings of a pitch of ``length`` x ``width`` metres sampled with unit tangents (``markings.py`` shapes,
    any size; the boxes, circles and arcs keep their IFAB size)."""

    def __init__(self, length: float = LENGTH, width: float = WIDTH, step: float = 0.25) -> None:
        self.length, self.width = length, width
        pts, tans = [], []
        for (x1, y1), (x2, y2) in _segments_for(length, width):
            n = max(int(math.hypot(x2 - x1, y2 - y1) / step), 1) + 1
            pts.append(np.c_[np.linspace(x1, x2, n), np.linspace(y1, y2, n)])
            d = np.array([x2 - x1, y2 - y1]) / math.hypot(x2 - x1, y2 - y1)
            tans.append(np.tile(d, (n, 1)))
        arcs = [((length / 2, width / 2), CIRCLE_R, 0.0, 2 * np.pi)]
        for x0, d in ((0.0, 1.0), (length, -1.0)):
            facing = 0.0 if d > 0 else np.pi
            arcs.append(((x0 + d * SPOT, width / 2), CIRCLE_R, facing - ARC_HALF_ANGLE, facing + ARC_HALF_ANGLE))
        for (cx, cy), r, a, b in arcs:
            n = max(int(r * (b - a) / step), 1) + 1
            t = np.linspace(a, b, n)
            pts.append(np.c_[cx + r * np.cos(t), cy + r * np.sin(t)])
            tans.append(np.c_[-np.sin(t), np.cos(t)])
        self.points = np.concatenate(pts)
        self.tangents = np.concatenate(tans)

    def landmarks(self) -> dict[str, tuple[float, float]]:
        """Named points as in ``markings.landmarks`` for this size."""
        length, width = self.length, self.width
        out = {"far left corner": (0.0, 0.0), "far right corner": (length, 0.0),
               "near left corner": (0.0, width), "near right corner": (length, width),
               "halfway, far touchline": (length / 2, 0.0), "halfway, near touchline": (length / 2, width),
               "centre spot": (length / 2, width / 2), "centre circle, far": (length / 2, width / 2 - CIRCLE_R),
               "centre circle, near": (length / 2, width / 2 + CIRCLE_R)}
        for (x0, d), side in zip(((0.0, 1.0), (length, -1.0)), ("left", "right"), strict=True):
            for name, depth, half in (("box", BOX_DEPTH, BOX_HALF), ("goal area", GOAL_AREA_DEPTH, GOAL_AREA_HALF)):
                front = x0 + d * depth
                out[f"{side} {name}, far, on goal line"] = (x0, width / 2 - half)
                out[f"{side} {name}, near, on goal line"] = (x0, width / 2 + half)
                out[f"{side} {name}, far front corner"] = (front, width / 2 - half)
                out[f"{side} {name}, near front corner"] = (front, width / 2 + half)
            out[f"{side} penalty spot"] = (x0 + d * SPOT, width / 2)
            out[f"{side} arc meets box, far"] = (x0 + d * BOX_DEPTH, width / 2 - ARC_MEETS_BOX)
            out[f"{side} arc meets box, near"] = (x0 + d * BOX_DEPTH, width / 2 + ARC_MEETS_BOX)
            out[f"{side} goal, far post"] = (x0, width / 2 - POST_HALF)
            out[f"{side} goal, near post"] = (x0, width / 2 + POST_HALF)
        return out


def associate(paint: np.ndarray, project: Callable[[np.ndarray], np.ndarray], model: PitchModel,
              tol_px: float, size: tuple[int, int]):
    """Match painted-line pixels (N, 2) to the nearest marking point as ``project`` (pitch metres -> pixels,
    NaN behind the camera) draws them. Returns (image_pts, pitch_pts, tangents) of the matches within ``tol_px``."""
    from scipy.spatial import cKDTree

    px = project(model.points)
    ok = np.isfinite(px).all(axis=1) & (px[:, 0] > -50) & (px[:, 0] < size[0] + 50) & (px[:, 1] > -50) \
        & (px[:, 1] < size[1] + 50)
    if ok.sum() < 20 or len(paint) == 0:
        return np.zeros((0, 2)), np.zeros((0, 2)), np.zeros((0, 2))
    idx = np.flatnonzero(ok)
    dist, j = cKDTree(px[idx]).query(paint, distance_upper_bound=tol_px)
    hit = np.isfinite(dist)
    sel = idx[j[hit]]
    return paint[hit], model.points[sel], model.tangents[sel]


def observations_from_frame(bgr: np.ndarray, guess: Callable[[np.ndarray], np.ndarray] | np.ndarray, *,
                            frame: int = 0, t: float = 0.0, shot: int | None = None,
                            model: PitchModel | None = None, n_points: int = 300, tol_px: float = 6.0,
                            init_h: np.ndarray | None = None) -> FrameObs:
    """Painted lines of ``bgr`` matched to the pitch markings under ``guess`` (a pitch-to-pixel homography, or a
    function from pitch metres to pixels such as ``FrameCalibration.pitch_to_image``)."""
    from .camera import FrameLines

    model = model or PitchModel()
    frame_lines = FrameLines(bgr)
    paint = frame_lines.paint
    if len(paint) > n_points * 4:
        paint = paint[np.linspace(0, len(paint) - 1, n_points * 4).astype(int)]
    return observations_from_paint(paint, guess, frame=frame, t=t, shot=shot, model=model, n_points=n_points,
                                   tol_px=tol_px, size=(bgr.shape[1], bgr.shape[0]), init_h=init_h)


def observations_from_paint(paint: np.ndarray, guess, *, size: tuple[int, int], frame: int = 0, t: float = 0.0,
                            shot: int | None = None, model: PitchModel | None = None, n_points: int = 300,
                            tol_px: float = 6.0, init_h: np.ndarray | None = None) -> FrameObs:
    model = model or PitchModel()
    if callable(guess):
        project = guess
    else:
        h = np.asarray(guess, np.float64)
        if init_h is None:
            init_h = h

        def project(xy: np.ndarray) -> np.ndarray:
            p = np.c_[xy, np.ones(len(xy))] @ h.T
            with np.errstate(divide="ignore", invalid="ignore"):
                out = p[:, :2] / p[:, 2:]
            out[p[:, 2] <= 1e-9] = np.nan
            return out
    img, pit, tan = associate(paint, project, model, tol_px * size[0] / REF_WIDTH, size)
    if len(img) > n_points:
        k = np.linspace(0, len(img) - 1, n_points).astype(int)
        img, pit, tan = img[k], pit[k], tan[k]
    return FrameObs(frame=frame, t=t, image_pts=img, pitch_pts=pit, tangents=tan, init_h=init_h, size=size, shot=shot)


# --------------------------------------------------------------------------------------------------------------
# A whole video


def _sampled_frames(video: Path, fps: float, width: int, max_seconds: float | None = None):
    """(index, seconds, bgr at ``width``) for about ``fps`` frames a second, from the start up to ``max_seconds``."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    try:
        src_fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
        stride = max(1, round(src_fps / fps))
        i = 0
        while cap.grab():
            if max_seconds is not None and i / src_fps > max_seconds:
                break
            if i % stride == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                h, w = frame.shape[:2]
                if w != width:
                    frame = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
                yield i, i / src_fps, frame
            i += 1
    finally:
        cap.release()


def solve_video(video: Path, *, fps: float = 5.0, anchors: list[tuple[np.ndarray, np.ndarray]] | None = None,
                venue: str | None = None, progress: Callable[[float], None] | None = None,
                rounds: int = 2, options: SolveOptions | None = None,
                max_seconds: float | None = None) -> MatchCalibration:
    """Calibrate a whole video: view filter, per-frame tracking (``camera.CameraTracker``), then ``solve_match``.

    ``anchors`` are (bgr image at 960 px wide, pitch-to-pixel homography) pairs, kept as keyframes so a cut back to
    their part of the pitch can be found. ``venue`` starts the solve from that ground's fitted camera.
    The first round uses the tracker's per-frame homographies to match painted lines to markings; each further
    round matches them again under the previous round's cameras, which also reaches frames the tracker lost.
    Times are video seconds at ``fps`` samples a second; images are 960 px wide. ``max_seconds`` stops after that
    much video (a trial run).
    """
    from .broadcast import WORK_WIDTH
    from .camera import CameraTracker, FrameLines
    from .footage import prepare
    from .views import cuts as view_cuts
    from .views import look

    report = progress or (lambda _: None)
    source = prepare(Path(video))
    prior = venue_prior(venue) if venue else CameraPrior()
    model = PitchModel(prior.pitch_length, prior.pitch_width)
    opt = options or SolveOptions()

    # Pass 1: which frames are the main camera.
    looks, stamps = [], []
    for idx, t, frame in _sampled_frames(source, fps, WORK_WIDTH, max_seconds):
        looks.append(look(frame))
        stamps.append((idx, t))
    if not looks:
        raise ValueError(f"cannot read frames from {video}")
    cut_idx = view_cuts(looks)
    main = np.zeros(len(looks), bool)
    edges = [0, *cut_idx, len(looks)]
    shot_of = np.zeros(len(looks), int)
    for k, (a, b) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
        main[a:b] = sum(lk.main for lk in looks[a:b]) * 2 > (b - a)
        shot_of[a:b] = k
    cutset = set(cut_idx)
    report(0.15)

    # Pass 2: track, and keep the painted lines of every main frame.
    tracker = CameraTracker()
    for image, h in anchors or []:
        tracker.seed(image, np.asarray(h, np.float64))
    paint: dict[int, np.ndarray] = {}
    guess: dict[int, np.ndarray] = {}
    size = None
    for n, (idx, t, frame) in enumerate(_sampled_frames(source, fps, WORK_WIDTH, max_seconds)):
        size = (frame.shape[1], frame.shape[0])
        cal = tracker.step(frame, main=bool(main[n]), cut=n in cutset)
        if main[n]:
            lines = FrameLines(frame).paint
            if len(lines) > 800:
                lines = lines[np.linspace(0, len(lines) - 1, 800).astype(int)]
            paint[n] = lines
            if cal is not None:
                guess[n] = cal.homography
        if n % 100 == 0:
            report(0.15 + 0.45 * n / len(looks))
    assert size is not None

    def build(project_for: Callable[[int], Callable | np.ndarray | None], tol: float) -> list[FrameObs]:
        out = []
        for n in sorted(paint):
            idx, t = stamps[n]
            g = project_for(n)
            if g is None:
                out.append(FrameObs(frame=idx, t=t, image_pts=np.zeros((0, 2)), pitch_pts=np.zeros((0, 2)),
                                    size=size, shot=int(shot_of[n])))
            else:
                out.append(observations_from_paint(paint[n], g, size=size, frame=idx, t=t, shot=int(shot_of[n]),
                                                   model=model, tol_px=tol))
        return out

    obs = build(lambda n: guess.get(n), 8.0)
    result = solve_match(obs, prior, opt)
    report(0.8)
    for r in range(1, rounds):
        cams = {c.frame: c for c in result.frames if c.usable}
        keys = np.array(sorted(cams))
        by_index = {stamps[n][0]: n for n in paint}

        def near(n: int) -> Callable | None:
            idx = stamps[n][0]
            if idx in cams:
                return cams[idx].pitch_to_image
            if not len(keys):
                return None
            j = int(np.searchsorted(keys, idx))
            cand = [keys[k] for k in (j - 1, j) if 0 <= k < len(keys)]
            best = min(cand, key=lambda k: abs(k - idx))
            ok = abs(stamps[by_index[int(best)]][1] - stamps[n][1]) <= 1.0 and \
                shot_of[by_index[int(best)]] == shot_of[n]
            return cams[int(best)].pitch_to_image if ok else None

        result = solve_match(build(near, 5.0), result.prior, opt)
        report(0.8 + 0.2 * r / max(rounds - 1, 1))
    result.info.update({"video": Path(video).name, "fps": fps, "venue": venue, "rounds": rounds,
                        "main_frames": int(main.sum()), "sampled_frames": len(looks)})
    report(1.0)
    return result
