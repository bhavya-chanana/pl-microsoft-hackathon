"""Clip pixels to pitch metres.

A panorama clip is undistorted with ``cv2.remap`` maps (rectified pixel ->
raw pixel), and a homography takes pitch metres to rectified pixels.
Detections live in raw clip pixels, so projecting a player's feet means
inverting the remap (nearest neighbour over the map, through a KD-tree) and
then the homography.
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy.spatial import cKDTree

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH

# A raw pixel further than this from every remapped pixel is outside the
# rectified view (sky, stands): it has no place on the pitch.
MAX_LOOKUP_PX = 3.0
# Feet a little over the lines are still on the pitch (throw-ins, corners).
MARGIN_M = 3.0


class PitchProjector:
    def __init__(self, map_x: np.ndarray, map_y: np.ndarray, homography: np.ndarray) -> None:
        """``map_x``/``map_y`` are float maps of the rectified image's shape: rectified
        pixel (row, col) came from raw pixel (map_x, map_y). ``homography`` maps pitch
        metres to rectified pixels."""
        self.width = map_x.shape[1]
        self._tree = cKDTree(np.c_[map_x.ravel(), map_y.ravel()])
        self._to_pitch = np.linalg.inv(homography)

    @classmethod
    def from_fixed_maps(cls, map1: np.ndarray, map2: np.ndarray, homography: np.ndarray) -> PitchProjector:
        """From OpenCV's fixed-point maps (``CV_16SC2`` + ``CV_16UC1``), as the dataset ships them."""
        map_x, map_y = cv2.convertMaps(map1, map2, cv2.CV_32FC1)
        return cls(map_x, map_y, homography)

    def to_pitch(self, pixels: np.ndarray) -> np.ndarray:
        """Raw clip pixels (N, 2) to pitch metres (N, 2). Points that fall outside the
        rectified view or well off the pitch come back as NaN."""
        pixels = np.asarray(pixels, dtype=np.float64).reshape(-1, 2)
        out = np.full_like(pixels, np.nan)
        seen = np.isfinite(pixels).all(axis=1)
        if not seen.any():
            return out
        dist, idx = self._tree.query(pixels[seen])
        rectified = np.c_[idx % self.width, idx // self.width].astype(np.float64)
        metres = cv2.perspectiveTransform(rectified[None], self._to_pitch)[0]
        on_view = dist <= MAX_LOOKUP_PX
        on_pitch = ((metres[:, 0] >= -MARGIN_M) & (metres[:, 0] <= PITCH_LENGTH + MARGIN_M)
                    & (metres[:, 1] >= -MARGIN_M) & (metres[:, 1] <= PITCH_WIDTH + MARGIN_M))
        metres[~(on_view & on_pitch)] = np.nan
        out[seen] = metres
        return out
