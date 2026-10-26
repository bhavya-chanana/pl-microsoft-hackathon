"""Conditional VAE that proposes repositionings on top of the frozen ``TacticsGAT`` encoder.

TRAINING ONLY (imports torch).

* ``q(z_i | graph, target positions, condition)``: one 8-d latent per node (TacticAI-style).
* ``p(dpos | graph, z, condition)``: per-node displacement, canonical metres, in the frame of the input
  graph. Nodes that are locked, not on the moved team, ball, goals or pad get exactly zero. The raw
  network output is passed through a smooth norm clamp ``d * R*tanh(|d|/R)/|d|`` with
  ``R = min(top_speed * horizon, max_move)`` (default ``8.8 m/s * 3 s``), so ``|dpos| < R`` always.
* Equivariance: with ``average=True`` the encoder and decoder run on all four D2 reflections with the
  same ``z``; each displacement is reflected back and the four are averaged, so
  ``sample(reflect(graph))`` equals ``reflect(sample(graph))`` for the same ``z``.

Condition: target one-hot ``{shot, retain, press, receiver}``, receiver node one-hot, team mode
``{attack, defence, both}`` (attack = possession team, nodes 0..10) and a locked mask.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

import torch
import torch.nn.functional as Fn
from torch import Tensor, nn

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train.model import N_SLOTS, POS_SCALE, TacticsGAT, gelu, signs_for

TARGETS = ("shot", "retain", "press", "receiver")
TEAMS = ("attack", "defence", "both")
FORMAT = "tacticsml-cvae-v1"
DISP_SCALE = 5.0  # raw decoder output is multiplied by this (metres)
N_PLAYERS = F.BALL  # nodes 0..21 can move


@dataclass(frozen=True)
class CVAEConfig:
    z_dim: int = 8
    hidden: int = 128
    q_hidden: int = 64
    horizon: float = 3.0  # seconds a player may run
    default_top_speed: float = 8.8
    kl_beta: float = 0.1
    guide_weight: float = 0.05  # weight of the frozen-predictor term in the loss

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CVAEConfig":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Condition:
    """Per-graph conditioning, all tensors on the same device. ``B`` graphs, ``N`` nodes."""

    target: Tensor  # (B,) long index into TARGETS
    team: Tensor  # (B,) long index into TEAMS
    receiver: Tensor  # (B,) long node index (0..10) for target "receiver", else -1
    locked: Tensor  # (B,N) 0/1
    top_speed: Tensor | None = None  # (B,N) m/s, default CVAEConfig.default_top_speed

    @classmethod
    def make(cls, graph: dict[str, Tensor], target: str = "shot", team: str = "both",
             receiver: int = -1, locked: Tensor | None = None) -> "Condition":
        B, N = graph["node_mask"].shape
        dev = graph["node_mask"].device
        return cls(
            target=torch.full((B,), TARGETS.index(target), dtype=torch.long, device=dev),
            team=torch.full((B,), TEAMS.index(team), dtype=torch.long, device=dev),
            receiver=torch.full((B,), receiver, dtype=torch.long, device=dev),
            locked=torch.zeros(B, N, device=dev) if locked is None else locked.float().to(dev))

    def repeat(self, n: int) -> "Condition":
        r = lambda t: None if t is None else t.repeat_interleave(n, dim=0)  # noqa: E731
        return Condition(r(self.target), r(self.team), r(self.receiver), r(self.locked), r(self.top_speed))


def movable_mask(node_mask: Tensor, team: Tensor, locked: Tensor) -> Tensor:
    """(B,N) 1 for nodes the cVAE may move: moved-team players, present, not locked."""
    N = node_mask.shape[1]
    idx = torch.arange(N, device=node_mask.device)
    attack = (idx < F.N_TEAM).float()
    defence = ((idx >= F.N_TEAM) & (idx < N_PLAYERS)).float()
    both = attack + defence
    by_team = torch.stack([attack, defence, both])[team]  # (B,N)
    return by_team * node_mask * (1.0 - locked)


def smooth_clamp(raw: Tensor, radius: Tensor) -> Tensor:
    """Vector clamp: output norm is ``radius * tanh(|raw| / radius)`` < radius, direction kept."""
    n = raw.norm(dim=-1, keepdim=True).clamp_min(1e-8)
    r = radius[..., None].clamp_min(1e-6)
    return raw * (r * torch.tanh(n / r) / n)


class ConditionalVAE(nn.Module):
    def __init__(self, encoder: TacticsGAT, cfg: CVAEConfig | None = None):
        super().__init__()
        self.cfg = cfg or CVAEConfig()
        c = self.cfg
        self.encoder = encoder
        for p in self.encoder.parameters():
            p.requires_grad_(False)
        H, E, Z = encoder.cfg.hidden, encoder.cfg.emb_dim, c.z_dim
        g_dim = len(TARGETS) + len(TEAMS)
        n_dim = 5  # movable, locked, receiver one-hot, pos x, pos y
        self.q_node = nn.Linear(H + 2 + n_dim + g_dim, c.q_hidden)
        self.q_out = nn.Linear(c.q_hidden, 2 * Z)
        self.dec1 = nn.Linear(H + E + Z + n_dim + g_dim, c.hidden)
        self.dec2 = nn.Linear(c.hidden, c.hidden)
        self.dec3 = nn.Linear(c.hidden, 2)

    def train(self, mode: bool = True):  # the encoder stays in eval mode (no dropout)
        super().train(mode)
        self.encoder.eval()
        return self

    def cvae_state_dict(self) -> dict[str, Tensor]:
        return {k: v for k, v in self.state_dict().items() if not k.startswith("encoder.")}

    # ------------------------------------------------------------ pieces
    def _cond_feats(self, graph: dict, cond: Condition, signs: Tensor | None) -> tuple[Tensor, Tensor, Tensor]:
        mask = graph["node_mask"]
        B, N = mask.shape
        mov = movable_mask(mask, cond.team, cond.locked)
        rec = torch.zeros(B, N, device=mask.device)
        ok = cond.receiver >= 0
        if bool(ok.any()):
            rec[ok, cond.receiver[ok]] = 1.0
        pos = graph["pos"][:, -1]
        if signs is not None:
            pos = pos * signs[:, None, :]
        node = torch.cat([mov[..., None], cond.locked[..., None], rec[..., None], pos / POS_SCALE], dim=-1)
        glob = torch.cat([Fn.one_hot(cond.target, len(TARGETS)), Fn.one_hot(cond.team, len(TEAMS))], -1).float()
        return node, glob, mov

    def _context(self, graph: dict, signs: Tensor | None) -> tuple[Tensor, Tensor]:
        with torch.no_grad():
            return self.encoder.node_states(graph, signs)

    def _radius(self, graph: dict, cond: Condition, max_move: float | None) -> Tensor:
        ts = cond.top_speed
        if ts is None:
            ts = torch.full_like(graph["node_mask"], self.cfg.default_top_speed)
        r = ts * self.cfg.horizon
        return r if max_move is None else r.clamp(max=max_move)

    def posterior(self, h: Tensor, node: Tensor, glob: Tensor, delta: Tensor) -> tuple[Tensor, Tensor]:
        """Per-node ``(mu, logvar)`` each ``(B,N,Z)``."""
        B, N, _ = h.shape
        x = torch.cat([h, delta / 10.0, node, glob[:, None].expand(B, N, -1)], dim=-1)
        mu, logvar = self.q_out(gelu(self.q_node(x))).chunk(2, dim=-1)
        return mu, logvar.clamp(-8.0, 4.0)

    def _decode_raw(self, h: Tensor, emb: Tensor, node: Tensor, glob: Tensor, z: Tensor) -> Tensor:
        B, N, _ = h.shape
        x = torch.cat([h, emb[:, None].expand(B, N, -1), z, node,
                       glob[:, None].expand(B, N, -1)], dim=-1)
        return self.dec3(gelu(self.dec2(gelu(self.dec1(x))))) * DISP_SCALE

    def decode(self, graph: dict, cond: Condition, z: Tensor, average: bool = True,
               signs: Tensor | None = None, max_move: float | None = None) -> Tensor:
        """(B,N,2) displacement for per-node latents ``z`` (B,N,Z)."""
        B = z.shape[0]
        sgs = [signs] if not average else [signs_for(g, B, z.device) for g in F.D2]
        raws = []
        for s in sgs:
            h, emb = self._context(graph, s)
            node, glob, mov = self._cond_feats(graph, cond, s)
            raw = self._decode_raw(h, emb, node, glob, z)
            raws.append(raw if s is None else raw * s[:, None, :])  # back to the input frame
        raw = torch.stack(raws).mean(0)
        _, _, mov = self._cond_feats(graph, cond, None)
        return smooth_clamp(raw, self._radius(graph, cond, max_move)) * mov[..., None]

    @torch.no_grad()
    def sample(self, graph: dict[str, Tensor], condition: Condition, n: int = 8,
               max_move: float | None = None, generator: torch.Generator | None = None,
               average: bool = True) -> Tensor:
        """``n`` displacement proposals per graph: ``(n, B, N, 2)`` metres in the input frame."""
        was = self.training
        self.eval()
        B = graph["node_mask"].shape[0]
        dev = graph["node_mask"].device
        outs = []
        for _ in range(n):
            z = torch.randn(B, graph["node_mask"].shape[1], self.cfg.z_dim, generator=generator).to(dev)
            outs.append(self.decode(graph, condition, z, average=average, max_move=max_move))
        self.train(was)
        return torch.stack(outs)

    # ------------------------------------------------------------ training
    def forward(self, graph: dict, cond: Condition, target_delta: Tensor, signs: Tensor | None = None,
                max_move: float | None = None) -> dict[str, Tensor]:
        """One reparameterised pass in the frame given by ``signs``. ``target_delta`` (B,N,2) is in the
        unreflected input frame."""
        h, emb = self._context(graph, signs)
        node, glob, mov = self._cond_feats(graph, cond, signs)
        delta = target_delta if signs is None else target_delta * signs[:, None, :]
        mu, logvar = self.posterior(h, node, glob, delta)
        z = mu + torch.randn_like(mu) * torch.exp(0.5 * logvar)
        raw = self._decode_raw(h, emb, node, glob, z)
        pred = smooth_clamp(raw, self._radius(graph, cond, max_move)) * mov[..., None]
        return {"pred": pred, "mu": mu, "logvar": logvar, "movable": mov, "delta": delta}


def _kl(mu: Tensor, logvar: Tensor, mov: Tensor) -> Tensor:
    """KL per movable node (nats), averaged over movable nodes."""
    kl = -0.5 * (1 + logvar - mu.pow(2) - logvar.exp()).sum(-1)
    return (kl * mov).sum() / mov.sum().clamp_min(1.0)


def guide_loss(encoder: TacticsGAT, graph: dict, cond: Condition, pred_delta: Tensor,
               signs: Tensor | None) -> Tensor:
    """Frozen-predictor term: the moved scene should look good for the requested target.

    ``pred_delta`` is in the reflected frame ``signs`` (so it can be added to the reflected positions).
    """
    s = signs[:, None, None, :] if signs is not None else 1.0
    pos = graph["pos"] * s
    vel = graph["vel"] * s
    last = pos[:, -1] + pred_delta
    pos = torch.cat([pos[:, :-1], last[:, None]], dim=1)
    d = torch.sqrt(((last[:, :, None] - last[:, None]) ** 2).sum(-1) + 1e-6) / 10.0
    nm = graph["node_mask"]
    ef = graph["edge_feat"].clone()
    ef[..., F.E_DISTANCE] = d * nm[:, :, None] * nm[:, None, :]
    moved = {**graph, "pos": pos, "vel": vel, "edge_feat": ef}
    out = encoder(moved, signs=None, average=False)
    shot, turn = out["outcome_logits"][:, 0], out["outcome_logits"][:, 3]
    logp_recv = torch.log_softmax(out["receiver_logits"], dim=-1)
    rec = cond.receiver.clamp_min(0)
    per = torch.stack([
        -Fn.logsigmoid(shot),  # shot: raise shot probability
        -Fn.logsigmoid(-turn),  # retain: lower turnover probability
        -Fn.logsigmoid(turn),  # press: raise turnover probability
        -logp_recv.gather(1, rec[:, None]).squeeze(1),  # receiver: raise that node's probability
    ], dim=-1)
    return per.gather(1, cond.target[:, None]).mean()


def cvae_loss(model: ConditionalVAE, graph: dict, cond: Condition, target_delta: Tensor,
              beta: float | None = None, guide_weight: float | None = None,
              signs: Tensor | None = None) -> dict[str, Tensor]:
    out = model(graph, cond, target_delta, signs=signs)
    mov = out["movable"]
    err = ((out["pred"] - out["delta"]) ** 2).sum(-1) * mov  # m^2 per movable node
    recon = err.sum() / mov.sum().clamp_min(1.0) / (DISP_SCALE ** 2)
    kl = _kl(out["mu"], out["logvar"], mov)
    b = model.cfg.kl_beta if beta is None else beta
    gw = model.cfg.guide_weight if guide_weight is None else guide_weight
    guide = guide_loss(model.encoder, graph, cond, out["pred"], signs) if gw > 0 else recon * 0.0
    return {"total": recon + b * kl + gw * guide, "recon": recon, "kl": kl, "guide": guide}


def make_training_condition(graph: dict[str, Tensor], gen: torch.Generator | None = None
                            ) -> Condition:
    """Random condition from the labels carried by ``graph`` (receiver_idx, shot10, turn5).

    Target is drawn per sample among the labelled outcomes: shot10==1 -> shot, receiver in 0..10 ->
    receiver, turn5==1 -> press, otherwise retain. Team mode and a random 0-30% locked set are random.
    """
    B, N = graph["node_mask"].shape
    dev = graph["node_mask"].device

    def rnd(*shape):
        return torch.rand(*shape, generator=gen).to(dev)

    ru = rnd(B)
    shot = graph.get("shot10", torch.full((B,), -1, device=dev)) == 1
    turn = graph.get("turn5", torch.full((B,), -1, device=dev)) == 1
    recv_lbl = graph.get("receiver_idx", torch.full((B,), -1, device=dev))
    has_recv = (recv_lbl >= 0) & (recv_lbl < N_SLOTS)
    target = torch.full((B,), TARGETS.index("retain"), dtype=torch.long, device=dev)
    target[turn] = TARGETS.index("press")
    target[shot] = TARGETS.index("shot")
    pick_recv = has_recv & (ru < 0.5)
    target[pick_recv] = TARGETS.index("receiver")
    receiver = torch.where(pick_recv, recv_lbl, torch.full_like(recv_lbl, -1))
    team = (rnd(B) * len(TEAMS)).long().clamp(max=len(TEAMS) - 1)
    locked = (rnd(B, N) < 0.3 * rnd(B, 1)).float() * graph["node_mask"]
    return Condition(target, team, receiver, locked)


def jitter_target(graph: dict[str, Tensor], cond: Condition, sigma: float = 2.5,
                  gen: torch.Generator | None = None) -> tuple[dict[str, Tensor], Tensor]:
    """Denoising fallback when shards carry no future positions.

    Movable players are displaced by Gaussian noise in the context graph (all history steps) and the
    target displacement restores the original last-step positions.
    """
    mov = movable_mask(graph["node_mask"], cond.team, cond.locked)
    noise = torch.randn(graph["pos"].shape[0], graph["pos"].shape[2], 2, generator=gen).to(mov.device)
    noise = noise * sigma * mov[..., None]
    ctx = {**graph, "pos": graph["pos"] + noise[:, None]}
    last = ctx["pos"][:, -1]
    nm = graph["node_mask"]
    ef = graph["edge_feat"].clone()
    d = torch.sqrt(((last[:, :, None] - last[:, None]) ** 2).sum(-1) + 1e-6) / 10.0
    ef[..., F.E_DISTANCE] = d * nm[:, :, None] * nm[:, None, :]
    ctx["edge_feat"] = ef
    return ctx, -noise
