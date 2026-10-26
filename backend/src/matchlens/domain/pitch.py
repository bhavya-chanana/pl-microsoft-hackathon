"""Pitch geometry and formation anchors used by the simulator and stats engine."""

from __future__ import annotations

import math

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH, Point

GOAL_Y = PITCH_WIDTH / 2
GOAL_HALF_WIDTH = 3.66

# Anchor positions in a team-relative frame: the team attacks towards x=105
# and y=68 is its left touchline.
ANCHORS: dict[str, tuple[float, float]] = {
    "GK": (5, 34),
    "LB": (26, 58),
    "LWB": (34, 60),
    "RWB": (34, 8),
    "LCB": (20, 43),
    "RCB": (20, 25),
    "RB": (26, 10),
    "CB": (20, 34),
    "LCM": (42, 46),
    "CM": (38, 34),
    "RCM": (42, 22),
    "LDM": (35, 42),
    "RDM": (35, 26),
    "LM": (46, 58),
    "RM": (46, 10),
    "LAM": (58, 54),
    "CAM": (58, 34),
    "RAM": (58, 14),
    "AM": (58, 34),
    "LW": (66, 58),
    "RW": (66, 10),
    "ST": (72, 34),
    "LST": (72, 42),  # split strikers in a front two
    "RST": (72, 26),
}


def clamp_point(x: float, y: float) -> Point:
    return Point(x=min(max(x, 0.0), PITCH_LENGTH), y=min(max(y, 0.0), PITCH_WIDTH))


def to_absolute(x: float, y: float, attacking_right: bool) -> tuple[float, float]:
    """Convert team-relative coordinates to absolute pitch coordinates."""
    if attacking_right:
        return x, y
    return PITCH_LENGTH - x, PITCH_WIDTH - y


def to_relative(x: float, y: float, attacking_right: bool) -> tuple[float, float]:
    return to_absolute(x, y, attacking_right)  # the transform is its own inverse


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def goal_distance_and_angle(x_rel: float, y_rel: float) -> tuple[float, float]:
    """Distance (m) to the centre of the goal being attacked, and the angle (rad)
    subtended by the goal mouth. Coordinates are team-relative."""
    dx = PITCH_LENGTH - x_rel
    dy = y_rel - GOAL_Y
    dist = math.hypot(dx, dy)
    left = math.atan2(GOAL_Y + GOAL_HALF_WIDTH - y_rel, dx)
    right = math.atan2(GOAL_Y - GOAL_HALF_WIDTH - y_rel, dx)
    return dist, abs(left - right)


def expected_goals(x_rel: float, y_rel: float, header: bool = False, big_chance: bool = False) -> float:
    """Simple logistic xG model on distance and goal-mouth angle."""
    dist, angle = goal_distance_and_angle(x_rel, y_rel)
    z = -1.1 - 0.11 * dist + 1.6 * angle
    if header:
        z -= 0.8
    if big_chance:
        z += 1.4
    return round(1 / (1 + math.exp(-z)), 3)


def point_to_segment_distance(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return distance(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return distance(p, (ax + t * dx, ay + t * dy))
