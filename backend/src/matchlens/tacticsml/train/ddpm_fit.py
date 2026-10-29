"""Train and evaluate the "play it out" DDPM on the on-disk tracking: ``python -m matchlens.tacticsml.train.ddpm_fit``.

The denoiser of ``diffusion.py`` is trained for a wall-clock budget on the open-play samples of the tracking sources
(SportEC, SkillCorner, Metrica train split) with a random D2 reflection per sample, and with the video view mask on
half of the conditioning inputs. It is then scored on the held-out open-play test samples with the same
``trajectory_metrics`` as the MDN forecaster: S samples play the role of the K modes (pi uniform), so ``minJADE`` /
``minJFDE`` are the best of S whole-scene continuations. Best-of-S grows with S, so the table also gives the DDPM at
S = 6 (the MDN's K) and the MDN, on the same samples.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
import torch

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train import diffusion as D
from matchlens.tacticsml.train import schema

CKPT = schema.NEXT10 / "ckpt_r2" / "ddpm_seed0.pt"


def train_ddpm(train, cfg: D.DiffusionConfig | None = None, minutes: float = 25.0, batch: int = 48, lr: float = 5e-4,
               seed: int = 0, device: str | torch.device = "auto", view_p: float = 0.5, max_steps: int | None = None,
               log: Callable[[str], None] = print) -> tuple[D.Denoiser, dict[str, Any]]:
    from matchlens.tacticsml.train.augment import video_config, view_mask_batch
    from matchlens.tacticsml.train.data10 import to_tensors10
    from matchlens.tacticsml.train.fit import pick_device, seed_everything

    dev = device if isinstance(device, torch.device) else pick_device(device)
    seed_everything(seed)
    model = D.Denoiser(cfg).to(dev)
    sch = D.Schedule(model.cfg.steps).to(dev)
    use = np.flatnonzero(train.a["open_play"] & train.a["fut_valid"][:, -1, F.BALL])
    rng = np.random.default_rng(seed)
    vcfg = video_config(p_apply=view_p) if view_p > 0 else None
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    t0, step, hist = time.time(), 0, {"loss": [], "n_params": model.n_params(), "n_train": int(len(use))}
    total_s = minutes * 60.0
    run, cnt = 0.0, 0
    while time.time() - t0 < total_s and (max_steps is None or step < max_steps):
        for s in range(0, len(use), batch):
            idx = np.sort(rng.choice(use, batch, replace=False))
            b = train.batch(idx)
            if vcfg is not None:
                b, _ = view_mask_batch(b, rng, vcfg)
            t = dict(to_tensors10(b, dev))
            frac = min(1.0, (time.time() - t0) / total_s)
            for g in opt.param_groups:  # warm-up then cosine to 5%
                g["lr"] = lr * (min(1.0, (step + 1) / 200)) * (0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * frac)))
            sg = torch.from_numpy(rng.choice([-1.0, 1.0], size=(len(idx), 2)).astype(np.float32)).to(dev)
            for k in ("pos", "vel", "fut_pos"):  # a random D2 reflection per sample (positions are centred)
                t[k] = t[k] * sg[:, None, None, :]
            x0, ok = D.future_to_x0(t["fut_pos"], t["fut_valid"], t)
            loss = D.ddpm_loss(model, sch, x0, ok, t)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            step += 1
            run += float(loss.detach())
            cnt += 1
            if step % 200 == 0:
                hist["loss"].append(run / cnt)
                log(f"ddpm step {step} loss {run / cnt:.4f} ({time.time() - t0:.0f}s)")
                run, cnt = 0.0, 0
            if time.time() - t0 >= total_s or (max_steps is not None and step >= max_steps):
                break
    hist["steps"] = step
    return model, hist


@torch.no_grad()
def sample_scenes(model: D.Denoiser, ds, idx: np.ndarray, device, S: int = 20, batch: int = 16, seed: int = 0,
                  average: bool = False) -> np.ndarray:
    """``(n, S, A, H, 2)`` sampled continuations in the input frame (metres)."""
    from matchlens.tacticsml.train.data10 import to_tensors10

    sch = D.Schedule(model.cfg.steps).to(device)
    gen = torch.Generator(device=device).manual_seed(seed)
    out = []
    for s in range(0, len(idx), batch):
        t = to_tensors10(ds.batch(idx[s:s + batch]), device)
        out.append(D.sample(model, sch, t, S, gen, average).float().cpu().numpy())
    return np.concatenate(out)


def evaluate_ddpm(model: D.Denoiser, traj, ds, idx: np.ndarray, device, S: int = 20, seed: int = 0, view_cfg=None,
                  average: bool = False) -> dict[str, Any]:
    """minJADE / minJFDE of the DDPM (best of S and of 6) next to the MDN forecaster, on the same samples. With
    ``view_cfg`` the conditioning is video-masked for both models (the masks are the same: same seed)."""
    from matchlens.tacticsml.train.data10 import to_tensors10
    from matchlens.tacticsml.train.trajectory import N_AGENTS, predict_numpy, trajectory_metrics

    A = N_AGENTS
    fut = ds.a["fut_pos"][idx].astype(np.float32)
    valid = ds.a["fut_valid"][idx]
    samples, nms, lasts = [], [], []
    sch = D.Schedule(model.cfg.steps).to(device)
    gen = torch.Generator(device=device).manual_seed(seed)
    rng = np.random.default_rng(seed)
    from matchlens.tacticsml.train.augment import view_mask_batch
    for s in range(0, len(idx), 16):
        b = ds.batch(idx[s:s + 16])
        if view_cfg is not None:
            b, _ = view_mask_batch(b, rng, view_cfg)
        t = to_tensors10(b, device)
        samples.append(D.sample(model, sch, t, S, gen, average).float().cpu().numpy())
        nms.append(np.asarray(b["node_mask"]))
        lasts.append(np.concatenate([b["pos"][:, -1, :A], b["vel"][:, -1, :A]], -1))
    smp, node_mask, last = np.concatenate(samples), np.concatenate(nms), np.concatenate(lasts)
    n = len(idx)
    pos_last = np.zeros((n, F.N_NODES, 2), np.float32)
    vel_last = np.zeros((n, F.N_NODES, 2), np.float32)
    pos_last[:, :A], vel_last[:, :A] = last[..., :2], last[..., 2:]
    res: dict[str, Any] = {"n": int(n), "S": S}
    for tag, k in (("ddpm_best_of_S", S), ("ddpm_best_of_6", 6)):
        pi = np.full((n, k), 1.0 / k)
        res[tag] = trajectory_metrics(smp[:, :k], pi, fut, valid, pos_last, vel_last, node_mask)
    if traj is not None:
        mu, pi, last_m, nm_m = predict_numpy(traj, ds, idx, device, 128, True, np.random.default_rng(seed) if view_cfg else None,
                                             view_cfg, return_mask=True)
        pl, vl = np.zeros_like(pos_last), np.zeros_like(vel_last)
        pl[:, :A], vl[:, :A] = last_m[..., :2], last_m[..., 2:]
        res["mdn_k6"] = trajectory_metrics(mu, pi, fut, valid, pl, vl, nm_m)
    keep = ("player_minADE", "player_minFDE", "player_top1_FDE", "player_static_FDE", "player_cv_FDE", "minJADE", "minJFDE",
            "ball_minFDE")
    res["summary"] = {k: {m: v.get(m) for m in keep} for k, v in res.items() if isinstance(v, dict) and "player_minFDE" in v}
    # physical plausibility of the samples: share of player-horizons above 12 m/s between consecutive horizons
    step = np.linalg.norm(np.diff(smp[:, :, :F.BALL], axis=3), axis=-1)  # 1 s apart, so m/s
    res["plausibility"] = {"share_player_steps_over_12ms": float((step > 12.0).mean()),
                           "sample_diversity_m": float(np.linalg.norm(smp[:, :, :F.BALL, -1] - smp[:, :1, :F.BALL, -1], axis=-1).mean())}
    return res


def main(argv: list[str] | None = None) -> int:
    from matchlens.tacticsml.train.augment import video_config
    from matchlens.tacticsml.train.data10 import TRACKING_SOURCES, load_next10
    from matchlens.tacticsml.train.fit import pick_device
    from matchlens.tacticsml.train.trajectory import load_trajectory

    p = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train.ddpm_fit", description=__doc__)
    p.add_argument("--shards", default=str(schema.NEXT10_SHARDS))
    p.add_argument("--minutes", type=float, default=25.0)
    p.add_argument("--batch", type=int, default=48)
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--eval-n", type=int, default=200)
    p.add_argument("--samples", type=int, default=20)
    p.add_argument("--steps", type=int, default=100)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--device", default="auto")
    p.add_argument("--traj", default=str(schema.NEXT10 / "ckpt_r2" / "trajectory_seed0.pt"))
    p.add_argument("--out", default=str(CKPT))
    p.add_argument("--report", default=str(schema.NEXT10 / "ddpm_report.json"))
    a = p.parse_args(argv)
    dev = pick_device(a.device)
    data = load_next10(list(TRACKING_SOURCES), Path(a.shards), None)
    cfg = D.DiffusionConfig(steps=a.steps)
    model, hist = train_ddpm(data["train"], cfg, a.minutes, a.batch, seed=a.seed, device=dev, max_steps=a.max_steps)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"config": asdict(cfg), "state_dict": model.state_dict(), "history": hist}, out)
    test = data["test"]
    src = test.a["source"]
    sel = np.flatnonzero(test.a["open_play"] & test.a["fut_valid"][:, -1, F.BALL] & np.isin(src, ["sportec", "skillcorner"]))
    sel = np.sort(np.random.default_rng(a.seed).choice(sel, min(a.eval_n, len(sel)), replace=False))
    tp = Path(a.traj)
    traj = load_trajectory(tp, dev) if tp.exists() else None
    model.eval()
    report = {"train": {k: hist[k] for k in ("steps", "n_params", "n_train")} | {"minutes": a.minutes, "final_loss": hist["loss"][-1:] },
              "clean": evaluate_ddpm(model, traj, test, sel, dev, a.samples, a.seed),
              "video_masked": evaluate_ddpm(model, traj, test, sel, dev, a.samples, a.seed, video_config())}
    Path(a.report).write_text(json.dumps(report, indent=1, default=float))
    print(json.dumps({k: v["summary"] for k, v in report.items() if k != "train"}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
