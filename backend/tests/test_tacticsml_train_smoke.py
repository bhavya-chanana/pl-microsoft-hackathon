"""Smoke tests of the TacticAI+ GATv2 and cVAE (torch only; skipped when torch is missing)."""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from matchlens.simulator.engine import generate_match  # noqa: E402
from matchlens.tacticsml import features as F  # noqa: E402
from matchlens.tacticsml.contract import from_tracking  # noqa: E402
from matchlens.tacticsml.train import cvae as cv  # noqa: E402
from matchlens.tacticsml.train import fit  # noqa: E402
from matchlens.tacticsml.train.model import (  # noqa: E402
    ModelConfig, TacticsGAT, to_tensors,
)

torch.set_num_threads(4)


def _labels(s: F.GraphSample) -> dict[str, int]:
    """Geometry-derived labels, learnable from the graph (stand-ins for the real shard labels)."""
    last = s.pos[-1]
    c = s.carrier_index
    if c < 0:
        return {"receiver_idx": -1, "shot10": -1, "turn5": -1}
    mates = [i for i in range(F.N_TEAM) if i != c and s.node_mask[i] > 0]
    opp = [i for i in range(F.N_TEAM, 2 * F.N_TEAM) if s.node_mask[i] > 0]
    d = lambda i: float(np.linalg.norm(last[i] - last[c]))  # noqa: E731
    recv = min(mates, key=d) if mates else -1
    return {"receiver_idx": recv, "shot10": int(last[c, 0] > 15.0),
            "turn5": int(bool(opp) and min(d(i) for i in opp) < 6.0)}


@pytest.fixture(scope="module")
def synth() -> fit.Dataset:
    samples, labs = [], []
    for seed in (3, 5):
        m = generate_match(seed=seed)
        for i in range(10, len(m.tracking), 15):
            team = m.meta.home.team_id if (i // 15) % 2 == 0 else m.meta.away.team_id
            s = F.build_sample(from_tracking(m.tracking[i - 4:i + 1], m.meta, team))
            if s.carrier_index < 0:
                continue
            samples.append(s)
            labs.append(_labels(s))
    labels = {k: np.array([lab[k] for lab in labs]) for k in ("receiver_idx", "shot10", "turn5")}
    return fit.Dataset.from_samples(samples, labels)


@pytest.fixture(scope="module")
def graph_batch(synth):
    return to_tensors(synth.batch(np.arange(8)))


def _random_model(seed: int = 0) -> TacticsGAT:
    torch.manual_seed(seed)
    model = TacticsGAT(ModelConfig.for_profile_keys())
    with torch.no_grad():  # make every head non-trivial
        for p in model.parameters():
            p.add_(torch.randn_like(p) * 0.05)
    return model.eval()


def test_param_count():
    n = TacticsGAT(ModelConfig.for_profile_keys()).n_params()
    assert 150_000 <= n <= 400_000, n


def test_d2_invariance_of_all_heads(synth):
    model = _random_model()
    idx = np.arange(0, 24, 3)
    base = None
    for g in F.D2:
        b = synth.batch(idx)
        pos, vel = F.apply_d2(g, b["pos"], b["vel"])
        b = to_tensors({**b, "pos": pos, "vel": vel})
        out = model.predict(b)
        base = base or out
        for k, v in out.items():
            assert (v - base[k]).abs().max().item() < 1e-5, (g, k)
    # without frame averaging the raw network is NOT invariant (the averaging does the work)
    raw = [model.predict(to_tensors({**synth.batch(idx), **dict(zip(("pos", "vel"), F.apply_d2(g, *[
        synth.batch(idx)[k] for k in ("pos", "vel")])))}), average=False)["shot10"] for g in F.D2]
    assert max((r - raw[0]).abs().max().item() for r in raw) > 1e-4


def test_possession_team_permutation_equivariance(graph_batch):
    model = _random_model(1)
    perm = np.random.default_rng(0).permutation(F.N_TEAM)
    idx = np.concatenate([perm, np.arange(F.N_TEAM, F.N_NODES)])
    b = {k: v.clone() for k, v in graph_batch.items()}
    b["pos"], b["vel"] = b["pos"][:, :, idx], b["vel"][:, :, idx]
    b["vel_mask"], b["node_feat"], b["node_mask"] = b["vel_mask"][:, idx], b["node_feat"][:, idx], b["node_mask"][:, idx]
    b["edge_feat"] = b["edge_feat"][:, idx][:, :, idx]
    p0, p1 = model.predict(graph_batch)["receiver_probs"], model.predict(b)["receiver_probs"]
    expect = torch.cat([p0[:, perm], p0[:, F.N_TEAM:]], dim=1)
    assert (p1 - expect).abs().max().item() < 1e-5
    assert torch.allclose(p1.sum(-1), torch.ones(len(p1)), atol=1e-5)


def test_training_loss_drops(synth):
    n_val = 60
    train = synth.subset(np.arange(n_val, len(synth)))
    val = synth.subset(np.arange(n_val))
    tcfg = fit.TrainConfig(epochs=100, batch=32, lr=2e-3, max_steps=200, patience=100)
    _, hist = fit.train_model(train, val, None, tcfg, seed=0, device="cpu", log=lambda _m: None)
    assert hist["steps"] == 200
    tl = hist["train_loss"]
    assert tl[-1] <= 0.7 * tl[0], tl
    m = hist["val"][-1]
    assert np.isfinite(m["loss"]) and 0.0 <= m["receiver_top3"] <= 1.0 and "shot10_auc" in m


def test_cvae_respects_lock_team_and_clamp(graph_batch):
    torch.manual_seed(0)
    enc = TacticsGAT(ModelConfig.for_profile_keys()).eval()
    model = cv.ConditionalVAE(enc)
    with torch.no_grad():  # push raw displacements far past the clamp
        model.dec3.weight.mul_(40.0)
        model.dec3.bias.add_(3.0)
    B, N = graph_batch["node_mask"].shape
    locked = torch.zeros(B, N)
    locked[:, [1, 3, 14]] = 1.0
    cond = cv.Condition.make(graph_batch, target="shot", team="both", locked=locked)
    out = model.sample(graph_batch, cond, n=4, max_move=8.0, generator=torch.Generator().manual_seed(0))
    assert out.shape == (4, B, N, 2)
    assert (out[:, :, [1, 3, 14]] == 0).all()  # locked
    assert (out[:, :, F.BALL:] == 0).all()  # ball and goals
    pad = graph_batch["node_mask"] == 0
    assert (out.norm(dim=-1)[:, pad] == 0).all()
    assert out.norm(dim=-1).max().item() <= 8.0 + 1e-5
    assert out.norm(dim=-1).max().item() > 3.0  # large raw outputs, saturated by the clamp
    # default radius is top_speed * horizon
    free = model.sample(graph_batch, cv.Condition.make(graph_batch, team="both"), n=2)
    assert free.norm(dim=-1).max().item() <= 8.8 * 3.0 + 1e-4
    # team mask: attack moves only possession-team slots
    att = model.sample(graph_batch, cv.Condition.make(graph_batch, team="attack"), n=2)
    assert (att[:, :, F.N_TEAM:] == 0).all() and att[:, :, :F.N_TEAM].abs().sum() > 0
    # equivariance: same z, reflected scene -> reflected displacements
    gen = lambda: torch.Generator().manual_seed(7)  # noqa: E731
    a = model.sample(graph_batch, cond, n=2, generator=gen())
    s = torch.tensor([-1.0, 1.0])
    refl = {**graph_batch, "pos": graph_batch["pos"] * s, "vel": graph_batch["vel"] * s}
    r = model.sample(refl, cond, n=2, generator=gen())
    assert (r - a * s).abs().max().item() < 1e-4


def test_cvae_training_step_runs(synth):
    torch.manual_seed(0)
    enc = TacticsGAT(ModelConfig.for_profile_keys()).eval()
    model, hist = fit.train_cvae(enc, synth.subset(np.arange(64)), None, epochs=2, batch=32, device="cpu",
                                 log=lambda _m: None)
    assert all(np.isfinite(h["total"]) for h in hist["train"])
    assert not any(k.startswith("encoder.") for k in model.cvae_state_dict())
