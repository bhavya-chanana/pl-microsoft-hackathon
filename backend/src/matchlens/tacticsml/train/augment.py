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

Round 2 adds a second mode, ``mode="footprint"``, fitted to real broadcast tracking (``train/domain_gap.py``):

* the visible region is a real camera footprint polygon of SkillCorner (``image_corners_projection``), stored
  relative to the ball in a small bank (``footprints.npz``) and replayed at the sample's ball, with a random
  mirror in x and y (the model is D2 symmetric); a player inside is missed with probability ``p_miss`` (97.7%
  of players inside the footprint are detected in SkillCorner);
* a player outside is REMOVED with probability ``p_drop`` (``node_mask`` 0, all features and positions zero,
  the receiver label becomes unknown if it was that player), which is what the video pipeline does: unseen
  players are absent, not imputed. The rest keep the round-1 behaviour (imputed, observed 0, noisy);
* seen players and the ball get small independent position noise per step (``obs_noise_m``), the 5 Hz jitter
  measured on video tracks (about 10 times that of the research tracking);
* the ball is replaced by the centroid of the seen players with probability ``p_ball_missing`` (the pipeline's
  behaviour when it has not seen the ball for 3 s);
* the ball carrier is never removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.schema import edge_feat_from

FOOTPRINT_BANK = schema.NEXT10 / "footprints.npz"
FOOTPRINT_SCALE = 1.3  # set by train/domain_gap.py --augmentation (the fitted value is reported in TACTICSML.md 9.8)


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
    # round 2: video-like masking (all default to the round-1 behaviour)
    mode: str = "cone"  # "cone" | "footprint"
    bank_path: str | None = None  # footprint bank (``footprints.npz``) for mode "footprint"
    p_drop: float = 0.0  # share of unseen players that are removed instead of imputed
    p_miss: float = 0.0  # a player inside the footprint is not detected
    obs_noise_m: tuple[float, float] = (0.0, 0.0)  # per-step position noise (sigma) of seen players and the ball
    p_ball_missing: float = 0.0  # the ball is replaced by the centroid of the seen players
    footprint_scale: float = 1.0  # polygon scale about the ball, fitted so the seen-player counts match SkillCorner


def video_config(p_apply: float = 1.0, bank_path: str | Path | None = None, p_drop: float = 0.9, p_miss: float = 0.03,
                 obs_noise_m: tuple[float, float] = (0.08, 0.2), p_ball_missing: float = 0.06,
                 footprint_scale: float = FOOTPRINT_SCALE) -> ViewMaskConfig:
    """The footprint-replay augmentation, with the defaults fitted by ``train/domain_gap.py`` (see TACTICSML.md 9.8)."""
    return ViewMaskConfig(p_apply=p_apply, mode="footprint", bank_path=str(bank_path or FOOTPRINT_BANK), p_drop=p_drop,
                          p_miss=p_miss, obs_noise_m=obs_noise_m, p_ball_missing=p_ball_missing, footprint_scale=footprint_scale)


_BANKS: dict[str, np.ndarray] = {}


def load_bank(path: str | Path) -> np.ndarray:
    """``(n, 10)``: x offsets then y offsets of the four footprint corners relative to the ball (metres), then the
    ball's own position (x, y) on the pitch, as recorded by SkillCorner."""
    key = str(path)
    if key not in _BANKS:
        if not Path(key).exists():
            raise FileNotFoundError(f"footprint bank {key} is missing; build it with "
                                    "python -m matchlens.tacticsml.train.domain_gap --bank")
        with np.load(key) as z:
            _BANKS[key] = np.concatenate([z["rel"], z["ball"]], 1).astype(np.float32)
    return _BANKS[key]


def footprint_visibility(pos_last: np.ndarray, node_mask: np.ndarray, rng: np.random.Generator, cfg: ViewMaskConfig,
                         carrier: np.ndarray | None = None) -> np.ndarray:
    """Visible flags ``(B, N)`` for a replayed footprint polygon around the ball (see the module docstring)."""
    bank = load_bank(cfg.bank_path or FOOTPRINT_BANK)
    B, N = pos_last.shape[:2]
    ball = pos_last[:, F.BALL]
    # a real footprint recorded with the ball near where this sample's ball is (the camera geometry depends on the
    # ball's place on the pitch), mirrored into this sample's quadrant: the pitch is D2 symmetric
    d = (np.abs(ball[:, :1]) - np.abs(bank[None, :, 8])) ** 2 + (np.abs(ball[:, 1:2]) - np.abs(bank[None, :, 9])) ** 2
    near = np.argpartition(d, 25, axis=1)[:, :25]
    rel = bank[near[np.arange(B), rng.integers(0, 25, B)]]  # (B, 10)
    sx = np.where(ball[:, 0] >= 0, 1.0, -1.0) * np.where(rel[:, 8] >= 0, 1.0, -1.0)
    sy = np.where(ball[:, 1] >= 0, 1.0, -1.0) * np.where(rel[:, 9] >= 0, 1.0, -1.0)
    poly = np.stack([ball[:, 0:1] + sx[:, None] * rel[:, :4], ball[:, 1:2] + sy[:, None] * rel[:, 4:8]], -1)  # (B, 4, 2)

    def inside(scale: np.ndarray) -> np.ndarray:
        q = ball[:, None] + (poly - ball[:, None]) * scale[:, None, None]
        a, b = q, np.roll(q, -1, axis=1)
        d = pos_last[:, :, None, :] - a[:, None]  # (B, N, 4, 2)
        e = (b - a)[:, None]
        cr = e[..., 0] * d[..., 1] - e[..., 1] * d[..., 0]  # (B, N, 4)
        return (cr >= 0).all(-1) | (cr <= 0).all(-1)

    vis = inside(np.full(B, cfg.footprint_scale))
    if cfg.p_miss > 0:
        vis &= (rng.random((B, N)) >= cfg.p_miss) | (np.arange(N) >= F.BALL)[None]
    if carrier is not None:
        has = carrier >= 0
        vis[np.flatnonzero(has), carrier[has]] = True
    for scale in (1.4 * cfg.footprint_scale, 1.9 * cfg.footprint_scale, 3.0 * cfg.footprint_scale):  # too few players in view: widen the polygon (a wide shot)
        few = ((vis & (node_mask > 0))[:, :F.BALL]).sum(1) < cfg.min_visible
        if not few.any():
            break
        v2 = inside(np.full(B, scale))
        vis[few] = v2[few]
    few = ((vis & (node_mask > 0))[:, :F.BALL]).sum(1) < cfg.min_visible
    vis[few] = True
    vis[:, F.BALL] = True  # the ball is placed by p_ball_missing below, not by the footprint
    return vis | (np.arange(N) >= F.GOAL_OWN)[None]


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
    not touched are shared) and ``masked (B, N)``, True for nodes that were moved to "unobserved" (imputed or removed)."""
    pos, vel = batch["pos"].astype(np.float32).copy(), batch["vel"].astype(np.float32).copy()
    nf = batch["node_feat"].copy()
    mask = batch["node_mask"].copy()
    B, T, N, _ = pos.shape
    on = rng.random(B) < cfg.p_apply
    footprint = cfg.mode == "footprint"
    carrier = np.asarray(batch["carrier_index"]) if "carrier_index" in batch else None
    if footprint:
        vis = footprint_visibility(pos[:, -1], mask, rng, cfg, carrier)
    else:
        vis, _ = camera_visibility(pos[:, -1], mask, rng, cfg)
    masked = (~vis) & (mask > 0) & on[:, None]
    masked[:, F.GOAL_OWN:] = False
    masked[:, F.BALL] = False if footprint else masked[:, F.BALL]
    drop = np.zeros_like(masked)
    if cfg.p_drop > 0:
        drop = masked & (rng.random((B, N)) < cfg.p_drop)
        drop[:, F.BALL:] = False
    imputed = masked & ~drop
    sigma = rng.uniform(*cfg.noise_m, size=(B, 1, 1, 1)).astype(np.float32)
    scale = np.ones((1, 1, N, 1), dtype=np.float32)
    scale[:, :, F.BALL] = cfg.ball_noise_scale
    static = rng.normal(size=(B, 1, N, 2)).astype(np.float32) * sigma * scale
    jitter = rng.normal(size=(B, T, N, 2)).astype(np.float32) * sigma * scale * cfg.step_noise
    noise = (static + jitter) * imputed[:, None, :, None]
    if cfg.obs_noise_m[1] > 0:  # seen players and the ball: small independent noise per step
        so = rng.uniform(*cfg.obs_noise_m, size=(B, 1, 1, 1)).astype(np.float32)
        seen = (mask > 0) & ~masked & (np.arange(N) <= F.BALL)[None] & on[:, None]
        noise = noise + rng.normal(size=(B, T, N, 2)).astype(np.float32) * so * seen[:, None, :, None]
    new_pos = pos + noise
    new_pos[..., 0] = np.clip(new_pos[..., 0], -F.HALF_L, F.HALF_L)
    new_pos[..., 1] = np.clip(new_pos[..., 1], -F.HALF_W, F.HALF_W)
    pos = np.where((imputed | (noise != 0).any((1, 3)))[:, None, :, None], new_pos, pos)
    dt = 1.0  # history spacing in seconds
    if T > 1:
        vel[:, -1] = vel[:, -1] + (noise[:, -1] - noise[:, -2]) / dt * (imputed | (noise != 0).any((1, 3)))[..., None]
    nf[..., F.F_OBSERVED] = np.where(masked, 0.0, nf[..., F.F_OBSERVED])
    if cfg.p_ball_missing > 0:  # no ball seen: the pipeline puts it at the centroid of the players it does see
        lost = on & (rng.random(B) < cfg.p_ball_missing)
        keep = ((mask > 0) & ~masked)[:, :F.BALL]
        cnt = np.maximum(keep.sum(1), 1)
        cen = (pos[:, :, :F.BALL] * keep[:, None, :, None]).sum(2) / cnt[:, None, None]  # (B, T, 2)
        pos[:, :, F.BALL] = np.where(lost[:, None, None], cen, pos[:, :, F.BALL])
        vel[:, -1, F.BALL] = np.where(lost[:, None], 0.0, vel[:, -1, F.BALL])
    out = dict(batch)
    if drop.any():
        keep_n = ~drop
        pos = pos * keep_n[:, None, :, None]
        vel = vel * keep_n[:, None, :, None]
        nf = nf * keep_n[..., None]
        mask = mask * keep_n
        out["node_mask"] = mask
        if "vel_mask" in batch:
            out["vel_mask"] = batch["vel_mask"] * keep_n
        if "receiver_idx" in batch:
            r = np.asarray(batch["receiver_idx"]).copy()
            ok = (r >= 0) & (r < F.N_TEAM)  # 0..10 a possession-team node; 11 is "lost", -1 unknown
            hit = np.zeros_like(ok)
            hit[ok] = drop[np.flatnonzero(ok), r[ok]]
            r[hit] = -1
            out["receiver_idx"] = r
    out.update(pos=pos, vel=vel, node_feat=nf, edge_feat=edge_feat_from(pos[:, -1], mask, dtype=np.float32))
    return out, masked
