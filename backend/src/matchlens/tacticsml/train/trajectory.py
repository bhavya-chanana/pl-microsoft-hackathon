"""Multi-horizon trajectory forecaster: a mixture-density set-attention model (TRAINING ONLY: torch).

Task. From the last ``T = 5`` one-second frames of the 22 players, the ball and the two goal anchors (the same
``features.stack`` batch as the critic, canonical frame: possession team attacks +x) predict the positions of the 22
players and the ball at ``t + 1 .. t + 10`` s (1 Hz), as ``K = 6`` joint scenarios.

Model (``TrajectoryNet``)
-------------------------
Tokens are (node, time) pairs. Token ``(i, t)`` = Linear[ pos_t / 50, (pos_t - pos_{t-1}) / 8, (pos_t - pos_T) / 20,
last velocity / 8, velocity-known flag, node features ] + a learned time embedding. ``AxialBlock``s alternate
*agent attention* (at each time step, over the 25 nodes, with an additive bias from an MLP of the pair's relative
position ``(dx, dy, dist)``) and *time attention* (per node, over the 5 steps), then a feed-forward layer (pre-LN
residual blocks). The last-time tokens are then copied ``K`` times, a learned mode embedding is added, and two
agent-attention blocks let the nodes of one mode agree with each other, which is what makes mode ``k`` one joint
scenario for everybody. Per node and mode the head emits ``mu`` and ``sigma`` for every horizon::

    mu_{k,i,h}    = pos_i + v_i * h + (1 + h) * raw_mu            (constant velocity plus a learned residual)
    sigma_{k,i,h} = (0.3 + softplus(raw_s)) * (1 + 0.3 h)         (metres, per axis)
    pi_k          = softmax_k( mean over real nodes of Linear(token_{k,i}) )

The predictive density of node ``i`` is the Gaussian mixture ``sum_k pi_k prod_h N(x_{i,h}; mu, sigma^2 I)`` with
the mode shared across nodes (scenario-level mixture).

D2. ``forward(..., average=True)`` runs the network on the four reflections of the input, maps ``mu`` back with
the same signs and averages ``mu``, ``log sigma`` and the mode logits *mode-wise*. Training uses the averaged
output, so mode ``k`` means the same thing in all four frames and the output is exactly equivariant:
``mu(g x) = g mu(x)``, ``pi(g x) = pi(x)``.

Loss (winner-take-all / hard-EM for mixture densities). With ``cost_k`` the mean Euclidean error of mode ``k`` over
the valid (node, horizon) cells of a sample and ``k* = argmin_k cost_k``::

    L = 0.3 * ( cost_{k*} + 0.05 * mean_k cost_k ) + CE(pi, k*) + 0.1 * GaussianNLL(mu_{k*}.detach(), sigma_{k*})

``mu`` is trained by the winner term, ``pi`` as a classifier of the winner, and ``sigma`` by the NLL with ``mu``
detached, so a bad ``sigma`` cannot drag the means.

Metrics (``trajectory_metrics``): ADE / FDE of the best of K per node (``minADE_K``, ``minFDE_K``), the joint
versions that pick one mode for all players (``minJADE``, ``minJFDE``), the most-probable mode, and the constant
velocity and standing-still baselines.
"""

from __future__ import annotations

import copy
import math
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable

import numpy as np
import torch
import torch.nn.functional as Fn
from torch import Tensor, nn

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train.model import NEG, gelu, signs_for

FORMAT = "tacticsml-traj-v1"
N_AGENTS = F.BALL + 1  # 22 players + ball
HORIZON = 10


@dataclass(frozen=True)
class TrajConfig:
    hidden: int = 96
    heads: int = 4
    blocks: int = 3
    mode_blocks: int = 2
    ffn: int = 192
    modes: int = 6
    horizon: int = HORIZON
    steps: int = F.T_STEPS
    n_node_feat: int = F.F_BASE
    pair_hidden: int = 16

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TrajConfig":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _attend(q: Tensor, k: Tensor, v: Tensor, bias: Tensor | None, key_mask: Tensor, heads: int) -> Tensor:
    """q,k,v (..., L, D) -> (..., L, D). ``bias`` (..., heads, L, L) or None; ``key_mask`` (..., L) 1 = real."""
    *lead, L, D = q.shape
    d = D // heads
    q, k, v = (x.reshape(*lead, L, heads, d).transpose(-3, -2) for x in (q, k, v))  # (..., heads, L, d)
    s = q @ k.transpose(-1, -2) / math.sqrt(d)
    if bias is not None:
        s = s + bias
    s = s.masked_fill(key_mask[..., None, None, :] == 0, NEG)
    return (torch.softmax(s, -1) @ v).transpose(-3, -2).reshape(*lead, L, D)


class AxialBlock(nn.Module):
    def __init__(self, cfg: TrajConfig, time_attn: bool = True):
        super().__init__()
        D = cfg.hidden
        self.heads, self.time_attn = cfg.heads, time_attn
        self.ln_a, self.qkv_a, self.out_a = nn.LayerNorm(D), nn.Linear(D, 3 * D), nn.Linear(D, D)
        if time_attn:
            self.ln_t, self.qkv_t, self.out_t = nn.LayerNorm(D), nn.Linear(D, 3 * D), nn.Linear(D, D)
        self.ln_f, self.ffn1, self.ffn2 = nn.LayerNorm(D), nn.Linear(D, cfg.ffn), nn.Linear(cfg.ffn, D)

    def forward(self, x: Tensor, bias: Tensor, mask: Tensor) -> Tensor:
        """x (B,T,N,D) [time_attn] or (B,N,D); bias (B,T,heads,N,N) / (B,heads,N,N); mask (B,N)."""
        if self.time_attn:
            B, T, N, D = x.shape
            q, k, v = self.qkv_a(self.ln_a(x)).chunk(3, -1)
            km = mask[:, None, :].expand(B, T, N)
            x = x + self.out_a(_attend(q, k, v, bias, km, self.heads))
            xt = x.transpose(1, 2)  # (B,N,T,D)
            q, k, v = self.qkv_t(self.ln_t(xt)).chunk(3, -1)
            km = torch.ones(B, N, T, device=x.device, dtype=x.dtype)
            x = x + self.out_t(_attend(q, k, v, None, km, self.heads)).transpose(1, 2)
        else:
            q, k, v = self.qkv_a(self.ln_a(x)).chunk(3, -1)
            x = x + self.out_a(_attend(q, k, v, bias, mask, self.heads))
        x = x + self.ffn2(gelu(self.ffn1(self.ln_f(x))))
        return x * mask[..., None] if not self.time_attn else x * mask[:, None, :, None]


class TrajectoryNet(nn.Module):
    def __init__(self, cfg: TrajConfig | None = None):
        super().__init__()
        self.cfg = cfg or TrajConfig()
        c = self.cfg
        D = c.hidden
        self.inp = nn.Linear(2 + 2 + 2 + 2 + 1 + c.n_node_feat, D)
        self.time_emb = nn.Parameter(torch.zeros(c.steps, D))
        nn.init.normal_(self.time_emb, std=0.02)
        self.pair1, self.pair2 = nn.Linear(3, c.pair_hidden), nn.Linear(c.pair_hidden, c.heads)
        self.blocks = nn.ModuleList(AxialBlock(c) for _ in range(c.blocks))
        self.mode_emb = nn.Parameter(torch.zeros(c.modes, D))
        nn.init.normal_(self.mode_emb, std=0.5)
        self.mode_blocks = nn.ModuleList(AxialBlock(c, time_attn=False) for _ in range(c.mode_blocks))
        self.ln_out = nn.LayerNorm(D)
        self.head = nn.Linear(D, c.horizon * 4)
        self.pi_head = nn.Linear(D, 1)
        with torch.no_grad():
            self.head.weight.mul_(0.1)
            self.head.bias.zero_()

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def pair_bias(self, pos: Tensor) -> Tensor:
        """pos (..., N, 2) -> (..., heads, N, N)"""
        rel = (pos[..., None, :, :] - pos[..., :, None, :]) / 20.0  # [i, j] = pos_j - pos_i
        feat = torch.cat([rel, rel.norm(dim=-1, keepdim=True)], -1)
        return self.pair2(gelu(self.pair1(feat))).movedim(-1, -3)

    def _single(self, batch: dict[str, Tensor], signs: Tensor | None) -> tuple[Tensor, Tensor, Tensor]:
        c = self.cfg
        pos, vel = batch["pos"], batch["vel"]
        if signs is not None:
            s = signs.to(pos.dtype)[:, None, None, :]
            pos, vel = pos * s, vel * s
        mask, nf = batch["node_mask"], batch["node_feat"]
        B, T, N, _ = pos.shape
        last = pos[:, -1:]
        step = torch.cat([torch.zeros_like(pos[:, :1]), pos[:, 1:] - pos[:, :-1]], 1) / 8.0
        static = torch.cat([vel[:, -1] / 8.0, batch["vel_mask"][..., None], nf], -1)[:, None].expand(B, T, N, -1)
        x = self.inp(torch.cat([pos / 50.0, step, (pos - last) / 20.0, static], -1)) + self.time_emb[None, :, None]
        x = x * mask[:, None, :, None]
        bias = self.pair_bias(pos)  # (B,T,heads,N,N)
        for blk in self.blocks:
            x = blk(x, bias, mask)
        g = x[:, -1]  # (B,N,D)
        K = c.modes
        y = (g[:, None] + self.mode_emb[None, :, None]).reshape(B * K, N, -1)
        mk = mask[:, None].expand(B, K, N).reshape(B * K, N)
        mb = self.pair_bias(pos[:, -1])[:, None].expand(B, K, -1, -1, -1).reshape(B * K, c.heads, N, N)
        for blk in self.mode_blocks:
            y = blk(y, mb, mk)
        y = self.ln_out(y)
        raw = self.head(y).view(B, K, N, c.horizon, 4)
        pi = (self.pi_head(y).squeeze(-1) * mk).view(B, K, N).sum(-1) / mask.sum(-1, keepdim=True)
        hs = torch.arange(1, c.horizon + 1, device=pos.device, dtype=pos.dtype)
        base = pos[:, -1][:, None, :, None] + vel[:, -1][:, None, :, None] * hs[None, None, None, :, None]
        mu = base + raw[..., :2] * (1.0 + hs)[None, None, None, :, None]
        log_sigma = torch.log((0.3 + Fn.softplus(raw[..., 2:])) * (1.0 + 0.3 * hs)[None, None, None, :, None])
        return mu, log_sigma, pi

    def forward(self, batch: dict[str, Tensor], signs: Tensor | None = None, average: bool = True
                ) -> dict[str, Tensor]:
        """``mu (B,K,N,H,2)``, ``log_sigma (B,K,N,H,2)``, ``pi_logits (B,K)`` in the input frame (nodes >= 23 are
        context only; slice ``[:, :, :N_AGENTS]``)."""
        B, dev = batch["pos"].shape[0], batch["pos"].device
        groups = [signs_for(g, B, dev) for g in F.D2] if average else [signs if signs is not None
                                                                          else signs_for("e", B, dev)]
        mus, lss, pis = [], [], []
        for s in groups:
            mu, ls, pi = self._single(batch, s)
            mus.append(mu * s.to(mu.dtype)[:, None, None, None, :])  # back to the input frame
            lss.append(ls)
            pis.append(pi)
        return {"mu": torch.stack(mus).mean(0), "log_sigma": torch.stack(lss).mean(0),
                "pi_logits": torch.stack(pis).mean(0)}


# ---------------------------------------------------------------- loss

def mode_costs(mu: Tensor, target: Tensor, valid: Tensor) -> Tensor:
    """Mean Euclidean error per mode: mu (B,K,A,H,2), target (B,H,A,2), valid (B,H,A) -> (B,K)."""
    tgt = target.permute(0, 2, 1, 3)[:, None]  # (B,1,A,H,2)
    err = (mu - tgt).norm(dim=-1)  # (B,K,A,H)
    v = valid.permute(0, 2, 1)[:, None].to(err.dtype)
    return (err * v).sum((-1, -2)) / v.sum((-1, -2)).clamp_min(1.0)


def trajectory_loss(out: dict[str, Tensor], target: Tensor, valid: Tensor, node_mask: Tensor
                    ) -> dict[str, Tensor]:
    """target (B,H,N,2) canonical future positions, valid (B,H,N) bool. Nodes beyond the 23 agents are ignored."""
    A = N_AGENTS
    mu, ls, logits = out["mu"][:, :, :A], out["log_sigma"][:, :, :A], out["pi_logits"]
    tgt, ok = target[:, :, :A], valid[:, :, :A] & (node_mask[:, None, :A] > 0)
    have = ok.any(-1).any(-1)
    if not bool(have.any()):
        z = mu.sum() * 0.0
        return {"total": z, "reg": z, "pi": z, "nll": z}
    mu, ls, logits, tgt, ok = mu[have], ls[have], logits[have], tgt[have], ok[have]
    costs = mode_costs(mu, tgt, ok)  # (B,K)
    best = costs.argmin(1)
    reg = costs.gather(1, best[:, None]).mean() + 0.05 * costs.mean()
    pi = Fn.cross_entropy(logits, best)
    idx = best[:, None, None, None, None].expand(-1, 1, mu.shape[2], mu.shape[3], 2)
    mu_b, ls_b = mu.gather(1, idx)[:, 0].detach(), ls.gather(1, idx)[:, 0]
    t = tgt.permute(0, 2, 1, 3)
    v = ok.permute(0, 2, 1)[..., None].to(mu.dtype)
    nll = ((0.5 * ((t - mu_b) / ls_b.exp()) ** 2 + ls_b) * v).sum() / (v.sum() * 2).clamp_min(1.0)
    return {"total": 0.3 * reg + pi + 0.1 * nll, "reg": reg, "pi": pi, "nll": nll}


# ---------------------------------------------------------------- metrics (numpy)

def constant_velocity(pos_last: np.ndarray, vel_last: np.ndarray, horizon: int = HORIZON) -> np.ndarray:
    """(B,N,2),(B,N,2) -> (B,H,N,2): position + velocity * h."""
    hs = np.arange(1, horizon + 1)[None, :, None, None]
    return pos_last[:, None] + vel_last[:, None] * hs


def trajectory_metrics(mu: np.ndarray, pi: np.ndarray, target: np.ndarray, valid: np.ndarray, pos_last: np.ndarray,
                       vel_last: np.ndarray, node_mask: np.ndarray) -> dict[str, float]:
    """mu (B,K,A,H,2), pi (B,K), target (B,H,N,2), valid (B,H,N). Players are nodes 0..21, the ball node 22.

    A player or ball counts at horizon ``h`` only where the future is valid. FDE needs the last horizon valid.
    """
    A = N_AGENTS
    H = mu.shape[3]
    tgt = np.transpose(target[:, :, :A], (0, 2, 1, 3))  # (B,A,H,2)
    ok = np.transpose(valid[:, :, :A] & (node_mask[:, None, :A] > 0), (0, 2, 1))  # (B,A,H)
    err = np.linalg.norm(mu - tgt[:, None], axis=-1)  # (B,K,A,H)
    cv = np.transpose(constant_velocity(pos_last, vel_last, H)[:, :, :A], (0, 2, 1, 3))
    err_cv = np.linalg.norm(cv - tgt, axis=-1)
    err_static = np.linalg.norm(pos_last[:, None, :A] - np.transpose(tgt, (0, 2, 1, 3)), axis=-1).transpose(0, 2, 1)
    last = ok[..., -1]
    players = np.arange(A) < F.BALL
    out: dict[str, float] = {}

    def mean_over(x: np.ndarray, m: np.ndarray) -> float:
        return float(x[m].mean()) if m.any() else float("nan")

    def ade(e: np.ndarray) -> np.ndarray:  # (..., A, H) -> (..., A) mean over valid h
        w = ok.astype(float)
        w = w[:, None] if e.ndim == 4 else w
        return (e * w).sum(-1) / np.maximum(w.sum(-1), 1)

    has = ok.any(-1)
    for name, sel in (("player", players), ("ball", ~players)):
        m_ade, m_fde = has & sel[None], last & sel[None]
        ade_k, fde_k = ade(err), err[..., -1]
        out[f"{name}_minADE"] = mean_over(ade_k.min(1), m_ade)
        out[f"{name}_minFDE"] = mean_over(fde_k.min(1), m_fde)
        top = pi.argmax(1)
        b = np.arange(len(top))
        out[f"{name}_top1_ADE"] = mean_over(ade_k[b, top], m_ade)
        out[f"{name}_top1_FDE"] = mean_over(fde_k[b, top], m_fde)
        out[f"{name}_cv_ADE"] = mean_over(ade(err_cv), m_ade)
        out[f"{name}_cv_FDE"] = mean_over(err_cv[..., -1], m_fde)
        out[f"{name}_static_ADE"] = mean_over(ade(err_static), m_ade)
        out[f"{name}_static_FDE"] = mean_over(err_static[..., -1], m_fde)
        out[f"{name}_minFDE_vs_cv"] = 1.0 - out[f"{name}_minFDE"] / out[f"{name}_cv_FDE"]
        out[f"{name}_top1_FDE_vs_cv"] = 1.0 - out[f"{name}_top1_FDE"] / out[f"{name}_cv_FDE"]
    # joint metrics over the players that have a full 10 s future: one mode for the whole team sheet
    pm = last & players[None]
    n_p = pm.sum(1)
    use = n_p >= 8
    if use.any():
        jade = (ade(err) * pm[:, None]).sum(-1) / np.maximum(n_p[:, None], 1)  # (B,K)
        jfde = (err[..., -1] * pm[:, None]).sum(-1) / np.maximum(n_p[:, None], 1)
        cv_j = (ade(err_cv) * pm).sum(-1) / np.maximum(n_p, 1)
        cv_jf = (err_cv[..., -1] * pm).sum(-1) / np.maximum(n_p, 1)
        out["minJADE"], out["minJFDE"] = float(jade.min(1)[use].mean()), float(jfde.min(1)[use].mean())
        out["cv_JADE"], out["cv_JFDE"] = float(cv_j[use].mean()), float(cv_jf[use].mean())
        out["minJFDE_vs_cv"] = 1.0 - out["minJFDE"] / out["cv_JFDE"]
        out["top1_JFDE"] = float(jfde[np.arange(len(pi)), pi.argmax(1)][use].mean())
    out["n_samples"] = int(has.any(1).sum())
    out["mode_usage"] = float(np.mean(np.bincount(pi.argmax(1), minlength=mu.shape[1]).max() / len(pi)))
    return out


# ---------------------------------------------------------------- training

@dataclass
class TrajTrainConfig:
    epochs: int = 12
    batch: int = 64
    lr: float = 1e-3
    weight_decay: float = 1e-2
    clip: float = 1.0
    patience: int = 4
    max_steps: int | None = None
    view_mask_p: float = 0.5
    max_hours: float = 0.9  # wall-clock budget; stops early and keeps the best checkpoint


def _batch_targets(b: dict[str, Tensor]) -> tuple[Tensor, Tensor]:
    return b["fut_pos"].permute(0, 1, 2, 3), b["fut_valid"]


@torch.no_grad()
def predict_numpy(model: TrajectoryNet, ds, idx: np.ndarray, device, batch: int = 128, average: bool = True,
                  view_rng: np.random.Generator | None = None, view_cfg=None
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """mu (n,K,A,H,2), pi (n,K), and the (possibly masked) pos_last/vel_last used, for ``idx`` of a Dataset10."""
    from matchlens.tacticsml.train.augment import view_mask_batch
    from matchlens.tacticsml.train.data10 import to_tensors10

    model.eval()
    mus, pis, last = [], [], []
    for s in range(0, len(idx), batch):
        b = ds.batch(idx[s:s + batch])
        if view_rng is not None:
            b, _ = view_mask_batch(b, view_rng, view_cfg)
        t = to_tensors10(b, device)
        o = model(t, average=average)
        mus.append(o["mu"][:, :, :N_AGENTS].cpu().numpy())
        pis.append(torch.softmax(o["pi_logits"], -1).cpu().numpy())
        last.append(np.concatenate([b["pos"][:, -1, :N_AGENTS], b["vel"][:, -1, :N_AGENTS]], -1))
    return np.concatenate(mus), np.concatenate(pis), np.concatenate(last)


def evaluate_trajectory(model: TrajectoryNet, ds, idx: np.ndarray, device, view_rng=None, view_cfg=None,
                        batch: int = 128) -> dict[str, float]:
    mu, pi, last = predict_numpy(model, ds, idx, device, batch, True, view_rng, view_cfg)
    fut = ds.a["fut_pos"][idx].astype(np.float32)
    valid = ds.a["fut_valid"][idx]
    n = len(idx)
    node_mask = ds.a["node_mask"][idx]
    # target tensors cover N nodes; pad the agents' arrays to (n,H,N,2) layout for the metric function
    pos_last = np.zeros((n, F.N_NODES, 2), np.float32)
    vel_last = np.zeros((n, F.N_NODES, 2), np.float32)
    pos_last[:, :N_AGENTS], vel_last[:, :N_AGENTS] = last[..., :2], last[..., 2:]
    return trajectory_metrics(mu, pi, fut, valid, pos_last, vel_last, node_mask)


def train_trajectory(train, val, cfg: TrajConfig | None = None, tcfg: TrajTrainConfig | None = None, seed: int = 0,
                     device: str | torch.device = "auto", log: Callable[[str], None] = print
                     ) -> tuple[TrajectoryNet, dict]:
    """Train on the samples of ``train`` (a Dataset10) whose future is available and which are open play."""
    from matchlens.tacticsml.train.augment import ViewMaskConfig, view_mask_batch
    from matchlens.tacticsml.train.data10 import to_tensors10
    from matchlens.tacticsml.train.fit import pick_device, seed_everything

    tcfg = tcfg or TrajTrainConfig()
    dev = device if isinstance(device, torch.device) else pick_device(device)
    seed_everything(seed)
    model = TrajectoryNet(cfg).to(dev)
    use = np.flatnonzero(train.a["open_play"] & train.a["fut_valid"][:, -1, F.BALL])
    vidx = np.flatnonzero(val.a["open_play"] & val.a["fut_valid"][:, -1, F.BALL])
    rng = np.random.default_rng(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=tcfg.lr, weight_decay=tcfg.weight_decay)
    spe = math.ceil(len(use) / tcfg.batch)
    total = min(tcfg.epochs * spe, tcfg.max_steps or 10 ** 12)
    warm = max(1, int(0.03 * total))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / warm if s < warm else 0.5 * (1 + math.cos(math.pi * min(1.0, (s - warm) / max(1, total - warm)))) * 0.99 + 0.01)
    vcfg = ViewMaskConfig(p_apply=tcfg.view_mask_p)
    best, best_state, bad, step, t0 = math.inf, None, 0, 0, time.time()
    hist: dict = {"train": [], "val": [], "n_params": model.n_params(), "device": str(dev), "n_train": int(len(use))}
    for epoch in range(tcfg.epochs):
        model.train()
        perm = rng.permutation(use)
        agg = {"total": 0.0, "reg": 0.0, "pi": 0.0, "nll": 0.0}
        cnt = 0
        for s in range(0, len(perm), tcfg.batch):
            b = train.batch(np.sort(perm[s:s + tcfg.batch]))
            if tcfg.view_mask_p > 0:
                b, _ = view_mask_batch(b, rng, vcfg)
            t = to_tensors10(b, dev)
            losses = trajectory_loss(model(t, average=True), t["fut_pos"], t["fut_valid"], t["node_mask"])
            opt.zero_grad(set_to_none=True)
            losses["total"].backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), tcfg.clip)
            opt.step()
            sched.step()
            step += 1
            cnt += 1
            for k in agg:
                agg[k] += float(losses[k].detach())
            if step >= total or (time.time() - t0) > tcfg.max_hours * 3600:
                break
        hist["train"].append({k: v / max(cnt, 1) for k, v in agg.items()})
        m = evaluate_trajectory(model, val, vidx[:4000], dev) if len(vidx) else {}
        hist["val"].append(m)
        msg = f"traj epoch {epoch + 1} step {step} " + " ".join(f"{k} {v:.3f}" for k, v in hist["train"][-1].items())
        if m:
            msg += f" | val player minFDE {m['player_minFDE']:.2f} top1 {m['player_top1_FDE']:.2f} cv {m['player_cv_FDE']:.2f}"
            if m["player_minFDE"] < best - 1e-4:
                best, bad = m["player_minFDE"], 0
                best_state = copy.deepcopy({k: v.detach().cpu() for k, v in model.state_dict().items()})
            else:
                bad += 1
        log(f"{msg} ({time.time() - t0:.0f}s)")
        if step >= total or bad >= tcfg.patience or (time.time() - t0) > tcfg.max_hours * 3600:
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    hist["steps"] = step
    return model, hist


def save_trajectory(model: TrajectoryNet, path: Path, metrics: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"format": FORMAT, "config": model.cfg.to_dict(),
                "state_dict": {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()},
                "metrics": metrics}, path)


def load_trajectory(path: Path, device="cpu") -> TrajectoryNet:
    blob = torch.load(path, map_location=device, weights_only=False)
    model = TrajectoryNet(TrajConfig.from_dict(blob["config"]))
    model.load_state_dict(blob["state_dict"])
    return model.to(device).eval()


def main(argv: list[str] | None = None) -> int:
    import argparse

    from matchlens.tacticsml.train import schema
    from matchlens.tacticsml.train.data10 import TRACKING_SOURCES, load_next10

    p = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train.trajectory", description=__doc__)
    p.add_argument("--shards", default=str(schema.NEXT10_SHARDS))
    p.add_argument("--sources", nargs="+", default=list(TRACKING_SOURCES))
    p.add_argument("--epochs", type=int, default=12)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--max-hours", type=float, default=0.9)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--view-mask-p", type=float, default=0.5)
    p.add_argument("--device", default="auto")
    p.add_argument("--out", default=str(schema.NEXT10 / "ckpt" / "trajectory_seed0.pt"))
    a = p.parse_args(argv)
    data = load_next10(a.sources, Path(a.shards), None)
    val = data.get("val") or data["train"]
    tcfg = TrajTrainConfig(epochs=a.epochs, batch=a.batch, max_steps=a.max_steps, max_hours=a.max_hours,
                           view_mask_p=a.view_mask_p)
    model, hist = train_trajectory(data["train"], val, None, tcfg, a.seed, a.device)
    save_trajectory(model, Path(a.out), {"history": hist})
    print(f"saved {a.out} ({model.n_params()} parameters, {hist['steps']} steps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
