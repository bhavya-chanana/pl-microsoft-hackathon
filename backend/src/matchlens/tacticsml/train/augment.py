"""View-mask augmentation: train a broadcast-video model on full-pitch tracking.

Broadcast video sees a cone of the pitch; the rest of the players are imputed. This module makes full-tracking
batches look like that. For a sample it draws a main-camera pose, marks the nodes inside the camera's field of
view as observed, and for every player outside it

* sets the ``observed`` node feature (column ``features.F_OBSERVED``) to 0,
* moves its positions by 0.5 - 1.0 m of noise (one offset per player per sample, ``N(0, sigma^2)`` with
  ``sigma ~ U(noise_m)``, plus smaller independent noise at each history step), clipped to the pitch,
* adds the matching error to its last-step velocity, and
* recomputes the distance edge feature from the noisy positions.

Everything else (observed players, goals, padding, labels) is left exactly as it was. Pose model, in the
canonical centred frame (metres): the camera sits beside a touchline, ``|y_cam| = 34 + U(8, 25)`` on a random
side, at ``x_cam = x_ball + N(0, 10)``, and looks at ``(x_ball + N(0, 5), N(0, 5))`` with a horizontal field of
view ``U(35, 65)`` degrees (about 50 m of pitch at the far touchline for the middle value). A node is visible
when the angle between its direction from the camera and the optical axis is at most half the field of view.
The ball is visible under the same rule. At least ``min_visible`` players are always kept observed (the camera
is moved onto the ball and widened if a draw would leave fewer).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train.schema import edge_feat_from


@dataclass(frozen=True)
class ViewMaskConfig:
    p_apply: float = 0.7  # share of samples that get a mask at all
    fov_deg: tuple[float, float] = (35.0, 65.0)
    cam_offset: tuple[float, float] = (8.0, 25.0)  # metres beyond the touchline
    cam_x_sd: float = 10.0
    look_sd: float = 5.0
    noise_m: tuple[float, float] = (0.5, 1.0)
    step_noise: float = 0.3  # per-step noise as a fraction of sigma
    ball_noise_scale: float = 2.0
    min_visible: int = 4


def camera_visibility(pos_last: np.ndarray, node_mask: np.ndarray, rng: np.random.Generator,
                      cfg: ViewMaskConfig = ViewMaskConfig()) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Visible flags ``(B, N)`` of nodes at ``pos_last (B, N, 2)`` for random camera poses, and the poses."""
    B = pos_last.shape[0]
    ball = pos_last[:, F.BALL]
    side = rng.choice([-1.0, 1.0], size=B)
    cam = np.stack([ball[:, 0] + rng.normal(0, cfg.cam_x_sd, B),
                    side * (F.HALF_W + rng.uniform(*cfg.cam_offset, B))], axis=-1)
    look = np.stack([ball[:, 0] + rng.normal(0, cfg.look_sd, B), rng.normal(0, cfg.look_sd, B)], axis=-1)
    fov = np.deg2rad(rng.uniform(*cfg.fov_deg, B))
    vis = _in_cone(pos_last, cam, look, fov)
    n_players = ((vis & (node_mask > 0))[:, :F.BALL]).sum(1)
    for b in np.flatnonzero(n_players < cfg.min_visible):  # recentre on the ball and open up until enough are seen
        cam[b, 0], look[b] = ball[b, 0], ball[b, :]
        for f in np.deg2rad([55.0, 70.0, 90.0, 120.0]):
            fov[b] = f
            vis[b] = _in_cone(pos_last[b:b + 1], cam[b:b + 1], look[b:b + 1], fov[b:b + 1])[0]
            if ((vis[b] & (node_mask[b] > 0))[:F.BALL]).sum() >= cfg.min_visible:
                break
    vis = vis | (np.arange(pos_last.shape[1]) >= F.GOAL_OWN)[None]  # goal anchors are always known
    return vis, {"cam": cam, "look": look, "fov": fov}


def _in_cone(pos: np.ndarray, cam: np.ndarray, look: np.ndarray, fov: np.ndarray) -> np.ndarray:
    axis = look - cam
    axis = axis / np.maximum(np.linalg.norm(axis, axis=-1, keepdims=True), 1e-9)
    d = pos - cam[:, None]
    dist = np.linalg.norm(d, axis=-1)
    cos = (d * axis[:, None]).sum(-1) / np.maximum(dist, 1e-9)
    return (cos >= np.cos(fov / 2)[:, None]) & (dist < 160.0)


def view_mask_batch(batch: dict[str, np.ndarray], rng: np.random.Generator, cfg: ViewMaskConfig = ViewMaskConfig()
                    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Apply the augmentation to a numpy batch (keys of ``features.stack``). Returns a new dict (arrays that were
    not touched are shared) and ``masked (B, N)``, True for nodes that were moved to "unobserved"."""
    pos, vel = batch["pos"].astype(np.float32).copy(), batch["vel"].astype(np.float32).copy()
    nf = batch["node_feat"].copy()
    mask = batch["node_mask"]
    B, T, N, _ = pos.shape
    on = rng.random(B) < cfg.p_apply
    vis, _ = camera_visibility(pos[:, -1], mask, rng, cfg)
    masked = (~vis) & (mask > 0) & on[:, None]
    masked[:, F.GOAL_OWN:] = False
    sigma = rng.uniform(*cfg.noise_m, size=(B, 1, 1, 1)).astype(np.float32)
    scale = np.ones((1, 1, N, 1), dtype=np.float32)
    scale[:, :, F.BALL] = cfg.ball_noise_scale
    static = rng.normal(size=(B, 1, N, 2)).astype(np.float32) * sigma * scale
    jitter = rng.normal(size=(B, T, N, 2)).astype(np.float32) * sigma * scale * cfg.step_noise
    noise = (static + jitter) * masked[:, None, :, None]
    new_pos = pos + noise
    new_pos[..., 0] = np.clip(new_pos[..., 0], -F.HALF_L, F.HALF_L)
    new_pos[..., 1] = np.clip(new_pos[..., 1], -F.HALF_W, F.HALF_W)
    pos = np.where(masked[:, None, :, None], new_pos, pos)
    dt = 1.0  # history spacing in seconds
    vel[:, -1] = vel[:, -1] + (noise[:, -1] - noise[:, -2]) / dt * masked[..., None] if T > 1 else vel[:, -1]
    nf[..., F.F_OBSERVED] = np.where(masked, 0.0, nf[..., F.F_OBSERVED])
    out = dict(batch)
    out.update(pos=pos, vel=vel, node_feat=nf, edge_feat=edge_feat_from(pos[:, -1], mask, dtype=np.float32))
    return out, masked
