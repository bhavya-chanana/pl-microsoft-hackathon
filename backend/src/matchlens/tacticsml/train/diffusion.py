"""Generative "play it out": a DDPM over the next 10 s of the whole scene (DESIGN + MINIMAL SKELETON, TRAINING ONLY).

STATUS (round 1). This module is a design and a tested skeleton: forward process, noise schedule, epsilon
denoiser, the training loss, an ancestral sampler and the D2 frame-averaged noise prediction. It has NOT been
trained on real tracking data; full training, guidance and evaluation (against ``trajectory.py`` on minJADE /
minJFDE and on diversity) are the round-2 job. Nothing in the serving path imports it.

Why a second forecaster. ``trajectory.TrajectoryNet`` returns K = 6 modes: a coarse, fast, calibrated summary. A
coach asking "play it out" wants many coherent, physically plausible 10 s continuations of *both* teams and the
ball, the way a video model rolls a clip forward. A conditional diffusion model gives that: samples from the joint
distribution of all 23 agents' paths, not the K cluster centres.

Model. Let ``c`` be the conditioning (the last 5 s of the canonical graph, node features, observed flags) and
``x_0 in R^{A x H x 2}`` the future of the ``A = 23`` agents (22 players + ball) at ``H = 10`` horizons, expressed
as the displacement from the last position in units of 20 m: ``x_0 = (fut - pos_T) / 20``.

    forward   q(x_s | x_0) = N( sqrt(abar_s) x_0 , (1 - abar_s) I ),   abar_s = prod_{u<=s} (1 - beta_u)
    schedule  cosine: abar_s = cos^2( (s/S + 0.008) / 1.008 * pi/2 ) / cos^2( 0.008 / 1.008 * pi/2 )
    train     L = E_{x_0,c,s,eps} [ mean_valid || eps - eps_theta(x_s, s, c) ||^2 ],   x_s = sqrt(abar_s) x_0 + sqrt(1-abar_s) eps
    sample    x_{s-1} = 1/sqrt(alpha_s) ( x_s - beta_s / sqrt(1 - abar_s) eps_theta ) + sigma_s z,   sigma_s^2 = beta~_s
              (posterior variance), then fut = pos_T + 20 x_0

``valid`` masks (node, horizon) cells without a future (players that left the view / data end) and padded nodes;
masked cells are zeroed in the input and ignored in the loss. The denoiser is a small axial-attention
network (the same ``AxialBlock`` as the forecaster: agent attention with a relative-position bias at every horizon,
and attention over the horizon axis), with the history summarised per node and added to every horizon token, plus
a sinusoidal embedding of the diffusion step.

Symmetry. The pitch has the dihedral symmetry D2 (mirror x, mirror y). If the conditional law of the future
is D2 equivariant, ``p(g x_0 | g c) = p(x_0 | c)``, it is enough that the noise predictor is equivariant,
``eps_theta(g x_s, s, g c) = g eps_theta(x_s, s, c)``, because the Gaussian noise is isotropic: the sampler then
produces an equivariant law. ``predict_eps(..., average=True)`` enforces it by frame averaging,
``eps = 1/4 sum_g g^{-1} eps_theta(g x_s, s, g c)``, exactly like the other heads (tested to < 1e-5 in float64).

Round-2 plan. (1) Train on sportec / skillcorner / metrica futures with the view-mask augmentation on the
conditioning. (2) Evaluate with minJADE/minJFDE over S = 20 samples and a diversity score (mean pairwise joint
displacement) against ``trajectory.py`` modes, plus a physical-plausibility check (speed <= 12 m/s, ball speed
<= 40 m/s). (3) Classifier-free guidance on a tactical prompt (team style, "press high") by dropping ``c`` with
probability 0.1. (4) A DDIM sampler (10-20 steps) for interactive latency. Serving would need a numpy mirror
like ``infer_next10`` (not written: until the model is trained there is nothing to serve).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import Tensor, nn

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train.model import gelu, signs_for
from matchlens.tacticsml.train.trajectory import AxialBlock, HORIZON, N_AGENTS, TrajConfig

SCALE_M = 20.0  # metres per unit of x_0


# ---------------------------------------------------------------- noise schedule

@dataclass(frozen=True)
class DiffusionConfig:
    steps: int = 100  # S
    hidden: int = 96
    heads: int = 4
    blocks: int = 3
    ffn: int = 192
    pair_hidden: int = 16
    n_node_feat: int = F.F_BASE
    history: int = F.T_STEPS
    horizon: int = HORIZON
    cond_drop: float = 0.0  # classifier-free-guidance dropout of the conditioning (round 2)


def cosine_alpha_bar(steps: int, s: float = 0.008, dtype=torch.float64) -> Tensor:
    """``abar_0..abar_S`` (S + 1 values, abar_0 = 1)."""
    u = torch.arange(steps + 1, dtype=dtype) / steps
    f = torch.cos((u + s) / (1 + s) * math.pi / 2) ** 2
    return f / f[0]


class Schedule:
    """Precomputed tensors for ``q_sample`` and the ancestral sampler (index ``s`` = 1..S, stored 0-based)."""

    def __init__(self, steps: int, dtype=torch.float32):
        ab = cosine_alpha_bar(steps)
        beta = (1.0 - ab[1:] / ab[:-1]).clamp(1e-5, 0.999)  # beta_1..beta_S
        alpha = 1.0 - beta
        abar = torch.cumprod(alpha, 0)
        abar_prev = torch.cat([torch.ones(1, dtype=abar.dtype), abar[:-1]])
        self.steps = steps
        self.beta, self.alpha, self.abar = (x.to(dtype) for x in (beta, alpha, abar))
        self.post_var = ((1.0 - abar_prev) / (1.0 - abar) * beta).clamp(min=1e-20).to(dtype)

    def to(self, device) -> "Schedule":
        for k in ("beta", "alpha", "abar", "post_var"):
            setattr(self, k, getattr(self, k).to(device))
        return self


def q_sample(x0: Tensor, s: Tensor, noise: Tensor, sch: Schedule) -> Tensor:
    """x_s for 0-based step index ``s`` (B,) in [0, S): ``sqrt(abar) x0 + sqrt(1 - abar) noise``."""
    ab = sch.abar[s].view(-1, *([1] * (x0.dim() - 1)))
    return ab.sqrt() * x0 + (1.0 - ab).sqrt() * noise


# ---------------------------------------------------------------- denoiser

def step_embedding(s: Tensor, dim: int, dtype=torch.float32) -> Tensor:
    half = dim // 2
    freq = torch.exp(-math.log(10000.0) * torch.arange(half, device=s.device, dtype=torch.float32) / half)
    a = s.float()[:, None] * freq[None]
    return torch.cat([a.sin(), a.cos()], -1).to(dtype)


class Denoiser(nn.Module):
    """``eps_theta(x_s, s, c)``: x_s (B,A,H,2), batch of the critic's keys, s (B,) -> (B,A,H,2)."""

    def __init__(self, cfg: DiffusionConfig | None = None):
        super().__init__()
        self.cfg = cfg or DiffusionConfig()
        c = self.cfg
        D = c.hidden
        tcfg = TrajConfig(hidden=D, heads=c.heads, blocks=c.blocks, ffn=c.ffn, steps=c.horizon, horizon=c.horizon,
                          pair_hidden=c.pair_hidden, n_node_feat=c.n_node_feat)
        self.hist = nn.Linear(c.history * 2 + 2 + 2 + 1 + c.n_node_feat, D)
        self.inp = nn.Linear(2, D)
        self.h_emb = nn.Parameter(torch.zeros(c.horizon, D))
        nn.init.normal_(self.h_emb, std=0.02)
        self.s1, self.s2 = nn.Linear(D, D), nn.Linear(D, D)
        self.pair1, self.pair2 = nn.Linear(3, c.pair_hidden), nn.Linear(c.pair_hidden, c.heads)
        self.blocks = nn.ModuleList(AxialBlock(tcfg) for _ in range(c.blocks))
        self.ln = nn.LayerNorm(D)
        self.out = nn.Linear(D, 2)
        with torch.no_grad():
            self.out.weight.mul_(0.1)
            self.out.bias.zero_()

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def _pair_bias(self, pos: Tensor) -> Tensor:
        rel = (pos[:, None, :] - pos[:, :, None]) / 20.0  # (B,A,A,2) [i, j] = pos_j - pos_i
        feat = torch.cat([rel, rel.norm(dim=-1, keepdim=True)], -1)
        return self.pair2(gelu(self.pair1(feat))).movedim(-1, 1)  # (B,heads,A,A)

    def _single(self, xs: Tensor, s: Tensor, batch: dict[str, Tensor], signs: Tensor | None) -> Tensor:
        c = self.cfg
        A = N_AGENTS
        pos, vel = batch["pos"][:, :, :A], batch["vel"][:, -1, :A]
        if signs is not None:
            sg = signs.to(pos.dtype)
            pos, vel, xs = pos * sg[:, None, None], vel * sg[:, None], xs * sg[:, None, None]
        mask = batch["node_mask"][:, :A]
        B = pos.shape[0]
        last = pos[:, -1]
        hist = ((pos - last[:, None]) / 20.0).permute(0, 2, 1, 3).reshape(B, A, -1)
        cond = self.hist(torch.cat([hist, last / 50.0, vel / 8.0, batch["vel_mask"][:, :A, None],
                                    batch["node_feat"][:, :A]], -1))  # (B,A,D)
        t = self.s2(gelu(self.s1(step_embedding(s, c.hidden, pos.dtype))))  # (B,D)
        x = self.inp(xs.transpose(1, 2)) + cond[:, None] + self.h_emb[None, :, None] + t[:, None, None]  # (B,H,A,D)
        x = x * mask[:, None, :, None]
        bias = self._pair_bias(last)[:, None].expand(B, c.horizon, -1, -1, -1)
        for blk in self.blocks:
            x = blk(x, bias, mask)
        eps = self.out(self.ln(x)).transpose(1, 2)  # (B,A,H,2)
        if signs is not None:
            eps = eps * sg[:, None, None]  # back to the input frame
        return eps

    def forward(self, xs: Tensor, s: Tensor, batch: dict[str, Tensor], average: bool = False) -> Tensor:
        """``average=True``: D2 frame-averaged noise prediction, exactly equivariant (see the module docstring)."""
        if not average:
            return self._single(xs, s, batch, None)
        B, dev = xs.shape[0], xs.device
        return torch.stack([self._single(xs, s, batch, signs_for(g, B, dev)) for g in F.D2]).mean(0)


# ---------------------------------------------------------------- training and sampling

def future_to_x0(fut: Tensor, valid: Tensor, batch: dict[str, Tensor]) -> tuple[Tensor, Tensor]:
    """fut (B,H,N,2) absolute, valid (B,H,N) -> x0 (B,A,H,2) displacement / 20 m and its validity (B,A,H)."""
    A = N_AGENTS
    last = batch["pos"][:, -1, :A]
    x0 = (fut[:, :, :A].permute(0, 2, 1, 3) - last[:, :, None]) / SCALE_M
    ok = valid[:, :, :A].permute(0, 2, 1).bool() & (batch["node_mask"][:, None, :A].transpose(1, 2) > 0)
    return torch.where(ok[..., None], x0, torch.zeros_like(x0)), ok


def ddpm_loss(model: Denoiser, sch: Schedule, x0: Tensor, ok: Tensor, batch: dict[str, Tensor],
              generator: torch.Generator | None = None, average: bool = False) -> Tensor:
    """Epsilon-prediction MSE over the valid cells, one random step per sample."""
    B = x0.shape[0]
    s = torch.randint(0, sch.steps, (B,), device=x0.device, generator=generator)
    noise = torch.randn(x0.shape, device=x0.device, dtype=x0.dtype, generator=generator)
    xs = q_sample(x0, s, noise, sch) * ok[..., None]
    pred = model(xs, s, batch, average=average)
    err = ((pred - noise) ** 2).sum(-1) * ok
    return err.sum() / ok.sum().clamp(min=1)


@torch.no_grad()
def sample(model: Denoiser, sch: Schedule, batch: dict[str, Tensor], n_samples: int = 4,
           generator: torch.Generator | None = None, average: bool = False) -> Tensor:
    """Ancestral DDPM sampling. Returns absolute positions ``(B, S, A, H, 2)`` in the input frame (metres)."""
    was = model.training
    model.eval()
    B, A, H = batch["pos"].shape[0], N_AGENTS, model.cfg.horizon
    dev, dt = batch["pos"].device, batch["pos"].dtype
    rep = {k: v.repeat_interleave(n_samples, 0) for k, v in batch.items() if torch.is_tensor(v)}
    mask = rep["node_mask"][:, :A]
    x = torch.randn((B * n_samples, A, H, 2), device=dev, dtype=dt, generator=generator) * mask[..., None, None]
    for i in reversed(range(sch.steps)):
        s = torch.full((B * n_samples,), i, device=dev, dtype=torch.long)
        eps = model(x, s, rep, average=average)
        mean = (x - sch.beta[i] / (1.0 - sch.abar[i]).sqrt() * eps) / sch.alpha[i].sqrt()
        noise = torch.randn(x.shape, device=dev, dtype=dt, generator=generator) if i > 0 else torch.zeros_like(x)
        x = (mean + sch.post_var[i].sqrt() * noise) * mask[..., None, None]
    model.train(was)
    last = rep["pos"][:, -1, :A]
    fut = last[:, :, None] + x * SCALE_M
    return fut.view(B, n_samples, A, H, 2)


def play_out(model: Denoiser, batch: dict[str, Tensor], n_samples: int = 8, seed: int = 0, average: bool = False) -> dict[str, Tensor]:
    """"Play it out": ``n_samples`` coherent joint continuations of the scene from ``batch`` (reproducible by ``seed``).

    Returns ``paths (B, S, A, H, 2)`` (metres, input frame) and ``spread (B, A)``, the mean over horizons of the
    standard deviation of each agent across the samples (a cheap uncertainty read-out)."""
    dev = batch["pos"].device
    gen = torch.Generator(device=dev).manual_seed(seed)
    sch = Schedule(model.cfg.steps, batch["pos"].dtype).to(dev)
    paths = sample(model, sch, batch, n_samples, gen, average)
    return {"paths": paths, "spread": paths.std(1).norm(dim=-1).mean(-1)}
