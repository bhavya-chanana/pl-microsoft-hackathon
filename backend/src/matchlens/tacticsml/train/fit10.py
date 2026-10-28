"""Training of the Next10 critic (``Next10GAT``): ``python -m matchlens.tacticsml.train.fit10 --help``.

TRAINING ONLY (torch). Writes to ``backend/data/local/tacticsml/next10/ckpt/`` and never to ``ckpt/`` or
``weights/`` of the deployed model. Trains on **open-play** samples only (``Dataset10.a["open_play"]``), with

* a random D2 element per sample (the frame average is used for validation and test),
* view-mask augmentation with probability ``view_mask_p`` per sample (``augment.py``),
* FiLM team ids dropped to "unknown" with probability ``team_dropout`` per side,
* the losses of ``heads.next10_losses`` (all ignore -1 labels).
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import torch

from matchlens.tacticsml import features as F
from matchlens.tacticsml import next10_common as C
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.augment import ViewMaskConfig, view_mask_batch
from matchlens.tacticsml.train.data10 import Dataset10, load_next10, to_tensors10
from matchlens.tacticsml.train.fit import auc_score, ece_score, pick_device, seed_everything
from matchlens.tacticsml.train.heads import next10_losses
from matchlens.tacticsml.train.model import random_signs
from matchlens.tacticsml.train.next10 import Next10Config, Next10GAT, save_critic

CKPT10 = schema.NEXT10 / "ckpt"


@dataclass
class Fit10Config:
    epochs: int = 12
    batch: int = 64
    lr: float = 1e-3
    weight_decay: float = 1e-2
    clip: float = 1.0
    patience: int = 4
    warmup_frac: float = 0.03
    film: bool = True
    view_mask_p: float = 0.5
    team_dropout: float = 0.3
    max_steps: int | None = None
    max_hours: float = 0.5
    val_samples: int = 6000
    weights: dict[str, float] = field(default_factory=dict)


def base_rates10(ds: Dataset10, idx: np.ndarray) -> dict[str, float]:
    rates = {}
    for k in ("shot10", "goal10", "concede10", "turn5", "poss10"):
        y = ds.a[k][idx]
        if (y >= 0).any():
            rates[k] = float((y[y >= 0] == 1).mean())
    return rates


def build_critic(ds: Dataset10, cfg: Fit10Config, idx: np.ndarray, device) -> Next10GAT:
    mcfg = Next10Config(n_node_feat=ds.a["node_feat"].shape[-1], film=cfg.film, n_teams=ds.table.size,
                        team_dropout=cfg.team_dropout)
    model = Next10GAT(mcfg)
    model.set_team_table(ds.table)
    model.init_heads(base_rates10(ds, idx))
    return model.to(device)


@torch.no_grad()
def predict_critic(model: Next10GAT, ds: Dataset10, idx: np.ndarray, device, batch: int = 256, average: bool = True,
                   view_rng: np.random.Generator | None = None, view_cfg: ViewMaskConfig | None = None,
                   team_ids: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Logits of ``idx`` as numpy arrays (see ``Next10GAT.forward``). ``view_rng`` masks the inputs like
    broadcast video (``view_cfg``); ``team_ids`` overrides the FiLM ids (e.g. zeros for "unknown")."""
    model.eval()
    parts: dict[str, list[np.ndarray]] = {}
    for s in range(0, len(idx), batch):
        sel = idx[s:s + batch]
        b = ds.batch(sel)
        if view_rng is not None:
            b, _ = view_mask_batch(b, view_rng, view_cfg or ViewMaskConfig(p_apply=1.0))
        if team_ids is not None:
            b["team_ids"] = team_ids[s:s + batch]
        o = model(to_tensors10(b, device), average=average)
        for k, v in o.items():
            if v is not None:
                parts.setdefault(k, []).append(v.float().cpu().numpy())
    return {k: np.concatenate(v) for k, v in parts.items()}


def val_metrics(model: Next10GAT, ds: Dataset10, idx: np.ndarray, device, weights=None) -> dict[str, float]:
    out = predict_critic(model, ds, idx, device)
    t = to_tensors10(ds.batch(idx), "cpu")
    o = {k: torch.as_tensor(v) for k, v in out.items()}
    losses = next10_losses(o, t, weights)
    m = {f"loss_{k}": float(v) for k, v in losses.items()}
    m["loss"] = float(losses["total"])
    y = ds.a["shot10"][idx]
    ok = y >= 0
    if ok.any() and 0 < y[ok].sum() < ok.sum():
        m["shot10_auc"] = auc_score(y[ok], 1 / (1 + np.exp(-out["outcome_logits"][ok, 0])))
        m["shot10_ece"] = ece_score(y[ok], 1 / (1 + np.exp(-out["outcome_logits"][ok, 0])), 15)
        m["shot_marg_auc"] = auc_score(y[ok], out["shot_marg_prob"][ok])
    return m


def train_next10(data: dict[str, Dataset10], cfg: Fit10Config | None = None, seed: int = 0,
                 device: str | torch.device = "auto", log: Callable[[str], None] = print) -> tuple[Next10GAT, dict]:
    cfg = cfg or Fit10Config()
    dev = device if isinstance(device, torch.device) else pick_device(device)
    seed_everything(seed)
    train, val = data["train"], data.get("val") or data["train"]
    tidx = np.flatnonzero(train.a["open_play"])
    vidx = np.flatnonzero(val.a["open_play"])
    vrng = np.random.default_rng(1234)
    if len(vidx) > cfg.val_samples:
        vidx = np.sort(vrng.choice(vidx, cfg.val_samples, replace=False))
    model = build_critic(train, cfg, tidx, dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    spe = math.ceil(len(tidx) / cfg.batch)
    total = min(cfg.epochs * spe, cfg.max_steps or 10 ** 12)
    warm = max(1, int(cfg.warmup_frac * total))
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: (s + 1) / warm if s < warm else
                                              0.5 * (1 + math.cos(math.pi * min(1.0, (s - warm) / max(1, total - warm)))) * 0.99 + 0.01)
    rng = np.random.default_rng(seed)
    vcfg = ViewMaskConfig(p_apply=cfg.view_mask_p)
    best, best_state, bad, step, t0 = math.inf, None, 0, 0, time.time()
    hist: dict = {"train": [], "val": [], "n_params": model.n_params(), "device": str(dev), "n_train": int(len(tidx)),
                  "config": asdict(cfg)}
    for epoch in range(cfg.epochs):
        model.train()
        perm = rng.permutation(tidx)
        run, cnt = 0.0, 0
        for s in range(0, len(perm), cfg.batch):
            b = train.batch(np.sort(perm[s:s + cfg.batch]))
            if cfg.view_mask_p > 0:
                b, _ = view_mask_batch(b, rng, vcfg)
            t = to_tensors10(b, dev)
            if cfg.film and cfg.team_dropout > 0:
                drop = torch.rand(t["team_ids"].shape, device=dev) < cfg.team_dropout
                t["team_ids"] = t["team_ids"].masked_fill(drop, 0)
            losses = next10_losses(model(t, signs=random_signs(t["pos"].shape[0], device=dev)), t, cfg.weights)
            opt.zero_grad(set_to_none=True)
            losses["total"].backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.clip)
            opt.step()
            sched.step()
            step += 1
            cnt += 1
            run += float(losses["total"].detach())
            if step >= total or (time.time() - t0) > cfg.max_hours * 3600:
                break
        hist["train"].append(run / max(cnt, 1))
        m = val_metrics(model, val, vidx, dev, cfg.weights)
        hist["val"].append(m)
        log(f"epoch {epoch + 1} step {step} train {hist['train'][-1]:.4f} val {m['loss']:.4f} "
            + " ".join(f"{k} {m[k]:.3f}" for k in ("loss_zone", "loss_poss10", "loss_shot_marg", "shot10_auc", "shot_marg_auc",
                                                   "shot10_ece") if k in m) + f" ({time.time() - t0:.0f}s)")
        if m["loss"] < best - 1e-5:
            best, bad = m["loss"], 0
            best_state = copy.deepcopy({k: v.detach().cpu() for k, v in model.state_dict().items()})
        else:
            bad += 1
        if step >= total or bad >= cfg.patience or (time.time() - t0) > cfg.max_hours * 3600:
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    hist["steps"], hist["best_val_loss"] = step, best
    return model, hist


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train.fit10", description=__doc__)
    p.add_argument("--legacy-shards", default=None, help="shards root holding statsbomb360 (older format)")
    p.add_argument("--shards", default=str(schema.NEXT10_SHARDS))
    p.add_argument("--sources", nargs="+", default=["sportec", "skillcorner", "metrica", "statsbomb360"])
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1])
    p.add_argument("--epochs", type=int, default=12)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--no-film", action="store_true")
    p.add_argument("--no-view-mask", action="store_true")
    p.add_argument("--tag", default="", help="suffix of the checkpoint names (ablations)")
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--max-hours", type=float, default=0.5)
    p.add_argument("--device", default="auto")
    p.add_argument("--ckpt-dir", default=str(CKPT10))
    a = p.parse_args(argv)
    data = load_next10(a.sources, Path(a.shards), Path(a.legacy_shards) if a.legacy_shards else None)
    for s, d in data.items():
        print(f"{s}: {len(d)} samples, {int(d.a['open_play'].sum())} open play, teams in table {d.table.size - 1}")
    cfg = Fit10Config(epochs=a.epochs, batch=a.batch, lr=a.lr, film=not a.no_film,
                      view_mask_p=0.0 if a.no_view_mask else 0.5, max_steps=a.max_steps, max_hours=a.max_hours)
    out_dir = Path(a.ckpt_dir)
    members = []
    for seed in a.seeds:
        print(f"== seed {seed} film={cfg.film} view_mask_p={cfg.view_mask_p}")
        model, hist = train_next10(data, cfg, seed, a.device)
        name = f"next10{a.tag}_seed{seed}.pt"
        save_critic(model, out_dir / name, seed, data["train"].table, {"history": hist})
        members.append({"file": name, "seed": seed, "best_val_loss": hist["best_val_loss"], "steps": hist["steps"]})
    (out_dir / f"ensemble{a.tag}.json").write_text(json.dumps({"members": members, "config": asdict(cfg),
                                                               "sources": a.sources}, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
