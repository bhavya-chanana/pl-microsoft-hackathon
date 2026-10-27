"""Where the pitch is in a frame, and which of its pixels are painted lines.

The field is the largest patch of grass-coloured pixels, closed over the lines
and players and filled to its convex hull. Lines are thin bright strokes on it:
a white top-hat (the image minus its morphological opening) keeps anything
brighter than its surroundings and narrower than the kernel; a stroke must have
grass on both sides of it, across or along, which drops the edge of the
advertising boards and the crowd; and compact blobs (shirts, socks, the ball)
are dropped by shape. The distance from every pixel
to the nearest line pixel is what the calibration fits the pitch model to.

Sizes are tuned at ``REF_WIDTH`` pixels across and scale with the frame.
"""

from __future__ import annotations

import cv2
import numpy as np

from .teams import GRASS_HUE, GRASS_MIN_SAT

REF_WIDTH = 960
GRASS_MIN_VALUE = 30
SPECK_PX = 9  # green patches smaller than this are not grass
CLOSE_PX = 25  # closes the field over lines and players
MIN_FIELD = 0.05  # a field smaller than this share of the frame is no field
PIECE = 0.2  # a patch of grass this big next to the largest is pitch too
EDGE_PX = 4  # line pixels this close to the field's edge are its boundary, not paint
TOPHAT_PX = 11  # wider than any line on a wide shot
MIN_CONTRAST = 18  # grey levels above the surroundings
NOISE_MADS = 6.0
FLANK_PX = 5  # grass must be within this either side of a line pixel
BLOB_PX = 6  # a component thicker than this both ways...
BLOB_FILL = 0.35  # ...and filling this much of its box is a player, not a line
DIST_CAP = 40.0


def _odd(n: float) -> int:
    return max(round(n) | 1, 3)


def _scale(image: np.ndarray) -> float:
    return image.shape[1] / REF_WIDTH


def grass_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (GRASS_HUE[0], GRASS_MIN_SAT, GRASS_MIN_VALUE), (GRASS_HUE[1], 255, 255))


def field_mask(bgr: np.ndarray, grass: np.ndarray | None = None) -> np.ndarray:
    """The pitch's region of the frame (uint8, 255 inside); empty when there is no pitch."""
    grass = grass_mask(bgr) if grass is None else grass
    scale = _scale(bgr)
    # Speckles of green in the stands first, so they cannot bridge the crowd to the pitch.
    solid = cv2.morphologyEx(grass, cv2.MORPH_OPEN, np.ones((_odd(SPECK_PX * scale),) * 2, np.uint8))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(CLOSE_PX * scale),) * 2)
    closed = cv2.morphologyEx(solid, cv2.MORPH_CLOSE, kernel)
    out = np.zeros_like(grass)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if n < 2:
        return out
    areas = stats[1:, cv2.CC_STAT_AREA]
    if areas.max() < MIN_FIELD * grass.size:
        return out
    # Pieces of pitch split by something in front of them (a player close to the lens) belong together.
    pieces = 1 + np.flatnonzero(areas >= PIECE * areas.max())
    hull = cv2.convexHull(cv2.findNonZero(np.isin(labels, pieces).astype(np.uint8)))
    cv2.fillConvexPoly(out, hull, 255)
    return out


def _shift(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """``mask`` moved by (dy, dx), zero where it moved in from outside."""
    out = np.zeros_like(mask)
    rows, cols = mask.shape
    out[max(dy, 0):rows + min(dy, 0), max(dx, 0):cols + min(dx, 0)] = \
        mask[max(-dy, 0):rows + min(-dy, 0), max(-dx, 0):cols + min(-dx, 0)]
    return out


def flanked(grass: np.ndarray, k: int) -> np.ndarray:
    """Pixels with grass ``k`` pixels away on both sides: above and below, left and right, or a diagonal."""
    g = grass > 0
    out = np.zeros_like(g)
    for dy, dx in ((k, 0), (0, k), (k, k), (k, -k)):
        out |= _shift(g, dy, dx) & _shift(g, -dy, -dx)
    return out


def line_mask(bgr: np.ndarray, field: np.ndarray, grass: np.ndarray | None = None) -> np.ndarray:
    """Painted-line pixels on the field (uint8, 255 on a line)."""
    scale = _scale(bgr)
    grass = grass_mask(bgr) if grass is None else grass
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(TOPHAT_PX * scale),) * 2)
    hat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    inside = cv2.erode(field, np.ones((_odd(2 * EDGE_PX * scale),) * 2, np.uint8))
    values = hat[inside > 0]
    if values.size == 0:
        return np.zeros_like(gray)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    strong = hat > max(MIN_CONTRAST, median + NOISE_MADS * mad)
    lines = (strong & (inside > 0) & flanked(grass, max(round(FLANK_PX * scale), 2))).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    w, h, area = stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT], stats[:, cv2.CC_STAT_AREA]
    blob = (np.minimum(w, h) > BLOB_PX * scale) & (area > BLOB_FILL * w * h)
    blob[0] = True  # the background
    keep = ~blob
    return (keep[labels] * 255).astype(np.uint8)


def distance_map(lines: np.ndarray, cap: float = DIST_CAP) -> np.ndarray:
    """Pixels to the nearest line pixel (float32), capped."""
    if not lines.any():
        return np.full(lines.shape, cap, np.float32)
    dist = cv2.distanceTransform(cv2.bitwise_not(lines), cv2.DIST_L2, 5)
    return np.minimum(dist, cap)
