"""Evaluation of the trained tactical model on the held-out test split.

``python -m matchlens.tacticsml.train evaluate`` (``npm run tacticsml:eval``) loads the checkpoints in
``backend/data/local/tacticsml/ckpt/`` (a ``gat_seed{S}.pt`` ensemble and, if present, ``cvae.pt``), runs them
on the ``test`` split of the shards and writes ``backend/data/local/tacticsml/eval/report.json`` and
``report.md``. Torch is imported lazily: the metric functions below are numpy only.

What is measured, per source and per situation (``open_play`` / ``corner`` / ... when the shards carry a
``set_piece`` array, otherwise just ``all``):

* receiver: top-1 / top-3 accuracy among the 11 possession-team slots (samples whose receiver is a known
  player; "ball lost" samples are scored separately as a binary head);
* outcome heads ``shot10 goal10 concede10 turn5``: AUC, Brier (and the base-rate Brier), ECE with 15 equal
  width bins, and the reliability table;
* ensemble spread vs error: Spearman / Pearson correlation of the across-member standard deviation of a
  prediction with its error, plus the error by spread quartile;
* D2 invariance: largest change of any output when the input is reflected, on 200 test samples;
* learned vs ``BaselineModel`` (pitch control + xT) on the same sub-sample, where the baseline produces a
  comparable output (receiver among the non-carrier teammates, shot / goal / concede / turnover);
* cVAE: displacement error against the real future vs standing still, the share of proposals that raise
  the critic's target probability (with a random-direction control of equal magnitude) and a check that
  the speed clamp, the move cap and the locked / non-movable nodes are respected.

The ensemble prediction is the mean of the members' logits (as in ``fit.evaluate_ensemble``). Every number
in the report comes from running the models on the shards named in its ``meta``; nothing is hard-coded.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml.train import schema

OUTCOMES = ("shot10", "goal10", "concede10", "turn5")
N_SLOTS = F.N_TEAM  # receiver candidates: possession-team slots 0..10
LOST = N_SLOTS  # index of "ball lost" in the 12-way receiver output
ECE_BINS = 15
CKPT_DIR = schema.ROOT / "ckpt"
EVAL_DIR = schema.ROOT / "eval"
BATCH_KEYS = ("pos", "vel", "vel_mask", "node_feat", "edge_feat", "node_mask")
SET_PIECES = {0: "open_play", 1: "corner", 2: "free_kick", 3: "throw_in", 4: "goal_kick"}
MIN_TARGET_N = 30  # a target is not scored on fewer labelled samples
TACTICAI_CORNER_REFERENCE = 0.78  # the figure quoted in the project plan (see docs/TACTICSML.md)
SYNTHETIC_SOURCES = frozenset({"synthetic"})

# Targets from the plan: (id, description, metric path, op, threshold)
TARGETS = (
    ("receiver_open_top3", "open-play receiver top-3 accuracy >= 0.85", "receiver_top3", ">=", 0.85),
    ("receiver_corner_top3", "corner receiver top-3 accuracy >= 0.70", "receiver_top3", ">=", 0.70),
    ("shot10_auc", "shot10 AUC >= 0.80", "shot10_auc", ">=", 0.80),
    ("shot10_ece", "shot10 ECE (15 bins) <= 0.03", "shot10_ece", "<=", 0.03),
    ("d2_equivariance", "D2 equivariance error (max abs probability change) < 1e-5", "d2", "<", 1e-5),
)


# ============================================================ metric functions (numpy only)

def rankdata(a: np.ndarray) -> np.ndarray:
    """1-based ranks, ties get the average rank."""
    a = np.asarray(a, dtype=float)
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    return (np.cumsum(cnt) - (cnt - 1) / 2.0)[inv]


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Area under the ROC curve (Mann-Whitney U, ties averaged); nan when one class is missing."""
    y = np.asarray(y).astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(p)
    return float((r[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    return float(np.mean((np.asarray(p, dtype=float) - y) ** 2)) if y.size else float("nan")


def _bin_index(p: np.ndarray, bins: int) -> np.ndarray:
    return np.clip((np.asarray(p, dtype=float) * bins).astype(int), 0, bins - 1)


def reliability(y: np.ndarray, p: np.ndarray, bins: int = ECE_BINS) -> list[dict[str, Any]]:
    """One row per equal-width probability bin: ``n``, ``mean_pred``, ``frac_pos`` (None when empty)."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    b = _bin_index(p, bins)
    rows = []
    for i in range(bins):
        sel = b == i
        n = int(sel.sum())
        rows.append({"bin": i, "lo": i / bins, "hi": (i + 1) / bins, "n": n,
                     "mean_pred": float(p[sel].mean()) if n else None,
                     "frac_pos": float(y[sel].mean()) if n else None})
    return rows


def ece(y: np.ndarray, p: np.ndarray, bins: int = ECE_BINS) -> float:
    """Expected calibration error: sum over bins of (bin share) * |frac_pos - mean_pred|."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if not y.size:
        return float("nan")
    total = 0.0
    for r in reliability(y, p, bins):
        if r["n"]:
            total += r["n"] / y.size * abs(r["frac_pos"] - r["mean_pred"])
    return float(total)


def topk_accuracy(scores: np.ndarray, y: np.ndarray, k: int) -> float:
    """Share of rows whose true class is among the ``k`` highest scores (ties count against the model)."""
    scores, y = np.asarray(scores, dtype=float), np.asarray(y, dtype=int)
    if not len(y):
        return float("nan")
    true = scores[np.arange(len(y)), y][:, None]
    rank = (scores > true).sum(1) + (scores == true).sum(1) - 1
    return float((rank < k).mean())


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.size < 3 or a.std() < 1e-12 or b.std() < 1e-12:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    return pearson(rankdata(a), rankdata(b))


def binary_report(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    """All binary-head metrics of one head. ``y`` in {0,1}, ``p`` probabilities of the same length."""
    y = np.asarray(y)
    if not y.size:
        return {"n": 0}
    base = float(y.mean())
    b = brier(y, p)
    ref = base * (1.0 - base)
    return {"n": int(y.size), "positives": int(y.sum()), "base_rate": base, "auc": auc(y, p), "brier": b,
            "brier_base_rate": ref, "brier_skill": (1.0 - b / ref) if ref > 0 else float("nan"),
            "ece": ece(y, p), "mean_pred": float(np.mean(p)), "reliability": reliability(y, p)}


def receiver_report(scores: np.ndarray, y: np.ndarray, n_present: np.ndarray | None = None) -> dict[str, Any]:
    """Top-1 / top-3 among the 11 slots; ``scores`` (n,11) with -inf / very low for absent players."""
    if not len(y):
        return {"n": 0}
    out = {"n": int(len(y)), "top1": topk_accuracy(scores, y, 1), "top3": topk_accuracy(scores, y, 3)}
    if n_present is not None:
        present = np.maximum(np.asarray(n_present, dtype=float), 1.0)
        out["chance_top1"] = float(np.mean(1.0 / present))
        out["chance_top3"] = float(np.mean(np.minimum(3.0, present) / present))
    return out


def spread_error_report(spread: np.ndarray, err: np.ndarray, quantiles: int = 4) -> dict[str, Any]:
    """Is the uncertainty informative: correlation of ensemble spread with error, and error by spread quartile."""
    spread, err = np.asarray(spread, dtype=float), np.asarray(err, dtype=float)
    out: dict[str, Any] = {"n": int(spread.size), "spearman": spearman(spread, err), "pearson": pearson(spread, err),
                           "mean_spread": float(spread.mean()) if spread.size else float("nan")}
    if spread.size >= quantiles * 2:
        edges = np.unique(np.quantile(spread, np.linspace(0, 1, quantiles + 1)))
        which = np.clip(np.searchsorted(edges, spread, side="right") - 1, 0, len(edges) - 2)
        out["by_spread_quantile"] = [
            {"quantile": int(q), "n": int((which == q).sum()), "mean_spread": float(spread[which == q].mean()),
             "mean_error": float(err[which == q].mean())}
            for q in range(len(edges) - 1) if (which == q).any()]
    return out


# ============================================================ prediction containers

def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def situation_masks(arrays: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Boolean masks per situation. ``all`` always; ``open_play`` / ``corner`` / ... only when the shards
    carry a ``set_piece`` array (int8 codes of ``SET_PIECES`` or strings) and the group is non-empty."""
    n = len(arrays["receiver_idx"])
    out = {"all": np.ones(n, dtype=bool)}
    sp = arrays.get("set_piece")
    if sp is None:
        return out
    sp = np.asarray(sp)
    if sp.dtype.kind in "US":
        names = np.array([str(s).replace("-", "_") for s in sp])
    else:
        names = np.array([SET_PIECES.get(int(c), "unknown") for c in sp])
    for name in list(SET_PIECES.values()):
        m = names == name
        if m.any():
            out[name] = m
    return out


def ensemble_outputs(recv_logits: np.ndarray, out_logits: np.ndarray) -> dict[str, np.ndarray]:
    """Members' logits ``(M,n,12)`` / ``(M,n,4)`` -> ensemble (mean logit) and per-member probabilities."""
    return {"recv_p": _softmax(recv_logits.mean(0)), "out_p": _sigmoid(out_logits.mean(0)),
            "recv_members": _softmax(recv_logits), "out_members": _sigmoid(out_logits)}


def evaluate_group(lab: dict[str, np.ndarray], recv_logits: np.ndarray, out_logits: np.ndarray) -> dict[str, Any]:
    """Metrics of one group of samples. ``lab`` holds ``receiver_idx``, the outcome labels and ``node_mask``;
    ``recv_logits`` / ``out_logits`` are the members' logits for the same samples."""
    n = len(lab["receiver_idx"])
    if not n:
        return {"n": 0}
    ens = ensemble_outputs(recv_logits, out_logits)
    n_members = recv_logits.shape[0]
    res: dict[str, Any] = {"n": n, "n_members": n_members}

    y = lab["receiver_idx"]
    known = y >= 0
    ok = known & (y < N_SLOTS)
    present = lab["node_mask"][:, :N_SLOTS].sum(1)
    scores = np.where(lab["node_mask"][:, :N_SLOTS] > 0, recv_logits.mean(0)[:, :N_SLOTS], -np.inf)
    res["receiver"] = receiver_report(scores[ok], y[ok], present[ok])
    if known.any():
        res["receiver"]["lost"] = binary_report((y[known] == LOST).astype(int), ens["recv_p"][known, LOST])
    res["outcomes"] = {}
    for i, name in enumerate(OUTCOMES):
        sel = lab[name] >= 0
        if sel.any():
            res["outcomes"][name] = binary_report(lab[name][sel], ens["out_p"][sel, i])

    sve: dict[str, Any] = {"n_members": n_members}
    if n_members >= 2:
        for i, name in enumerate(OUTCOMES):
            sel = lab[name] >= 0
            if sel.sum() >= 3:
                spread = ens["out_members"][:, sel, i].std(0)
                sve[name] = spread_error_report(spread, np.abs(ens["out_p"][sel, i] - lab[name][sel]))
        if ok.sum() >= 3:
            std = ens["recv_members"][:, :, :N_SLOTS].std(0)
            m = lab["node_mask"][:, :N_SLOTS]
            spread = (std * m).sum(1) / np.maximum(m.sum(1), 1.0)
            err = 1.0 - ens["recv_p"][np.arange(n), np.clip(y, 0, LOST)]
            sve["receiver"] = spread_error_report(spread[ok], err[ok])
    else:
        sve["note"] = "needs at least 2 ensemble members"
    res["spread_vs_error"] = sve
    return res


# ============================================================ data and models

def _label_view(arrays: dict[str, np.ndarray], idx: np.ndarray | None = None) -> dict[str, np.ndarray]:
    keys = ("receiver_idx", *OUTCOMES, "node_mask", "carrier_index", "set_piece")
    return {k: (arrays[k] if idx is None else arrays[k][idx]) for k in keys if k in arrays}


def _even_subset(n: int, k: int) -> np.ndarray:
    return np.arange(n) if n <= k else np.unique(np.linspace(0, n - 1, k).astype(int))


def load_test_sources(root: Path | None, sources: Sequence[str] | None, log=print) -> dict[str, dict[str, np.ndarray]]:
    """``{source: arrays}`` of the test split (``schema.read_shards``). Sources without test shards are skipped."""
    index = schema.ShardIndex.load(root)
    names = sorted({s.source for s in index.select(sources, "test")})
    out = {}
    for name in names:
        try:
            out[name] = schema.read_shards([name], "test", root)
            log(f"test split {name}: {len(out[name]['pos'])} samples")
        except schema.DataUnavailable as exc:
            log(f"skipping {name}: {exc}")
    return out


def load_ensemble(ckpt_dir: Path, device: str = "cpu"):
    """Members listed in ``ensemble.json`` (or every ``gat_seed*.pt``) as eval-mode ``TacticsGAT`` models."""
    from matchlens.tacticsml.train import fit

    info_path = ckpt_dir / "ensemble.json"
    info = json.loads(info_path.read_text()) if info_path.exists() else {}
    files = [m["file"] for m in info.get("members", [])] or sorted(p.name for p in ckpt_dir.glob("gat_seed*.pt"))
    if not files:
        raise SystemExit(f"no gat_seed*.pt checkpoints under {ckpt_dir}; train first (npm run tacticsml:train)")
    models = [fit.load_member(ckpt_dir / f, device) for f in files]
    return models, files, info


def predict_members(models: list, arrays: dict[str, np.ndarray], device, batch: int = 128
                    ) -> tuple[np.ndarray, np.ndarray]:
    """Frame-averaged logits of every member: ``(M,n,12)`` receiver and ``(M,n,4)`` outcomes (float64)."""
    import torch

    from matchlens.tacticsml.train.model import to_tensors

    n = len(arrays["pos"])
    recv = np.zeros((len(models), n, N_SLOTS + 1))
    out = np.zeros((len(models), n, len(OUTCOMES)))
    with torch.no_grad():
        for s in range(0, n, batch):
            sl = slice(s, min(s + batch, n))
            b = to_tensors({k: arrays[k][sl] for k in BATCH_KEYS}, device)
            for m, model in enumerate(models):
                model.eval()
                o = model(b, average=True)
                recv[m, sl] = o["receiver_logits"].float().cpu().numpy()
                out[m, sl] = o["outcome_logits"].float().cpu().numpy()
    return recv, out


# ============================================================ D2 invariance

def d2_invariance(models: list, arrays: dict[str, np.ndarray], n: int = 200) -> dict[str, Any]:
    """Reflect the inputs by every non-identity D2 element and measure how much any output moves.

    Runs on the CPU in float32 (what the models are trained and exported in) and, for the first member,
    in float64 to separate numerical noise from a real symmetry break. Edge features are D2 invariant
    (distances) and are not touched.
    """
    import copy

    import torch

    from matchlens.tacticsml.train.model import to_tensors

    idx = _even_subset(len(arrays["pos"]), n)
    base = to_tensors({k: arrays[k][idx] for k in BATCH_KEYS}, "cpu")

    def reflect(b: dict, g: str, dtype) -> dict:
        s = torch.tensor(F.d2_signs(g), dtype=dtype)
        out = {k: v.to(dtype) for k, v in b.items()}
        out["pos"], out["vel"] = out["pos"] * s, out["vel"] * s
        return out

    def run(model, b):
        with torch.no_grad():
            o = model.eval()(b, average=True)
        return o["receiver_logits"].numpy(), o["outcome_logits"].numpy()

    cpu_models = [m.to("cpu") for m in models]
    ref = [run(m, base) for m in cpu_models]
    worst = {"receiver_prob": 0.0, "outcome_prob": 0.0, "receiver_logit": 0.0, "outcome_logit": 0.0}
    worst_member = dict(worst)
    ens_ref = (np.mean([r[0] for r in ref], 0), np.mean([r[1] for r in ref], 0))
    for g in F.D2[1:]:
        b = reflect(base, g, torch.float32)
        outs = [run(m, b) for m in cpu_models]
        er, eo = np.mean([o[0] for o in outs], 0), np.mean([o[1] for o in outs], 0)
        worst["receiver_prob"] = max(worst["receiver_prob"], float(np.abs(_softmax(er) - _softmax(ens_ref[0])).max()))
        worst["outcome_prob"] = max(worst["outcome_prob"], float(np.abs(_sigmoid(eo) - _sigmoid(ens_ref[1])).max()))
        worst["receiver_logit"] = max(worst["receiver_logit"], float(np.abs(er - ens_ref[0]).max()))
        worst["outcome_logit"] = max(worst["outcome_logit"], float(np.abs(eo - ens_ref[1]).max()))
        for (r0, o0), (r1, o1) in zip(ref, outs):
            worst_member["receiver_prob"] = max(worst_member["receiver_prob"],
                                                float(np.abs(_softmax(r1) - _softmax(r0)).max()))
            worst_member["outcome_prob"] = max(worst_member["outcome_prob"],
                                               float(np.abs(_sigmoid(o1) - _sigmoid(o0)).max()))
            worst_member["receiver_logit"] = max(worst_member["receiver_logit"], float(np.abs(r1 - r0).max()))
            worst_member["outcome_logit"] = max(worst_member["outcome_logit"], float(np.abs(o1 - o0).max()))
    # float64 on the first member
    m64 = copy.deepcopy(cpu_models[0]).double()
    b64 = {k: v.double() for k, v in base.items()}
    r0, o0 = run(m64, b64)
    f64 = 0.0
    for g in F.D2[1:]:
        r1, o1 = run(m64, reflect(b64, g, torch.float64))
        f64 = max(f64, float(np.abs(_softmax(r1) - _softmax(r0)).max()), float(np.abs(_sigmoid(o1) - _sigmoid(o0)).max()))
    max_prob = max(worst["receiver_prob"], worst["outcome_prob"])
    return {"n_samples": int(len(idx)), "elements_tested": list(F.D2[1:]),
            "ensemble": worst, "members_worst": worst_member,
            "max_abs_prob_error": max_prob,
            "max_abs_prob_error_members": max(worst_member["receiver_prob"], worst_member["outcome_prob"]),
            "max_abs_prob_error_float64_member0": f64}


# ============================================================ baseline comparison

def row_to_tframe(a: dict[str, np.ndarray], i: int):
    """One shard sample -> ``TFrame`` in absolute coordinates. The shard is canonical (possession team attacks
    +x), so the possession team is "poss" attacking right. Player ids are ``n<node index>``; no history is kept
    (the baseline only uses the last frame and the velocities)."""
    from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH
    from matchlens.tacticsml.contract import TBall, TFrame, TPlayer

    pos, vel, vm = a["pos"][i, -1], a["vel"][i, -1], a["vel_mask"][i]
    nf, mask = a["node_feat"][i], a["node_mask"][i]
    centre = np.array([F.HALF_L, F.HALF_W])
    players = []
    for j in range(2 * F.N_TEAM):
        if mask[j] == 0:
            continue
        x, y = np.clip(pos[j] + centre, [0.0, 0.0], [PITCH_LENGTH, PITCH_WIDTH])
        known = bool(vm[j] > 0)
        players.append(TPlayer(player_id=f"n{j}", team_id="poss" if j < F.N_TEAM else "opp", x=float(x), y=float(y),
                               vx=float(vel[j, 0]) if known else None, vy=float(vel[j, 1]) if known else None,
                               is_gk=bool(nf[j, F.F_IS_GK] > 0)))
    bx, by = np.clip(pos[F.BALL] + centre, [0.0, 0.0], [PITCH_LENGTH, PITCH_WIDTH])
    ci = int(a["carrier_index"][i])
    return TFrame(players=players, ball=TBall(x=float(bx), y=float(by), vx=float(vel[F.BALL, 0]),
                                              vy=float(vel[F.BALL, 1])),
                  carrier_id=f"n{ci}" if ci >= 0 else None, possession_team_id="poss",
                  attacking_right={"poss": True, "opp": False}, elapsed=float(a["elapsed"][i]))


def baseline_predictions(a: dict[str, np.ndarray], idx: np.ndarray) -> dict[str, np.ndarray]:
    """``BaselineModel`` outputs for the samples ``idx``: receiver scores ``(n,11)`` (-inf where it gives no
    probability: the carrier, absent players) and the shot / goal / concede / turnover probabilities."""
    from matchlens.tacticsml.baseline import BaselineModel

    model = BaselineModel()
    recv = np.full((len(idx), N_SLOTS), -np.inf)
    out = {k: np.zeros(len(idx)) for k in OUTCOMES}
    for r, i in enumerate(idx):
        res = model.score(row_to_tframe(a, int(i)))
        for pid, p in res.receiver_probs.items():
            recv[r, int(pid[1:])] = p
        out["shot10"][r], out["goal10"][r] = res.shot_prob, res.goal_prob
        out["concede10"][r], out["turn5"][r] = res.concede_prob, res.turnover_prob
    return {"receiver_scores": recv, **out}


def compare_with_baseline(a: dict[str, np.ndarray], recv_logits: np.ndarray, out_logits: np.ndarray,
                          max_samples: int) -> dict[str, Any]:
    """Learned vs baseline on the same evenly spaced sub-sample of one source.

    Receiver: only samples whose receiver is a known player other than the carrier (the baseline never
    proposes the carrier), and the learned scores get the carrier slot removed too, so both rank the same
    candidates. Outcome heads: all labelled samples of the sub-sample.
    """
    idx = _even_subset(len(a["pos"]), max_samples)
    t0 = time.time()
    base = baseline_predictions(a, idx)
    seconds = time.time() - t0
    sub = _label_view(a, idx)
    ens = ensemble_outputs(recv_logits[:, idx], out_logits[:, idx])
    y = sub["receiver_idx"]
    carrier = sub["carrier_index"]
    ok = (y >= 0) & (y < N_SLOTS) & (y != carrier)
    learned = np.where(sub["node_mask"][:, :N_SLOTS] > 0, recv_logits[:, idx].mean(0)[:, :N_SLOTS], -np.inf)
    rows = np.flatnonzero(ok)
    learned[rows, np.clip(carrier[rows], 0, N_SLOTS - 1)] = -np.inf
    present = sub["node_mask"][:, :N_SLOTS].sum(1) - (carrier >= 0)
    res: dict[str, Any] = {
        "n_subsample": int(len(idx)), "baseline_seconds": seconds, "baseline_version": "baseline-pc-xt",
        "receiver": {"n": int(ok.sum()),
                     "learned": receiver_report(learned[ok], y[ok], present[ok]),
                     "baseline": receiver_report(base["receiver_scores"][ok], y[ok], present[ok])},
        "outcomes": {}}
    for i, name in enumerate(OUTCOMES):
        sel = sub[name] >= 0
        if not sel.any():
            continue
        lr = binary_report(sub[name][sel], ens["out_p"][sel, i])
        br = binary_report(sub[name][sel], base[name][sel])
        keep = ("n", "base_rate", "auc", "brier", "brier_skill", "ece")
        res["outcomes"][name] = {"learned": {k: lr[k] for k in keep}, "baseline": {k: br[k] for k in keep}}
    return res


# ============================================================ cVAE

def _moved_graph(b: dict, delta):
    """The graph after adding ``delta`` (B,N,2) to the last-step positions; distances recomputed."""
    pos = b["pos"].clone()
    last = pos[:, -1] + delta
    pos[:, -1] = last
    d = ((last[:, :, None] - last[:, None]) ** 2).sum(-1).add(1e-6).sqrt() / 10.0
    nm = b["node_mask"]
    ef = b["edge_feat"].clone()
    ef[..., F.E_DISTANCE] = d * nm[:, :, None] * nm[:, None, :]
    return {**b, "pos": pos, "edge_feat": ef}


def _critic(models: list, graph: dict, chunk: int = 128) -> dict[str, Any]:
    """Ensemble critic (mean logit, D2 averaged): shot / turnover probabilities and the receiver softmax."""
    import torch

    n = graph["pos"].shape[0]
    shot, turn, recv = [], [], []
    with torch.no_grad():
        for s in range(0, n, chunk):
            sub = {k: graph[k][s:s + chunk] for k in BATCH_KEYS}
            outs = [m(sub, average=True) for m in models]
            ol = torch.stack([o["outcome_logits"] for o in outs]).mean(0)
            rl = torch.stack([o["receiver_logits"] for o in outs]).mean(0)
            shot.append(torch.sigmoid(ol[:, 0]))
            turn.append(torch.sigmoid(ol[:, 3]))
            recv.append(torch.softmax(rl, -1))
    return {"shot": torch.cat(shot), "turn": torch.cat(turn), "recv": torch.cat(recv)}


def _batches(b: dict, step: int):
    n = b["pos"].shape[0]
    for s in range(0, n, step):
        yield {k: v[s:s + step] for k, v in b.items()}


def evaluate_cvae(models: list, cvae, arrays: dict[str, np.ndarray], device, n_samples: int = 128, k: int = 8,
                  seed: int = 0) -> dict[str, Any]:
    """cVAE checks on ``n_samples`` evenly spaced test samples (see the module docstring)."""
    import torch

    from matchlens.tacticsml.train import cvae as cv
    from matchlens.tacticsml.train.model import to_tensors

    gen = torch.Generator().manual_seed(seed)
    idx = _even_subset(len(arrays["pos"]), n_samples)
    keys = BATCH_KEYS + ("receiver_idx", "shot10", "turn5", "target_pos", "target_valid")
    full = to_tensors({kk: arrays[kk][idx] for kk in keys if kk in arrays}, device)
    n = len(idx)
    res: dict[str, Any] = {"n_samples": n, "k_proposals": k, "critic": "ensemble mean logit, D2 averaged"}

    # --- reconstruction against the real future vs standing still (condition: retain, both teams) ---
    if "target_pos" in full and bool((full["target_valid"] > 0).any()):
        valid = full["target_valid"] > 0
        v = {kk: vv[valid] for kk, vv in full.items()}
        nv = int(valid.sum())
        cond = cv.Condition.make(v, "retain", "both")
        mov = cv.movable_mask(v["node_mask"], cond.team, cond.locked)
        delta = (v["target_pos"] - v["pos"][:, -1]) * mov[..., None]
        denom = mov.sum(1).clamp_min(1.0)
        per = lambda d: (((d - delta) ** 2).sum(-1) * mov).sum(1) / denom  # noqa: E731  m^2 per movable node
        with torch.no_grad():
            recon, nomove, prior_mean, prior_best = [], [], [], []
            for s in range(0, nv, 64):
                sub = {kk: vv[s:s + 64] for kk, vv in v.items()}
                c = cv.Condition.make(sub, "retain", "both")
                m_ = cv.movable_mask(sub["node_mask"], c.team, c.locked)
                d_true = (sub["target_pos"] - sub["pos"][:, -1]) * m_[..., None]
                dn = m_.sum(1).clamp_min(1.0)
                err = lambda d: (((d - d_true) ** 2).sum(-1) * m_).sum(1) / dn  # noqa: E731
                h, emb = cvae._context(sub, None)
                node, glob, _ = cvae._cond_feats(sub, c, None)
                mu, _lv = cvae.posterior(h, node, glob, d_true)
                recon.append(err(cvae.decode(sub, c, mu, average=True)))
                nomove.append(err(torch.zeros_like(d_true)))
                draws = cvae.sample(sub, c, n=k, generator=gen)  # (k,B,N,2)
                e = torch.stack([err(d) for d in draws])
                prior_mean.append(e.mean(0))
                prior_best.append(e.min(0).values)
        cat = lambda xs: torch.cat(xs).cpu().numpy()  # noqa: E731
        recon, nomove, prior_mean, prior_best = cat(recon), cat(nomove), cat(prior_mean), cat(prior_best)
        rmse = lambda x: float(np.sqrt(x.mean()))  # noqa: E731
        res["reconstruction"] = {
            "n": nv, "unit": "metres, RMS displacement error per movable player",
            "no_move_rmse": rmse(nomove), "posterior_mean_rmse": rmse(recon),
            f"prior_mean_of_{k}_rmse": rmse(prior_mean), f"prior_best_of_{k}_rmse": rmse(prior_best),
            "skill_posterior_vs_no_move": float(1.0 - recon.mean() / max(nomove.mean(), 1e-12)),
            f"skill_prior_best_of_{k}_vs_no_move": float(1.0 - prior_best.mean() / max(nomove.mean(), 1e-12)),
            "note": "future = target_pos, about 3 s ahead; only samples with target_valid"}
    else:
        res["reconstruction"] = {"n": 0, "note": "no sample with real future positions (target_valid) in this split"}

    # --- proposals vs the critic's target probability ---
    p0 = _critic(models, full)
    label = full["receiver_idx"]
    recv_ok = (label >= 0) & (label < N_SLOTS)
    carriers_only = full["node_feat"][..., F.F_IS_CARRIER].sum(1) > 0
    res["proposals"] = {}
    for team in ("attack", "both"):
        for target in cv.TARGETS:
            sel = recv_ok if target == "receiver" else torch.ones(n, dtype=torch.bool, device=full["pos"].device)
            sel = sel & carriers_only
            if int(sel.sum()) == 0:
                continue
            g = {kk: vv[sel] for kk, vv in full.items()}
            cond = cv.Condition.make(g, target, team, receiver=-1)
            if target == "receiver":
                cond.receiver = g["receiver_idx"].clone()
            base_p = {"shot": p0["shot"][sel], "retain": p0["turn"][sel], "press": p0["turn"][sel],
                      "receiver": p0["recv"][sel].gather(1, g["receiver_idx"].clamp(0, N_SLOTS)[:, None]).squeeze(1)}[target]
            sign = -1.0 if target == "retain" else 1.0
            gains, ctrl_gains, mags = [], [], []
            for s in range(0, int(sel.sum()), 64):
                sub = {kk: vv[s:s + 64] for kk, vv in g.items()}
                c = cv.Condition(cond.target[s:s + 64], cond.team[s:s + 64], cond.receiver[s:s + 64],
                                 cond.locked[s:s + 64])
                draws = cvae.sample(sub, c, n=k, generator=gen)  # (k,B,N,2)
                for d in draws:
                    pick = {"shot": "shot", "retain": "turn", "press": "turn"}.get(target)
                    for tag, disp in (("model", d), ("control", _rotate_randomly(d, gen))):
                        pn = _critic(models, _moved_graph(sub, disp))
                        new = pn[pick] if pick else pn["recv"].gather(
                            1, sub["receiver_idx"].clamp(0, N_SLOTS)[:, None]).squeeze(1)
                        gain = sign * (new - base_p[s:s + len(new)])
                        (gains if tag == "model" else ctrl_gains).append(gain)
                    mags.append(d.norm(dim=-1)[sub["node_mask"] > 0])
            gain_all = torch.cat([x for x in gains]).cpu().numpy()
            ctrl_all = torch.cat([x for x in ctrl_gains]).cpu().numpy()
            per_sample = _per_sample(gains, k, int(sel.sum()))
            res["proposals"][f"{team}/{target}"] = {
                "n_samples": int(sel.sum()), "n_proposals": int(gain_all.size),
                "share_raising_target": float((gain_all > 0).mean()), "mean_gain": float(gain_all.mean()),
                "share_samples_with_a_raising_proposal": float((per_sample.max(0) > 0).mean()),
                "control_share_raising": float((ctrl_all > 0).mean()), "control_mean_gain": float(ctrl_all.mean()),
                "mean_displacement_m": float(torch.cat(mags).mean()) if mags else 0.0}
    shares = [v["share_raising_target"] for v in res["proposals"].values()]
    res["proposals_mean_share_raising"] = float(np.mean(shares)) if shares else None
    res["proposals_note"] = ("gain = change of the critic's target probability (shot10 up; turn5 down for retain, "
                             "up for press; receiver slot probability up); control = same displacement magnitudes "
                             "in random directions")

    # --- clamp / lock checks ---
    res["clamp_check"] = _clamp_check(cvae, full, gen)
    return res


def _rotate_randomly(d, gen):
    """Rotate every displacement vector by an independent random angle (same norm, random direction)."""
    import torch

    ang = torch.rand(d.shape[:-1], generator=gen).to(d.device) * (2 * math.pi)
    c, s = torch.cos(ang), torch.sin(ang)
    return torch.stack([c * d[..., 0] - s * d[..., 1], s * d[..., 0] + c * d[..., 1]], dim=-1)


def _per_sample(gains: list, k: int, n: int) -> np.ndarray:
    """Chunked gains (appended chunk-major, then draw) -> array (k, n)."""
    import torch

    out = torch.zeros(k, n)
    pos, col = 0, 0
    # gains is a flat list: for each chunk, k tensors of chunk size
    while col < n:
        size = gains[pos].shape[0]
        for j in range(k):
            out[j, col:col + size] = gains[pos + j].cpu()
        pos += k
        col += size
    return out.numpy()


def _clamp_check(cvae, full: dict, gen) -> dict[str, Any]:
    """Proposal displacements must stay within ``min(top_speed * horizon, max_move)`` and be exactly zero for
    nodes that are locked, not on the moved team, the ball, the goals or padding."""
    import torch

    from matchlens.tacticsml.train import cvae as cv

    n = min(64, full["pos"].shape[0])
    g = {kk: vv[:n] for kk, vv in full.items()}
    B, N = g["node_mask"].shape
    dev = g["node_mask"].device
    out: dict[str, Any] = {"n_graphs": B, "tolerance_m": 1e-4}
    for team in cv.TEAMS:
        cond = cv.Condition.make(g, "shot", team)
        cond.locked = ((torch.rand(B, N, generator=gen) < 0.2).float().to(dev)) * g["node_mask"]
        cond.top_speed = (6.0 + 3.5 * torch.rand(B, N, generator=gen)).to(dev)
        mov = cv.movable_mask(g["node_mask"], cond.team, cond.locked)
        for cap in (None, 8.0):
            d = cvae.sample(g, cond, n=8, max_move=cap, generator=gen)  # (8,B,N,2)
            radius = cond.top_speed * cvae.cfg.horizon
            if cap is not None:
                radius = radius.clamp(max=cap)
            norm = d.norm(dim=-1)
            over = (norm - radius[None] > 1e-4) & (mov[None] > 0)
            frozen = (norm > 1e-6) & (mov[None] == 0)
            key = f"{team}/max_move={cap}"
            out[key] = {"max_norm_over_radius": float((norm / radius[None].clamp_min(1e-6)).max()),
                        "max_displacement_m": float(norm.max()),
                        "violations_speed_or_cap": int(over.sum()), "violations_non_movable_moved": int(frozen.sum()),
                        "movable_nodes": int(mov.sum())}
    out["all_respected"] = all(v["violations_speed_or_cap"] == 0 and v["violations_non_movable_moved"] == 0
                               for kk, v in out.items() if isinstance(v, dict))
    return out


def load_cvae(ckpt_dir: Path, models: list, files: list[str], device):
    path = ckpt_dir / "cvae.pt"
    if not path.exists():
        return None
    import torch

    from matchlens.tacticsml.train import fit

    blob = torch.load(path, map_location="cpu", weights_only=False)
    member = blob.get("encoder_member", files[0])
    enc = models[files.index(member)] if member in files else models[0]
    return fit.load_cvae(path, enc, device)


# ============================================================ targets and report

def _status(value: float | None, op: str, thr: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NOT_EVALUATED"
    ok = {">=": value >= thr, "<=": value <= thr, "<": value < thr}[op]
    return "PASS" if ok else "FAIL"


def evaluate_targets(sources: dict[str, Any], d2: dict | None) -> list[dict[str, Any]]:
    """The plan's targets, scored per source scope. Missing data is NOT_EVALUATED, never a PASS."""
    rows: list[dict[str, Any]] = []
    for scope, entry in sources.items():
        sit = entry["situations"]
        synthetic = scope in SYNTHETIC_SOURCES
        note_syn = "synthetic simulator data: pipeline check, not evidence of real-football quality" if synthetic else ""
        for tid, desc, path, op, thr in TARGETS:
            if tid == "d2_equivariance":
                continue
            note, n_used = "", 0
            if tid == "receiver_open_top3":
                grp = sit.get("open_play")
                if grp is None and "all" in sit:
                    grp, note = sit["all"], "no open_play flag in this source's shards: all samples are used"
                rec = (grp or {}).get("receiver", {})
                value, n_used = rec.get("top3"), rec.get("n", 0)
            elif tid == "receiver_corner_top3":
                grp = sit.get("corner")
                rec = (grp or {}).get("receiver", {})
                value, n_used = rec.get("top3"), rec.get("n", 0)
                note = (f"TacticAI reference {TACTICAI_CORNER_REFERENCE} on 7,176 Liverpool corners (private data, "
                        "not reproducible here)") if grp else "no corner samples in this test split"
            else:
                head = ((sit.get("all") or {}).get("outcomes") or {}).get("shot10") or {}
                value, n_used = head.get("auc" if path.endswith("auc") else "ece"), head.get("n", 0)
            if value is not None and n_used < MIN_TARGET_N:
                note = "; ".join(x for x in (note, f"only {n_used} labelled samples (< {MIN_TARGET_N}): not scored") if x)
                value = None
            rows.append({"id": tid, "scope": scope, "target": desc, "value": value, "threshold": thr, "op": op,
                         "n": n_used, "status": _status(value, op, thr),
                         "note": "; ".join(x for x in (note, note_syn) if x)})
    if d2 is not None:
        value = d2["max_abs_prob_error"]
        rows.append({"id": "d2_equivariance", "scope": "global", "target": TARGETS[-1][1], "value": value,
                     "threshold": TARGETS[-1][4], "op": "<", "n": d2["n_samples"],
                     "status": _status(value, "<", TARGETS[-1][4]),
                     "note": f"float64 member 0: {d2['max_abs_prob_error_float64_member0']:.2e}"})
    return rows


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


def run(ckpt_dir: Path | None = None, shards_root: Path | None = None, sources: Sequence[str] | None = None,
        out_dir: Path | None = None, device: str = "auto", baseline_max: int = 1500, cvae_samples: int = 128,
        cvae_k: int = 8, d2_samples: int = 200, max_samples: int | None = None, seed: int = 0,
        do_baseline: bool = True, do_cvae: bool = True, log=print) -> dict[str, Any]:
    """Evaluate the checkpoints on the test split and write ``report.json`` / ``report.md``. Returns the report."""
    import torch

    from matchlens.tacticsml.train import fit

    ckpt_dir, out_dir = Path(ckpt_dir or CKPT_DIR), Path(out_dir or EVAL_DIR)
    dev = fit.pick_device(device)
    models, files, info = load_ensemble(ckpt_dir, "cpu")
    data = load_test_sources(shards_root, sources, log)
    if not data:
        raise SystemExit(f"no test-split shards under {schema.shards_root(shards_root)}; "
                         "build them with: uv run --group tactics-ml python -m matchlens.tacticsml.train shards")
    width = models[0].cfg.n_node_feat
    for name, a in data.items():
        if a["node_feat"].shape[2] != width:
            raise SystemExit(f"{name}: shards have {a['node_feat'].shape[2]} node features, the model {width} "
                             "(different profile_keys?)")
        if max_samples:
            keep = _even_subset(len(a["pos"]), max_samples)
            data[name] = {k: (v[keep] if isinstance(v, np.ndarray) and v.ndim and len(v) == len(a["pos"]) else v)
                          for k, v in a.items()}
    for m in models:
        m.to(dev)

    t0 = time.time()
    preds: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    src_report: dict[str, Any] = {}
    for name, a in data.items():
        log(f"predicting {name} ({len(a['pos'])} samples, {len(models)} members) on {dev}")
        preds[name] = predict_members(models, a, dev)
        lab = _label_view(a)
        sits = {}
        for sit, mask in situation_masks(a).items():
            sub = {k: v[mask] for k, v in lab.items()}
            sits[sit] = evaluate_group(sub, preds[name][0][:, mask], preds[name][1][:, mask])
        src_report[name] = {"n": len(a["pos"]), "synthetic": name in SYNTHETIC_SOURCES, "situations": sits}

    real = [s for s in data if s not in SYNTHETIC_SOURCES]
    if len(real) >= 2:
        cat = lambda key: np.concatenate([_label_view(data[s])[key] for s in real])  # noqa: E731
        lab = {k: cat(k) for k in ("receiver_idx", *OUTCOMES, "node_mask")}
        if all("set_piece" in data[s] for s in real):
            lab["set_piece"] = cat("set_piece")
        rl = np.concatenate([preds[s][0] for s in real], axis=1)
        ol = np.concatenate([preds[s][1] for s in real], axis=1)
        sits = {}
        for sit, mask in situation_masks(lab).items():
            sits[sit] = evaluate_group({k: v[mask] for k, v in lab.items()}, rl[:, mask], ol[:, mask])
        src_report["pooled_real"] = {"n": len(lab["receiver_idx"]), "synthetic": False, "sources": real,
                                     "situations": sits}

    all_arrays = _concat_arrays(data)
    d2 = d2_invariance(models, all_arrays, d2_samples)
    for m in models:
        m.to(dev)
    log(f"D2 invariance: max abs probability change {d2['max_abs_prob_error']:.2e} on {d2['n_samples']} samples")

    baseline = None
    if do_baseline:
        baseline = {}
        for name, a in data.items():
            log(f"baseline comparison {name} (up to {baseline_max} samples)")
            baseline[name] = compare_with_baseline(a, preds[name][0], preds[name][1], baseline_max)

    cvae_report = None
    if do_cvae:
        cvae = load_cvae(ckpt_dir, models, files, dev)
        if cvae is None:
            cvae_report = {"available": False, "note": f"no cvae.pt under {ckpt_dir} (train with fit --cvae)"}
        else:
            log("evaluating the cVAE")
            cvae_report = {"available": True,
                           **evaluate_cvae(models, cvae, all_arrays, dev, cvae_samples, cvae_k, seed)}

    report = {
        "meta": {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "split": "test",
                 "ckpt_dir": str(ckpt_dir), "members": files, "n_members": len(models),
                 "model_config": models[0].cfg.to_dict(), "profile_keys": info.get("profile_keys", []),
                 "ensemble_combination": "mean of member logits, then softmax / sigmoid",
                 "train_ensemble_val": info.get("ensemble_val", {}),
                 "sources": {s: len(a["pos"]) for s, a in data.items()}, "device": str(dev),
                 "torch": torch.__version__, "seconds": round(time.time() - t0, 1),
                 "has_set_piece_flag": any("set_piece" in a for a in data.values()),
                 "caveat": ("Synthetic sources are simulator output; their numbers check the pipeline and say "
                            "nothing about real football.")},
        "sources": src_report, "d2_invariance": d2, "baseline_comparison": baseline, "cvae": cvae_report,
    }
    report["targets"] = evaluate_targets(src_report, d2)
    report = _jsonable(report)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "report.md").write_text(to_markdown(report))
    log(f"wrote {out_dir / 'report.json'} and report.md")
    return report


def _concat_arrays(data: dict[str, dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    keys = set.intersection(*(set(a) for a in data.values())) - {"profile_keys"}
    return {k: np.concatenate([a[k] for a in data.values()]) for k in keys}


# ============================================================ markdown

def _f(v: Any, nd: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.2e}" if (v != 0 and abs(v) < 10 ** -nd) else f"{v:.{nd}f}"
    return str(v)


def to_markdown(r: dict[str, Any]) -> str:
    m = r["meta"]
    L = ["# Tactics model evaluation (test split)", "",
         f"Generated {m['created']} from {m['n_members']} ensemble member(s) {m['members']}; "
         f"sources {m['sources']}; device {m['device']}.", "",
         f"> {m['caveat']} Numbers below were produced by this run only.", "", "## Targets", "",
         "| target | scope | value | threshold | n | status | note |", "|---|---|---|---|---|---|---|"]
    for t in r["targets"]:
        L.append(f"| {t['target']} | {t['scope']} | {_f(t['value'], 4)} | {t['op']} {t['threshold']} | {t['n']} "
                 f"| **{t['status']}** | {t['note']} |")
    L += ["", "## Per source and situation", "",
          "| source | situation | n | recv top-1 | recv top-3 | chance top-3 | "
          + " | ".join(f"{o} AUC / Brier / ECE" for o in OUTCOMES) + " |",
          "|---|---|---|---|---|---|" + "---|" * len(OUTCOMES)]
    for s, e in r["sources"].items():
        for sit, g in e["situations"].items():
            if not g.get("n"):
                continue
            rc = g.get("receiver", {})
            cells = []
            for o in OUTCOMES:
                h = g.get("outcomes", {}).get(o)
                cells.append("n/a" if not h else f"{_f(h['auc'])} / {_f(h['brier'], 4)} / {_f(h['ece'], 4)}")
            L.append(f"| {s} | {sit} | {g['n']} | {_f(rc.get('top1'))} | {_f(rc.get('top3'))} | "
                     f"{_f(rc.get('chance_top3'))} | " + " | ".join(cells) + " |")
    L += ["", "## Ensemble spread vs error (all samples of each source)", "",
          "| source | head | n | Spearman | Pearson | error in lowest / highest spread quartile |", "|---|---|---|---|---|---|"]
    for s, e in r["sources"].items():
        sve = e["situations"].get("all", {}).get("spread_vs_error", {})
        for head, v in sve.items():
            if not isinstance(v, dict) or "spearman" not in v:
                continue
            q = v.get("by_spread_quantile") or []
            qs = f"{_f(q[0]['mean_error'], 4)} / {_f(q[-1]['mean_error'], 4)}" if len(q) >= 2 else "n/a"
            L.append(f"| {s} | {head} | {v['n']} | {_f(v['spearman'])} | {_f(v['pearson'])} | {qs} |")
    d2 = r["d2_invariance"]
    L += ["", "## D2 invariance", "",
          f"{d2['n_samples']} test samples, reflected by {', '.join(d2['elements_tested'])}. Largest probability "
          f"change: ensemble {_f(d2['max_abs_prob_error'])}, single members {_f(d2['max_abs_prob_error_members'])}, "
          f"float64 member 0 {_f(d2['max_abs_prob_error_float64_member0'])}."]
    if r.get("baseline_comparison"):
        L += ["", "## Learned vs baseline (same sub-sample)", "",
              "| source | metric | learned | baseline |", "|---|---|---|---|"]
        for s, b in r["baseline_comparison"].items():
            rc = b["receiver"]
            for k in ("top1", "top3"):
                L.append(f"| {s} (n={rc['n']}) | receiver {k} | {_f(rc['learned'].get(k))} | {_f(rc['baseline'].get(k))} |")
            for o, v in b["outcomes"].items():
                for k in ("auc", "brier", "ece"):
                    L.append(f"| {s} (n={v['learned']['n']}) | {o} {k} | {_f(v['learned'][k], 4)} | "
                             f"{_f(v['baseline'][k], 4)} |")
    c = r.get("cvae")
    L += ["", "## cVAE", ""]
    if not c or not c.get("available"):
        L.append((c or {}).get("note", "not evaluated"))
    else:
        rec = c["reconstruction"]
        if rec.get("n"):
            kk = c["k_proposals"]
            L.append(f"Displacement error ({rec['n']} samples with real future positions, RMS metres per movable "
                     f"player): no move {_f(rec['no_move_rmse'])}, posterior-mean reconstruction "
                     f"{_f(rec['posterior_mean_rmse'])}, prior mean-of-{kk} {_f(rec[f'prior_mean_of_{kk}_rmse'])}, "
                     f"prior best-of-{kk} {_f(rec[f'prior_best_of_{kk}_rmse'])}.")
        else:
            L.append(rec["note"])
        L += ["", "| condition | samples | share of proposals raising the target | control (random directions) | "
              "mean gain | mean displacement (m) |", "|---|---|---|---|---|---|"]
        for key, v in c["proposals"].items():
            L.append(f"| {key} | {v['n_samples']} | {_f(v['share_raising_target'])} | {_f(v['control_share_raising'])} "
                     f"| {_f(v['mean_gain'], 4)} | {_f(v['mean_displacement_m'])} |")
        cc = c["clamp_check"]
        L += ["", f"Speed clamp, move cap and locked-node check: **{'respected' if cc['all_respected'] else 'VIOLATED'}** "
                  f"over {cc['n_graphs']} graphs."]
    L += ["", "## Reliability (shot10, all situations)", ""]
    for s, e in r["sources"].items():
        h = e["situations"].get("all", {}).get("outcomes", {}).get("shot10")
        if not h:
            continue
        L += [f"### {s}", "", "| bin | n | mean predicted | fraction positive |", "|---|---|---|---|"]
        L += [f"| {x['lo']:.2f}-{x['hi']:.2f} | {x['n']} | {_f(x['mean_pred'], 4)} | {_f(x['frac_pos'], 4)} |"
              for x in h["reliability"] if x["n"]]
        L.append("")
    return "\n".join(L) + "\n"


# ============================================================ CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train evaluate", description=__doc__)
    p.add_argument("--ckpt-dir", default=str(CKPT_DIR))
    p.add_argument("--shards-root", default=None, help="shards directory (default backend/data/local/tacticsml/shards)")
    p.add_argument("--sources", nargs="+", default=None, help="restrict to these sources (default: all with test shards)")
    p.add_argument("--out-dir", default=str(EVAL_DIR))
    p.add_argument("--device", default="auto", help="auto, cpu, mps or cuda (D2 invariance always runs on cpu)")
    p.add_argument("--max-samples", type=int, default=None, help="cap test samples per source (evenly spaced)")
    p.add_argument("--baseline-max", type=int, default=1500, help="samples per source for the baseline comparison")
    p.add_argument("--no-baseline", action="store_true")
    p.add_argument("--no-cvae", action="store_true")
    p.add_argument("--cvae-samples", type=int, default=128)
    p.add_argument("--cvae-k", type=int, default=8, help="proposals per sample")
    p.add_argument("--d2-samples", type=int, default=200)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    report = run(Path(a.ckpt_dir), Path(a.shards_root) if a.shards_root else None, a.sources, Path(a.out_dir),
                 a.device, a.baseline_max, a.cvae_samples, a.cvae_k, a.d2_samples, a.max_samples, a.seed,
                 not a.no_baseline, not a.no_cvae)
    for t in report["targets"]:
        print(f"{t['status']:>13}  [{t['scope']}] {t['target']}: {_f(t['value'], 4)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
