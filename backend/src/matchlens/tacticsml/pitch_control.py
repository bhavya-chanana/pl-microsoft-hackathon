"""Pitch control: which team gets to each point of the pitch first (Spearman / Fernandez style, numpy).

For every cell of a 32 x 21 grid each player gets a time-to-intercept (reaction time, then a run at top
speed from where the current velocity carries them). A team's time to a cell is a soft minimum over its
players. The possession team also has to get the ball there, so its time is at least the ball travel time
(15 m/s). Control is a logistic of the time difference, so the two teams' shares always sum to 1.
"""

from __future__ import annotations

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH
from matchlens.tacticsml.contract import TFrame

GRID_NX, GRID_NY = 32, 21
REACTION_S = 0.7
SIGMA_S = 0.45  # logistic scale of the control model
SOFTMIN_TAU = 0.4  # how much a second defender nearby helps
BALL_SPEED = 15.0  # m/s
MAX_SPEED_FLOOR = 4.0

_GX = (np.arange(GRID_NX) + 0.5) * PITCH_LENGTH / GRID_NX
_GY = (np.arange(GRID_NY) + 0.5) * PITCH_WIDTH / GRID_NY
_CELL_X, _CELL_Y = np.meshgrid(_GX, _GY)  # [NY, NX]


def grid_axes() -> tuple[np.ndarray, np.ndarray]:
    return _GX, _GY


def _softmin(t: np.ndarray) -> np.ndarray:
    """Soft minimum over axis 0 of ``t`` [players, NY, NX]."""
    if t.shape[0] == 0:
        return np.full(t.shape[1:], 30.0)
    m = t.min(axis=0)
    return m - SOFTMIN_TAU * np.log(np.exp(-(t - m) / SOFTMIN_TAU).sum(axis=0))


def control_from_arrays(px: np.ndarray, py: np.ndarray, vx: np.ndarray, vy: np.ndarray, speed: np.ndarray,
                        is_poss: np.ndarray, ball_xy: tuple[float, float]) -> np.ndarray:
    """Possession team's control share on the grid, shape [GRID_NY, GRID_NX]."""
    # where each player is once the reaction time has passed (velocity is 0 when unknown)
    rx = px + vx * REACTION_S * 0.5
    ry = py + vy * REACTION_S * 0.5
    dist = np.hypot(_CELL_X[None] - rx[:, None, None], _CELL_Y[None] - ry[:, None, None])
    tti = REACTION_S + dist / np.maximum(speed, MAX_SPEED_FLOOR)[:, None, None]
    t_ball = np.hypot(_CELL_X - ball_xy[0], _CELL_Y - ball_xy[1]) / BALL_SPEED
    t_att = np.maximum(_softmin(tti[is_poss]), t_ball)
    t_def = _softmin(tti[~is_poss])
    z = np.clip((t_def - t_att) / SIGMA_S, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-z))


def frame_arrays(frame: TFrame) -> tuple[np.ndarray, ...]:
    ps = frame.players
    px = np.array([p.x for p in ps])
    py = np.array([p.y for p in ps])
    vx = np.array([p.vx or 0.0 for p in ps])
    vy = np.array([p.vy or 0.0 for p in ps])
    speed = np.array([p.top_speed for p in ps])
    is_poss = np.array([p.team_id == frame.possession_team_id for p in ps], dtype=bool)
    return px, py, vx, vy, speed, is_poss


def pitch_control(frame: TFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(grid_x [32], grid_y [21], control [21, 32]) as the possession team's share of each cell."""
    px, py, vx, vy, speed, is_poss = frame_arrays(frame)
    control = control_from_arrays(px, py, vx, vy, speed, is_poss, (frame.ball.x, frame.ball.y))
    return _GX, _GY, control
