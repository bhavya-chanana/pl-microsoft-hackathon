"""Serve-time Next10 inference in numpy (no torch): ``infer_next10(frame) -> dict``.

It reproduces ``train/next10.py`` (critic + FiLM + heads) and ``train/trajectory.py`` (forecaster) op for op from the
``.npz`` files written by ``train/export10.py``; ``tests/test_next10_model.py`` checks numpy against torch to 1e-5.
Constants are duplicated from the training modules on purpose (importing them would import torch).

Weights are read from ``MATCHLENS_NEXT10_WEIGHTS`` if set (point it at ``.../weights`` for v1), else ``backend/data/local/tacticsml/next10/weights_v2`` (next10-v2), else ``.../weights``
(``manifest.json`` + ``critic_seed*.npz`` + optional ``trajectory.npz``); every file's sha256 is checked. The
deployed Tactics Board model (``learned.py``, ``registry.py``) is a different, untouched code path.

Output of ``Next10Model.predict(frame, ...)`` (also ``infer_next10``); coordinates are centred metres
(x in [-52.5, 52.5], y in [-34, 34]) **in the orientation of the input frame** (the possession team's attacking
direction is undone), so a home team that attacks +x in the input is still attacking +x in the output::

    {"model_version", "possession_team_id", "team_known": bool,
     "probs": {"shot10", "goal10", "concede10", "turn5", "poss10"},   # for the team in possession
     "prob_std": {...same keys...},                                   # across ensemble members
     "poss10_by_team": {team_id: p},
     "shot10_parts": {"direct", "via_receiver"},
     "ball_zone10": {"cols": 16, "rows": 12, "x0": -52.5, "y0": -34.0, "cell_w", "cell_h", "p": [192 floats],
                     "top_cells": [{"row", "col", "p"} x5], "expected_xy": [x, y]},
     "receivers": [{"player_id", "p", "std"} x top_k], "receiver_lost": p,
     "action_next": {"pass", "carry", "shot", "other"},
     "embedding": [64 floats],      # mean over members of the D2-averaged critic embedding (retrieval / similar moments)
     "trajectories": {"dt_s": 1.0, "horizon_s": 10, "k": 6,
                      "entities": [{"entity_id", "modes": [{"p", "path": [[x, y] x 10]}]}]}}   # path[i] = t+i+1 s
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml import next10_common as C
from matchlens.tacticsml.contract import TFrame
from matchlens.tacticsml.infer_numpy import (
    NEG, CARRIER_COL, GatConfig, GatNet, N_SLOTS, POS_SCALE, REL_SCALE, VEL_SCALE, gelu, layer_norm, sigmoid,
    softmax, _linear,
)

log = logging.getLogger(__name__)

NEXT10_FORMAT = "tacticsml-next10-v1"
TRAJ_FORMAT = "tacticsml-traj-v1"
ENV_WEIGHTS = "MATCHLENS_NEXT10_WEIGHTS"
BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DIR_V1 = BACKEND_DIR / "data" / "local" / "tacticsml" / "next10" / "weights"
DEFAULT_DIR_V2 = BACKEND_DIR / "data" / "local" / "tacticsml" / "next10" / "weights_v2"
DEFAULT_DIR = DEFAULT_DIR_V1  # kept for callers that name it; ``weights_dir`` prefers v2
MANIFEST = "manifest.json"
ACTIONS = ("pass", "carry", "shot", "other")
N_AGENTS = F.BALL + 1
_META = ("__config__", "__format__", "__seed__")


def _load(path: Path | str, dtype) -> tuple[dict[str, np.ndarray], dict]:
    with np.load(path, allow_pickle=False) as z:
        params = {k: np.asarray(z[k], dtype=dtype if np.asarray(z[k]).dtype.kind == "f" else None)
                  for k in z.files if k not in _META}
        cfg = json.loads(str(z["__config__"]))
        fmt = str(z["__format__"])
    return params, {"config": cfg, "format": fmt}


def _cfg(cls: type, raw: dict) -> Any:
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in names})


# ---------------------------------------------------------------- critic

class Next10Net(GatNet):
    """One critic member: ``TacticsGAT`` + FiLM + Next10 heads."""

    def __init__(self, params: dict[str, np.ndarray], raw_cfg: dict, seed: int = 0):
        super().__init__({k: v for k, v in params.items()}, _cfg(GatConfig, raw_cfg), (), seed)
        self.raw = raw_cfg
        self.film = bool(raw_cfg.get("film", True)) and "film.0.weight" in params
        self.table = (C.TeamTable([str(n) for n in params["team_names"]], params["style"], params["style_mask"])
                      if "style" in params else None)

    @classmethod
    def load(cls, path: Path | str, dtype=np.float32) -> "Next10Net":  # type: ignore[override]
        params, meta = _load(path, dtype)
        if meta["format"] != NEXT10_FORMAT:
            raise ValueError(f"{path}: not a {NEXT10_FORMAT} file")
        with np.load(path, allow_pickle=False) as z:
            names = [str(n) for n in z["team_names"]] if "team_names" in z.files else []
        params["team_names"] = np.array(names)
        return cls(params, meta["config"], 0)

    def team_vector(self, ids: np.ndarray) -> np.ndarray:
        p = self.p
        e = p["team_emb.weight"][ids] + (p["style"][ids] * p["style_mask"][ids]) @ p["style_proj.weight"].T
        return (e * (ids > 0)[..., None]).reshape(ids.shape[0], -1)

    def run_next10(self, batch: dict[str, np.ndarray], signs: tuple[float, float], team_ids: np.ndarray
                   ) -> dict[str, np.ndarray]:
        dt, p = self.dtype, self.p
        s = np.asarray(signs, dtype=dt)
        pos = np.asarray(batch["pos"], dtype=dt) * s
        vel = np.asarray(batch["vel"], dtype=dt) * s
        node_feat = np.asarray(batch["node_feat"], dtype=dt)
        mask = np.asarray(batch["node_mask"], dtype=dt)
        B, T, N, _ = pos.shape
        nf = self._norm_feat(node_feat)
        x = np.concatenate([(pos / POS_SCALE).transpose(0, 2, 1, 3).reshape(B, N, T * 2),
                            vel[:, -1] / VEL_SCALE, np.asarray(batch["vel_mask"], dtype=dt)[..., None], nf], axis=-1)
        h = _linear(x, p, "node_in") * mask[..., None]
        last = pos[:, -1]
        rel = (last[:, None, :, :] - last[:, :, None, :]) / REL_SCALE
        e = np.concatenate([np.asarray(batch["edge_feat"], dtype=dt), rel], axis=-1)
        cond = self.team_vector(team_ids) if self.film else None
        for i in range(self.cfg.layers):
            h = self._layer(i, h, e, mask)
            if cond is not None:
                gamma, beta = np.split(cond @ p[f"film.{i}.weight"].T, 2, axis=-1)
                h = (h * (1.0 + gamma[:, None]) + beta[:, None]) * mask[..., None]
        w = _linear(np.tanh(_linear(h, p, "pool_proj")), p, "pool_vec")[..., 0]
        w = softmax(np.where(mask == 0, NEG, w), axis=-1)
        pooled = (w[..., None] * h).sum(1)
        carrier = (node_feat[..., CARRIER_COL:CARRIER_COL + 1] * h).sum(1)
        emb = _linear(np.concatenate([pooled, carrier], axis=-1), p, "emb_lin")
        z = gelu(emb)
        lost = _linear(z, p, "lost_lin")
        outcome = _linear(gelu(_linear(z, p, "out1")), p, "out2")
        cb = np.broadcast_to(carrier[:, None], (B, N_SLOTS, carrier.shape[-1]))
        slots = _linear(gelu(_linear(np.concatenate([h[:, :N_SLOTS], cb], -1), p, "recv1")), p, "recv2")[..., 0]
        slots = np.where(mask[:, :N_SLOTS] == 0, NEG, slots)
        recv = np.concatenate([slots, lost], axis=-1)
        # ---- heads
        ball_xy = pos[:, -1, F.BALL]
        zq = (_linear(emb, p, "zone.q_emb")[:, None] + _linear(h[:, F.BALL], p, "zone.q_ball")[:, None]
              + _linear(C.cell_features(ball_xy).astype(dt), p, "zone.q_cell"))
        zone = _linear(gelu(_linear(gelu(zq), p, "zone.mid")), p, "zone.out")[..., 0].reshape(B, C.GRID_ROWS, C.GRID_COLS)
        zone = C.reflect_grid(zone, signs)
        poss = _linear(gelu(_linear(z, p, "poss10_head.0")), p, "poss10_head.2")[..., 0]
        action = _linear(gelu(_linear(z, p, "action_head.0")), p, "action_head.2")
        x_s = np.concatenate([h[:, :N_SLOTS], cb, np.broadcast_to(emb[:, None], (B, N_SLOTS, emb.shape[-1]))], -1)
        d_slot = _linear(gelu(_linear(x_s, p, "shot_head.slot1")), p, "shot_head.slot2")[..., 0]
        d_lost = _linear(gelu(_linear(emb, p, "shot_head.lost1")), p, "shot_head.lost2")
        cond_logits = outcome[:, :1] + np.concatenate([d_slot, d_lost], axis=-1)
        return {"receiver_logits": recv, "outcome_logits": outcome, "embedding": emb, "zone_logits": zone,
                "poss10_logit": poss, "action_logits": action, "shot_cond_logits": cond_logits}

    def forward_next10(self, batch: dict[str, np.ndarray], team_ids: np.ndarray) -> dict[str, np.ndarray]:
        runs = [self.run_next10(batch, F.d2_signs(g), team_ids) for g in F.D2]
        out = {k: np.mean([r[k] for r in runs], axis=0) for k in runs[0]}
        out["shot_marg_prob"] = (softmax(out["receiver_logits"], -1) * sigmoid(out["shot_cond_logits"])).sum(-1)
        return out


# ---------------------------------------------------------------- trajectory

@dataclass(frozen=True)
class TrajCfg:
    hidden: int = 96
    heads: int = 4
    blocks: int = 3
    mode_blocks: int = 2
    ffn: int = 192
    modes: int = 6
    horizon: int = 10
    steps: int = F.T_STEPS
    n_node_feat: int = F.F_BASE
    pair_hidden: int = 16


def _attend(q, k, v, bias, key_mask, heads):
    *lead, L, D = q.shape
    d = D // heads
    q, k, v = (np.swapaxes(x.reshape(*lead, L, heads, d), -3, -2) for x in (q, k, v))
    s = q @ np.swapaxes(k, -1, -2) / np.sqrt(d)
    if bias is not None:
        s = s + bias
    s = np.where(key_mask[..., None, None, :] == 0, NEG, s)
    return np.swapaxes(softmax(s, -1) @ v, -3, -2).reshape(*lead, L, D)


class TrajNet:
    def __init__(self, params: dict[str, np.ndarray], cfg: TrajCfg):
        self.p, self.cfg = params, cfg

    @classmethod
    def load(cls, path: Path | str, dtype=np.float32) -> "TrajNet":
        params, meta = _load(path, dtype)
        if meta["format"] != TRAJ_FORMAT:
            raise ValueError(f"{path}: not a {TRAJ_FORMAT} file")
        return cls(params, _cfg(TrajCfg, meta["config"]))

    def _ln(self, x, name):
        return layer_norm(x, self.p[f"{name}.weight"], self.p[f"{name}.bias"])

    def _block(self, pre: str, x, bias, mask, time_attn: bool):
        p, heads = self.p, self.cfg.heads
        if time_attn:
            B, T, N, D = x.shape
            q, k, v = np.split(_linear(self._ln(x, pre + "ln_a"), p, pre + "qkv_a"), 3, -1)
            x = x + _linear(_attend(q, k, v, bias, np.broadcast_to(mask[:, None, :], (B, T, N)), heads), p, pre + "out_a")
            xt = np.swapaxes(x, 1, 2)
            q, k, v = np.split(_linear(self._ln(xt, pre + "ln_t"), p, pre + "qkv_t"), 3, -1)
            km = np.ones((B, N, T), dtype=x.dtype)
            x = x + np.swapaxes(_linear(_attend(q, k, v, None, km, heads), p, pre + "out_t"), 1, 2)
        else:
            q, k, v = np.split(_linear(self._ln(x, pre + "ln_a"), p, pre + "qkv_a"), 3, -1)
            x = x + _linear(_attend(q, k, v, bias, mask, heads), p, pre + "out_a")
        x = x + _linear(gelu(_linear(self._ln(x, pre + "ln_f"), p, pre + "ffn1")), p, pre + "ffn2")
        return x * mask[:, None, :, None] if time_attn else x * mask[..., None]

    def _pair_bias(self, pos):
        rel = (pos[..., None, :, :] - pos[..., :, None, :]) / 20.0
        feat = np.concatenate([rel, np.linalg.norm(rel, axis=-1, keepdims=True)], -1)
        return np.moveaxis(_linear(gelu(_linear(feat, self.p, "pair1")), self.p, "pair2"), -1, -3)

    def run(self, batch: dict[str, np.ndarray], signs: tuple[float, float]):
        c, p = self.cfg, self.p
        dt = p["inp.weight"].dtype
        s = np.asarray(signs, dtype=dt)
        pos = np.asarray(batch["pos"], dtype=dt) * s
        vel = np.asarray(batch["vel"], dtype=dt) * s
        mask = np.asarray(batch["node_mask"], dtype=dt)
        nf = np.asarray(batch["node_feat"], dtype=dt)
        B, T, N, _ = pos.shape
        step = np.concatenate([np.zeros_like(pos[:, :1]), pos[:, 1:] - pos[:, :-1]], 1) / 8.0
        static = np.broadcast_to(np.concatenate([vel[:, -1] / 8.0, np.asarray(batch["vel_mask"], dtype=dt)[..., None], nf],
                                                -1)[:, None], (B, T, N, 3 + nf.shape[-1]))
        x = _linear(np.concatenate([pos / 50.0, step, (pos - pos[:, -1:]) / 20.0, static], -1), p, "inp")
        x = (x + p["time_emb"][None, :, None]) * mask[:, None, :, None]
        bias = self._pair_bias(pos)
        for i in range(c.blocks):
            x = self._block(f"blocks.{i}.", x, bias, mask, True)
        g = x[:, -1]
        K = c.modes
        y = (g[:, None] + p["mode_emb"][None, :, None]).reshape(B * K, N, -1)
        mk = np.broadcast_to(mask[:, None], (B, K, N)).reshape(B * K, N)
        mb = np.broadcast_to(self._pair_bias(pos[:, -1])[:, None], (B, K, c.heads, N, N)).reshape(B * K, c.heads, N, N)
        for i in range(c.mode_blocks):
            y = self._block(f"mode_blocks.{i}.", y, mb, mk, False)
        y = self._ln(y, "ln_out")
        raw = _linear(y, p, "head").reshape(B, K, N, c.horizon, 4)
        pi = ((_linear(y, p, "pi_head")[..., 0] * mk).reshape(B, K, N).sum(-1) / mask.sum(-1, keepdims=True))
        hs = np.arange(1, c.horizon + 1, dtype=dt)
        base = pos[:, -1][:, None, :, None] + vel[:, -1][:, None, :, None] * hs[None, None, None, :, None]
        mu = base + raw[..., :2] * (1.0 + hs)[None, None, None, :, None]
        sp = np.logaddexp(raw[..., 2:], 0.0)
        ls = np.log((0.3 + sp) * (1.0 + 0.3 * hs)[None, None, None, :, None])
        return mu, ls, pi

    def forward(self, batch: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        mus, lss, pis = [], [], []
        for g in F.D2:
            sg = F.d2_signs(g)
            mu, ls, pi = self.run(batch, sg)
            mus.append(mu * np.asarray(sg, dtype=mu.dtype)[None, None, None, None, :])
            lss.append(ls)
            pis.append(pi)
        return {"mu": np.mean(mus, 0), "log_sigma": np.mean(lss, 0), "pi_logits": np.mean(pis, 0)}


# ---------------------------------------------------------------- weights directory

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def weights_dir() -> Path | None:
    env = os.getenv(ENV_WEIGHTS)
    for d in ([Path(env)] if env else []) + [DEFAULT_DIR_V2, DEFAULT_DIR_V1]:
        if (d / MANIFEST).is_file():
            return d
    return None


def next10_available(path: Path | str | None = None) -> bool:
    """True when a complete, hash-checked weight set can be loaded. Never raises."""
    try:
        d = Path(path) if path is not None else weights_dir()
        if d is None:
            return False
        Next10Model.load(d)
        return True
    except Exception:  # noqa: BLE001 - the caller falls back to "no Next10"
        return False


class Next10Model:
    """Critic ensemble + trajectory forecaster, with the frame <-> canonical conversions."""

    def __init__(self, members: list[Next10Net], traj: TrajNet | None, manifest: dict):
        self.members, self.traj, self.manifest = members, traj, manifest
        self.version = str(manifest.get("version", "next10-v1"))
        self.shot_mode = str(manifest.get("serve", {}).get("shot", "blend"))
        self.table = members[0].table

    @classmethod
    def load(cls, path: Path | str | None = None, verify: bool = True) -> "Next10Model":
        d = Path(path) if path is not None else weights_dir()
        if d is None or not (d / MANIFEST).is_file():
            raise FileNotFoundError("no Next10 weights (set MATCHLENS_NEXT10_WEIGHTS or run train/export10.py)")
        man = json.loads((d / MANIFEST).read_text())
        entries = list(man["members"]) + ([man["trajectory"]] if man.get("trajectory") else [])
        for e in entries:
            f = d / e["file"]
            if Path(e["file"]).name != e["file"] or not f.is_file():
                raise FileNotFoundError(f)
            if verify and _sha(f) != e["sha256"]:
                raise ValueError(f"{e['file']}: sha256 differs from the manifest")
        members = [Next10Net.load(d / m["file"]) for m in man["members"]]
        traj = TrajNet.load(d / man["trajectory"]["file"]) if man.get("trajectory") else None
        return cls(members, traj, man)

    # ------------------------------------------------------------ batch builders
    def _batch(self, frames: Sequence[TFrame], observed: Sequence[frozenset[str] | None] | None
               ) -> tuple[dict[str, np.ndarray], list[F.GraphSample]]:
        samples = [F.build_sample(f, observed_ids=(observed[i] if observed else None)) for i, f in enumerate(frames)]
        return F.stack(samples), samples

    def _team_ids(self, frames: Sequence[TFrame], team_names: Sequence[tuple[str | None, str | None]] | None
                  ) -> np.ndarray:
        if self.table is None or not team_names:
            return np.zeros((len(frames), 2), dtype=np.int64)
        return np.array([[self.table.lookup(a), self.table.lookup(b)] for a, b in team_names], dtype=np.int64)

    # ------------------------------------------------------------ predict
    def predict_batch(self, frames: Sequence[TFrame], team_names: Sequence[tuple[str | None, str | None]] | None = None,
                      observed_ids: Sequence[frozenset[str] | None] | None = None, top_k: int = 3,
                      with_trajectories: bool = True, with_embedding: bool = True) -> list[dict[str, Any]]:
        if not frames:
            return []
        batch, samples = self._batch(frames, observed_ids)
        ids = self._team_ids(frames, team_names)
        outs = [m.forward_next10(batch, ids) for m in self.members]
        mean = {k: np.mean([o[k] for o in outs], axis=0) for k in outs[0]}
        # probabilities of every member (for the spread) and of the averaged logits (for the answer)
        pm = {
            "shot_direct": [sigmoid(o["outcome_logits"][:, 0]) for o in outs],
            "shot_decomp": [o["shot_marg_prob"] for o in outs],
            "goal10": [sigmoid(o["outcome_logits"][:, 1]) for o in outs],
            "concede10": [sigmoid(o["outcome_logits"][:, 2]) for o in outs],
            "turn5": [sigmoid(o["outcome_logits"][:, 3]) for o in outs],
            "poss10": [sigmoid(o["poss10_logit"]) for o in outs],
            "receiver": [softmax(o["receiver_logits"], -1) for o in outs],
        }
        trajs = self.traj.forward(batch) if (self.traj is not None and with_trajectories) else None
        res = []
        for b, (frame, s) in enumerate(zip(frames, samples)):
            res.append(self._one(b, frame, s, mean, pm, trajs, top_k, ids[b], with_embedding))
        return res

    def predict(self, frame: TFrame, team_names: tuple[str | None, str | None] | None = None,
                observed_ids: frozenset[str] | None = None, top_k: int = 3, with_trajectories: bool = True,
                with_embedding: bool = True) -> dict[str, Any]:
        return self.predict_batch([frame], [team_names] if team_names else None,
                                  [observed_ids] if observed_ids is not None else None, top_k, with_trajectories,
                                  with_embedding)[0]

    def _blend(self, direct, decomp):
        return {"direct": direct, "decomposed": decomp}.get(self.shot_mode, 0.5 * (direct + decomp))

    def _one(self, b: int, frame: TFrame, s: F.GraphSample, mean: dict, pm: dict, trajs: dict | None, top_k: int,
             tids: np.ndarray, with_embedding: bool = True) -> dict[str, Any]:
        flip = np.array([-1.0 if s.flipped_x else 1.0, 1.0])
        sd = lambda key: float(np.std([m[b] for m in pm[key]])) if len(pm[key]) > 1 else 0.0  # noqa: E731
        direct = float(sigmoid(mean["outcome_logits"][b, 0]))
        decomp = float(mean["shot_marg_prob"][b])
        shot = float(self._blend(direct, decomp))
        shot_members = [self._blend(d[b], q[b]) for d, q in zip(pm["shot_direct"], pm["shot_decomp"])]
        poss = float(sigmoid(mean["poss10_logit"][b]))
        other = sorted({p.team_id for p in frame.players} - {frame.possession_team_id})
        by_team = {frame.possession_team_id: poss, **({other[0]: 1.0 - poss} if other else {})}
        # ball zone: canonical grid -> input orientation (mirror the columns when the possession team attacked left)
        zl = mean["zone_logits"][b]
        zp = softmax(zl.reshape(-1), -1).reshape(C.GRID_ROWS, C.GRID_COLS)
        if s.flipped_x:
            zp = zp[:, ::-1]
        flat = zp.reshape(-1)
        top = np.argsort(-flat)[:5]
        ex = (flat[:, None] * C.CELL_CENTRES).sum(0)
        rp = softmax(mean["receiver_logits"][b], -1)
        rstd = np.std([m[b] for m in pm["receiver"]], axis=0) if len(pm["receiver"]) > 1 else np.zeros(12)
        order = [i for i in np.argsort(-rp[:N_SLOTS]) if s.node_ids[i] != "pad"][:top_k]
        out: dict[str, Any] = {
            "model_version": self.version, "possession_team_id": frame.possession_team_id,
            "team_known": bool(tids[0] > 0), "probs": {"shot10": shot, "goal10": float(sigmoid(mean["outcome_logits"][b, 1])),
                                                       "concede10": float(sigmoid(mean["outcome_logits"][b, 2])),
                                                       "turn5": float(sigmoid(mean["outcome_logits"][b, 3])), "poss10": poss},
            "prob_std": {"shot10": float(np.std(shot_members)) if len(shot_members) > 1 else 0.0,
                         "goal10": sd("goal10"), "concede10": sd("concede10"), "turn5": sd("turn5"), "poss10": sd("poss10")},
            "poss10_by_team": by_team, "shot10_parts": {"direct": direct, "via_receiver": decomp},
            "ball_zone10": {"cols": C.GRID_COLS, "rows": C.GRID_ROWS, "x0": -F.HALF_L, "y0": -F.HALF_W,
                            "cell_w": C.CELL_W, "cell_h": C.CELL_H, "p": [float(v) for v in flat],
                            "top_cells": [{"row": int(i // C.GRID_COLS), "col": int(i % C.GRID_COLS), "p": float(flat[i])}
                                          for i in top],
                            "expected_xy": [float(ex[0]), float(ex[1])]},
            "receivers": [{"player_id": s.node_ids[i], "p": float(rp[i]), "std": float(rstd[i])} for i in order],
            "receiver_lost": float(rp[N_SLOTS]),
            "action_next": dict(zip(ACTIONS, (float(v) for v in softmax(mean["action_logits"][b], -1)))),
        }
        if with_embedding:
            out["embedding"] = [float(v) for v in mean["embedding"][b]]
        if trajs is not None:
            pi = softmax(trajs["pi_logits"][b], -1)
            mu = trajs["mu"][b]  # (K, N, H, 2) canonical
            ents = []
            names = [*s.node_ids[:F.BALL], "ball"]
            for n, name in enumerate(names):
                if name == "pad":
                    continue
                node = n if name != "ball" else F.BALL
                modes = [{"p": float(pi[k]), "path": [[float(x), float(y)] for x, y in (mu[k, node] * flip)]}
                         for k in np.argsort(-pi)]
                ents.append({"entity_id": name, "modes": modes})
            out["trajectories"] = {"dt_s": 1.0, "horizon_s": mu.shape[2], "k": int(mu.shape[0]), "entities": ents}
        return out


@lru_cache(maxsize=1)
def _default_model() -> Next10Model | None:
    try:
        return Next10Model.load()
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - corrupt weights must not break the caller
        log.exception("could not load the Next10 model")
        return None


def infer_next10(frame: TFrame, team_names: tuple[str | None, str | None] | None = None,
                 observed_ids: frozenset[str] | None = None, top_k: int = 3, with_trajectories: bool = True,
                 with_embedding: bool = True) -> dict[str, Any] | None:
    """The live-service entry point: predict the next 10 s of ``frame`` (None when no weights are installed).

    ``frame``: a ``contract.TFrame`` in absolute metres (0..105 x 0..68) with up to 4 history frames 1 s apart,
    ``possession_team_id`` and ``attacking_right``. ``team_names``: (possession team, opponent) display names for the FiLM
    conditioning (unknown names fall back to the plain critic). ``observed_ids``: ids of the players that are really
    seen (video); the rest count as imputed (observed = 0).
    """
    model = _default_model()
    return (None if model is None else
            model.predict(frame, team_names, observed_ids, top_k, with_trajectories, with_embedding))
