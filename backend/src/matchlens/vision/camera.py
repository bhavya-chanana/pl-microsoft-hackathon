"""Calibrate every main-camera frame: a homography from pitch metres to frame pixels.

The main camera stays in one place and pans, tilts and zooms. That makes the
change between two of its frames a homography too, for the stands as much as
the pitch, so a calibration carries from frame to frame:

1. **Anchor.** A frame a person labelled (four or more pitch landmarks) starts it.
2. **Track.** Corners on the pitch are followed into the next frame (pyramidal
   Lucas-Kanade); a RANSAC homography over them, players voted out, moves the
   last calibration on.
3. **Fit to the lines.** The pitch model's markings, projected with that guess,
   are pulled onto the painted lines found in the frame: a robust least-squares
   fit of the eight homography parameters. It runs both ways: every marking in
   view should sit on paint (pixels to the nearest line pixel), and every bit of
   paint should sit on a marking (back on the pitch, metres to the nearest
   marking, in that spot's pixels). One way alone lets the model slide along its
   own lines. A weak pull towards the guess steadies views where the lines leave
   some direction free (only the halfway line in shot, say). Tracking alone
   would drift; the lines stop it.
4. **Relocate.** After a cut back to the main camera there is no previous frame.
   Calibrated frames are kept as keyframes (SIFT features over the whole frame,
   since the stands and boards hold still for a panning camera, with their
   homography); the new frame is matched against them, and the few best matches
   each give a guess to fit. The best fit wins.

A frame whose fit leaves too few markings on a line is not calibrated, unless
it is tracked from a calibrated one: then it coasts on the tracked guess for up
to two seconds (a close view of open grass has no lines to fit).

Sizes are tuned at ``REF_WIDTH`` pixels across and scale with the frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import cv2
import numpy as np
from scipy.ndimage import map_coordinates
from scipy.optimize import least_squares

from .lines import DIST_CAP, REF_WIDTH, distance_map, field_mask, grass_mask, line_mask
from .markings import model_points, polylines

MODEL = model_points(0.5)
TOL_PX = 3.0  # a marking within this of a line pixel is on it
# Soft-L1 scales, coarse to fine: a wide one first so a guess well off can still find its lines,
# then narrower ones so stray paint and players stop pulling.
LOSS_PX = (8.0, 4.0, 2.0)
MIN_VISIBLE = 60  # model points in view needed to fit
# The distance from any spot on (and around) the pitch to the nearest marking.
PITCH_EXTENT = (-20.0, -20.0, 125.0, 88.0)  # metres
PITCH_PX_PER_M = 10
MIN_SCORE = 0.5  # share of the visible markings on a line for a calibration to stand
PRIOR = 0.1  # pull towards the guess, per point, per 1% of the frame
MAX_COAST = 50  # frames
# Tracking.
MAX_CORNERS, CORNER_QUALITY, CORNER_SPACING = 500, 0.01, 8
MIN_TRACKED = 25
RANSAC_PX = 2.0
# Keyframes.
KEY_SCORE = 0.7
KEY_SPACING_M = 6.0
KEY_ZOOM = 1.25
MAX_KEYFRAMES = 60
MIN_MATCHES = 25
RATIO = 0.75
RELOCATE_TRIES = 3  # best-matching keyframes fitted after a cut
RELOCATE_EVERY = 5  # frames between attempts while lost


@dataclass(frozen=True)
class Calibration:
    homography: np.ndarray  # pitch metres -> frame pixels
    score: float  # share of the markings in view that land on a painted line
    visible: int  # model points in view
    how: str  # "anchor", "tracked", "relocated" or "coasted"


def project(h: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pitch metres (N, 2) to pixels (N, 2), and which points are in front of the camera."""
    p = np.c_[points, np.ones(len(points))] @ h.T
    front = p[:, 2] > 1e-9
    with np.errstate(divide="ignore", invalid="ignore"):
        return p[:, :2] / p[:, 2:], front


def to_pitch(h: np.ndarray, pixels: np.ndarray) -> np.ndarray:
    """Pixels (N, 2) to pitch metres (N, 2)."""
    return cv2.perspectiveTransform(np.asarray(pixels, np.float64).reshape(1, -1, 2), np.linalg.inv(h))[0]


def scaled(h: np.ndarray, factor: float) -> np.ndarray:
    """The same calibration for a frame resized by ``factor``."""
    return np.diag([factor, factor, 1.0]) @ h


@cache
def _pitch_distance() -> np.ndarray:
    """Metres to the nearest marking, on a grid over ``PITCH_EXTENT``."""
    x0, y0, x1, y1 = PITCH_EXTENT
    grid = np.full((int((y1 - y0) * PITCH_PX_PER_M), int((x1 - x0) * PITCH_PX_PER_M)), 255, np.uint8)
    for line in polylines(0.05):
        cv2.polylines(grid, [((line - [x0, y0]) * PITCH_PX_PER_M).round().astype(np.int32)], False, 0, 1)
    return cv2.distanceTransform(grid, cv2.DIST_L2, 5) / PITCH_PX_PER_M


def _pixels_per_metre(h: np.ndarray, pitch: np.ndarray) -> np.ndarray:
    """How many pixels a metre of pitch spans at each point (N, 2) under ``h``."""
    at, _ = project(h, pitch)
    across, _ = project(h, pitch + [0.5, 0.0])
    down, _ = project(h, pitch + [0.0, 0.5])
    return np.linalg.norm(across - at, axis=1) + np.linalg.norm(down - at, axis=1)


class FrameLines:
    """What the fit needs from one frame."""

    def __init__(self, bgr: np.ndarray) -> None:
        self.bgr = bgr
        grass = grass_mask(bgr)
        self.field = field_mask(bgr, grass)
        self.lines = line_mask(bgr, self.field, grass)
        self.dist = distance_map(self.lines, DIST_CAP * self.scale)
        rows, cols = np.nonzero(self.lines)
        self.paint = np.c_[cols, rows].astype(np.float64)
        np.random.default_rng(0).shuffle(self.paint)

    @property
    def scale(self) -> float:
        return self.bgr.shape[1] / REF_WIDTH

    def visible(self, h: np.ndarray) -> np.ndarray:
        """The model points that land on the field in this frame."""
        pts, front = project(h, MODEL)
        rows, cols = self.field.shape
        with np.errstate(invalid="ignore"):
            inside = front & (pts[:, 0] >= 0) & (pts[:, 0] < cols - 1) & (pts[:, 1] >= 0) & (pts[:, 1] < rows - 1)
        keep = np.zeros(len(MODEL), bool)
        idx = np.flatnonzero(inside)
        keep[idx] = self.field[pts[idx, 1].astype(int), pts[idx, 0].astype(int)] > 0
        return MODEL[keep]

    def distances(self, h: np.ndarray, points: np.ndarray) -> np.ndarray:
        cap = DIST_CAP * self.scale
        pts, front = project(h, points)
        pts = np.where(np.isfinite(pts), pts, -1.0)
        d = map_coordinates(self.dist, [pts[:, 1], pts[:, 0]], order=1, mode="constant", cval=cap)
        d[~front] = cap
        return d

    def paint_distances(self, h: np.ndarray, paint: np.ndarray, px_per_m: np.ndarray) -> np.ndarray:
        """Pixels from each paint pixel to the nearest marking, measured on the pitch."""
        cap = DIST_CAP * self.scale
        p = np.c_[paint, np.ones(len(paint))] @ np.linalg.inv(h).T
        front = p[:, 2] * np.linalg.det(h) > 0
        with np.errstate(divide="ignore", invalid="ignore"):
            metres = p[:, :2] / p[:, 2:]
        x0, y0 = PITCH_EXTENT[:2]
        grid = np.where(np.isfinite(metres), (metres - [x0, y0]) * PITCH_PX_PER_M, -1.0)
        d = map_coordinates(_pitch_distance(), [grid[:, 1], grid[:, 0]], order=1, mode="constant", cval=1e3)
        d = np.minimum(d * px_per_m, cap)
        d[~front] = cap
        return d


def fit(guess: np.ndarray, frame: FrameLines, how: str) -> Calibration | None:
    """Pull the pitch markings, projected with ``guess``, onto the frame's painted lines."""
    rows, cols = frame.field.shape
    norm = np.array([[2.0 / cols, 0, -1], [0, 2.0 / rows, -1], [0, 0, 1]])
    h = guess / guess[2, 2]
    for loss_px in LOSS_PX:
        points = frame.visible(h)
        if len(points) < MIN_VISIBLE:
            return None
        # As much paint as markings, so neither side of the fit outweighs the other.
        paint = frame.paint[: len(points)]
        if len(paint) < MIN_VISIBLE:
            return None
        px_per_m = _pixels_per_metre(h, to_pitch(h, paint))
        base = norm @ h
        weight = PRIOR * np.sqrt(len(points)) / 0.01

        def homography(d: np.ndarray, base: np.ndarray = base) -> np.ndarray:
            step = np.eye(3) + np.array([[d[0], d[1], d[2]], [d[3], d[4], d[5]], [d[6], d[7], 0.0]])
            return np.linalg.solve(norm, step @ base)

        def residuals(d: np.ndarray, points: np.ndarray = points, paint: np.ndarray = paint,
                      px_per_m: np.ndarray = px_per_m, homography=homography, weight: float = weight) -> np.ndarray:
            h = homography(d)
            return np.r_[frame.distances(h, points), frame.paint_distances(h, paint, px_per_m), weight * d]

        result = least_squares(residuals, np.zeros(8), loss="soft_l1", f_scale=loss_px * frame.scale,
                               x_scale=0.01, max_nfev=200)
        h = homography(result.x)
        h /= h[2, 2]
    points = frame.visible(h)
    if len(points) < MIN_VISIBLE:
        return None
    on_line = frame.distances(h, points) <= TOL_PX * frame.scale
    return Calibration(homography=h, score=float(on_line.mean()), visible=len(points), how=how)


def motion(prev_gray: np.ndarray, gray: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    """The homography taking the previous frame's pixels to this one's, from corners on the pitch."""
    scale = gray.shape[1] / REF_WIDTH
    corners = cv2.goodFeaturesToTrack(prev_gray, MAX_CORNERS, CORNER_QUALITY, CORNER_SPACING * scale, mask=mask)
    if corners is None or len(corners) < MIN_TRACKED:
        return None
    moved, status, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, corners, None, winSize=(21, 21), maxLevel=3)
    ok = status.ravel() == 1
    if ok.sum() < MIN_TRACKED:
        return None
    g, inliers = cv2.findHomography(corners[ok], moved[ok], cv2.RANSAC, RANSAC_PX * scale)
    if g is None or inliers.sum() < MIN_TRACKED:
        return None
    return g


@dataclass
class _Key:
    points: np.ndarray
    descriptors: np.ndarray
    homography: np.ndarray
    centre: np.ndarray  # pitch metres under the frame's centre
    span: float  # metres across the middle of the frame


def _view(h: np.ndarray, shape: tuple[int, ...]) -> tuple[np.ndarray, float]:
    rows, cols = shape[:2]
    left, centre, right = to_pitch(h, np.array([[0, rows / 2], [cols / 2, rows / 2], [cols, rows / 2]]))
    return centre, float(np.linalg.norm(right - left))


class Keyframes:
    """Calibrated views of the main camera, to find the way back after a cut."""

    def __init__(self) -> None:
        self.items: list[_Key] = []
        self._sift = cv2.SIFT_create(nfeatures=1500)
        self._matcher = cv2.BFMatcher(cv2.NORM_L2)

    def _covered(self, centre: np.ndarray, span: float) -> bool:
        return any(np.linalg.norm(k.centre - centre) < KEY_SPACING_M and 1 / KEY_ZOOM < span / k.span < KEY_ZOOM
                   for k in self.items)

    def add(self, gray: np.ndarray, h: np.ndarray) -> None:
        centre, span = _view(h, gray.shape)
        if len(self.items) >= MAX_KEYFRAMES or not np.isfinite(centre).all() or self._covered(centre, span):
            return
        keypoints, descriptors = self._sift.detectAndCompute(gray, None)
        if descriptors is None or len(keypoints) < MIN_MATCHES:
            return
        points = np.float32([k.pt for k in keypoints])
        self.items.append(_Key(points, descriptors, h.copy(), centre, span))

    def find(self, gray: np.ndarray) -> list[np.ndarray]:
        """Calibration guesses for a frame with no previous one, via the keyframes it matches best."""
        if not self.items:
            return []
        keypoints, descriptors = self._sift.detectAndCompute(gray, None)
        if descriptors is None or len(keypoints) < MIN_MATCHES:
            return []
        points = np.float32([k.pt for k in keypoints])
        found: list[tuple[int, np.ndarray]] = []
        for key in self.items:
            pairs = [p[0] for p in self._matcher.knnMatch(key.descriptors, descriptors, k=2)
                     if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
            if len(pairs) < MIN_MATCHES:
                continue
            src = key.points[[m.queryIdx for m in pairs]]
            dst = points[[m.trainIdx for m in pairs]]
            g, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 3.0 * gray.shape[1] / REF_WIDTH)
            if g is not None and inliers.sum() >= MIN_MATCHES:
                found.append((int(inliers.sum()), g @ key.homography))
        found.sort(key=lambda f: -f[0])
        return [h for _, h in found[:RELOCATE_TRIES]]


@dataclass
class _Last:
    gray: np.ndarray
    field: np.ndarray
    homography: np.ndarray
    coasting: int


class CameraTracker:
    """Feed it frames in order; it returns each one's calibration, or None."""

    def __init__(self) -> None:
        self.keyframes = Keyframes()
        self._last: _Last | None = None
        self._since_attempt = RELOCATE_EVERY

    def reset(self) -> None:
        """Forget the previous frame (a cut, or a jump in the clip); keep the keyframes."""
        self._last = None
        self._since_attempt = RELOCATE_EVERY

    def step(self, bgr: np.ndarray, *, main: bool, cut: bool = False,
             anchor: np.ndarray | None = None) -> Calibration | None:
        if cut or not main:
            self.reset()
        if not main:
            return None
        frame = FrameLines(bgr)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        last, self._last = self._last, None
        guesses, how = [], ""
        if anchor is not None:
            guesses, how = [anchor], "anchor"
        elif last is not None:
            g = motion(last.gray, gray, last.field)
            if g is not None:
                guesses, how = [g @ last.homography], "tracked"
        elif self._since_attempt >= RELOCATE_EVERY:
            self._since_attempt = 0
            guesses, how = self.keyframes.find(gray), "relocated"
        else:
            self._since_attempt += 1
        if not guesses:
            return None
        fits = [c for c in (fit(g, frame, how) for g in guesses) if c is not None]
        cal = max(fits, key=lambda c: c.score, default=None)
        guess = guesses[0]
        if cal is not None and cal.score >= MIN_SCORE:
            self._last = _Last(gray, frame.field, cal.homography, 0)
            self._since_attempt = RELOCATE_EVERY
            if cal.score >= KEY_SCORE:
                self.keyframes.add(gray, cal.homography)
            return cal
        if how == "tracked" and last is not None and last.coasting < MAX_COAST:
            self._last = _Last(gray, frame.field, guess, last.coasting + 1)
            score, visible = (cal.score, cal.visible) if cal is not None else (0.0, 0)
            return Calibration(homography=guess / guess[2, 2], score=score, visible=visible, how="coasted")
        return None
