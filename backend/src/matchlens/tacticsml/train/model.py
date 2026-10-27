"""Dense pure-torch GATv2 over the 25-node match graph, D2 invariant by frame averaging.

TRAINING ONLY: torch is imported here and nowhere outside ``tacticsml/train`` (the deploy image has no
torch). Inference runs in numpy from the exported weights and must reproduce this file op for op.

Forward pass (``TacticsGAT.forward`` with one D2 element ``s = (sx, sy)`` in {+-1}^2)
------------------------------------------------------------------------------------
Inputs (batch ``B``, steps ``T=5``, nodes ``N=25``): ``pos (B,T,N,2)``, ``vel (B,T,N,2)``,
``vel_mask (B,N)``, ``node_feat (B,N,F)``, ``edge_feat (B,N,N,5)``, ``node_mask (B,N)``.

1. Reflect: ``pos *= s``, ``vel *= s`` (broadcast over the last axis). Features are not touched.
2. Node features ``nf``: ``node_feat`` with, for the P profile keys, columns ``7..7+P`` replaced by
   ``(v - feat_mean) / feat_std * mask`` where ``mask = node_feat[..., 7+P:7+2P]``. Other columns as is.
3. Node input ``x_i = concat[ (pos[:, :, i] / 50) flattened t-major -> 2T, vel[:, -1, i] / 8 (2),
   vel_mask[i] (1), nf_i (F) ]`` and ``h = (x @ node_in.weight.T + node_in.bias) * node_mask``.
4. Edge input ``e_ij = concat[edge_feat_ij (5), (pos[:, -1, j] - pos[:, -1, i]) / 20 (2)]`` (7 values).
5. Each of ``L`` layers (post-LayerNorm, ``d = H / heads``)::

       g = lin_l(h); r = lin_r(h)                    # (B,N,heads,d), lin_* are Linear(H,H) with bias
       q_ij = lin_e(e_ij)                            # Linear(7,H) WITHOUT bias, -> (heads,d)
       s_ijk = sum_d att[k,d] * leaky_relu(g_i,k + r_j,k + q_ij,k, slope=0.2)
       s_ijk = -1e9 where node_mask[j] == 0
       a_ijk = softmax_j(s_ijk)                      # over ALL j (self included), per head k
       m_ik  = sum_j a_ijk * r_j,k                   # messages are the lin_r projections
       h = LN1(h + lin_o(concat_k m_ik))             # lin_o Linear(H,H) with bias
       h = LN2(h + ffn2(gelu_tanh(ffn1(h))))
       h = h * node_mask

   ``LN`` is ``torch.nn.LayerNorm`` (eps=1e-5, affine, biased variance over the last axis).
   ``gelu_tanh(x) = 0.5 x (1 + tanh(sqrt(2/pi) (x + 0.044715 x^3)))``. Dropout is identity at inference.
6. Pooling: ``w_i = pool_vec(tanh(pool_proj(h_i)))`` (``pool_vec`` has no bias), softmax over real nodes
   (node_mask), ``pooled = sum_i w_i h_i``; ``carrier = sum_i node_feat[i, 4] * h_i`` (column 4 =
   ``is_carrier``, raw not normalised; zero vector if there is no carrier);
   ``emb = emb_lin(concat[pooled, carrier])`` (64-d).
7. Heads (``z = gelu_tanh(emb)``):
   * ``lost = lost_lin(z)`` (1 logit)
   * ``outcome = out2(gelu_tanh(out1(z)))`` -> 4 logits, order ``OUTCOMES = (shot10, goal10, concede10, turn5)``
   * receiver per slot ``i in 0..10``: ``recv2(gelu_tanh(recv1(concat[h_i, carrier])))`` set to -1e9 where
     ``node_mask[i] == 0``; ``receiver_logits = concat[slots (11), lost (1)]`` (12).
8. Frame averaging: steps 1-7 are run for the four elements of D2 (e, fx, fy, fxy) and
   ``receiver_logits``, ``outcome`` logits and ``emb`` are averaged (arithmetic mean) over the four runs.
   Probabilities: ``softmax(receiver_logits)`` (12-way, last = lost) and ``sigmoid(outcome)``. Every
   probability is D2 invariant because the orbit of the input is the same set for every group element.

Training uses one random group element per sample (``signs``) as augmentation; evaluation uses the
full average.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields

import numpy as np
import torch
import torch.nn.functional as Fn
from torch import Tensor, nn

from matchlens.tacticsml import features as F

OUTCOMES = ("shot10", "goal10", "concede10", "turn5")
N_SLOTS = F.N_TEAM  # receiver candidates: possession-team slots 0..10
LOST = N_SLOTS  # index of the "ball lost" class in the 12-way receiver output
NEG = -1e9
POS_SCALE = 50.0
VEL_SCALE = 8.0
REL_SCALE = 20.0
CARRIER_COL = F.F_IS_CARRIER
FORMAT = "tacticsml-gat-v1"


@dataclass(frozen=True)
class ModelConfig:
    hidden: int = 96
    heads: int = 4
    layers: int = 4
    ffn: int = 144
    dropout: float = 0.1
    steps: int = F.T_STEPS
    n_node_feat: int = F.F_BASE  # F = 7 + 2P
    n_edge_feat: int = F.N_EDGE
    n_profile: int = 0  # P
    emb_dim: int = 64

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ModelConfig":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    @classmethod
    def for_profile_keys(cls, profile_keys=(), **kw) -> "ModelConfig":
        p = len(profile_keys)
        return cls(n_node_feat=F.node_feat_dim(tuple(profile_keys)), n_profile=p, **kw)


def gelu(x: Tensor) -> Tensor:
    return Fn.gelu(x, approximate="tanh")


def signs_for(group: str, batch: int, device=None) -> Tensor:
    sx, sy = F.d2_signs(group)
    return torch.tensor([sx, sy], dtype=torch.float32, device=device).expand(batch, 2)


def random_signs(batch: int, generator: torch.Generator | None = None, device=None) -> Tensor:
    s = torch.randint(0, 2, (batch, 2), generator=generator).float() * 2.0 - 1.0
    return s.to(device) if device is not None else s


class GATv2Layer(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        h, k = cfg.hidden, cfg.heads
        assert h % k == 0, "hidden must be divisible by heads"
        self.heads, self.d = k, h // k
        self.lin_l = nn.Linear(h, h)
        self.lin_r = nn.Linear(h, h)
        self.lin_e = nn.Linear(cfg.n_edge_feat + 2, h, bias=False)
        self.att = nn.Parameter(torch.empty(k, self.d))
        nn.init.xavier_uniform_(self.att)
        self.lin_o = nn.Linear(h, h)
        self.norm1 = nn.LayerNorm(h)
        self.ffn1 = nn.Linear(h, cfg.ffn)
        self.ffn2 = nn.Linear(cfg.ffn, h)
        self.norm2 = nn.LayerNorm(h)
        self.drop = nn.Dropout(cfg.dropout)

    def forward(self, h: Tensor, e: Tensor, node_mask: Tensor) -> Tensor:
        B, N, _ = h.shape
        g = self.lin_l(h).view(B, N, self.heads, self.d)
        r = self.lin_r(h).view(B, N, self.heads, self.d)
        q = self.lin_e(e).view(B, N, N, self.heads, self.d)
        s = Fn.leaky_relu(g[:, :, None] + r[:, None, :] + q, 0.2)  # (B,i,j,heads,d)
        logit = (s * self.att).sum(-1)  # (B,i,j,heads)
        logit = logit.masked_fill(node_mask[:, None, :, None] == 0, NEG)
        a = torch.softmax(logit, dim=2)
        m = torch.einsum("bijh,bjhd->bihd", a, r).reshape(B, N, -1)
        h = self.norm1(h + self.drop(self.lin_o(m)))
        h = self.norm2(h + self.drop(self.ffn2(gelu(self.ffn1(h)))))
        return h * node_mask[..., None]


class TacticsGAT(nn.Module):
    def __init__(self, cfg: ModelConfig | None = None):
        super().__init__()
        self.cfg = cfg or ModelConfig()
        c = self.cfg
        self.node_in = nn.Linear(c.steps * 2 + 2 + 1 + c.n_node_feat, c.hidden)
        self.layers = nn.ModuleList(GATv2Layer(c) for _ in range(c.layers))
        self.pool_proj = nn.Linear(c.hidden, 32)
        self.pool_vec = nn.Linear(32, 1, bias=False)
        self.emb_lin = nn.Linear(2 * c.hidden, c.emb_dim)
        self.recv1 = nn.Linear(2 * c.hidden, c.hidden)
        self.recv2 = nn.Linear(c.hidden, 1)
        self.lost_lin = nn.Linear(c.emb_dim, 1)
        self.out1 = nn.Linear(c.emb_dim, c.emb_dim)
        self.out2 = nn.Linear(c.emb_dim, len(OUTCOMES))
        self.register_buffer("feat_mean", torch.zeros(c.n_profile))
        self.register_buffer("feat_std", torch.ones(c.n_profile))

    # ------------------------------------------------------------ helpers
    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def set_profile_stats(self, mean: np.ndarray, std: np.ndarray) -> None:
        self.feat_mean.copy_(torch.as_tensor(mean, dtype=torch.float32))
        self.feat_std.copy_(torch.as_tensor(std, dtype=torch.float32).clamp_min(1e-6))

    @torch.no_grad()
    def init_output_bias(self, rates: dict[str, float]) -> None:
        """Start the outcome heads at the training base rates (faster, better calibrated)."""
        for i, name in enumerate(OUTCOMES):
            p = min(max(rates.get(name, 0.1), 1e-3), 1 - 1e-3)
            self.out2.bias[i] = math.log(p / (1 - p))

    def _norm_feat(self, node_feat: Tensor) -> Tensor:
        P = self.cfg.n_profile
        if P == 0:
            return node_feat
        a, b = F.F_BASE, F.F_BASE + P
        prof = (node_feat[..., a:b] - self.feat_mean) / self.feat_std * node_feat[..., b:b + P]
        return torch.cat([node_feat[..., :a], prof, node_feat[..., b:]], dim=-1)

    # ------------------------------------------------------------ core
    def node_states(self, batch: dict[str, Tensor], signs: Tensor | None = None) -> tuple[Tensor, Tensor]:
        """One non-averaged pass for the cVAE: ``(h (B,N,H), emb (B,64))`` in the reflected frame."""
        h, _, _, emb = self._forward_single(batch, signs)
        return h, emb

    def _forward_single(self, batch: dict[str, Tensor], signs: Tensor | None
                        ) -> tuple[Tensor, Tensor, Tensor, Tensor]:
        pos, vel = batch["pos"], batch["vel"]
        node_feat, mask = batch["node_feat"], batch["node_mask"]
        if signs is not None:
            s = signs.to(pos.dtype)[:, None, None, :]
            pos, vel = pos * s, vel * s
        B, T, N, _ = pos.shape
        nf = self._norm_feat(node_feat)
        x = torch.cat([(pos / POS_SCALE).permute(0, 2, 1, 3).reshape(B, N, T * 2),
                       vel[:, -1] / VEL_SCALE, batch["vel_mask"][..., None], nf], dim=-1)
        h = self.node_in(x) * mask[..., None]
        last = pos[:, -1]
        rel = (last[:, None, :, :] - last[:, :, None, :]) / REL_SCALE  # [b,i,j] = pos_j - pos_i
        e = torch.cat([batch["edge_feat"], rel], dim=-1)
        for layer in self.layers:
            h = layer(h, e, mask)
        # attention pooling and carrier context
        w = self.pool_vec(torch.tanh(self.pool_proj(h))).squeeze(-1).masked_fill(mask == 0, NEG)
        w = torch.softmax(w, dim=-1)
        pooled = (w[..., None] * h).sum(1)
        carrier = (node_feat[..., CARRIER_COL:CARRIER_COL + 1] * h).sum(1)
        emb = self.emb_lin(torch.cat([pooled, carrier], dim=-1))
        z = gelu(emb)
        lost = self.lost_lin(z)
        outcome = self.out2(gelu(self.out1(z)))
        slots = self.recv2(gelu(self.recv1(torch.cat(
            [h[:, :N_SLOTS], carrier[:, None].expand(B, N_SLOTS, -1)], dim=-1)))).squeeze(-1)
        slots = slots.masked_fill(mask[:, :N_SLOTS] == 0, NEG)
        return h, torch.cat([slots, lost], dim=-1), outcome, emb

    def forward(self, batch: dict[str, Tensor], signs: Tensor | None = None, average: bool = False
                ) -> dict[str, Tensor]:
        """Logits. ``signs`` (B,2): one D2 element per sample. ``average``: frame average over D2."""
        if average:
            parts = [self._forward_single(batch, signs_for(g, batch["pos"].shape[0], batch["pos"].device))
                     for g in F.D2]
            recv = torch.stack([p[1] for p in parts]).mean(0)
            out = torch.stack([p[2] for p in parts]).mean(0)
            emb = torch.stack([p[3] for p in parts]).mean(0)
            h = None
        else:
            h, recv, out, emb = self._forward_single(batch, signs)
        return {"receiver_logits": recv, "outcome_logits": out, "embedding": emb, "h": h}

    @torch.no_grad()
    def predict(self, batch: dict[str, Tensor], average: bool = True) -> dict[str, Tensor]:
        was = self.training
        self.eval()
        out = self.forward(batch, average=average)
        self.train(was)
        probs = {"receiver_probs": torch.softmax(out["receiver_logits"], dim=-1),
                 "embedding": out["embedding"]}
        for i, name in enumerate(OUTCOMES):
            probs[name] = torch.sigmoid(out["outcome_logits"][:, i])
        return probs


# ---------------------------------------------------------------- batches and losses

BATCH_KEYS = ("pos", "vel", "vel_mask", "node_feat", "edge_feat", "node_mask")
LABEL_KEYS = ("receiver_idx", "shot10", "goal10", "concede10", "turn5")


def to_tensors(batch: dict, device=None) -> dict[str, Tensor]:
    """numpy ``features.stack`` output (+ labels) -> torch dict (float32 geometry, int64 labels)."""
    out: dict[str, Tensor] = {}
    for k in BATCH_KEYS:
        out[k] = torch.as_tensor(np.asarray(batch[k]), dtype=torch.float32, device=device)
    for k in LABEL_KEYS + ("target_valid",):
        if k in batch and batch[k] is not None:
            out[k] = torch.as_tensor(np.asarray(batch[k]), dtype=torch.long, device=device)
    if "target_pos" in batch and batch["target_pos"] is not None:
        out["target_pos"] = torch.as_tensor(np.asarray(batch["target_pos"]), dtype=torch.float32, device=device)
    return out


DEFAULT_WEIGHTS = {"receiver": 1.0, "shot10": 1.0, "goal10": 0.5, "concede10": 0.5, "turn5": 1.0}


def masked_losses(out: dict[str, Tensor], batch: dict[str, Tensor],
                  weights: dict[str, float] | None = None) -> dict[str, Tensor]:
    """Per-head losses ignoring -1 labels; ``total`` is the weighted sum. Missing labels contribute 0."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    zero = out["outcome_logits"].sum() * 0.0
    parts: dict[str, Tensor] = {}
    if "receiver_idx" in batch:
        y = batch["receiver_idx"]
        ok = y >= 0
        if bool(ok.any()):
            parts["receiver"] = Fn.cross_entropy(out["receiver_logits"][ok], y[ok])
    for i, name in enumerate(OUTCOMES):
        if name not in batch:
            continue
        y = batch[name]
        ok = y >= 0
        if bool(ok.any()):
            parts[name] = Fn.binary_cross_entropy_with_logits(out["outcome_logits"][ok, i], y[ok].float())
    total = zero
    for k, v in parts.items():
        total = total + w.get(k, 1.0) * v
    parts["total"] = total
    return parts
