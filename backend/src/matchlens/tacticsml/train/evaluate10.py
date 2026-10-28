"""Next10 evaluation: ``python -m matchlens.tacticsml.train.evaluate10`` -> ``next10/report_next10.json`` + ``.md``.

Everything in the report is computed by this run on the ``test`` split of the shards (held-out matches); no number
is typed in. Scope: per source (``sportec``, ``skillcorner``, ``statsbomb360``) and pooled, for **open play**
(``Dataset10.a["open_play"]``: no set piece, ball alive for 10 s) and for **all** samples.

Heads and metrics (formulas in docs/TACTICSML.md "Next10")
  shot10 / goal10 / concede10 / turn5 / poss10   AUC, Brier (and base-rate Brier), ECE (15 bins)
  shot10 variants                                direct head, receiver-decomposed, and the served blend
  receiver                                       top-1 / top-3 among the 11 slots (known receivers), chance
  ball_zone10 (16 x 12)                          top-1/3/5 cell accuracy, hard-cell NLL, RPS along x and y,
                                                 expected distance error (m); baselines: uniform, static prior
                                                 from the train split, persistence (Gaussian around the ball)
  action_next                                    accuracy and NLL against the majority class (SkillCorner only)
  trajectories                                   minADE/minFDE (K = 6) per player and ball, minJADE/minJFDE,
                                                 most-probable mode, constant velocity and standing still
Ablations (same data, same split): FiLM on / off / teams set to unknown; view-mask augmentation on / off,
scored on clean and on view-masked test inputs; shot10 direct vs receiver-decomposed.

Targets (round 1): open-play shot10 AUC >= 0.85, shot10 ECE <= 0.03, ball_zone10 top-5 >= 0.6, minFDE@10s at least
25% better than constant velocity, D2 error < 1e-5. Each is PASS / FAIL, or NOT_EVALUATED when fewer than 30 labelled
samples exist.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml import next10_common as C
from matchlens.tacticsml.train import evaluate as E
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.augment import ViewMaskConfig
from matchlens.tacticsml.train.data10 import Dataset10, load_next10

MIN_N = 30
SOURCES = ("sportec", "skillcorner", "statsbomb360")
MASK_SEED = 777


def _sig(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _sm(x: np.ndarray, axis: int = -1) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


# ============================================================ metric functions (numpy only)

def binary(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    ok = y >= 0
    y, p = y[ok], p[ok]
    if len(y) < MIN_N:
        return {"n": int(len(y))}
    r = E.binary_report(y, p)
    r.pop("reliability", None)
    return r


def rps_1d(p: np.ndarray, true_bin: np.ndarray) -> float:
    """Ranked probability score of ordered bins: mean over samples of sum_r (F_r - O_r)^2 / (R - 1)."""
    R = p.shape[-1]
    cdf = np.cumsum(p, -1)[:, :-1]
    obs = (np.arange(R - 1)[None] >= true_bin[:, None]).astype(float)
    return float(((cdf - obs) ** 2).sum(-1).mean() / (R - 1))


def zone_report(probs: np.ndarray, cell: np.ndarray, true_xy: np.ndarray) -> dict[str, Any]:
    """probs (n,192) over the flat grid, cell (n,) true flat cell, true_xy (n,2) true canonical position."""
    n = len(cell)
    if n < MIN_N:
        return {"n": n}
    order = np.argsort(-probs, axis=1)
    rank = (order == cell[:, None]).argmax(1)
    p3 = probs.reshape(n, C.GRID_ROWS, C.GRID_COLS)
    row, col = cell // C.GRID_COLS, cell % C.GRID_COLS
    dist = np.linalg.norm(C.CELL_CENTRES[None] - true_xy[:, None], axis=-1)
    return {"n": n, "top1": float((rank < 1).mean()), "top3": float((rank < 3).mean()), "top5": float((rank < 5).mean()),
            "nll": float(-np.log(np.clip(probs[np.arange(n), cell], 1e-9, None)).mean()),
            "rps_x": rps_1d(p3.sum(1), col), "rps_y": rps_1d(p3.sum(2), row),
            "dist_m": float((probs * dist).sum(1).mean())}


def zone_baselines(train: Dataset10, test_cur_xy: np.ndarray) -> dict[str, np.ndarray]:
    """Baseline distributions (n,192) for the test samples: uniform, static prior of the train split, persistence."""
    n = len(test_cur_xy)
    idx = np.flatnonzero(train.a["open_play"] & (train.a["zone_cell"] >= 0))
    hist = np.bincount(train.a["zone_cell"][idx], minlength=C.N_CELLS).astype(float) + 1.0
    sub = idx[:: max(1, len(idx) // 5000)]
    cur = train.a["pos"][sub, -1, F.BALL].astype(float)
    cells = train.a["zone_cell"][sub]
    best, best_nll = 10.0, np.inf
    for sigma in (3.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 65.0, 80.0):
        d2 = ((C.CELL_CENTRES[None] - cur[:, None]) ** 2).sum(-1)
        w = np.exp(-d2 / (2 * sigma ** 2))
        w /= w.sum(1, keepdims=True)
        nll = -np.log(np.clip(w[np.arange(len(cells)), cells], 1e-9, None)).mean()
        if nll < best_nll:
            best, best_nll = sigma, nll
    d2 = ((C.CELL_CENTRES[None] - test_cur_xy[:, None]) ** 2).sum(-1)
    pers = np.exp(-d2 / (2 * best ** 2))
    return {"uniform": np.full((n, C.N_CELLS), 1.0 / C.N_CELLS), "static_prior": np.tile(hist / hist.sum(), (n, 1)),
            f"persistence_sigma{best:g}m": pers / pers.sum(1, keepdims=True)}


def action_report(logits: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    ok = y >= 0
    if ok.sum() < MIN_N:
        return {"n": int(ok.sum())}
    p, y = _sm(logits[ok]), y[ok]
    major = np.bincount(y, minlength=4).argmax()
    prior = np.bincount(y, minlength=4) / len(y)
    return {"n": int(len(y)), "acc": float((p.argmax(1) == y).mean()), "acc_majority": float((y == major).mean()),
            "nll": float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-9, None)).mean()),
            "nll_prior": float(-np.log(np.clip(prior[y], 1e-9, None)).mean())}


def status(value: float | None, op: str, thr: float, n: int = MIN_N) -> str:
    if value is None or not np.isfinite(value) or n < MIN_N:
        return "NOT_EVALUATED"
    ok = {">=": value >= thr, "<=": value <= thr, "<": value < thr}[op]
    return "PASS" if ok else "FAIL"


# ============================================================ predictions

def predict_members(models: list, ds: Dataset10, idx: np.ndarray, device, masked: bool = False,
                    unknown_teams: bool = False) -> list[dict[str, np.ndarray]]:
    from matchlens.tacticsml.train.fit10 import predict_critic

    outs = []
    for m in models:
        rng = np.random.default_rng(MASK_SEED) if masked else None
        outs.append(predict_critic(m, ds, idx, device, view_rng=rng, view_cfg=ViewMaskConfig(p_apply=1.0),
                                   team_ids=np.zeros((len(idx), 2), np.int64) if unknown_teams else None))
    return outs


def ensemble(outs: list[dict[str, np.ndarray]], shot_mode: str = "blend") -> dict[str, np.ndarray]:
    """Mean of the members' logits and the probabilities derived from them, plus per-member shot probabilities."""
    mean = {k: np.mean([o[k] for o in outs], 0) for k in outs[0]}
    direct = _sig(mean["outcome_logits"][:, 0])
    decomp = (_sm(mean["receiver_logits"]) * _sig(mean["shot_cond_logits"])).sum(-1)
    served = {"direct": direct, "decomposed": decomp}.get(shot_mode, 0.5 * (direct + decomp))
    members = np.stack([{"direct": _sig(o["outcome_logits"][:, 0]), "decomposed": o["shot_marg_prob"]}.get(
        shot_mode, 0.5 * (_sig(o["outcome_logits"][:, 0]) + o["shot_marg_prob"])) for o in outs])
    return {"shot_direct": direct, "shot_decomposed": decomp, "shot": served, "shot_members": members,
            "goal10": _sig(mean["outcome_logits"][:, 1]), "concede10": _sig(mean["outcome_logits"][:, 2]),
            "turn5": _sig(mean["outcome_logits"][:, 3]), "poss10": _sig(mean["poss10_logit"]),
            "receiver": _sm(mean["receiver_logits"]), "zone": _sm(mean["zone_logits"].reshape(len(direct), -1)),
            "action_logits": mean["action_logits"]}


def head_report(ds: Dataset10, idx: np.ndarray, pr: dict[str, np.ndarray], sel: np.ndarray) -> dict[str, Any]:
    """All Next10 metrics for the samples ``idx[sel]`` (``pr`` is aligned with ``idx``)."""
    a = ds.a
    i = idx[sel]
    r: dict[str, Any] = {"n": int(len(i))}
    for name, p in (("shot10", pr["shot"]), ("shot10_direct", pr["shot_direct"]),
                    ("shot10_decomposed", pr["shot_decomposed"])):
        r[name] = binary(a["shot10"][i], p[sel])
    for k in ("goal10", "concede10", "turn5"):
        r[k] = binary(a[k][i], pr[k][sel])
    r["poss10"] = binary(a["poss10"][i], pr["poss10"][sel])
    y = a["receiver_idx"][i]
    rk = (y >= 0) & (y < F.N_TEAM)
    if rk.sum() >= MIN_N:
        sc = pr["receiver"][sel][rk][:, : F.N_TEAM]
        n_present = a["node_mask"][i][rk][:, : F.N_TEAM].sum(1)
        r["receiver"] = E.receiver_report(sc, y[rk], n_present)
    lost = y >= 0
    if lost.sum() >= MIN_N:
        r["receiver_lost"] = binary((y[lost] == F.N_TEAM).astype(int), pr["receiver"][sel][lost][:, F.N_TEAM])
    zc = a["zone_cell"][i]
    zk = zc >= 0
    if zk.sum() >= MIN_N:
        txy = a["fut_pos"][i][zk][:, -1, F.BALL].astype(np.float32)
        r["zone"] = zone_report(pr["zone"][sel][zk], zc[zk], txy)
    r["action"] = action_report(pr["action_logits"][sel], a["action_next"][i])
    # ensemble spread vs error for the served shot10
    if pr["shot_members"].shape[0] > 1:
        y = a["shot10"][i]
        ok = y >= 0
        if ok.sum() >= MIN_N:
            spread = pr["shot_members"][:, sel][:, ok].std(0)
            err = np.abs(pr["shot"][sel][ok] - y[ok])
            r["shot10_spread_error_spearman"] = E.spearman(spread, err)
    return r


# ============================================================ baselines

def shot_baselines(ds: Dataset10, idx: np.ndarray, sel: np.ndarray, pr: dict[str, np.ndarray], max_n: int = 1500
                   ) -> dict[str, Any]:
    """Pitch-control + xT ``BaselineModel`` against the model on an evenly spaced subsample of ``idx[sel]``."""
    pos = np.flatnonzero(sel)
    pos = pos[:: max(1, len(pos) // max_n)][:max_n]
    i = idx[pos]
    y = ds.a["shot10"][i]
    ok = y >= 0
    if ok.sum() < MIN_N:
        return {"n": int(ok.sum())}
    b = ds.batch(i, edges=False)
    base = E.baseline_predictions(b, np.arange(len(i)))
    return {"n": int(ok.sum()), "model": binary(y, pr["shot"][pos]), "baseline_pc_xt": binary(y, base["shot10"]),
            "base_rate_brier": float(y[ok].mean() * (1 - y[ok].mean()))}


# ============================================================ D2 checks

def d2_check(models: list, traj, ds: Dataset10, idx: np.ndarray, n: int = 200, device="cpu") -> dict[str, Any]:
    """Largest change of any output when the input is reflected, in float32 and float64 (CPU)."""
    import torch

    from matchlens.tacticsml.train import heads as H
    from matchlens.tacticsml.train.data10 import to_tensors10

    rng = np.random.default_rng(0)
    pick = np.sort(rng.choice(idx, min(n, len(idx)), replace=False))
    res: dict[str, Any] = {"n": int(len(pick))}
    for dtype, name in ((torch.float32, "float32"), (torch.float64, "float64")):
        worst = 0.0
        worst_traj = 0.0
        batch = to_tensors10(ds.batch(pick), "cpu")
        batch = {k: (v.to(dtype) if v.is_floating_point() else v) for k, v in batch.items()}
        for m in models[:2]:
            m = m.to("cpu").to(dtype).eval()
            ref = None
            for g in F.D2:
                sx, sy = F.d2_signs(g)
                s = torch.tensor([sx, sy], dtype=dtype)
                b = dict(batch)
                b["pos"], b["vel"] = batch["pos"] * s, batch["vel"] * s
                with torch.no_grad():
                    o = m(b, average=True)
                probs = {"recv": torch.softmax(o["receiver_logits"], -1), "outcomes": torch.sigmoid(o["outcome_logits"]),
                         "poss": torch.sigmoid(o["poss10_logit"]), "action": torch.softmax(o["action_logits"], -1),
                         "shot_marg": o["shot_marg_prob"],
                         "zone": H.reflect_grid(torch.softmax(o["zone_logits"].flatten(1), -1).view_as(o["zone_logits"]),
                                                s.expand(len(pick), 2))}
                if ref is None:
                    ref = probs
                else:
                    worst = max(worst, max(float((probs[k] - ref[k]).abs().max()) for k in probs))
        if traj is not None:
            traj = traj.to("cpu").to(dtype).eval()
            base = None
            for g in F.D2:
                sx, sy = F.d2_signs(g)
                s = torch.tensor([sx, sy], dtype=dtype)
                b = dict(batch)
                b["pos"], b["vel"] = batch["pos"] * s, batch["vel"] * s
                with torch.no_grad():
                    o = traj(b, average=True)
                mu = o["mu"] * s  # map back
                if base is None:
                    base = (mu, o["pi_logits"], o["log_sigma"])
                else:
                    worst_traj = max(worst_traj, float((mu - base[0]).abs().max()), float((o["pi_logits"] - base[1]).abs().max()),
                                     float((o["log_sigma"] - base[2]).abs().max()))
        res[name] = {"critic_max_abs_prob_change": worst, "trajectory_max_abs_change_m": worst_traj if traj is not None else None}
    return res


# ============================================================ trajectories

def trajectory_section(traj, ds: Dataset10, idx: np.ndarray, device, sources: Sequence[str]) -> dict[str, Any]:
    from matchlens.tacticsml.train.trajectory import evaluate_trajectory

    out: dict[str, Any] = {}
    for masked in (False, True):
        key = "masked" if masked else "clean"
        out[key] = {}
        for src in (*sources, "pooled"):
            sel = idx[(ds.a["fut_valid"][idx, -1, F.BALL]) & ds.a["open_play"][idx]
                      & (np.isin(ds.a["source"][idx], sources) if src == "pooled" else ds.a["source"][idx] == src)]
            if len(sel) < MIN_N:
                continue
            sel = sel[:: max(1, len(sel) // 6000)]
            rng = np.random.default_rng(MASK_SEED) if masked else None
            out[key][src] = evaluate_trajectory(traj, ds, sel, device, rng, ViewMaskConfig(p_apply=1.0))
    return out


# ============================================================ driver

def _subsets(ds: Dataset10, idx: np.ndarray) -> dict[str, np.ndarray]:
    src = ds.a["source"][idx]
    op = ds.a["open_play"][idx]
    out: dict[str, np.ndarray] = {}
    for sit, m in (("open_play", op), ("all", np.ones(len(idx), bool))):
        for s in SOURCES:
            out[f"{s}/{sit}"] = (src == s) & m
        out[f"pooled/{sit}"] = np.isin(src, SOURCES) & m
    return out


def _even(idx: np.ndarray, k: int) -> np.ndarray:
    return idx if len(idx) <= k else idx[np.linspace(0, len(idx) - 1, k).astype(int)]


def run(ckpt_dir: Path | None = None, shards: Path | None = None, legacy_shards: Path | None = None,
        out_dir: Path | None = None, device: str = "auto", abl_max: int = 6000, shot_mode: str = "blend",
        log=print) -> dict[str, Any]:
    import torch

    from matchlens.tacticsml.train.fit import pick_device
    from matchlens.tacticsml.train.next10 import load_critic
    from matchlens.tacticsml.train.trajectory import load_trajectory

    t0 = time.time()
    ckpt_dir, out_dir = Path(ckpt_dir or schema.NEXT10 / "ckpt"), Path(out_dir or schema.NEXT10)
    dev = pick_device(device)
    files = sorted(p for p in ckpt_dir.glob("next10_seed*.pt"))
    if not files:
        raise SystemExit(f"no next10_seed*.pt in {ckpt_dir}")
    loaded = [load_critic(p, dev) for p in files]
    models = [m for m, _ in loaded]
    data = load_next10(SOURCES, shards, legacy_shards, table=loaded[0][1])  # the checkpoint's own club table
    train, test = data["train"], data["test"]
    log(f"test samples {len(test)} ({int(test.a['open_play'].sum())} open play)")
    idx = np.arange(len(test))
    subsets = _subsets(test, idx)
    # ---- main ensemble on the whole test split
    log("main ensemble, clean test")
    outs = predict_members(models, test, idx, dev)
    pr = ensemble(outs, shot_mode)
    report: dict[str, Any] = {"meta": {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "members": [p.name for p in files],
        "shot_mode": shot_mode, "device": str(dev), "sources": list(SOURCES),
        "test_samples": int(len(test)), "train_samples": int(len(train)), "team_table": train.table.names,
        "test_team_known_share": float((test.a["team_ids"][:, 0] > 0).mean()),
        "splits_by_source": {s: {sp: int((d.a["source"] == s).sum()) for sp, d in data.items()} for s in SOURCES}}}
    report["heads"] = {name: head_report(test, idx, pr, sel) for name, sel in subsets.items()}
    # ---- baselines
    zb: dict[str, Any] = {}
    for name in ("pooled/open_play", "sportec/open_play", "skillcorner/open_play"):
        sel = subsets[name] & (test.a["zone_cell"][idx] >= 0)
        if sel.sum() < MIN_N:
            continue
        cur = test.a["pos"][idx[sel], -1, F.BALL].astype(np.float32)
        cell = test.a["zone_cell"][idx[sel]]
        txy = test.a["fut_pos"][idx[sel]][:, -1, F.BALL].astype(np.float32)
        zb[name] = {"model": zone_report(pr["zone"][sel], cell, txy)}
        for bn, bp in zone_baselines(train, cur).items():
            zb[name][bn] = zone_report(bp, cell, txy)
    report["zone_baselines"] = zb
    report["shot_baselines"] = {name: shot_baselines(test, idx, subsets[name], pr)
                                for name in ("pooled/open_play", "sportec/open_play", "skillcorner/open_play",
                                             "statsbomb360/open_play") if subsets[name].sum() >= MIN_N}
    pbase = {}
    for name in ("pooled/open_play", "sportec/open_play", "skillcorner/open_play"):
        i = idx[subsets[name]]
        y = train.a["poss10"][train.a["open_play"]]
        r = float((y[y >= 0] == 1).mean())
        yt = test.a["poss10"][i]
        ok = yt >= 0
        if ok.sum() >= MIN_N:
            pbase[name] = {"train_base_rate": r, "brier_train_base_rate": float(np.mean((r - yt[ok]) ** 2)),
                           "brier_model": float(np.mean((pr["poss10"][subsets[name]][ok] - yt[ok]) ** 2))}
    report["poss10_baselines"] = pbase
    # ---- ablations on a fixed open-play subset of each real source
    abl_pos = np.concatenate([_even(np.flatnonzero(subsets[f"{s}/open_play"]), abl_max) for s in SOURCES])
    abl_pos = np.sort(abl_pos)
    abl_idx = idx[abl_pos]
    abl_subsets = {f"{s}": test.a["source"][abl_idx] == s for s in SOURCES}
    abl_subsets["pooled"] = np.ones(len(abl_idx), bool)

    def abl_report(prd: dict[str, np.ndarray]) -> dict[str, Any]:
        return {k: head_report(test, abl_idx, prd, v) for k, v in abl_subsets.items()}

    ablations: dict[str, Any] = {"subset": f"open-play test, up to {abl_max} evenly spaced samples per source",
                                 "main_clean": abl_report({k: v[abl_pos] if k != "shot_members" else v[:, abl_pos]
                                                           for k, v in pr.items()})}
    log("ablation: main ensemble, view-masked test")
    ablations["main_masked"] = abl_report(ensemble(predict_members(models, test, abl_idx, dev, masked=True), shot_mode))
    log("ablation: FiLM teams set to unknown")
    ablations["main_unknown_teams"] = abl_report(ensemble(predict_members(models, test, abl_idx, dev, unknown_teams=True),
                                                         shot_mode))
    for tag, label in (("_nofilm", "film_off"), ("_noaug", "viewmask_aug_off")):
        fs = sorted(ckpt_dir.glob(f"next10{tag}_seed*.pt"))
        if not fs:
            ablations[label] = "NOT_EVALUATED: no checkpoint"
            continue
        ms = [load_critic(p, dev)[0] for p in fs]
        log(f"ablation: {label}")
        ablations[label] = {"members": [p.name for p in fs],
                            "clean": abl_report(ensemble(predict_members(ms, test, abl_idx, dev), shot_mode)),
                            "masked": abl_report(ensemble(predict_members(ms, test, abl_idx, dev, masked=True), shot_mode))}
        if label == "film_off":  # like with like: the main model's first member alone
            single = ensemble(predict_members(models[:1], test, abl_idx, dev), shot_mode)
            ablations["film_on_single_member"] = abl_report(single)
        if label == "viewmask_aug_off":
            single = ensemble(predict_members(models[:1], test, abl_idx, dev, masked=True), shot_mode)
            ablations["viewmask_aug_on_single_member_masked"] = abl_report(single)
            ablations["viewmask_aug_on_single_member_clean"] = abl_report(ensemble(predict_members(models[:1], test, abl_idx, dev),
                                                                                  shot_mode))
    report["ablations"] = ablations
    # ---- trajectories
    tp = ckpt_dir / "trajectory_seed0.pt"
    traj = load_trajectory(tp, dev) if tp.exists() else None
    if traj is not None:
        log("trajectories")
        report["trajectory"] = trajectory_section(traj, test, idx, dev, ("sportec", "skillcorner"))
    else:
        report["trajectory"] = "NOT_EVALUATED: no trajectory checkpoint"
    # ---- D2
    log("D2 checks")
    d2 = d2_check(models, traj, test, idx[subsets["pooled/open_play"]], device="cpu")
    report["d2"] = d2
    report["targets"] = targets(report)
    report["meta"]["seconds"] = round(time.time() - t0)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report_next10.json").write_text(json.dumps(report, indent=1, default=float))
    (out_dir / "report_next10.md").write_text(to_markdown(report))
    log(f"wrote {out_dir / 'report_next10.md'}")
    return report


def _get(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def targets(r: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []

    def add(tid: str, desc: str, scope: str, value, n, op: str, thr: float) -> None:
        rows.append({"id": tid, "target": desc, "scope": scope, "value": value, "n": n, "status": status(value, op, thr, n or 0)})

    for scope in ("pooled/open_play", "sportec/open_play", "skillcorner/open_play", "statsbomb360/open_play"):
        h = r["heads"].get(scope, {})
        add("shot10_auc", "open-play shot10 AUC >= 0.85", scope, _get(h, "shot10", "auc"), _get(h, "shot10", "n"), ">=", 0.85)
        add("shot10_ece", "open-play shot10 ECE <= 0.03", scope, _get(h, "shot10", "ece"), _get(h, "shot10", "n"), "<=", 0.03)
    for scope in ("pooled/open_play", "sportec/open_play", "skillcorner/open_play"):
        h = r["heads"].get(scope, {})
        add("zone_top5", "ball_zone10 top-5 cell accuracy >= 0.6", scope, _get(h, "zone", "top5"), _get(h, "zone", "n"), ">=", 0.6)
    tr = r.get("trajectory")
    if isinstance(tr, dict):
        for src, m in tr.get("clean", {}).items():
            add("minfde_vs_cv", "player minFDE@10s >= 25% better than constant velocity (K=6 best-of vs 1 CV)", f"{src}/open_play",
                m.get("player_minFDE_vs_cv"), m.get("n_samples"), ">=", 0.25)
    d2 = r.get("d2", {})
    for dt in ("float32", "float64"):
        add("d2", f"D2 error < 1e-5 ({dt}, critic probabilities)", "test open play", _get(d2, dt, "critic_max_abs_prob_change"), d2.get("n"), "<", 1e-5)
        v = _get(d2, dt, "trajectory_max_abs_change_m")
        if v is not None and dt == "float64":  # float32 metres cannot resolve 1e-5 (ULP of 50 m is 4e-6); shown in the D2 table
            add("d2_traj", f"D2 error < 1e-5 ({dt}, trajectory mu in m / pi / log sigma)", "test open play", v, d2.get("n"), "<", 1e-5)
    return rows


# ---------------------------------------------------------------- markdown

def _f(v: Any, nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    return f"{v:.{nd}f}"


def _vs(t: dict[str, Any], a: str, b: str) -> float | None:
    return 1.0 - t[a] / t[b] if t.get(a) is not None and t.get(b) else None


def to_markdown(r: dict[str, Any]) -> str:
    m = r["meta"]
    L = ["# Next10 report (round 1)", "",
         f"Generated {m['created']} by `train/evaluate10.py` ({m['seconds']} s, device {m['device']}). Checkpoints: "
         f"{', '.join(m['members'])}. Test samples {m['test_samples']:,}; train samples {m['train_samples']:,}. Shot probability "
         f"served as `{m['shot_mode']}`. Teams in the FiLM table: {len(m['team_table'])}; test samples with a known "
         f"possession-team id: {_f(m['test_team_known_share'])}.", "",
         "Every number below was computed by this run on the held-out `test` split. Splits per source: "
         + "; ".join(f"{s}: " + ", ".join(f"{k} {v:,}" for k, v in d.items()) for s, d in m["splits_by_source"].items()) + ".", "",
         "## Targets", "", "| target | scope | value | n | status |", "|---|---|---|---|---|"]
    for t in r["targets"]:
        L.append(f"| {t['target']} | {t['scope']} | {_f(t['value'], 6 if t['id'].startswith('d2') else 3)} | {_f(t['n'])} | **{t['status']}** |")
    L += ["", "## Heads per source (test split)", "",
          "| scope | n | shot10 AUC | Brier (base) | ECE | direct AUC | decomposed AUC | goal10 AUC | turn5 AUC | poss10 AUC | poss10 Brier (base) | recv top1 / top3 | zone top1 / 3 / 5 | zone NLL | RPS x / y | dist m |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, h in r["heads"].items():
        s, d, dc, p = h.get("shot10", {}), h.get("shot10_direct", {}), h.get("shot10_decomposed", {}), h.get("poss10", {})
        z, rc = h.get("zone", {}), h.get("receiver", {})
        L.append(f"| {name} | {_f(h['n'])} | {_f(s.get('auc'))} | {_f(s.get('brier'), 4)} ({_f(s.get('brier_base_rate'), 4)}) | {_f(s.get('ece'), 4)} | "
                 f"{_f(d.get('auc'))} | {_f(dc.get('auc'))} | {_f(h.get('goal10', {}).get('auc'))} | {_f(h.get('turn5', {}).get('auc'))} | "
                 f"{_f(p.get('auc'))} | {_f(p.get('brier'), 4)} ({_f(p.get('brier_base_rate'), 4)}) | "
                 f"{_f(rc.get('top1'))} / {_f(rc.get('top3'))} | {_f(z.get('top1'))} / {_f(z.get('top3'))} / {_f(z.get('top5'))} | "
                 f"{_f(z.get('nll'))} | {_f(z.get('rps_x'), 4)} / {_f(z.get('rps_y'), 4)} | {_f(z.get('dist_m'), 2)} |")
    L += ["", "## ball_zone10 against baselines (open play, samples with a 10 s ball position)", "",
          "| scope | model / baseline | n | top1 | top3 | top5 | NLL | RPS x | RPS y | dist m |", "|---|---|---|---|---|---|---|---|---|---|"]
    for scope, d in r["zone_baselines"].items():
        for bn, z in d.items():
            L.append(f"| {scope} | {bn} | {_f(z.get('n'))} | {_f(z.get('top1'))} | {_f(z.get('top3'))} | {_f(z.get('top5'))} | {_f(z.get('nll'))} | "
                     f"{_f(z.get('rps_x'), 4)} | {_f(z.get('rps_y'), 4)} | {_f(z.get('dist_m'), 2)} |")
    L += ["", "## shot10 against the pitch-control + xT baseline and the base rate (open play, even subsample)", "",
          "| scope | n | model AUC | baseline AUC | model Brier | baseline Brier | base-rate Brier |", "|---|---|---|---|---|---|---|"]
    for scope, d in r["shot_baselines"].items():
        L.append(f"| {scope} | {_f(d.get('n'))} | {_f(_get(d, 'model', 'auc'))} | {_f(_get(d, 'baseline_pc_xt', 'auc'))} | "
                 f"{_f(_get(d, 'model', 'brier'), 4)} | {_f(_get(d, 'baseline_pc_xt', 'brier'), 4)} | {_f(d.get('base_rate_brier'), 4)} |")
    L += ["", "## poss10 Brier against the train base rate", "", "| scope | train base rate | Brier base rate | Brier model |", "|---|---|---|---|"]
    for scope, d in r["poss10_baselines"].items():
        L.append(f"| {scope} | {_f(d['train_base_rate'])} | {_f(d['brier_train_base_rate'], 4)} | {_f(d['brier_model'], 4)} |")
    a = r["heads"].get("skillcorner/open_play", {}).get("action", {})
    L += ["", "## action_next (SkillCorner open play: the only source with carry events)", "",
          f"n {_f(a.get('n'))}, accuracy {_f(a.get('acc'))} (majority class {_f(a.get('acc_majority'))}), NLL {_f(a.get('nll'))} "
          f"(class prior {_f(a.get('nll_prior'))})."]
    ab = r["ablations"]
    L += ["", f"## Ablations ({ab['subset']})", "",
          "| variant | input | scope | shot10 AUC | Brier | ECE | zone top5 | zone NLL | poss10 AUC | recv top3 |", "|---|---|---|---|---|---|---|---|---|---|"]

    def abl_rows(label: str, inp: str, d: Any) -> None:
        if not isinstance(d, dict):
            L.append(f"| {label} | {inp} | | {d} | | | | | | |")
            return
        for scope in ("pooled", "sportec", "skillcorner", "statsbomb360"):
            h = d.get(scope)
            if not h:
                continue
            s = h.get("shot10", {})
            L.append(f"| {label} | {inp} | {scope} | {_f(s.get('auc'))} | {_f(s.get('brier'), 4)} | {_f(s.get('ece'), 4)} | "
                     f"{_f(_get(h, 'zone', 'top5'))} | {_f(_get(h, 'zone', 'nll'))} | {_f(_get(h, 'poss10', 'auc'))} | {_f(_get(h, 'receiver', 'top3'))} |")

    abl_rows("main ensemble", "clean", ab.get("main_clean"))
    abl_rows("main ensemble", "view-masked", ab.get("main_masked"))
    abl_rows("main ensemble, teams -> unknown", "clean", ab.get("main_unknown_teams"))
    abl_rows("FiLM on (member 0)", "clean", ab.get("film_on_single_member"))
    fo = ab.get("film_off")
    abl_rows("FiLM off", "clean", fo.get("clean") if isinstance(fo, dict) else fo)
    abl_rows("view-mask aug on (member 0)", "clean", ab.get("viewmask_aug_on_single_member_clean"))
    abl_rows("view-mask aug on (member 0)", "view-masked", ab.get("viewmask_aug_on_single_member_masked"))
    na = ab.get("viewmask_aug_off")
    abl_rows("view-mask aug off", "clean", na.get("clean") if isinstance(na, dict) else na)
    abl_rows("view-mask aug off", "view-masked", na.get("masked") if isinstance(na, dict) else na)
    L += ["", "Receiver decomposition on / off is the `direct AUC` and `decomposed AUC` columns of the table above: the "
          "same model scored with its direct shot10 head and with `sum_i P(shot | recv i) P(recv i)`.", ""]
    tr = r["trajectory"]
    L += ["## Trajectories (open play, K = 6, 1 Hz to 10 s)", ""]
    if isinstance(tr, str):
        L.append(tr)
    else:
        L += ["| input | source | n | player minADE | minFDE | top-1 FDE | CV FDE | still FDE | minFDE vs CV | minFDE vs still | top-1 vs still | ball minFDE | ball CV FDE | minJADE | minJFDE | CV JFDE |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for key in ("clean", "masked"):
            for src, t in tr.get(key, {}).items():
                L.append(f"| {key} | {src} | {_f(t.get('n_samples'))} | {_f(t.get('player_minADE'), 2)} | {_f(t.get('player_minFDE'), 2)} | "
                         f"{_f(t.get('player_top1_FDE'), 2)} | {_f(t.get('player_cv_FDE'), 2)} | {_f(t.get('player_static_FDE'), 2)} | "
                         f"{_f(t.get('player_minFDE_vs_cv'))} | {_f(_vs(t, 'player_minFDE', 'player_static_FDE'))} | "
                         f"{_f(_vs(t, 'player_top1_FDE', 'player_static_FDE'))} | {_f(t.get('ball_minFDE'), 2)} | {_f(t.get('ball_cv_FDE'), 2)} | "
                         f"{_f(t.get('minJADE'), 2)} | {_f(t.get('minJFDE'), 2)} | {_f(t.get('cv_JFDE'), 2)} |")
        L += ["", "Distances are in metres. `minFDE` is the best of 6 modes per player; constant velocity is one deterministic "
              "guess, so the comparison favours the model; `top-1 FDE` (most probable mode) is the like-for-like single guess. "
              "`still FDE` is the error of standing at the last position: at 10 s constant velocity is worse than standing still "
              "(players change direction), so \"vs CV\" is the weaker yardstick and \"vs still\" the honest one."]
    d2 = r["d2"]
    L += ["", "## D2 invariance", "", f"{d2['n']} test samples, every output compared across the four reflections.", "",
          "| dtype | critic max abs probability change | trajectory max abs change (m / logits) |", "|---|---|---|"]
    for dt in ("float32", "float64"):
        L.append(f"| {dt} | {_f(_get(d2, dt, 'critic_max_abs_prob_change'), 9)} | {_f(_get(d2, dt, 'trajectory_max_abs_change_m'), 9)} |")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train.evaluate10", description=__doc__)
    p.add_argument("--ckpt-dir", default=None)
    p.add_argument("--shards", default=None)
    p.add_argument("--legacy-shards", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--device", default="auto")
    p.add_argument("--ablation-max", type=int, default=6000)
    p.add_argument("--shot-mode", default="blend", choices=("blend", "direct", "decomposed"))
    a = p.parse_args(argv)
    run(a.ckpt_dir, Path(a.shards) if a.shards else None, Path(a.legacy_shards) if a.legacy_shards else None, a.out_dir,
        a.device, a.ablation_max, a.shot_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
