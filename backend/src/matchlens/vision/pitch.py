"""Clip pixels to pitch metres.

A panorama clip is undistorted with ``cv2.remap`` maps (rectified pixel ->
raw pixel), and a homography takes pitch metres to rectified pixels.
Detections live in raw clip pixels, so projecting a player's feet means
inverting the remap (nearest neighbour over the map, through a KD-tree) and
then the homography.

The rectified view is not quite one flat plane: on SoccerTrack 128057 the
shipped homography is 10-14 m out at the far goal. Surveyed pitch points
(line crossings with known positions) recalibrate it: a fresh homography plus a
smooth thin-plate correction, about 1 m out on points left out of the fit.
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy.interpolate import RBFInterpolator
from scipy.spatial import cKDTree

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH

# A raw pixel further than this from every remapped pixel is outside the
# rectified view (sky, stands): it has no place on the pitch.
MAX_LOOKUP_PX = 3.0
# Feet a little over the lines are still on the pitch (throw-ins, corners).
MARGIN_M = 3.0
# Thin-plate smoothing: some give, so one badly surveyed point cannot bend the pitch.
CALIBRATION_SMOOTHING = 100.0


class PitchProjector:
    def __init__(self, map_x: np.ndarray, map_y: np.ndarray, homography: np.ndarray) -> None:
        """``map_x``/``map_y`` are float maps of the rectified image's shape: rectified
        pixel (row, col) came from raw pixel (map_x, map_y). ``homography`` maps pitch
        metres to rectified pixels."""
        self.width = map_x.shape[1]
        self._tree = cKDTree(np.c_[map_x.ravel(), map_y.ravel()])
        self._to_pitch = np.linalg.inv(homography)
        self._correction: RBFInterpolator | None = None

    @classmethod
    def from_fixed_maps(cls, map1: np.ndarray, map2: np.ndarray, homography: np.ndarray) -> PitchProjector:
        """From OpenCV's fixed-point maps (``CV_16SC2`` + ``CV_16UC1``), as the dataset ships them."""
        map_x, map_y = cv2.convertMaps(map1, map2, cv2.CV_32FC1)
        return cls(map_x, map_y, homography)

    def _rectify(self, pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        dist, idx = self._tree.query(pixels)
        return np.c_[idx % self.width, idx // self.width].astype(np.float64), dist

    def calibrate(self, pixels: np.ndarray, pitch: np.ndarray) -> PitchProjector:
        """Refit from surveyed points: raw clip pixels (N, 2) whose pitch metres (N, 2) are known."""
        rectified, dist = self._rectify(np.asarray(pixels, dtype=np.float64))
        on_view = dist <= MAX_LOOKUP_PX
        rectified, pitch = rectified[on_view], np.asarray(pitch, dtype=np.float64)[on_view]
        if len(pitch) < 8:
            raise ValueError("calibration needs at least 8 surveyed points in view")
        self._to_pitch, _ = cv2.findHomography(rectified, pitch, 0)
        residual = pitch - cv2.perspectiveTransform(rectified[None], self._to_pitch)[0]
        self._correction = RBFInterpolator(rectified, residual, kernel="thin_plate_spline",
                                           smoothing=CALIBRATION_SMOOTHING)
        return self

    def to_pitch(self, pixels: np.ndarray) -> np.ndarray:
        """Raw clip pixels (N, 2) to pitch metres (N, 2). Points that fall outside the
        rectified view or well off the pitch come back as NaN."""
        pixels = np.asarray(pixels, dtype=np.float64).reshape(-1, 2)
        out = np.full_like(pixels, np.nan)
        seen = np.isfinite(pixels).all(axis=1)
        if not seen.any():
            return out
        rectified, dist = self._rectify(pixels[seen])
        metres = cv2.perspectiveTransform(rectified[None], self._to_pitch)[0]
        if self._correction is not None:
            metres += self._correction(rectified)
        on_view = dist <= MAX_LOOKUP_PX
        on_pitch = ((metres[:, 0] >= -MARGIN_M) & (metres[:, 0] <= PITCH_LENGTH + MARGIN_M)
                    & (metres[:, 1] >= -MARGIN_M) & (metres[:, 1] <= PITCH_WIDTH + MARGIN_M))
        metres[~(on_view & on_pitch)] = np.nan
        out[seen] = metres
        return out

    def on_pitch(self, pixels: np.ndarray, margin: float = 0.0) -> np.ndarray:
        """Mask of raw clip pixels that land inside the lines (give or take ``margin`` metres):
        players, not the officials and staff along the touchline."""
        metres = self.to_pitch(pixels)
        with np.errstate(invalid="ignore"):
            return ((metres[:, 0] >= -margin) & (metres[:, 0] <= PITCH_LENGTH + margin)
                    & (metres[:, 1] >= -margin) & (metres[:, 1] <= PITCH_WIDTH + margin))
