"""Numpy forward pass of the tactical model: the GATv2 critic and the cVAE decoder (no torch).

This reproduces ``train.model.TacticsGAT`` and ``train.cvae.ConditionalVAE`` op for op (the forward spec is
the docstring of ``train/model.py``), reading the ``.npz`` files written by ``train.export``. Constants are
duplicated here on purpose: importing ``train.model`` would import torch, which the deploy image lacks;
``tests/test_tacticsml_infer.py`` checks they have not drifted.

npz layout: every key of the torch ``state_dict`` under the same name, plus ``__config__`` (JSON of the
``ModelConfig`` / ``CVAEConfig``), ``__profile_keys__`` and ``__format__``.

All functions are batch friendly: ``pos (B,T,N,2)``, ``vel (B,T,N,2)``, ``vel_mask (B,N)``,
``node_feat (B,N,F)``, ``edge_feat (B,N,N,5)``, ``node_mask (B,N)`` as built by ``features.stack``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np

from matchlens.tacticsml import features as F

OUTCOMES = ("shot10", "goal10", "concede10", "turn5")
N_SLOTS = F.N_TEAM  # receiver candidates: possession-team slots 0..10
LOST = N_SLOTS  # index of the "ball lost" class in the 12-way receiver output
NEG = -1e9
POS_SCALE = 50.0
VEL_SCALE = 8.0
REL_SCALE = 20.0
CARRIER_COL = F.F_IS_CARRIER
GAT_FORMAT = "tacticsml-gat-v1"
CVAE_FORMAT = "tacticsml-cvae-v1"
TARGETS = ("shot", "retain", "press", "receiver")
TEAMS = ("attack", "defence", "both")
DISP_SCALE = 5.0
N_PLAYERS = F.BALL
META_KEYS = ("__config__", "__profile_keys__", "__format__", "__seed__", "__encoder_member__")


# ---------------------------------------------------------------- primitives

def gelu(x: np.ndarray) -> np.ndarray:
    return 0.5 * x * (1.0 + np.tanh(0.7978845608028654 * (x + 0.044715 * x * x * x)))


def layer_norm(x: np.ndarray, w: np.ndarray, b: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    mu = x.mean(-1, keepdims=True)
    var = ((x - mu) ** 2).mean(-1, keepdims=True)  # biased, like torch
    return (x - mu) / np.sqrt(var + eps) * w + b


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _linear(x: np.ndarray, p: dict[str, np.ndarray], name: str) -> np.ndarray:
    y = x @ p[f"{name}.weight"].T
    b = p.get(f"{name}.bias")
    return y if b is None else y + b


def _config_from(z: Any, cls: type) -> Any:
    raw = json.loads(str(z["__config__"]))
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in names})


def _load_npz(path: Path | str, dtype) -> tuple[dict[str, np.ndarray], Any]:
    with np.load(path, allow_pickle=False) as z:
        params = {k: np.asarray(z[k], dtype=dtype) for k in z.files if k not in META_KEYS}
        return params, {k: z[k] for k in z.files if k in META_KEYS}


# ---------------------------------------------------------------- GAT critic

@dataclass(frozen=True)
class GatConfig:
    """Mirror of ``train.model.ModelConfig`` (unknown keys such as ``dropout`` are ignored)."""

    hidden: int = 96
    heads: int = 4
    layers: int = 4
    ffn: int = 144
    steps: int = F.T_STEPS
    n_node_feat: int = F.F_BASE
    n_edge_feat: int = F.N_EDGE
    n_profile: int = 0
    emb_dim: int = 64


class GatNet:
    """One ensemble member."""

    def __init__(self, params: dict[str, np.ndarray], cfg: GatConfig, profile_keys: tuple[str, ...] = (),
                 seed: int = 0):
        self.p, self.cfg, self.profile_keys, self.seed = params, cfg, tuple(profile_keys), seed
        want = cfg.steps * 2 + 2 + 1 + cfg.n_node_feat
        if params["node_in.weight"].shape != (cfg.hidden, want):
            raise ValueError(f"node_in.weight {params['node_in.weight'].shape} does not match config "
                             f"({cfg.hidden}, {want})")

    @classmethod
    def load(cls, path: Path | str, dtype=np.float32) -> "GatNet":
        params, meta = _load_npz(path, dtype)
        if str(meta.get("__format__", GAT_FORMAT)) != GAT_FORMAT:
            raise ValueError(f"{path}: not a {GAT_FORMAT} file")
        with np.load(path, allow_pickle=False) as z:
            cfg = _config_from(z, GatConfig)
        keys = tuple(str(k) for k in meta.get("__profile_keys__", ()))
        return cls(params, cfg, keys, int(meta.get("__seed__", 0)))

    @property
    def dtype(self):
        return self.p["node_in.weight"].dtype

    # ------------------------------------------------------------ pieces
    def _norm_feat(self, node_feat: np.ndarray) -> np.ndarray:
        P = self.cfg.n_profile
        if P == 0:
            return node_feat
        a, b = F.F_BASE, F.F_BASE + P
        prof = (node_feat[..., a:b] - self.p["feat_mean"]) / self.p["feat_std"] * node_feat[..., b:b + P]
        return np.concatenate([node_feat[..., :a], prof, node_feat[..., b:]], axis=-1)

    def _layer(self, i: int, h: np.ndarray, e: np.ndarray, mask: np.ndarray) -> np.ndarray:
        p, k = self.p, self.cfg.heads
        pre = f"layers.{i}."
        B, N, H = h.shape
        d = H // k
        g = _linear(h, p, pre + "lin_l").reshape(B, N, k, d)
        r = _linear(h, p, pre + "lin_r").reshape(B, N, k, d)
        q = (e @ p[pre + "lin_e.weight"].T).reshape(B, N, N, k, d)
        s = g[:, :, None] + r[:, None, :] + q  # (B,i,j,k,d)
        s = np.where(s > 0, s, 0.2 * s)
        logit = (s * p[pre + "att"]).sum(-1)  # (B,i,j,k)
        logit = np.where(mask[:, None, :, None] == 0, NEG, logit)
        a = softmax(logit, axis=2)
        m = np.einsum("bijh,bjhd->bihd", a, r).reshape(B, N, H)
        h = layer_norm(h + _linear(m, p, pre + "lin_o"), p[pre + "norm1.weight"], p[pre + "norm1.bias"])
        ff = _linear(gelu(_linear(h, p, pre + "ffn1")), p, pre + "ffn2")
        h = layer_norm(h + ff, p[pre + "norm2.weight"], p[pre + "norm2.bias"])
        return h * mask[..., None]

    def run(self, batch: dict[str, np.ndarray], signs: tuple[float, float] = (1.0, 1.0)
            ) -> dict[str, np.ndarray]:
        """One non-averaged pass in the frame reflected by ``signs`` (a D2 element)."""
        dt, p = self.dtype, self.p
        s = np.asarray(signs, dtype=dt)
        pos = np.asarray(batch["pos"], dtype=dt) * s
        vel = np.asarray(batch["vel"], dtype=dt) * s
        node_feat = np.asarray(batch["node_feat"], dtype=dt)
        mask = np.asarray(batch["node_mask"], dtype=dt)
        B, T, N, _ = pos.shape
        nf = self._norm_feat(node_feat)
        x = np.concatenate([(pos / POS_SCALE).transpose(0, 2, 1, 3).reshape(B, N, T * 2),
                            vel[:, -1] / VEL_SCALE, np.asarray(batch["vel_mask"], dtype=dt)[..., None], nf],
                           axis=-1)
        h = _linear(x, p, "node_in") * mask[..., None]
        last = pos[:, -1]
        rel = (last[:, None, :, :] - last[:, :, None, :]) / REL_SCALE  # [b,i,j] = pos_j - pos_i
        e = np.concatenate([np.asarray(batch["edge_feat"], dtype=dt), rel], axis=-1)
        for i in range(self.cfg.layers):
            h = self._layer(i, h, e, mask)
        w = _linear(np.tanh(_linear(h, p, "pool_proj")), p, "pool_vec")[..., 0]
        w = softmax(np.where(mask == 0, NEG, w), axis=-1)
        pooled = (w[..., None] * h).sum(1)
        carrier = (node_feat[..., CARRIER_COL:CARRIER_COL + 1] * h).sum(1)
        emb = _linear(np.concatenate([pooled, carrier], axis=-1), p, "emb_lin")
        z = gelu(emb)
        lost = _linear(z, p, "lost_lin")
        outcome = _linear(gelu(_linear(z, p, "out1")), p, "out2")
        rin = np.concatenate([h[:, :N_SLOTS], np.broadcast_to(carrier[:, None], (B, N_SLOTS, carrier.shape[-1]))],
                             axis=-1)
        slots = _linear(gelu(_linear(rin, p, "recv1")), p, "recv2")[..., 0]
        slots = np.where(mask[:, :N_SLOTS] == 0, NEG, slots)
        return {"h": h, "receiver_logits": np.concatenate([slots, lost], axis=-1), "outcome_logits": outcome,
                "embedding": emb}

    def run_d2(self, batch: dict[str, np.ndarray]) -> list[dict[str, np.ndarray]]:
        """The four non-averaged passes in ``features.D2`` order (the cVAE decoder needs each of them)."""
        return [self.run(batch, F.d2_signs(g)) for g in F.D2]

    def forward(self, batch: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """D2 frame-averaged logits and embedding."""
        runs = self.run_d2(batch)
        return {k: np.mean([r[k] for r in runs], axis=0) for k in ("receiver_logits", "outcome_logits", "embedding")}

    def predict(self, batch: dict[str, np.ndarray], chunk: int = 32) -> dict[str, np.ndarray]:
        """Probabilities: ``receiver_probs (B,12)``, ``outcomes (B,4)`` (OUTCOMES order), ``embedding (B,E)``."""
        n = int(np.asarray(batch["pos"]).shape[0])
        parts = []
        for a in range(0, n, chunk):
            sub = {k: v[a:a + chunk] for k, v in batch.items() if k in _BATCH_KEYS}
            o = self.forward(sub)
            parts.append({"receiver_probs": softmax(o["receiver_logits"], axis=-1),
                          "outcomes": sigmoid(o["outcome_logits"]), "embedding": o["embedding"]})
        return {k: np.concatenate([q[k] for q in parts]) for k in parts[0]}


_BATCH_KEYS = ("pos", "vel", "vel_mask", "node_feat", "edge_feat", "node_mask")


class Ensemble:
    """Several members over the same feature config; predictions are stacked on a leading member axis."""

    def __init__(self, members: list[GatNet]):
        if not members:
            raise ValueError("an ensemble needs at least one member")
        self.members = members

    @classmethod
    def load(cls, paths: list[Path | str], dtype=np.float32) -> "Ensemble":
        return cls([GatNet.load(p, dtype) for p in paths])

    @property
    def profile_keys(self) -> tuple[str, ...]:
        return self.members[0].profile_keys

    def predict(self, batch: dict[str, np.ndarray], chunk: int = 32) -> dict[str, np.ndarray]:
        outs = [m.predict(batch, chunk) for m in self.members]
        return {k: np.stack([o[k] for o in outs]) for k in outs[0]}


# ---------------------------------------------------------------- cVAE decoder

@dataclass(frozen=True)
class CvaeConfig:
    z_dim: int = 8
    hidden: int = 128
    q_hidden: int = 64
    horizon: float = 3.0
    default_top_speed: float = 8.8


def movable_mask(node_mask: np.ndarray, team: np.ndarray, locked: np.ndarray) -> np.ndarray:
    """(B,N) 1 for nodes the cVAE may move: moved-team players, present, not locked."""
    idx = np.arange(node_mask.shape[1])
    attack = (idx < F.N_TEAM).astype(node_mask.dtype)
    defence = ((idx >= F.N_TEAM) & (idx < N_PLAYERS)).astype(node_mask.dtype)
    by_team = np.stack([attack, defence, attack + defence])[team]
    return by_team * node_mask * (1.0 - locked)


def smooth_clamp(raw: np.ndarray, radius: np.ndarray) -> np.ndarray:
    n = np.maximum(np.linalg.norm(raw, axis=-1, keepdims=True), 1e-8)
    r = np.maximum(radius[..., None], 1e-6)
    return raw * (r * np.tanh(n / r) / n)


class CvaeDecoder:
    """The decoder half of the cVAE (proposals only need ``p(dpos | graph, z, condition)``)."""

    def __init__(self, params: dict[str, np.ndarray], cfg: CvaeConfig, encoder_member: str = ""):
        self.p, self.cfg, self.encoder_member = params, cfg, encoder_member

    @classmethod
    def load(cls, path: Path | str, dtype=np.float32) -> "CvaeDecoder":
        params, meta = _load_npz(path, dtype)
        if str(meta.get("__format__", CVAE_FORMAT)) != CVAE_FORMAT:
            raise ValueError(f"{path}: not a {CVAE_FORMAT} file")
        with np.load(path, allow_pickle=False) as z:
            cfg = _config_from(z, CvaeConfig)
            member = str(z["__encoder_member__"]) if "__encoder_member__" in z.files else ""
        return cls(params, cfg, member)

    def radius(self, top_speed: np.ndarray | None, shape: tuple[int, int], max_move: float | None = None
               ) -> np.ndarray:
        ts = np.full(shape, self.cfg.default_top_speed) if top_speed is None else np.asarray(top_speed, float)
        r = ts * self.cfg.horizon
        return r if max_move is None else np.minimum(r, max_move)

    def decode(self, runs: list[dict[str, np.ndarray]], pos_last: np.ndarray, node_mask: np.ndarray,
               target: np.ndarray, team: np.ndarray, receiver: np.ndarray, locked: np.ndarray, z: np.ndarray,
               radius: np.ndarray) -> np.ndarray:
        """(n,N,2) displacements (canonical metres, input frame) for ``n`` latents ``z (n,N,Z)``.

        ``runs`` are the encoder's four ``run_d2`` outputs; their ``h``/``embedding`` (and the graph arrays
        and condition) may have batch 1 and are broadcast against ``z``. ``target``/``team``/``receiver``
        are ``(n,)`` or ``(1,)`` ints (``receiver`` -1 = none), ``locked`` ``(n,N)`` or ``(1,N)``.
        """
        p = self.p
        dt = p["dec1.weight"].dtype
        n, N, _ = z.shape
        bc = lambda a, *shape: np.broadcast_to(a, (n, *shape))  # noqa: E731
        mask = bc(np.asarray(node_mask, dt), N)
        locked = bc(np.asarray(locked, dt), N)
        team = np.broadcast_to(np.asarray(team), (n,))
        mov = movable_mask(mask, team, locked)
        rec = np.zeros((n, N), dtype=dt)
        receiver = np.broadcast_to(np.asarray(receiver), (n,))
        ok = receiver >= 0
        rec[np.flatnonzero(ok), receiver[ok]] = 1.0
        glob = np.concatenate([np.eye(len(TARGETS), dtype=dt)[np.broadcast_to(np.asarray(target), (n,))],
                               np.eye(len(TEAMS), dtype=dt)[team]], axis=-1)
        glob = np.broadcast_to(glob[:, None], (n, N, glob.shape[-1]))
        last = bc(np.asarray(pos_last, dt), N, 2)
        raws = []
        for g, run in zip(F.D2, runs):
            s = np.asarray(F.d2_signs(g), dtype=dt)
            node = np.concatenate([mov[..., None], locked[..., None], rec[..., None], last * s / POS_SCALE], axis=-1)
            h = bc(run["h"].astype(dt), N, run["h"].shape[-1])
            emb = bc(run["embedding"].astype(dt)[:, None], N, run["embedding"].shape[-1])
            x = np.concatenate([h, emb, z.astype(dt), node, glob], axis=-1)
            raw = _linear(gelu(_linear(gelu(_linear(x, p, "dec1")), p, "dec2")), p, "dec3") * DISP_SCALE
            raws.append(raw * s)  # back to the input frame
        raw = np.mean(raws, axis=0)
        return smooth_clamp(raw, bc(np.asarray(radius, dt), N)) * mov[..., None]
