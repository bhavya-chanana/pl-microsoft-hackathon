"""Next10 heads and losses (TRAINING ONLY: imports torch). See docs/TACTICSML.md "Next10" for the maths.

Heads sit on the critic's node states ``h (B,N,H)`` and 64-d embedding ``emb``:

* ``BallZoneHead``: where the ball is 10 s later, a softmax over the 16 x 12 pitch grid. A query MLP scores each
  cell from ``[emb, h_ball, cell features]``; the cell features contain the cell relative to the ball, so the
  head can learn "the ball tends to stay near where it is" without learning it per cell. Trained against a
  Gaussian-smoothed one-hot of the true cell (sigma = 1 cell). In a reflected frame the grid is the mirrored
  grid, so the frame average un-reflects (flips) each grid before averaging: D2 *equivariant* by construction.
* ``poss10`` (BCE) and ``action_next`` (4-way CE: pass, carry, shot, other): MLPs on ``gelu(emb)``.
* ``ReceiverShotHead``: ``P(shot | receiver = i)`` for each possession-team slot and for "ball lost". Logits are
  residual to the direct shot10 logit (``l_i = l_direct + delta_i``), so at initialisation the decomposition
  equals the direct head. ``P(shot) = sum_i P(recv = i) P(shot | recv = i)``.
"""

from __future__ import annotations

import math

import numpy as np
import torch
import torch.nn.functional as Fn
from torch import Tensor, nn

from matchlens.tacticsml import features as F
from matchlens.tacticsml import next10_common as C
from matchlens.tacticsml.train.model import N_SLOTS, gelu, masked_losses

ACTIONS = ("pass", "carry", "shot", "other")
CELLS = torch.as_tensor(C.CELL_CENTRES)  # (192, 2)
SMOOTH_TABLE = torch.as_tensor(np.stack([C.smoothed_target(np.array([i]))[0] for i in range(C.N_CELLS)]))  # (192,192)


def cell_features(ball_xy: Tensor) -> Tensor:
    """Torch twin of ``next10_common.cell_features``: (B,2) -> (B,192,6)."""
    c = CELLS.to(ball_xy)[None]
    rel = (c - ball_xy[:, None]) / 30.0
    scale = torch.tensor([F.HALF_L, F.HALF_W], dtype=ball_xy.dtype, device=ball_xy.device)
    return torch.cat([(c / scale).expand_as(rel), rel, rel.norm(dim=-1, keepdim=True),
                      ((F.HALF_L - c[..., :1]) / F.HALF_L).expand(rel.shape[0], -1, 1)], dim=-1)


def reflect_grid(g: Tensor, signs: Tensor) -> Tensor:
    """Per-sample D2 action on grids (B,12,16); ``signs`` (B,2). Every element is its own inverse."""
    g = torch.where((signs[:, 0] < 0)[:, None, None], g.flip(-1), g)
    return torch.where((signs[:, 1] < 0)[:, None, None], g.flip(-2), g)


class BallZoneHead(nn.Module):
    def __init__(self, hidden: int, emb_dim: int, zdim: int = 96):
        super().__init__()
        self.q_emb = nn.Linear(emb_dim, zdim)
        self.q_ball = nn.Linear(hidden, zdim, bias=False)
        self.q_cell = nn.Linear(6, zdim, bias=False)
        self.mid = nn.Linear(zdim, zdim)
        self.out = nn.Linear(zdim, 1)

    def forward(self, emb: Tensor, h_ball: Tensor, ball_xy: Tensor) -> Tensor:
        x = gelu(self.q_emb(emb)[:, None] + self.q_ball(h_ball)[:, None] + self.q_cell(cell_features(ball_xy)))
        logit = self.out(gelu(self.mid(x))).squeeze(-1)  # (B, 192)
        return logit.view(-1, C.GRID_ROWS, C.GRID_COLS)


class ReceiverShotHead(nn.Module):
    def __init__(self, hidden: int, emb_dim: int):
        super().__init__()
        self.slot1 = nn.Linear(2 * hidden + emb_dim, hidden)
        self.slot2 = nn.Linear(hidden, 1)
        self.lost1 = nn.Linear(emb_dim, emb_dim)
        self.lost2 = nn.Linear(emb_dim, 1)
        for lin in (self.slot2, self.lost2):  # residual to the direct logit: zero at initialisation
            nn.init.zeros_(lin.weight)
            nn.init.zeros_(lin.bias)

    def forward(self, h_slots: Tensor, carrier: Tensor, emb: Tensor, direct: Tensor) -> Tensor:
        B = h_slots.shape[0]
        x = torch.cat([h_slots, carrier[:, None].expand(B, N_SLOTS, -1), emb[:, None].expand(B, N_SLOTS, -1)], -1)
        d_slot = self.slot2(gelu(self.slot1(x))).squeeze(-1)
        d_lost = self.lost2(gelu(self.lost1(emb)))
        return direct[:, None] + torch.cat([d_slot, d_lost], dim=-1)  # (B, 12)


def mlp(d_in: int, d_hidden: int, d_out: int) -> nn.Sequential:
    return nn.Sequential(nn.Linear(d_in, d_hidden), nn.GELU(approximate="tanh"), nn.Linear(d_hidden, d_out))


# ---------------------------------------------------------------- losses

NEXT10_WEIGHTS = {"zone": 1.0, "poss10": 0.5, "action": 0.5, "shot_cond": 0.5, "shot_marg": 1.0}


def marginal_shot_prob(receiver_logits: Tensor, cond_logits: Tensor) -> Tensor:
    return (torch.softmax(receiver_logits, -1) * torch.sigmoid(cond_logits)).sum(-1)


def zone_loss(logits: Tensor, cell: Tensor) -> Tensor:
    """Cross-entropy against the Gaussian-smoothed one-hot of ``cell`` (B,) over the flattened grid; ignores
    ``cell < 0``. Equal to KL(target || p) up to the (constant) entropy of the target."""
    ok = cell >= 0
    if not bool(ok.any()):
        return logits.sum() * 0.0
    tgt = SMOOTH_TABLE.to(logits)[cell[ok]]
    return -(tgt * torch.log_softmax(logits[ok].flatten(1), -1)).sum(-1).mean()


def next10_losses(out: dict[str, Tensor], batch: dict[str, Tensor], weights: dict[str, float] | None = None
                  ) -> dict[str, Tensor]:
    """Critic losses (receiver, shot10, goal10, concede10, turn5) plus the Next10 heads; labels of -1 are ignored."""
    w = {**NEXT10_WEIGHTS, **(weights or {})}
    parts = masked_losses(out, batch, weights)
    total = parts.pop("total")
    zero = out["outcome_logits"].sum() * 0.0
    if "zone_logits" in out and "zone_cell" in batch:
        parts["zone"] = zone_loss(out["zone_logits"], batch["zone_cell"])
    if "poss10_logit" in out and "poss10" in batch:
        ok = batch["poss10"] >= 0
        if bool(ok.any()):
            parts["poss10"] = Fn.binary_cross_entropy_with_logits(out["poss10_logit"][ok], batch["poss10"][ok].float())
    if "action_logits" in out and "action_next" in batch:
        ok = batch["action_next"] >= 0
        if bool(ok.any()):
            parts["action"] = Fn.cross_entropy(out["action_logits"][ok], batch["action_next"][ok])
    if "shot_cond_logits" in out and "shot10" in batch:
        y, r = batch["shot10"], batch["receiver_idx"]
        ok = (y >= 0) & (r >= 0)
        if bool(ok.any()):  # teacher forcing: score the head of the receiver who actually got the ball
            lg = out["shot_cond_logits"][ok].gather(1, r[ok][:, None]).squeeze(1)
            parts["shot_cond"] = Fn.binary_cross_entropy_with_logits(lg, y[ok].float())
        ok = y >= 0
        if bool(ok.any()):
            p = marginal_shot_prob(out["receiver_logits"][ok], out["shot_cond_logits"][ok]).clamp(1e-6, 1 - 1e-6)
            parts["shot_marg"] = Fn.binary_cross_entropy(p, y[ok].float())
    for k, v in parts.items():
        if k in w:
            total = total + w[k] * v
    parts["total"] = total + zero
    return parts


def init_biases(poss10_head: nn.Sequential, rate: float | None) -> None:
    if rate is not None:
        p = min(max(rate, 1e-3), 1 - 1e-3)
        with torch.no_grad():
            poss10_head[-1].bias.fill_(math.log(p / (1 - p)))
