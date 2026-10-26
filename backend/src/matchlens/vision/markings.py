"""The pitch markings in metres, and the named points on them a person can click.

Pitch coordinates follow the broadcast view from the main camera: x runs 0 to
105 left to right, y runs 0 at the far touchline to 68 at the near one (the
camera's side), so the pitch keeps the image's handedness. Line positions are
the IFAB ones (penalty area 16.5 x 40.32 m, goal area 5.5 x 18.32 m, circles
of 9.15 m); a real pitch varies in length and width, which shows up as
calibration error.
"""

from __future__ import annotations

import numpy as np

from matchlens.domain.models import PITCH_LENGTH as L
from matchlens.domain.models import PITCH_WIDTH as W

CIRCLE_R = 9.15
BOX_DEPTH, BOX_HALF = 16.5, 20.16
GOAL_AREA_DEPTH, GOAL_AREA_HALF = 5.5, 9.16
SPOT = 11.0
POST_HALF = 3.66
# Where the penalty arc meets the front of the penalty area, either side of the centre.
ARC_HALF_ANGLE = float(np.arccos((BOX_DEPTH - SPOT) / CIRCLE_R))
ARC_MEETS_BOX = CIRCLE_R * float(np.sin(ARC_HALF_ANGLE))


def _ends() -> list[tuple[float, float]]:
    """Each goal line's x and the direction into the pitch from it."""
    return [(0.0, 1.0), (L, -1.0)]


def segments() -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Every straight marking, as end points in metres."""
    out = [((0, 0), (L, 0)), ((0, W), (L, W)), ((0, 0), (0, W)), ((L, 0), (L, W)), ((L / 2, 0), (L / 2, W))]
    for x0, d in _ends():
        for depth, half in ((BOX_DEPTH, BOX_HALF), (GOAL_AREA_DEPTH, GOAL_AREA_HALF)):
            front = x0 + d * depth
            out += [((x0, W / 2 - half), (front, W / 2 - half)), ((x0, W / 2 + half), (front, W / 2 + half)),
                    ((front, W / 2 - half), (front, W / 2 + half))]
    return out


def arcs() -> list[tuple[tuple[float, float], float, float, float]]:
    """Every curved marking: centre, radius, start and end angle (radians)."""
    out = [((L / 2, W / 2), CIRCLE_R, 0.0, 2 * np.pi)]
    for x0, d in _ends():
        facing = 0.0 if d > 0 else np.pi
        out.append(((x0 + d * SPOT, W / 2), CIRCLE_R, facing - ARC_HALF_ANGLE, facing + ARC_HALF_ANGLE))
    return out


def polylines(step: float = 0.5) -> list[np.ndarray]:
    """Every marking sampled every ``step`` metres, one (N, 2) array each."""
    out = []
    for (x1, y1), (x2, y2) in segments():
        n = max(int(np.hypot(x2 - x1, y2 - y1) / step), 1) + 1
        out.append(np.c_[np.linspace(x1, x2, n), np.linspace(y1, y2, n)])
    for (cx, cy), r, a, b in arcs():
        n = max(int(r * (b - a) / step), 1) + 1
        t = np.linspace(a, b, n)
        out.append(np.c_[cx + r * np.cos(t), cy + r * np.sin(t)])
    return out


def model_points(step: float = 0.5) -> np.ndarray:
    """All markings as one cloud of points (N, 2) in metres."""
    return np.concatenate(polylines(step))


def landmarks() -> dict[str, tuple[float, float]]:
    """Named points where markings meet or end: what a labeller clicks."""
    out = {
        "far left corner": (0.0, 0.0), "far right corner": (L, 0.0),
        "near left corner": (0.0, W), "near right corner": (L, W),
        "halfway, far touchline": (L / 2, 0.0), "halfway, near touchline": (L / 2, W),
        "centre spot": (L / 2, W / 2),
        "centre circle, far": (L / 2, W / 2 - CIRCLE_R), "centre circle, near": (L / 2, W / 2 + CIRCLE_R),
    }
    for (x0, d), side in zip(_ends(), ("left", "right"), strict=True):
        for name, depth, half in (("box", BOX_DEPTH, BOX_HALF), ("goal area", GOAL_AREA_DEPTH, GOAL_AREA_HALF)):
            front = x0 + d * depth
            out[f"{side} {name}, far, on goal line"] = (x0, W / 2 - half)
            out[f"{side} {name}, near, on goal line"] = (x0, W / 2 + half)
            out[f"{side} {name}, far front corner"] = (front, W / 2 - half)
            out[f"{side} {name}, near front corner"] = (front, W / 2 + half)
        out[f"{side} penalty spot"] = (x0 + d * SPOT, W / 2)
        out[f"{side} arc meets box, far"] = (x0 + d * BOX_DEPTH, W / 2 - ARC_MEETS_BOX)
        out[f"{side} arc meets box, near"] = (x0 + d * BOX_DEPTH, W / 2 + ARC_MEETS_BOX)
        out[f"{side} goal, far post"] = (x0, W / 2 - POST_HALF)
        out[f"{side} goal, near post"] = (x0, W / 2 + POST_HALF)
    return out
