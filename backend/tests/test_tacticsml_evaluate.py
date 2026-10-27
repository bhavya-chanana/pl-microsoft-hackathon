"""Metric functions of tacticsml.train.evaluate on hand-computed cases, and the whole evaluation on tiny
synthetic shards with a tiny random model (torch only for the second part)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from matchlens.tacticsml.train import evaluate as ev

# ---------------------------------------------------------------- metrics (numpy only)


def test_auc_hand_computed():
    # pairs (pos, neg): .35>.1 yes, .35>.4 no, .8>.1 yes, .8>.4 yes -> 3/4
    assert ev.auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.4, 0.35, 0.8])) == pytest.approx(0.75)
    assert ev.auc(np.array([0, 1]), np.array([0.2, 0.9])) == 1.0
    assert ev.auc(np.array([1, 0]), np.array([0.2, 0.9])) == 0.0
    # all scores tied -> 0.5
    assert ev.auc(np.array([0, 1, 0, 1]), np.full(4, 0.3)) == pytest.approx(0.5)
    assert math.isnan(ev.auc(np.ones(3), np.array([0.1, 0.2, 0.3])))


def test_brier_and_ece_hand_computed():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.1, 0.1, 0.9, 0.9])
    assert ev.brier(y, p) == pytest.approx(0.01)
    # two occupied bins, each |frac_pos - mean_pred| = 0.1, weight 0.5 each
    assert ev.ece(y, p) == pytest.approx(0.1)
    # perfectly calibrated: 4 samples at 0.25 with exactly one positive
    assert ev.ece(np.array([1, 0, 0, 0]), np.full(4, 0.25)) == pytest.approx(0.0)
    # one bin: predicted 0.5 but always positive
    assert ev.ece(np.ones(5), np.full(5, 0.5)) == pytest.approx(0.5)
    # p = 1.0 falls in the last bin, not out of range
    rel = ev.reliability(np.array([1.0]), np.array([1.0]))
    assert len(rel) == ev.ECE_BINS and rel[-1]["n"] == 1 and rel[-1]["frac_pos"] == 1.0


def test_reliability_table_bins():
    y = np.array([0, 1, 1, 0])
    p = np.array([0.01, 0.02, 0.5, 0.99])
    rel = ev.reliability(y, p)
    assert sum(r["n"] for r in rel) == 4
    assert rel[0]["n"] == 2 and rel[0]["frac_pos"] == 0.5 and rel[0]["mean_pred"] == pytest.approx(0.015)
    assert rel[7]["n"] == 1  # 0.5 * 15 = 7.5 -> bin 7
    assert rel[1]["mean_pred"] is None


def test_topk_accuracy_hand_computed():
    scores = np.array([[0.1, 0.5, 0.3, 0.2],   # true 1 -> rank 0
                       [0.4, 0.1, 0.3, 0.2],   # true 2 -> rank 1
                       [0.4, 0.1, 0.3, 0.2]])  # true 1 -> rank 3
    y = np.array([1, 2, 1])
    assert ev.topk_accuracy(scores, y, 1) == pytest.approx(1 / 3)
    assert ev.topk_accuracy(scores, y, 2) == pytest.approx(2 / 3)
    assert ev.topk_accuracy(scores, y, 3) == pytest.approx(2 / 3)
    assert ev.topk_accuracy(scores, y, 4) == pytest.approx(1.0)
    # ties count against the model: a flat score vector is never "top-1" among 4
    assert ev.topk_accuracy(np.zeros((2, 4)), np.array([0, 3]), 1) == 0.0
    # absent players (-inf) never outrank
    s = np.array([[-np.inf, 0.2, 0.1]])
    assert ev.topk_accuracy(s, np.array([2]), 2) == 1.0


def test_correlations():
    a = np.arange(10, dtype=float)
    assert ev.spearman(a, a ** 3) == pytest.approx(1.0)
    assert ev.spearman(a, -a) == pytest.approx(-1.0)
    assert ev.pearson(a, 2 * a + 1) == pytest.approx(1.0)
    assert math.isnan(ev.pearson(a, np.ones(10)))
    assert list(ev.rankdata(np.array([5.0, 1.0, 5.0, 3.0]))) == [3.5, 1.0, 3.5, 2.0]


def test_spread_error_report_quartiles():
    spread = np.arange(16, dtype=float)
    rep = ev.spread_error_report(spread, spread * 0.1)
    assert rep["spearman"] == pytest.approx(1.0)
    q = rep["by_spread_quantile"]
    assert len(q) == 4 and q[0]["mean_error"] < q[-1]["mean_error"]


def test_binary_and_receiver_reports():
    rep = ev.binary_report(np.array([0, 0, 1, 1]), np.array([0.1, 0.4, 0.35, 0.8]))
    assert rep["n"] == 4 and rep["positives"] == 2 and rep["base_rate"] == 0.5
    assert rep["brier_base_rate"] == pytest.approx(0.25)
    assert rep["brier_skill"] == pytest.approx(1 - rep["brier"] / 0.25)
    r = ev.receiver_report(np.array([[3.0, 2.0, 1.0, 0.0]]), np.array([2]), np.array([4]))
    assert r["top1"] == 0.0 and r["top3"] == 1.0
    assert r["chance_top1"] == pytest.approx(0.25) and r["chance_top3"] == pytest.approx(0.75)


def test_status_and_targets_not_scored_without_data():
    assert ev._status(0.9, ">=", 0.85) == "PASS"
    assert ev._status(0.8, ">=", 0.85) == "FAIL"
    assert ev._status(0.031, "<=", 0.03) == "FAIL"
    assert ev._status(float("nan"), ">=", 0.1) == "NOT_EVALUATED"
    assert ev._status(None, "<", 1e-5) == "NOT_EVALUATED"
    # a corner group with only a handful of labelled samples is not scored, and a missing group is not a PASS
    group = {"n": 5, "receiver": {"n": 5, "top3": 1.0}, "outcomes": {}}
    rows = ev.evaluate_targets({"s": {"situations": {"all": group, "corner": group}}}, None)
    by_id = {r["id"]: r for r in rows}
    assert by_id["receiver_corner_top3"]["status"] == "NOT_EVALUATED"
    assert by_id["shot10_auc"]["status"] == "NOT_EVALUATED"


def test_situation_masks():
    arrays = {"receiver_idx": np.zeros(5, dtype=int), "set_piece": np.array([0, 1, 1, -1, 3], dtype=np.int8)}
    m = ev.situation_masks(arrays)
    assert set(m) == {"all", "open_play", "corner", "throw_in"}
    assert m["corner"].sum() == 2 and m["open_play"].sum() == 1
    assert set(ev.situation_masks({"receiver_idx": np.zeros(3, dtype=int)})) == {"all"}


# ---------------------------------------------------------------- end to end on tiny shards and a tiny model

try:  # the metric tests above need numpy only; everything below needs torch (tactics-ml group)
    import torch
except ImportError:  # pragma: no cover
    torch = None

needs_torch = pytest.mark.skipif(torch is None, reason="torch not installed (uv run --group tactics-ml)")

if torch is not None:
    from matchlens.tacticsml import features as F
    from matchlens.tacticsml.contract import from_tracking
    from matchlens.tacticsml.train import cvae as cv
    from matchlens.tacticsml.train import fit, schema
    from matchlens.tacticsml.train.model import ModelConfig, TacticsGAT


@pytest.fixture(scope="module")
def tiny_setup(demo_match, tmp_path_factory):
    torch.set_num_threads(2)
    root = tmp_path_factory.mktemp("tacticsml")
    rng = np.random.default_rng(0)
    samples, recv, labels, kinds, sp = [], [], {k: [] for k in ("shot10", "goal10", "concede10", "turn5")}, [], []
    for i in range(10, 400, 10):
        team = demo_match.meta.home.team_id if (i // 10) % 2 == 0 else demo_match.meta.away.team_id
        s = F.build_sample(from_tracking(demo_match.tracking[i - 4:i + 1], demo_match.meta, team))
        if s.carrier_index < 0:
            continue
        samples.append(s)
        recv.append(int(rng.integers(0, 12)) if len(samples) % 7 else -1)
        for k in labels:
            labels[k].append(int(rng.random() < 0.3) if len(samples) % 11 else -1)
        kinds.append(schema.KIND_PASS)
        sp.append(1 if len(samples) % 5 == 0 else 0)
    tp = np.stack([s.pos[-1] + rng.normal(0, 1.5, size=s.pos[-1].shape) for s in samples]).astype(np.float32)
    arrays = schema.pack(samples, recv, labels, kinds, tp, np.ones(len(samples), dtype=bool), sp)
    shards = root / "shards"
    schema.write_shard("synthetic", "t1", arrays, split="test", root=shards)
    schema.write_shard("synthetic", "t2", arrays, split="train", root=shards)

    cfg = ModelConfig.for_profile_keys((), hidden=16, heads=2, layers=1, ffn=16, emb_dim=16, dropout=0.0)
    ckpt = root / "ckpt"
    members = []
    for seed in (0, 1):
        torch.manual_seed(seed)
        model = TacticsGAT(cfg)
        with torch.no_grad():
            for p in model.parameters():
                p.add_(torch.randn_like(p) * 0.05)
        fit.save_member(model, ckpt / f"gat_seed{seed}.pt", seed, (), {})
        members.append({"file": f"gat_seed{seed}.pt", "seed": seed})
        if seed == 0:
            enc = model
    (ckpt / "ensemble.json").write_text(json.dumps({"members": members, "config": cfg.to_dict(), "profile_keys": []}))
    torch.manual_seed(5)
    fit.save_cvae(cv.ConditionalVAE(enc), ckpt / "cvae.pt", "gat_seed0.pt")
    return {"root": root, "shards": shards, "ckpt": ckpt, "n": len(samples)}


@pytest.fixture(scope="module")
def report(tiny_setup):
    return ev.run(tiny_setup["ckpt"], tiny_setup["shards"], None, tiny_setup["root"] / "eval", device="cpu",
                  baseline_max=12, cvae_samples=12, cvae_k=2, d2_samples=20, log=lambda *_: None)


@needs_torch
def test_report_files_and_structure(report, tiny_setup):
    out = tiny_setup["root"] / "eval"
    assert json.loads((out / "report.json").read_text())["meta"]["split"] == "test"
    md = (out / "report.md").read_text()
    assert "## Targets" in md and "## cVAE" in md
    n = tiny_setup["n"]
    src = report["sources"]["synthetic"]
    assert src["n"] == n  # only the test shard is evaluated, not the train one
    assert set(src["situations"]) >= {"all", "open_play", "corner"}
    allg = src["situations"]["all"]
    assert allg["n_members"] == 2
    for head in ev.OUTCOMES:
        h = allg["outcomes"][head]
        assert 0.0 <= h["brier"] <= 1.0 and len(h["reliability"]) == ev.ECE_BINS
    assert 0.0 <= allg["receiver"]["top1"] <= allg["receiver"]["top3"] <= 1.0
    assert "spearman" in allg["spread_vs_error"]["shot10"]


@needs_torch
def test_targets_are_flagged_honestly(report):
    rows = {(r["id"], r["scope"]): r for r in report["targets"]}
    for r in report["targets"]:
        assert r["status"] in ("PASS", "FAIL", "NOT_EVALUATED")
    # a random model does not reach 0.85 open-play top-3
    assert rows[("receiver_open_top3", "synthetic")]["status"] in ("FAIL", "NOT_EVALUATED")
    assert "synthetic" in rows[("shot10_auc", "synthetic")]["note"]


@needs_torch
def test_d2_invariance_is_tiny(report):
    d2 = report["d2_invariance"]
    assert d2["n_samples"] == 20
    assert d2["max_abs_prob_error"] < 1e-5
    assert d2["max_abs_prob_error_float64_member0"] < 1e-9


@needs_torch
def test_baseline_comparison_present(report):
    b = report["baseline_comparison"]["synthetic"]
    assert b["n_subsample"] == 12
    assert "learned" in b["receiver"] and "baseline" in b["receiver"]
    assert "shot10" in b["outcomes"] and "auc" in b["outcomes"]["shot10"]["baseline"]


@needs_torch
def test_cvae_checks(report):
    c = report["cvae"]
    assert c["available"] is True
    assert c["reconstruction"]["n"] > 0 and c["reconstruction"]["no_move_rmse"] > 0
    assert c["clamp_check"]["all_respected"] is True
    assert any(k.startswith("attack/shot") for k in c["proposals"])
    for v in c["proposals"].values():
        assert 0.0 <= v["share_raising_target"] <= 1.0 and 0.0 <= v["control_share_raising"] <= 1.0


@needs_torch
def test_clamp_check_detects_a_violation(tiny_setup):
    """The checker must fail when proposals exceed the cap (here a decoder whose clamp is bypassed)."""
    from matchlens.tacticsml.train.model import to_tensors

    a = schema.read_shards(["synthetic"], "test", tiny_setup["shards"])
    full = to_tensors({k: a[k][:8] for k in (*ev.BATCH_KEYS, "receiver_idx")})
    model = fit.load_member(tiny_setup["ckpt"] / "gat_seed0.pt")
    vae = fit.load_cvae(tiny_setup["ckpt"] / "cvae.pt", model)
    gen = torch.Generator().manual_seed(0)
    assert ev._clamp_check(vae, full, gen)["all_respected"]
    vae.sample = lambda graph, cond, n=8, max_move=None, generator=None, average=True: torch.full(  # type: ignore
        (n, *graph["node_mask"].shape, 2), 30.0)
    assert not ev._clamp_check(vae, full, gen)["all_respected"]


@needs_torch
def test_evaluate_command_is_wired(tiny_setup, capsys):
    from matchlens.tacticsml.train.__main__ import main

    out = tiny_setup["root"] / "eval_cli"
    rc = main(["evaluate", "--ckpt-dir", str(tiny_setup["ckpt"]), "--shards-root", str(tiny_setup["shards"]),
               "--out-dir", str(out), "--device", "cpu", "--no-baseline", "--no-cvae", "--d2-samples", "5"])
    assert rc == 0 and (out / "report.json").exists()
    assert json.loads((out / "report.json").read_text())["baseline_comparison"] is None
