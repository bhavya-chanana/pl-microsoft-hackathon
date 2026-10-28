"""The Next10 critic: ``TacticsGAT`` + FiLM team conditioning + ball-zone / possession / action / receiver-shot heads.

TRAINING ONLY (torch). It subclasses ``model.TacticsGAT`` and changes nothing in it except that FiLM is applied
through the ``_post_layer`` hook. Old checkpoints load into it with ``strict=False`` (``load_critic``); a
checkpoint of this class does not load into the old class (extra keys), which is intended.

FiLM (feature-wise linear modulation) on a team embedding. For the possession team ``a`` and opponent ``b``::

    c = concat[ E[a] + S_proj(style[a] * mask[a]),  E[b] + S_proj(style[b] * mask[b]) ]      (2 d_team)
    (gamma_l, beta_l) = W_l c            (W_l: Linear(2 d_team, 2 H), NO bias, one per GAT layer l)
    h <- (h * (1 + gamma_l) + beta_l) * node_mask

``E[0]`` is the all-zero "unknown team" row and ``style[0] = 0``, so an unknown team gives ``c = 0`` and FiLM is
exactly the identity: a club the model has never seen gets the plain critic. Training replaces each team id by 0
with probability ``team_dropout`` so that path is exercised. ``W_l`` is zero-initialised.

Outputs of ``forward(batch, average=True)``: the base ``receiver_logits`` (B,12), ``outcome_logits`` (B,4),
``embedding`` and, averaged over the four D2 reflections after mapping each back to the input frame:
``zone_logits (B,12,16)`` (grid flipped back), ``poss10_logit (B,)``, ``action_logits (B,4)`` and
``shot_cond_logits (B,12)``. Logits are averaged, probabilities are computed from the averages, so every
probability is D2 invariant (zone: equivariant) exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch
from torch import Tensor, nn

from matchlens.tacticsml import features as F
from matchlens.tacticsml import next10_common as C
from matchlens.tacticsml.train import heads as H
from matchlens.tacticsml.train.model import ModelConfig, TacticsGAT, gelu, signs_for

FORMAT = "tacticsml-next10-v1"


@dataclass(frozen=True)
class Next10Config(ModelConfig):
    film: bool = True
    n_teams: int = 1  # vocabulary size including the unknown slot 0
    team_dim: int = 16
    n_style: int = len(C.STYLE_KEYS)
    zone_dim: int = 96
    team_dropout: float = 0.3  # training only


class Next10GAT(TacticsGAT):
    def __init__(self, cfg: Next10Config | None = None):
        cfg = cfg or Next10Config()
        super().__init__(cfg)
        c = self.cfg
        self.team_emb = nn.Embedding(c.n_teams, c.team_dim, padding_idx=0)
        self.style_proj = nn.Linear(c.n_style, c.team_dim, bias=False)
        self.register_buffer("style", torch.zeros(c.n_teams, c.n_style))
        self.register_buffer("style_mask", torch.zeros(c.n_teams, c.n_style))
        if c.film:
            self.film = nn.ModuleList(nn.Linear(2 * c.team_dim, 2 * c.hidden, bias=False) for _ in range(c.layers))
            for lin in self.film:
                nn.init.zeros_(lin.weight)
        self.zone = H.BallZoneHead(c.hidden, c.emb_dim, c.zone_dim)
        self.poss10_head = H.mlp(c.emb_dim, c.emb_dim, 1)
        self.action_head = H.mlp(c.emb_dim, c.emb_dim, len(H.ACTIONS))
        self.shot_head = H.ReceiverShotHead(c.hidden, c.emb_dim)

    # ------------------------------------------------------------ team conditioning
    def set_team_table(self, table: C.TeamTable) -> None:
        if table.size != self.cfg.n_teams:
            raise ValueError(f"table has {table.size} rows, config n_teams={self.cfg.n_teams}")
        self.style.copy_(torch.as_tensor(table.style))
        self.style_mask.copy_(torch.as_tensor(table.style_mask))

    def team_vector(self, ids: Tensor) -> Tensor:
        """(B,2) team ids -> (B, 2 * team_dim); id 0 maps to exactly zero."""
        e = self.team_emb(ids) + self.style_proj(self.style[ids] * self.style_mask[ids])
        return (e * (ids > 0)[..., None]).flatten(1)

    def _post_layer(self, index: int, h: Tensor, batch: dict[str, Tensor], mask: Tensor) -> Tensor:
        if not self.cfg.film or "team_ids" not in batch:
            return h
        gamma, beta = self.film[index](self.team_vector(batch["team_ids"])).chunk(2, dim=-1)
        return (h * (1.0 + gamma[:, None]) + beta[:, None]) * mask[..., None]

    # ------------------------------------------------------------ heads
    def _extras(self, batch: dict[str, Tensor], signs: Tensor, h: Tensor, recv: Tensor, out: Tensor, emb: Tensor
                ) -> dict[str, Tensor]:
        z = gelu(emb)
        ball_xy = batch["pos"][:, -1, F.BALL] * signs.to(batch["pos"].dtype)
        carrier = (batch["node_feat"][..., F.F_IS_CARRIER:F.F_IS_CARRIER + 1] * h).sum(1)
        zone = self.zone(emb, h[:, F.BALL], ball_xy)
        return {"zone_logits": H.reflect_grid(zone, signs),  # back to the input frame
                "poss10_logit": self.poss10_head(z).squeeze(-1), "action_logits": self.action_head(z),
                "shot_cond_logits": self.shot_head(h[:, :F.N_TEAM], carrier, emb, out[:, 0])}

    def forward(self, batch: dict[str, Tensor], signs: Tensor | None = None, average: bool = False
                ) -> dict[str, Tensor]:
        B, dev = batch["pos"].shape[0], batch["pos"].device
        groups = [signs_for(g, B, dev) for g in F.D2] if average else [signs if signs is not None
                                                                          else signs_for("e", B, dev)]
        runs = []
        for s in groups:
            h, recv, out, emb = self._forward_single(batch, s)
            ex = self._extras(batch, s, h, recv, out, emb)
            runs.append({"receiver_logits": recv, "outcome_logits": out, "embedding": emb, **ex})
        res = {k: torch.stack([r[k] for r in runs]).mean(0) for k in runs[0]}
        res["h"] = None
        res["shot_marg_prob"] = H.marginal_shot_prob(res["receiver_logits"], res["shot_cond_logits"])
        return res

    @torch.no_grad()
    def predict(self, batch: dict[str, Tensor], average: bool = True) -> dict[str, Tensor]:
        was = self.training
        self.eval()
        o = self.forward(batch, average=average)
        self.train(was)
        p = {"receiver_probs": torch.softmax(o["receiver_logits"], -1), "embedding": o["embedding"],
             "zone_probs": torch.softmax(o["zone_logits"].flatten(1), -1).view_as(o["zone_logits"]),
             "poss10": torch.sigmoid(o["poss10_logit"]), "action_probs": torch.softmax(o["action_logits"], -1),
             "shot_given": torch.sigmoid(o["shot_cond_logits"]), "shot_marg": o["shot_marg_prob"]}
        for i, name in enumerate(("shot10", "goal10", "concede10", "turn5")):
            p[name] = torch.sigmoid(o["outcome_logits"][:, i])
        return p

    @torch.no_grad()
    def init_heads(self, rates: dict[str, float]) -> None:
        self.init_output_bias(rates)
        H.init_biases(self.poss10_head, rates.get("poss10"))


def save_critic(model: Next10GAT, path: Path, seed: int, table: C.TeamTable, metrics: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sd = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()}
    torch.save({"format": FORMAT, "config": model.cfg.to_dict(), "state_dict": sd, "seed": seed,
                "profile_keys": [], "teams": table.to_arrays(), "metrics": metrics}, path)


def load_critic(path: Path, device="cpu") -> tuple[Next10GAT, C.TeamTable]:
    blob = torch.load(path, map_location=device, weights_only=False)
    if blob.get("format") == FORMAT:
        model = Next10GAT(Next10Config.from_dict(blob["config"]))
        model.load_state_dict(blob["state_dict"])
        t = blob["teams"]
        table = C.TeamTable([str(n) for n in t["team_names"]], t["team_style"], t["team_style_mask"])
        return model.to(device).eval(), table
    # a plain tacticsml-gat-v1 checkpoint: backbone weights only, new heads and FiLM start fresh
    cfg = ModelConfig.from_dict(blob["config"])
    model = Next10GAT(Next10Config(**cfg.to_dict(), film=False))
    missing, unexpected = model.load_state_dict(blob["state_dict"], strict=False)
    if unexpected:
        raise ValueError(f"unexpected keys in {path}: {unexpected[:3]}")
    return model.to(device).eval(), C.TeamTable([], torch.zeros(1, len(C.STYLE_KEYS)).numpy(),
                                                 torch.zeros(1, len(C.STYLE_KEYS)).numpy())
