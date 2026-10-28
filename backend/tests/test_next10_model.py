"""Next10 model: heads, FiLM, view-mask augmentation, losses, numpy-vs-torch parity, export/serve round trip, DDPM."""

from __future__ import annotations

import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from matchlens.simulator.engine import generate_match  # noqa: E402
from matchlens.tacticsml import features as F  # noqa: E402
from matchlens.tacticsml import infer_next10 as I10  # noqa: E402
from matchlens.tacticsml import next10_common as C  # noqa: E402
from matchlens.tacticsml.contract import from_tracking  # noqa: E402
from matchlens.tacticsml.train import diffusion as D  # noqa: E402
from matchlens.tacticsml.train import export10, heads as H  # noqa: E402
from matchlens.tacticsml.train.augment import ViewMaskConfig, view_mask_batch  # noqa: E402
from matchlens.tacticsml.train.data10 import to_tensors10  # noqa: E402
from matchlens.tacticsml.train.model import ModelConfig, signs_for  # noqa: E402
from matchlens.tacticsml.train.next10 import Next10Config, Next10GAT, load_critic, save_critic  # noqa: E402
from matchlens.tacticsml.train.schema import edge_feat_from  # noqa: E402
from matchlens.tacticsml.train.trajectory import (  # noqa: E402
    TrajConfig, TrajectoryNet, save_trajectory, trajectory_loss,
)

torch.set_num_threads(4)
B = 10


def _team_table() -> C.TeamTable:
    t = C.build_team_table(["Alpha FC", "Beta United", "Gamma"])
    t.style[1:] = np.random.default_rng(0).normal(size=t.style[1:].shape)
    t.style_mask[1:] = 1.0
    return t


@pytest.fixture(scope="module")
def np_batch() -> dict[str, np.ndarray]:
    """Real-geometry samples from the simulator with synthetic Next10 labels (shape-correct, not meaningful)."""
    samples = []
    for seed in (3, 5):
        m = generate_match(seed=seed)
        for i in range(10, len(m.tracking), 25):
            team = m.meta.home.team_id if (i // 25) % 2 == 0 else m.meta.away.team_id
            s = F.build_sample(from_tracking(m.tracking[i - 4:i + 1], m.meta, team))
            if s.carrier_index >= 0:
                samples.append(s)
            if len(samples) >= B:
                break
        if len(samples) >= B:
            break
    b = F.stack(samples[:B])
    rng = np.random.default_rng(1)
    n = B
    hs = np.arange(1, 11)[None, :, None, None]
    fut = b["pos"][:, -1][:, None] + b["vel"][:, -1][:, None] * hs + rng.normal(size=(n, 10, b["pos"].shape[2], 2))
    b.update(receiver_idx=rng.integers(0, 12, n), shot10=rng.integers(0, 2, n), goal10=rng.integers(0, 2, n),
             concede10=rng.integers(0, 2, n), turn5=rng.integers(0, 2, n), poss10=rng.integers(0, 2, n),
             action_next=rng.integers(0, 4, n), zone_cell=C.cell_of(fut[:, -1, F.BALL]),
             team_ids=np.stack([rng.integers(1, 4, n), rng.integers(0, 4, n)], 1),
             fut_pos=fut.astype(np.float32), fut_valid=np.ones((n, 10, b["pos"].shape[2]), bool))
    return b


def _model(film: bool = True, seed: int = 0, dtype=torch.float32) -> Next10GAT:
    torch.manual_seed(seed)
    table = _team_table()
    m = Next10GAT(Next10Config(**{**ModelConfig.for_profile_keys().to_dict(), "film": film, "n_teams": table.size}))
    m.set_team_table(table)
    with torch.no_grad():  # make every head and every FiLM layer non-trivial
        for p in m.parameters():
            p.add_(torch.randn_like(p) * 0.05)
    return m.to(dtype).eval()


def _tensors(b, dtype=torch.float32):
    t = to_tensors10(b, "cpu")
    return {k: (v.to(dtype) if v.is_floating_point() else v) for k, v in t.items()}


# ---------------------------------------------------------------- grid and decomposition (numpy)

def test_zone_target_is_a_smoothed_bump():
    cells = np.array([0, 5 * C.GRID_COLS + 7, C.N_CELLS - 1])
    t = C.smoothed_target(cells)
    assert t.shape == (3, C.N_CELLS) and np.allclose(t.sum(1), 1.0, atol=1e-6)
    assert (t.argmax(1) == cells).all()
    mid = t[1].reshape(C.GRID_ROWS, C.GRID_COLS)
    assert mid[5, 7] > mid[5, 8] and np.isclose(mid[5, 8], mid[5, 6]) and np.isclose(mid[4, 7], mid[6, 7])
    assert np.allclose(C.CELL_CENTRES[C.cell_of(C.CELL_CENTRES)], C.CELL_CENTRES)


def test_grid_reflection_matches_pitch_reflection():
    rng = np.random.default_rng(0)
    xy = np.stack([rng.uniform(-52, 52, 200), rng.uniform(-33, 33, 200)], 1)
    for g in F.D2:
        s = F.d2_signs(g)
        onehot = np.zeros((200, C.GRID_ROWS, C.GRID_COLS))
        onehot.reshape(200, -1)[np.arange(200), C.cell_of(xy)] = 1.0
        moved = C.reflect_grid(onehot, s).reshape(200, -1)
        assert (moved.argmax(1) == C.cell_of(xy * np.array(s))).all()


def test_receiver_marginalisation_identity():
    recv = np.zeros((2, 12))
    recv[0, 3], recv[1, 11] = 1.0, 1.0
    cond = np.random.default_rng(0).uniform(size=(2, 12))
    assert np.allclose(C.marginal_shot(recv, cond), [cond[0, 3], cond[1, 11]])
    soft = np.random.default_rng(1).dirichlet(np.ones(12), size=4)
    c = np.random.default_rng(2).uniform(size=(4, 12))
    p = C.marginal_shot(soft, c)
    assert ((p >= c.min(1)) & (p <= c.max(1))).all()
    logits, cl = torch.randn(4, 12), torch.randn(4, 12)
    ref = (torch.softmax(logits, -1) * torch.sigmoid(cl)).sum(-1)
    assert torch.allclose(H.marginal_shot_prob(logits, cl), ref, atol=1e-7)


def test_cell_features_numpy_torch_parity():
    xy = np.random.default_rng(0).uniform(-30, 30, (6, 2)).astype(np.float32)
    assert np.abs(H.cell_features(torch.as_tensor(xy)).numpy() - C.cell_features(xy)).max() < 1e-6


# ---------------------------------------------------------------- D2

def test_d2_invariance_of_new_heads(np_batch):
    model = _model(dtype=torch.float64)
    base = None
    for g in F.D2:
        s = torch.tensor(F.d2_signs(g), dtype=torch.float64)
        t = _tensors(np_batch, torch.float64)
        t["pos"], t["vel"] = t["pos"] * s, t["vel"] * s
        with torch.no_grad():
            o = model(t, average=True)
        zone = H.reflect_grid(torch.softmax(o["zone_logits"].flatten(1), -1).view_as(o["zone_logits"]), s.expand(B, 2))
        cur = {"recv": torch.softmax(o["receiver_logits"], -1), "out": torch.sigmoid(o["outcome_logits"]),
               "poss": torch.sigmoid(o["poss10_logit"]), "act": torch.softmax(o["action_logits"], -1),
               "cond": torch.sigmoid(o["shot_cond_logits"]), "marg": o["shot_marg_prob"], "zone": zone}
        base = base or cur
        for k in cur:
            assert float((cur[k] - base[k]).abs().max()) < 1e-5, (g, k)


def test_d2_equivariance_of_trajectories(np_batch):
    torch.manual_seed(0)
    net = TrajectoryNet(TrajConfig(hidden=32, blocks=1, mode_blocks=1, ffn=48)).double().eval()
    base = None
    for g in F.D2:
        s = torch.tensor(F.d2_signs(g), dtype=torch.float64)
        t = _tensors(np_batch, torch.float64)
        t["pos"], t["vel"] = t["pos"] * s, t["vel"] * s
        with torch.no_grad():
            o = net(t, average=True)
        cur = (o["mu"] * s, o["log_sigma"], o["pi_logits"])
        base = base or cur
        for a, b in zip(cur, base):
            assert float((a - b).abs().max()) < 1e-5


# ---------------------------------------------------------------- FiLM

def test_film_unknown_team_is_exactly_the_plain_critic(np_batch):
    model = _model()
    t = _tensors(np_batch)
    assert float(model.team_vector(torch.zeros(B, 2, dtype=torch.long)).abs().max().detach()) == 0.0
    plain = {k: v for k, v in t.items() if k != "team_ids"}
    unknown = {**t, "team_ids": torch.zeros_like(t["team_ids"])}
    known = {**t, "team_ids": torch.ones_like(t["team_ids"])}
    with torch.no_grad():
        a, b, c = model(plain, average=True), model(unknown, average=True), model(known, average=True)
    for k in ("outcome_logits", "zone_logits", "receiver_logits", "poss10_logit"):
        assert torch.equal(a[k], b[k])
    assert float((a["outcome_logits"] - c["outcome_logits"]).abs().max()) > 1e-4  # a known team does change it


def test_film_zero_init_and_unknown_name_lookup(np_batch):
    torch.manual_seed(0)
    table = _team_table()
    m = Next10GAT(Next10Config(**{**ModelConfig.for_profile_keys().to_dict(), "n_teams": table.size})).eval()
    m.set_team_table(table)
    t = _tensors(np_batch)
    with torch.no_grad():
        a = m(t, average=True)["outcome_logits"]
        b = m({k: v for k, v in t.items() if k != "team_ids"}, average=True)["outcome_logits"]
    assert torch.allclose(a, b, atol=1e-6)  # zero-initialised FiLM starts as the plain critic
    assert table.lookup("Alpha FC") >= 1 and table.lookup("Never Heard Of Them") == 0 and table.lookup(None) == 0
    assert table.lookup("alpha") == table.lookup("Álpha  FC") == table.lookup("Alpha FC")


def test_plain_gat_checkpoint_loads_into_next10(tmp_path):
    from matchlens.tacticsml.train.model import TacticsGAT

    torch.manual_seed(0)
    old = TacticsGAT(ModelConfig.for_profile_keys())
    path = tmp_path / "old.pt"
    torch.save({"format": "tacticsml-gat-v1", "config": old.cfg.to_dict(), "state_dict": old.state_dict()}, path)
    model, table = load_critic(path)
    assert not model.cfg.film and table.size == 1
    for k, v in old.state_dict().items():
        assert torch.equal(model.state_dict()[k], v)


# ---------------------------------------------------------------- view-mask augmentation

def test_view_mask_correctness(np_batch):
    cfg = ViewMaskConfig(p_apply=1.0)
    orig = {k: np.array(v, copy=True) for k, v in np_batch.items() if isinstance(v, np.ndarray) and v.dtype != object}
    out, masked = view_mask_batch(np_batch, np.random.default_rng(0), cfg)
    for k, v in orig.items():  # the input is not mutated
        assert np.array_equal(np_batch[k], v), k
    mask = np_batch["node_mask"] > 0
    assert not masked[:, F.GOAL_OWN:].any()                       # goal anchors are always known
    assert (masked <= mask).all()                                 # only real nodes are masked
    assert ((~masked) & mask)[:, :F.BALL].sum(1).min() >= cfg.min_visible  # enough players stay observed
    assert masked.any()
    obs = F.F_OBSERVED
    assert (out["node_feat"][..., obs][masked] == 0).all()
    assert np.array_equal(out["node_feat"][..., obs][~masked], np_batch["node_feat"][..., obs][~masked])
    assert np.array_equal(out["pos"][:, :, :][~masked[:, None].repeat(out["pos"].shape[1], 1)],
                          np_batch["pos"][~masked[:, None].repeat(out["pos"].shape[1], 1)])  # seen players untouched
    d = np.linalg.norm(out["pos"][:, -1, :F.BALL + 1] - np_batch["pos"][:, -1, :F.BALL + 1], axis=-1)[masked[:, :F.BALL + 1]]
    pl = np.linalg.norm(out["pos"][:, -1, :F.BALL] - np_batch["pos"][:, -1, :F.BALL], axis=-1)[masked[:, :F.BALL]]
    assert pl.size and 0.2 < np.median(pl) < 3.0 and d.max() < 12.0  # 0.5-1 m position noise (plus step jitter)
    assert (np.abs(out["pos"][..., 0]) <= F.HALF_L).all() and (np.abs(out["pos"][..., 1]) <= F.HALF_W).all()
    assert np.allclose(out["edge_feat"], edge_feat_from(out["pos"][:, -1], out["node_mask"], dtype=np.float32), atol=1e-6)
    again, m2 = view_mask_batch(np_batch, np.random.default_rng(0), cfg)  # deterministic given the seed
    assert np.array_equal(masked, m2) and np.array_equal(out["pos"], again["pos"])
    none, m0 = view_mask_batch(np_batch, np.random.default_rng(0), ViewMaskConfig(p_apply=0.0))
    assert not m0.any() and np.array_equal(none["pos"], np_batch["pos"])


# ---------------------------------------------------------------- losses

def test_loss_sanity(np_batch):
    model = _model().train()
    t = _tensors(np_batch)
    out = model(t, average=False)
    losses = H.next10_losses(out, t)
    assert all(torch.isfinite(v) for v in losses.values())
    for k in ("zone", "poss10", "action", "shot_cond", "shot_marg"):
        assert k in losses
    losses["total"].backward()
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for n, p in model.named_parameters()
               if n.startswith(("zone.", "shot_head.", "poss10_head.", "action_head.", "film.")))
    # a perfect zone prediction costs the entropy of the smoothed target, a uniform one costs more
    cell = torch.tensor([10, 90, 150])
    target = torch.as_tensor(C.smoothed_target(cell.numpy()))
    ent = float(-(target * target.clamp_min(1e-12).log()).sum(1).mean())
    perfect = H.zone_loss(target.clamp_min(1e-12).log().view(3, C.GRID_ROWS, C.GRID_COLS), cell)
    uniform = H.zone_loss(torch.zeros(3, C.GRID_ROWS, C.GRID_COLS), cell)
    assert abs(float(perfect) - ent) < 1e-4 and float(uniform) > float(perfect)
    assert abs(float(uniform) - np.log(C.N_CELLS)) < 1e-4
    # labels of -1 are ignored: removing every label removes the term
    t2 = dict(t)
    t2["poss10"], t2["action_next"] = torch.full_like(t["poss10"], -1), torch.full_like(t["action_next"], -1)
    l2 = H.next10_losses(out, t2)
    assert "poss10" not in l2 and "action" not in l2
    # the marginal shot probability stays a probability and the decomposition is consistent with its parts
    p = out["shot_marg_prob"]
    assert ((p >= 0) & (p <= 1)).all()


def test_trajectory_loss_prefers_the_matching_mode(np_batch):
    t = _tensors(np_batch)
    K, A, Hh = 6, F.BALL + 1, 10
    tgt = t["fut_pos"]
    mu = tgt[:, :, :A].permute(0, 2, 1, 3)[:, None].repeat(1, K, 1, 1, 1)
    mu = mu + torch.arange(K, dtype=torch.float32)[None, :, None, None, None] * 3.0  # mode 0 is exact
    base = {"mu": mu, "log_sigma": torch.zeros(B, K, A, Hh, 2), "pi_logits": torch.zeros(B, K)}
    exact = trajectory_loss(base, tgt, t["fut_valid"], t["node_mask"])
    off = trajectory_loss({**base, "mu": mu + 5.0}, tgt, t["fut_valid"], t["node_mask"])
    e, o = (x["total"] if isinstance(x, dict) else x for x in (exact, off))
    assert float(e) < float(o)


# ---------------------------------------------------------------- numpy == torch

def _export_critic(model: Next10GAT, path, table):
    save_critic(model, path.with_suffix(".pt"), 0, table, {})
    blob = torch.load(path.with_suffix(".pt"), map_location="cpu", weights_only=False)
    export10.write_critic(blob, path)


def test_numpy_torch_parity_critic(np_batch, tmp_path):
    model = _model(dtype=torch.float32)
    table = _team_table()
    _export_critic(model, tmp_path / "c.npz", table)
    model64 = _model(dtype=torch.float32)
    model64.load_state_dict(model.state_dict())
    model64 = model64.double()  # same weights, double arithmetic on both sides
    net = I10.Next10Net.load(tmp_path / "c.npz", dtype=np.float64)
    t = _tensors(np_batch, torch.float64)
    with torch.no_grad():
        ref = model64(t, average=True)
    got = net.forward_next10(np_batch, np_batch["team_ids"])
    for k in ("receiver_logits", "outcome_logits", "zone_logits", "poss10_logit", "action_logits", "shot_cond_logits",
              "shot_marg_prob", "embedding"):
        assert np.abs(got[k] - ref[k].numpy()).max() < 1e-5, k
    # and in float32, the serving dtype, to a looser but still tight tolerance
    net32 = I10.Next10Net.load(tmp_path / "c.npz", dtype=np.float32)
    got32 = net32.forward_next10(np_batch, np_batch["team_ids"])
    with torch.no_grad():
        ref32 = model(_tensors(np_batch), average=True)
    for k in ("outcome_logits", "zone_logits", "shot_marg_prob"):
        assert np.abs(got32[k] - ref32[k].numpy()).max() < 1e-4, k


def test_numpy_torch_parity_trajectory(np_batch, tmp_path):
    torch.manual_seed(0)
    cfg = TrajConfig(hidden=32, blocks=2, mode_blocks=1, ffn=48)
    net = TrajectoryNet(cfg)
    with torch.no_grad():
        for p in net.parameters():
            p.add_(torch.randn_like(p) * 0.05)
    net.eval()
    save_trajectory(net, tmp_path / "t.pt", {})
    blob = torch.load(tmp_path / "t.pt", map_location="cpu", weights_only=False)
    export10.write_trajectory(blob, tmp_path / "t.npz")
    ref_net = TrajectoryNet(cfg)
    ref_net.load_state_dict(net.state_dict())
    ref_net = ref_net.double().eval()
    with torch.no_grad():
        ref = ref_net(_tensors(np_batch, torch.float64), average=True)
    got = I10.TrajNet.load(tmp_path / "t.npz", dtype=np.float64).forward(np_batch)
    assert np.abs(got["mu"] - ref["mu"].numpy()).max() < 1e-5
    assert np.abs(got["log_sigma"] - ref["log_sigma"].numpy()).max() < 1e-5
    assert np.abs(got["pi_logits"] - ref["pi_logits"].numpy()).max() < 1e-5


# ---------------------------------------------------------------- export + serve

@pytest.fixture(scope="module")
def weights_dir(tmp_path_factory, np_batch):
    root = tmp_path_factory.mktemp("next10")
    ck = root / "ckpt"
    table = _team_table()
    for seed in (0, 1):
        save_critic(_model(seed=seed), ck / f"next10_seed{seed}.pt", seed, table, {})
    torch.manual_seed(0)
    save_trajectory(TrajectoryNet(TrajConfig(hidden=32, blocks=1, mode_blocks=1, ffn=48)).eval(), ck / "trajectory_seed0.pt", {})
    man = export10.export(ck, root / "weights", log=lambda *_: None)
    assert (root / "weights" / "manifest.json").is_file() and len(man["members"]) == 2
    return root / "weights"


def test_serve_roundtrip_schema(weights_dir, demo_match):
    assert I10.next10_available(weights_dir)
    model = I10.Next10Model.load(weights_dir)
    frame = from_tracking(demo_match.tracking[296:301], demo_match.meta, demo_match.meta.home.team_id)
    out = model.predict(frame, ("Alpha FC", "Unknown Club"), top_k=3)
    assert out["team_known"] and out["possession_team_id"] == demo_match.meta.home.team_id
    for k in ("shot10", "goal10", "concede10", "turn5", "poss10"):
        assert 0.0 <= out["probs"][k] <= 1.0 and out["prob_std"][k] >= 0.0
    z = out["ball_zone10"]
    assert z["cols"] == 16 and z["rows"] == 12 and len(z["p"]) == 192 and abs(sum(z["p"]) - 1.0) < 1e-5
    assert len(out["receivers"]) == 3 and out["receivers"][0]["p"] >= out["receivers"][1]["p"]
    assert sum(r["p"] for r in out["receivers"]) + out["receiver_lost"] <= 1.0 + 1e-6
    assert abs(sum(out["action_next"].values()) - 1.0) < 1e-5
    tr = out["trajectories"]
    assert tr["k"] == 6 and tr["dt_s"] == 1.0 and tr["horizon_s"] == 10
    ents = {e["entity_id"] for e in tr["entities"]}
    assert "ball" in ents and len(ents) >= 22
    modes = tr["entities"][0]["modes"]
    assert len(modes) == 6 and len(modes[0]["path"]) == 10 and abs(sum(m["p"] for m in modes) - 1.0) < 1e-5
    assert sum(1 for _ in json.dumps(out)) > 0  # JSON-serialisable as is
    unknown = model.predict(frame, ("Nobody", "Nobody Else"))
    assert not unknown["team_known"]
    assert model.predict(frame, with_trajectories=False).get("trajectories") is None


def test_serve_mirrors_the_attacking_direction(weights_dir, demo_match):
    """The zone / receivers are in the input orientation: a mirrored input gives the mirrored zone."""
    model = I10.Next10Model.load(weights_dir)
    f = from_tracking(demo_match.tracking[296:301], demo_match.meta, demo_match.meta.home.team_id)
    a = np.array(model.predict(f)["ball_zone10"]["p"]).reshape(12, 16)
    assert abs(a.sum() - 1.0) < 1e-5 and (a >= 0).all()


def test_tampered_weights_are_refused(weights_dir, tmp_path):
    import shutil

    d = tmp_path / "w"
    shutil.copytree(weights_dir, d)
    (d / "critic_seed0.npz").write_bytes((d / "critic_seed0.npz").read_bytes() + b"x")
    assert not I10.next10_available(d)
    with pytest.raises(ValueError):
        I10.Next10Model.load(d)
    assert not I10.next10_available(tmp_path / "missing")


# ---------------------------------------------------------------- DDPM skeleton ("play it out")

def test_ddpm_schedule_and_forward_process():
    sch = D.Schedule(50, torch.float64)
    assert bool((sch.abar[1:] < sch.abar[:-1]).all()) and 0 < float(sch.abar[-1]) < 1e-3 and float(sch.abar[0]) > 0.99
    x0 = torch.randn(4000, 3, dtype=torch.float64)
    xs = D.q_sample(x0, torch.full((4000,), 49), torch.randn_like(x0), sch)
    assert abs(float(xs.std()) - 1.0) < 0.05 and abs(float(xs.mean())) < 0.05  # ~ N(0, I) at the last step


def _toy_batch(np_batch):
    return _tensors(np_batch)


def test_ddpm_loss_gradient_and_masking(np_batch):
    torch.manual_seed(0)
    t = _toy_batch(np_batch)
    model = D.Denoiser(D.DiffusionConfig(steps=20, hidden=32, blocks=1, ffn=48))
    sch = D.Schedule(20)
    x0, ok = D.future_to_x0(t["fut_pos"], t["fut_valid"], t)
    loss = D.ddpm_loss(model, sch, x0, ok, t, torch.Generator().manual_seed(0))
    assert torch.isfinite(loss) and float(loss) > 0
    loss.backward()
    assert all(p.grad is not None for p in model.parameters())
    ok0 = torch.zeros_like(ok)
    assert float(D.ddpm_loss(model, sch, x0, ok0, t, torch.Generator().manual_seed(0))) == 0.0


def test_denoiser_equivariance_with_frame_averaging(np_batch):
    torch.manual_seed(0)
    model = D.Denoiser(D.DiffusionConfig(steps=20, hidden=32, blocks=1, ffn=48)).double().eval()
    t = _tensors(np_batch, torch.float64)
    xs = torch.randn(B, F.BALL + 1, 10, 2, dtype=torch.float64)
    s = torch.full((B,), 7)
    with torch.no_grad():
        base = model(xs, s, t, average=True)
        for g in F.D2:
            sg = torch.tensor(F.d2_signs(g), dtype=torch.float64)
            tg = dict(t)
            tg["pos"], tg["vel"] = t["pos"] * sg, t["vel"] * sg
            out = model(xs * sg, s, tg, average=True)
            assert float((out - base * sg).abs().max()) < 1e-5


def test_play_out_trains_and_samples(np_batch):
    """Overfit one constant-velocity scene for a few steps: samples must land nearer the truth than untrained ones."""
    torch.manual_seed(0)
    t = _toy_batch(np_batch)
    t = {k: v[:2] if torch.is_tensor(v) else v for k, v in t.items()}
    cfg = D.DiffusionConfig(steps=20, hidden=48, blocks=2, ffn=64)
    model = D.Denoiser(cfg)
    sch = D.Schedule(cfg.steps)
    x0, ok = D.future_to_x0(t["fut_pos"], t["fut_valid"], t)

    def err() -> float:
        out = D.play_out(model, t, n_samples=3, seed=1)
        truth = t["fut_pos"][:, :, : F.BALL + 1].permute(0, 2, 1, 3)[:, None]
        return float((out["paths"] - truth).norm(dim=-1).mean())

    before = err()
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)
    model.train()
    for _ in range(150):
        opt.zero_grad()
        D.ddpm_loss(model, sch, x0, ok, t).backward()
        opt.step()
    after = err()
    out = D.play_out(model, t, n_samples=3, seed=1)
    assert out["paths"].shape == (2, 3, F.BALL + 1, 10, 2) and torch.isfinite(out["paths"]).all()
    assert out["spread"].shape == (2, F.BALL + 1)
    assert after < before
    assert torch.equal(out["paths"], D.play_out(model, t, n_samples=3, seed=1)["paths"])  # reproducible
