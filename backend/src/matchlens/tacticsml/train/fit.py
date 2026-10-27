"""Training loop for the TacticAI+ GATv2 (single model, seed ensemble) and the cVAE stage.

TRAINING ONLY (imports torch). Checkpoints go to ``backend/data/local/tacticsml/ckpt/`` (gitignored):

* ``gat_seed{S}.pt``   ``{"format": "tacticsml-gat-v1", "config": ModelConfig dict, "state_dict": {...},
  "profile_keys": [...], "seed": S, "metrics": {...}}`` and a human-readable ``gat_seed{S}.json``
* ``ensemble.json``    member list, config and ensemble validation metrics
* ``cvae.pt``          ``{"format": "tacticsml-cvae-v1", "config": CVAEConfig dict, "encoder_config":
  ModelConfig dict, "encoder_member": "gat_seed0.pt", "state_dict": cVAE-only tensors}``
"""

from __future__ import annotations

import copy
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import torch

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train import cvae as cv
from matchlens.tacticsml.train.model import (
    LABEL_KEYS, N_SLOTS, OUTCOMES, ModelConfig, TacticsGAT, masked_losses, random_signs, to_tensors,
)

CKPT_DIR = Path(__file__).resolve().parents[4] / "data" / "local" / "tacticsml" / "ckpt"
GEOM_KEYS = ("pos", "vel", "vel_mask", "node_feat", "edge_feat", "node_mask")


# ---------------------------------------------------------------- data

@dataclass
class Dataset:
    """In-memory arrays in ``features.stack`` layout plus labels (``-1`` = unknown)."""

    a: dict[str, np.ndarray]
    profile_keys: tuple[str, ...] = ()

    def __len__(self) -> int:
        return int(self.a["pos"].shape[0])

    def batch(self, idx: np.ndarray) -> dict[str, np.ndarray]:
        return {k: v[idx] for k, v in self.a.items()}

    def subset(self, idx: np.ndarray) -> "Dataset":
        return Dataset(self.batch(idx), self.profile_keys)

    @classmethod
    def from_samples(cls, samples: Sequence[F.GraphSample], labels: dict[str, np.ndarray] | None = None,
                     target_pos: np.ndarray | None = None) -> "Dataset":
        a = {k: v for k, v in F.stack(samples).items() if k in GEOM_KEYS}
        n = len(samples)
        for k in LABEL_KEYS:
            a[k] = np.asarray((labels or {}).get(k, np.full(n, -1)), dtype=np.int64)
        if target_pos is not None:
            a["target_pos"] = np.asarray(target_pos, dtype=np.float32)
            a["target_valid"] = np.ones(n, dtype=np.int64)
        return cls(a, tuple(samples[0].profile_keys))


def load_dataset(sources: Sequence[str], split: str) -> Dataset:
    """Concatenate ``train.build.read_shards(sources, split)`` into one in-memory dataset."""
    try:
        from matchlens.tacticsml.train.build import read_shards
    except ImportError as exc:  # pragma: no cover - depends on teammate C3
        raise SystemExit("matchlens.tacticsml.train.build is not available yet (shard reader)") from exc
    parts: list[dict] = []
    keys: tuple[str, ...] = ()
    try:
        got = read_shards(list(sources), split)
    except Exception as exc:  # C3's DataUnavailable when no shards match the split
        if type(exc).__name__ == "DataUnavailable":
            raise SystemExit(f"no shards found for sources={list(sources)} split={split!r}: {exc}") from exc
        raise
    for shard in ([got] if isinstance(got, dict) else got):  # one concatenated dict, or an iterable of them
        if not isinstance(shard, dict):
            shard = getattr(shard, "arrays", None) or dict(shard)
        pk = shard.get("profile_keys")
        if pk is not None and len(pk):
            keys = tuple(str(k) for k in pk)
        parts.append({k: np.asarray(v) for k, v in shard.items()
                      if k in GEOM_KEYS or k in LABEL_KEYS or k in ("target_pos", "target_valid")})
    if not parts:
        raise SystemExit(f"no shards found for sources={list(sources)} split={split!r}")
    out: dict[str, np.ndarray] = {}
    for k in GEOM_KEYS:
        out[k] = np.concatenate([p[k] for p in parts]).astype(np.float32)
    for k in LABEL_KEYS:
        out[k] = np.concatenate([p[k] if k in p else np.full(len(p["pos"]), -1) for p in parts]).astype(np.int64)
    if all("target_pos" in p for p in parts):
        out["target_pos"] = np.concatenate([p["target_pos"] for p in parts]).astype(np.float32)
        out["target_valid"] = np.concatenate([p.get("target_valid", np.ones(len(p["pos"]))) for p in parts]
                                             ).astype(np.int64)
    return Dataset(out, keys)


def split_dataset(ds: Dataset, val_frac: float = 0.1, seed: int = 0) -> tuple[Dataset, Dataset]:
    idx = np.random.default_rng(seed).permutation(len(ds))
    n_val = max(1, int(len(ds) * val_frac))
    return ds.subset(np.sort(idx[n_val:])), ds.subset(np.sort(idx[:n_val]))


def profile_stats(ds: Dataset) -> tuple[np.ndarray, np.ndarray]:
    P = len(ds.profile_keys)
    if P == 0:
        return np.zeros(0), np.ones(0)
    nf, m = ds.a["node_feat"], ds.a["node_mask"] > 0
    mean, std = np.zeros(P), np.ones(P)
    for k in range(P):
        vals = nf[..., F.F_BASE + k][m & (nf[..., F.F_BASE + P + k] > 0)]
        if vals.size > 1:
            mean[k], std[k] = vals.mean(), max(vals.std(), 1e-6)
    return mean, std


def base_rates(ds: Dataset) -> dict[str, float]:
    rates = {}
    for name in OUTCOMES:
        y = ds.a[name]
        if (y >= 0).any():
            rates[name] = float((y[y >= 0] == 1).mean())
    return rates


# ---------------------------------------------------------------- device and seeds

def pick_device(pref: str = "auto") -> torch.device:
    if pref != "auto":
        return torch.device(pref)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def seed_everything(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)


# ---------------------------------------------------------------- metrics

def auc_score(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y).astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    _, inv, cnt = np.unique(p, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(cnt) - (cnt - 1) / 2.0)[inv]  # average rank of ties, 1-based
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def f1_best(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y).astype(bool)
    if not y.any():
        return float("nan")
    order = np.argsort(-p)
    tp = np.cumsum(y[order])
    k = np.arange(1, len(y) + 1)
    f1 = 2 * tp / (k + y.sum())
    return float(f1.max())


def ece_score(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    y = np.asarray(y, dtype=float)
    b = np.minimum((p * bins).astype(int), bins - 1)
    err = 0.0
    for i in range(bins):
        sel = b == i
        if sel.any():
            err += sel.mean() * abs(y[sel].mean() - p[sel].mean())
    return float(err)


def metrics_from_outputs(recv_logits: np.ndarray, outcome_logits: np.ndarray,
                         labels: dict[str, np.ndarray]) -> dict[str, float]:
    m: dict[str, float] = {}
    y = labels["receiver_idx"]
    ok = (y >= 0) & (y < N_SLOTS)
    if ok.any():
        order = np.argsort(-recv_logits[ok][:, :N_SLOTS], axis=1)
        rank = (order == y[ok][:, None]).argmax(1)
        m["receiver_top1"] = float((rank < 1).mean())
        m["receiver_top3"] = float((rank < 3).mean())
        m["receiver_n"] = int(ok.sum())
    for i, name in enumerate(OUTCOMES):
        yy = labels[name]
        sel = yy >= 0
        if not sel.any():
            continue
        p = 1.0 / (1.0 + np.exp(-outcome_logits[sel, i]))
        m[f"{name}_auc"] = auc_score(yy[sel], p)
        m[f"{name}_ece"] = ece_score(yy[sel], p)
        if name == "shot10":
            m["shot10_f1"] = f1_best(yy[sel], p)
        m[f"{name}_n"] = int(sel.sum())
    return m


@torch.no_grad()
def predict_logits(model: TacticsGAT, ds: Dataset, device, batch: int = 256, average: bool = True
                   ) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    recv, out = [], []
    for s in range(0, len(ds), batch):
        b = to_tensors(ds.batch(np.arange(s, min(s + batch, len(ds)))), device)
        o = model(b, average=average)
        recv.append(o["receiver_logits"].cpu())
        out.append(o["outcome_logits"].cpu())
    return torch.cat(recv).numpy(), torch.cat(out).numpy()


def evaluate_model(model: TacticsGAT, ds: Dataset, device, batch: int = 256,
                   weights: dict[str, float] | None = None) -> dict[str, float]:
    """Frame-averaged metrics: loss, receiver top-1/3, shot10 AUC/F1/ECE, turn5 AUC, ..."""
    recv, out = predict_logits(model, ds, device, batch)
    labels = {k: ds.a[k] for k in LABEL_KEYS}
    loss = masked_losses({"receiver_logits": torch.as_tensor(recv), "outcome_logits": torch.as_tensor(out)},
                         {k: torch.as_tensor(v) for k, v in labels.items()}, weights)
    m = {"loss": float(loss["total"])}
    m.update({f"loss_{k}": float(v) for k, v in loss.items() if k != "total"})
    m.update(metrics_from_outputs(recv, out, labels))
    return m


def evaluate_ensemble(models: Sequence[TacticsGAT], ds: Dataset, device, batch: int = 256) -> dict[str, float]:
    rs, os_ = zip(*(predict_logits(m, ds, device, batch) for m in models))
    labels = {k: ds.a[k] for k in LABEL_KEYS}
    return metrics_from_outputs(np.mean(rs, 0), np.mean(os_, 0), labels)


# ---------------------------------------------------------------- GAT training

@dataclass
class TrainConfig:
    epochs: int = 30
    batch: int = 64
    lr: float = 1e-3
    weight_decay: float = 1e-2
    clip: float = 1.0
    patience: int = 6
    warmup_frac: float = 0.03
    max_steps: int | None = None  # hard cap (smoke tests)
    augment: bool = True  # random D2 element per sample
    weights: dict[str, float] = field(default_factory=dict)


def _lr_lambda(total: int, warmup: int) -> Callable[[int], float]:
    def f(step: int) -> float:
        if step < warmup:
            return (step + 1) / max(1, warmup)
        t = (step - warmup) / max(1, total - warmup)
        return 0.5 * (1.0 + math.cos(math.pi * min(1.0, t))) * 0.99 + 0.01
    return f


def build_model(ds: Dataset, mcfg: ModelConfig | None = None, device=None) -> TacticsGAT:
    mcfg = mcfg or ModelConfig.for_profile_keys(ds.profile_keys)
    if mcfg.n_node_feat != ds.a["node_feat"].shape[-1]:
        raise ValueError(f"model expects F={mcfg.n_node_feat}, data has F={ds.a['node_feat'].shape[-1]}")
    model = TacticsGAT(mcfg)
    if mcfg.n_profile:
        model.set_profile_stats(*profile_stats(ds))
    model.init_output_bias(base_rates(ds))
    return model.to(device) if device is not None else model


def train_model(train: Dataset, val: Dataset | None = None, mcfg: ModelConfig | None = None,
                tcfg: TrainConfig | None = None, seed: int = 0, device: str | torch.device = "auto",
                log: Callable[[str], None] = print) -> tuple[TacticsGAT, dict]:
    """AdamW + warmup/cosine LR + grad clipping + early stopping on frame-averaged val loss."""
    tcfg = tcfg or TrainConfig()
    dev = device if isinstance(device, torch.device) else pick_device(device)
    seed_everything(seed)
    model = build_model(train, mcfg, dev)
    opt = torch.optim.AdamW(model.parameters(), lr=tcfg.lr, weight_decay=tcfg.weight_decay)
    spe = math.ceil(len(train) / tcfg.batch)
    total = min(tcfg.epochs * spe, tcfg.max_steps or 10 ** 12)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, _lr_lambda(total, int(tcfg.warmup_frac * total)))
    rng = np.random.default_rng(seed)
    best, best_state, bad, step = math.inf, None, 0, 0
    history: dict = {"train_loss": [], "val": [], "steps": 0, "device": str(dev), "n_params": model.n_params()}
    t0 = time.time()
    for epoch in range(tcfg.epochs):
        model.train()
        perm = rng.permutation(len(train))
        run, cnt = 0.0, 0
        for s in range(0, len(perm), tcfg.batch):
            b = to_tensors(train.batch(np.sort(perm[s:s + tcfg.batch])), dev)
            signs = random_signs(b["pos"].shape[0], device=dev) if tcfg.augment else None
            losses = masked_losses(model(b, signs=signs), b, tcfg.weights)
            opt.zero_grad(set_to_none=True)
            losses["total"].backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), tcfg.clip)
            opt.step()
            sched.step()
            step += 1
            run += float(losses["total"].detach())
            cnt += 1
            if step >= total:
                break
        history["train_loss"].append(run / max(cnt, 1))
        msg = f"epoch {epoch + 1} step {step} train_loss {history['train_loss'][-1]:.4f}"
        if val is not None:
            m = evaluate_model(model, val, dev, weights=tcfg.weights)
            history["val"].append(m)
            msg += f" val_loss {m['loss']:.4f}" + "".join(
                f" {k} {m[k]:.3f}" for k in ("receiver_top1", "receiver_top3", "shot10_auc", "shot10_ece", "turn5_auc")
                if k in m)
            if m["loss"] < best - 1e-5:
                best, bad = m["loss"], 0
                best_state = copy.deepcopy({k: v.detach().cpu() for k, v in model.state_dict().items()})
            else:
                bad += 1
        log(f"{msg} ({time.time() - t0:.0f}s)")
        if step >= total or (val is not None and bad >= tcfg.patience):
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    history["steps"] = step
    history["best_val_loss"] = None if best is math.inf else best
    return model, history


def save_member(model: TacticsGAT, path: Path, seed: int, profile_keys: Sequence[str], metrics: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sd = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()}
    torch.save({"format": "tacticsml-gat-v1", "config": model.cfg.to_dict(), "state_dict": sd,
                "profile_keys": list(profile_keys), "seed": seed, "metrics": metrics}, path)
    path.with_suffix(".json").write_text(json.dumps(
        {"format": "tacticsml-gat-v1", "config": model.cfg.to_dict(), "profile_keys": list(profile_keys),
         "seed": seed, "metrics": metrics, "n_params": model.n_params(),
         "state_dict_shapes": {k: list(v.shape) for k, v in sd.items()}}, indent=2, default=float))


def load_member(path: Path, device="cpu") -> TacticsGAT:
    blob = torch.load(path, map_location=device, weights_only=False)
    model = TacticsGAT(ModelConfig.from_dict(blob["config"]))
    model.load_state_dict(blob["state_dict"])
    return model.to(device).eval()


def train_ensemble(train: Dataset, val: Dataset | None = None, seeds: Sequence[int] = (0, 1, 2),
                   mcfg: ModelConfig | None = None, tcfg: TrainConfig | None = None,
                   device: str | torch.device = "auto", ckpt_dir: Path | None = None,
                   log: Callable[[str], None] = print) -> dict:
    """Train one model per seed; write ``gat_seed{S}.pt`` and ``ensemble.json``."""
    ckpt_dir = Path(ckpt_dir or CKPT_DIR)
    models, members = [], []
    for seed in seeds:
        log(f"== seed {seed}")
        model, hist = train_model(train, val, mcfg, tcfg, seed, device, log)
        met = evaluate_model(model, val, next(model.parameters()).device, weights=(tcfg or TrainConfig()).weights
                             ) if val is not None else {}
        save_member(model, ckpt_dir / f"gat_seed{seed}.pt", seed, train.profile_keys,
                    {"val": met, "steps": hist["steps"], "best_val_loss": hist["best_val_loss"]})
        models.append(model)
        members.append({"file": f"gat_seed{seed}.pt", "seed": seed, "val": met})
    ens = (evaluate_ensemble(models, val, next(models[0].parameters()).device)
           if val is not None and len(models) > 1 else {})
    info = {"members": members, "config": models[0].cfg.to_dict(), "profile_keys": list(train.profile_keys),
            "ensemble_val": ens}
    (ckpt_dir / "ensemble.json").write_text(json.dumps(info, indent=2, default=float))
    log(f"ensemble val: {json.dumps(ens, default=float)}")
    return {"models": models, **info}


# ---------------------------------------------------------------- cVAE stage

def cvae_batch(b: dict[str, torch.Tensor], gen: torch.Generator | None = None, jitter: float = 2.5
               ) -> tuple[dict, cv.Condition, torch.Tensor]:
    """(context graph, condition, target displacement) from a labelled batch.

    Uses ``target_pos`` (future positions) where ``target_valid`` is set, else the denoising fallback.
    """
    cond = cv.make_training_condition(b, gen)
    mov = cv.movable_mask(b["node_mask"], cond.team, cond.locked)
    ctx, delta = cv.jitter_target(b, cond, jitter, gen)
    if "target_pos" in b:
        valid = (b["target_valid"] > 0) if "target_valid" in b else torch.ones_like(b["receiver_idx"]).bool()
        real = (b["target_pos"] - b["pos"][:, -1]) * mov[..., None]
        v = valid[:, None, None]
        ctx = {**ctx, "pos": torch.where(v[:, None], b["pos"], ctx["pos"]),
               "edge_feat": torch.where(v[..., None], b["edge_feat"], ctx["edge_feat"])}
        delta = torch.where(v, real, delta)
    return ctx, cond, delta


def train_cvae(encoder: TacticsGAT, train: Dataset, val: Dataset | None = None,
               ccfg: cv.CVAEConfig | None = None, epochs: int = 10, batch: int = 64, lr: float = 1e-3,
               max_steps: int | None = None, seed: int = 0, device: str | torch.device = "auto",
               log: Callable[[str], None] = print) -> tuple[cv.ConditionalVAE, dict]:
    dev = device if isinstance(device, torch.device) else pick_device(device)
    seed_everything(seed)
    encoder = encoder.to(dev)
    model = cv.ConditionalVAE(encoder, ccfg).to(dev)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=1e-2)
    spe = math.ceil(len(train) / batch)
    total = min(epochs * spe, max_steps or 10 ** 12)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, _lr_lambda(total, int(0.03 * total)))
    rng = np.random.default_rng(seed)
    gen = torch.Generator().manual_seed(seed)
    hist: dict = {"train": [], "val": []}
    best, best_state, step = math.inf, None, 0
    for epoch in range(epochs):
        model.train()
        perm = rng.permutation(len(train))
        agg = {"total": 0.0, "recon": 0.0, "kl": 0.0, "guide": 0.0}
        cnt = 0
        for s in range(0, len(perm), batch):
            b = to_tensors(train.batch(np.sort(perm[s:s + batch])), dev)
            ctx, cond, delta = cvae_batch(b, gen)
            beta = model.cfg.kl_beta * min(1.0, step / max(1.0, 0.3 * total))
            losses = cv.cvae_loss(model, ctx, cond, delta, beta=beta,
                                  signs=random_signs(b["pos"].shape[0], device=dev))
            opt.zero_grad(set_to_none=True)
            losses["total"].backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            step += 1
            cnt += 1
            for k in agg:
                agg[k] += float(losses[k].detach())
            if step >= total:
                break
        hist["train"].append({k: v / max(cnt, 1) for k, v in agg.items()})
        msg = f"cvae epoch {epoch + 1} " + " ".join(f"{k} {v:.4f}" for k, v in hist["train"][-1].items())
        if val is not None:
            vm = evaluate_cvae(model, val, dev, gen)
            hist["val"].append(vm)
            msg += f" | val recon {vm['recon']:.4f} kl {vm['kl']:.3f}"
            if vm["recon"] < best:
                best = vm["recon"]
                best_state = copy.deepcopy({k: v.detach().cpu() for k, v in model.cvae_state_dict().items()})
        log(msg)
        if step >= total:
            break
    if best_state is not None:
        model.load_state_dict(best_state, strict=False)
    return model, hist


@torch.no_grad()
def evaluate_cvae(model: cv.ConditionalVAE, ds: Dataset, device, gen: torch.Generator | None = None,
                  batch: int = 128) -> dict[str, float]:
    model.eval()
    agg = {"recon": 0.0, "kl": 0.0}
    n = 0
    for s in range(0, len(ds), batch):
        b = to_tensors(ds.batch(np.arange(s, min(s + batch, len(ds)))), device)
        ctx, cond, delta = cvae_batch(b, gen)
        l = cv.cvae_loss(model, ctx, cond, delta, guide_weight=0.0)
        agg["recon"] += float(l["recon"]) * len(b["pos"])
        agg["kl"] += float(l["kl"]) * len(b["pos"])
        n += len(b["pos"])
    return {k: v / max(n, 1) for k, v in agg.items()}


def save_cvae(model: cv.ConditionalVAE, path: Path, encoder_member: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"format": cv.FORMAT, "config": model.cfg.to_dict(), "encoder_config": model.encoder.cfg.to_dict(),
                "encoder_member": encoder_member, "state_dict": model.cvae_state_dict()}, path)


def load_cvae(path: Path, encoder: TacticsGAT, device="cpu") -> cv.ConditionalVAE:
    blob = torch.load(path, map_location=device, weights_only=False)
    model = cv.ConditionalVAE(encoder, cv.CVAEConfig.from_dict(blob["config"]))
    model.load_state_dict(blob["state_dict"], strict=False)
    return model.to(device).eval()


# ---------------------------------------------------------------- CLI

def add_arguments(p) -> None:
    p.add_argument("--sources", nargs="+", default=["synthetic"], help="shard sources for read_shards")
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--device", default="auto", help="auto (mps > cuda > cpu), mps, cuda or cpu")
    p.add_argument("--cvae", action="store_true", help="also train the cVAE on the frozen first member")
    p.add_argument("--cvae-epochs", type=int, default=10)
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--ckpt-dir", default=str(CKPT_DIR))


def main(args) -> int:
    train = load_dataset(args.sources, "train")
    try:
        val = load_dataset(args.sources, "val")
    except SystemExit:
        train, val = split_dataset(train)
    print(f"train {len(train)} val {len(val)} profile_keys {list(train.profile_keys)}")
    tcfg = TrainConfig(epochs=args.epochs, batch=args.batch, lr=args.lr, max_steps=args.max_steps)
    ens = train_ensemble(train, val, tuple(args.seeds), None, tcfg, args.device, Path(args.ckpt_dir))
    if args.cvae:
        model, _ = train_cvae(ens["models"][0], train, val, epochs=args.cvae_epochs, batch=args.batch,
                              max_steps=args.max_steps, device=args.device)
        save_cvae(model, Path(args.ckpt_dir) / "cvae.pt", ens["members"][0]["file"])
        print(f"saved {Path(args.ckpt_dir) / 'cvae.pt'}")
    return 0
