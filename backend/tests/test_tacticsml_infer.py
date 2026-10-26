"""Numpy inference of the tactical model: parity with torch, D2 invariance, registry and no-torch import."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from matchlens.tacticsml import features as F
from matchlens.tacticsml import learned, registry
from matchlens.tacticsml.contract import Intent, from_tracking
from matchlens.tacticsml.infer_numpy import (
    CvaeDecoder, Ensemble, GatNet, sigmoid, softmax,
)

TINY = dict(hidden=16, heads=2, layers=2, ffn=24, emb_dim=8)
PROFILE = ("pace", "height_cm")


def _frames(match, n: int = 6):
    out = []
    for j, i in enumerate(range(20, 20 + 30 * n, 30)):
        team = match.meta.home.team_id if j % 2 == 0 else match.meta.away.team_id
        out.append(from_tracking(match.tracking[i - 4:i + 1], match.meta, team))
    return out


def _batch(frames, profile_keys=(), seed: int = 0):
    samples = [F.build_sample(f, profile_keys) for f in frames]
    b = F.stack(samples)
    if profile_keys:  # make the profile columns non-trivial
        rng = np.random.default_rng(seed)
        P = len(profile_keys)
        b["node_feat"][..., F.F_BASE:F.F_BASE + P] = rng.normal(70, 10, b["node_feat"][..., :P].shape)
        b["node_feat"][..., F.F_BASE + P:F.F_BASE + 2 * P] = rng.integers(0, 2, b["node_feat"][..., :P].shape)
    return samples, b


def _no_torch_env_clean(monkeypatch, tmp_path: Path | None = None):
    monkeypatch.delenv("MATCHLENS_EVALUATOR", raising=False)
    monkeypatch.setattr(learned, "CANDIDATE_DIRS", ())
    monkeypatch.setenv(learned.ENV_WEIGHTS, str(tmp_path or "/nonexistent-tacticsml-weights"))
    registry.get_tactical_model.cache_clear()


@pytest.fixture
def clean_registry(monkeypatch):
    _no_torch_env_clean(monkeypatch)
    yield monkeypatch
    registry.get_tactical_model.cache_clear()


# ---------------------------------------------------------------- torch-backed fixtures

@pytest.fixture(scope="module")
def torch_mod():
    return pytest.importorskip("torch")


def _random_model(torch, profile_keys=(), seed: int = 0):
    from matchlens.tacticsml.train.model import ModelConfig, TacticsGAT

    torch.manual_seed(seed)
    model = TacticsGAT(ModelConfig.for_profile_keys(profile_keys, **TINY))
    with torch.no_grad():
        for p in model.parameters():
            p.add_(torch.randn_like(p) * 0.2)
        if profile_keys:
            model.set_profile_stats(np.array([70.0, 65.0]), np.array([9.0, 12.0]))
    return model.eval()


def _random_cvae(torch, encoder, seed: int = 0):
    from matchlens.tacticsml.train import cvae as cv

    torch.manual_seed(seed)
    model = cv.ConditionalVAE(encoder, cv.CVAEConfig(hidden=24, q_hidden=8))
    with torch.no_grad():
        for p in model.parameters():
            if p.requires_grad:
                p.add_(torch.randn_like(p) * 0.3)
    return model.eval()


@pytest.fixture(scope="module")
def exported(torch_mod, tmp_path_factory):
    """Two tiny members + a cVAE exported through the real ``train.export`` path."""
    from matchlens.tacticsml.train import export, fit

    root = tmp_path_factory.mktemp("tacticsml")
    ckpt, out = root / "ckpt", root / "weights"
    models = [_random_model(torch_mod, seed=s) for s in (0, 1)]
    for s, m in enumerate(models):
        fit.save_member(m, ckpt / f"gat_seed{s}.pt", s, (), {})
    cv = _random_cvae(torch_mod, models[0])
    fit.save_cvae(cv, ckpt / "cvae.pt", "gat_seed0.pt")
    manifest = export.export(ckpt, out, log=lambda _m: None)
    return {"dir": out, "models": models, "cvae": cv, "manifest": manifest}


# ---------------------------------------------------------------- (a) torch vs numpy

def test_gat_parity_with_torch(torch_mod, demo_match):
    from matchlens.tacticsml.train import export
    from matchlens.tacticsml.train.model import to_tensors

    for keys in ((), PROFILE):
        model = _random_model(torch_mod, keys)
        blob = {"format": "tacticsml-gat-v1", "config": model.cfg.to_dict(), "state_dict": model.state_dict(),
                "profile_keys": list(keys), "seed": 0}
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            export.write_member(blob, Path(d) / "m.npz")
            net = GatNet.load(Path(d) / "m.npz")
        assert net.profile_keys == keys
        _, batch = _batch(_frames(demo_match), keys)
        ref = model.predict(to_tensors(batch))
        got = net.predict(batch)
        assert np.abs(got["receiver_probs"] - ref["receiver_probs"].numpy()).max() < 1e-4
        assert np.abs(got["embedding"] - ref["embedding"].numpy()).max() < 1e-4
        for i, name in enumerate(("shot10", "goal10", "concede10", "turn5")):
            assert np.abs(got["outcomes"][:, i] - ref[name].numpy()).max() < 1e-4, name


def test_cvae_decoder_parity_with_torch(torch_mod, exported, demo_match):
    from matchlens.tacticsml.train import cvae as cv
    from matchlens.tacticsml.train.model import to_tensors

    torch = torch_mod
    frames = _frames(demo_match, 3)
    _, batch = _batch(frames)
    graph = to_tensors(batch)
    B, N = graph["node_mask"].shape
    rng = np.random.default_rng(3)
    target, team = np.array([0, 2, 3]), np.array([0, 1, 2])
    receiver = np.array([-1, -1, 4])
    locked = (rng.random((B, N)) < 0.2).astype(np.float32)
    z = rng.standard_normal((B, N, 8)).astype(np.float32)
    speed = rng.uniform(6, 9, (B, N)).astype(np.float32)
    cond = cv.Condition(torch.as_tensor(target), torch.as_tensor(team), torch.as_tensor(receiver),
                        torch.as_tensor(locked), torch.as_tensor(speed))
    with torch.no_grad():
        ref = exported["cvae"].decode(graph, cond, torch.as_tensor(z), average=True, max_move=8.0).numpy()

    ens = Ensemble.load([exported["dir"] / m["file"] for m in exported["manifest"]["members"]])
    dec = CvaeDecoder.load(exported["dir"] / "cvae.npz")
    runs = ens.members[0].run_d2(batch)
    got = dec.decode(runs, batch["pos"][:, -1], batch["node_mask"], target, team, receiver, locked, z,
                     dec.radius(speed, (B, N), 8.0))
    assert np.abs(got - ref).max() < 1e-4
    assert np.linalg.norm(got, axis=-1).max() < 8.0  # speed cap holds


def test_constants_match_training_code(torch_mod):
    from matchlens.tacticsml import infer_numpy as I
    from matchlens.tacticsml.train import cvae as cv
    from matchlens.tacticsml.train import model as M

    assert (I.OUTCOMES, I.N_SLOTS, I.LOST, I.NEG) == (M.OUTCOMES, M.N_SLOTS, M.LOST, M.NEG)
    assert (I.POS_SCALE, I.VEL_SCALE, I.REL_SCALE, I.CARRIER_COL) == (M.POS_SCALE, M.VEL_SCALE, M.REL_SCALE,
                                                                       M.CARRIER_COL)
    assert (I.TARGETS, I.TEAMS, I.DISP_SCALE, I.GAT_FORMAT, I.CVAE_FORMAT) == (cv.TARGETS, cv.TEAMS, cv.DISP_SCALE,
                                                                                M.FORMAT, cv.FORMAT)


def test_manifest_hashes(exported):
    from matchlens.tacticsml.train import export

    d, m = exported["dir"], exported["manifest"]
    assert m["seeds"] == [0, 1] and m["cvae"]["encoder_member"] == "gat_seed0.npz"
    for f in m["members"] + [m["cvae"]]:
        assert export.sha256_file(d / f["file"]) == f["sha256"]
    assert json.loads((d / "manifest.json").read_text())["hash"] == m["hash"]
    assert learned.weights_available(d)
    (d / "gat_seed1.npz").write_bytes((d / "gat_seed1.npz").read_bytes() + b"x")
    try:
        assert not learned.weights_available(d)  # a corrupted file falls back instead of failing in the registry
    finally:
        (d / "gat_seed1.npz").write_bytes((d / "gat_seed1.npz").read_bytes()[:-1])
    assert learned.weights_available(d)


# ---------------------------------------------------------------- (b) invariance

def test_numpy_outputs_are_d2_invariant(torch_mod, exported, demo_match):
    net = GatNet.load(exported["dir"] / "gat_seed0.npz", dtype=np.float64)
    _, batch = _batch(_frames(demo_match))
    base = net.predict(batch)
    for g in F.D2:
        pos, vel = F.apply_d2(g, batch["pos"], batch["vel"])
        got = net.predict({**batch, "pos": pos, "vel": vel})
        for k in base:
            assert np.abs(got[k] - base[k]).max() < 1e-5, (g, k)
    assert not np.allclose(base["outcomes"][0], base["outcomes"][1])  # the random model is not constant


def test_probabilities_are_proper(exported, demo_match):
    net = GatNet.load(exported["dir"] / "gat_seed0.npz")
    _, batch = _batch(_frames(demo_match))
    out = net.predict(batch)
    assert np.allclose(out["receiver_probs"].sum(-1), 1.0, atol=1e-5)
    assert ((out["outcomes"] >= 0) & (out["outcomes"] <= 1)).all()
    pads = batch["node_mask"][:, :F.N_TEAM] == 0
    assert (out["receiver_probs"][:, :F.N_TEAM][pads] < 1e-6).all()


# ---------------------------------------------------------------- LearnedModel

def test_learned_model_end_to_end(exported, demo_match):
    model = learned.LearnedModel.load(exported["dir"])
    assert model.version == f"learned-gat-v1/{exported['manifest']['hash'][:8]}"
    frames = _frames(demo_match)
    res = model.score_batch(frames)
    assert len(res) == len(frames) and all(r.model_version == model.version for r in res)
    for f, r in zip(frames, res):
        assert 0 <= r.shot_prob <= 1 and r.shot_std >= 0 and r.turnover_std >= 0
        assert abs(sum(r.receiver_probs.values()) - 1.0) < 1e-3
        assert f.carrier_id not in r.receiver_probs
        assert len(r.embedding) == 8
        assert abs(r.value - (r.goal_prob - r.concede_prob)) < 1e-4
    # two members that differ -> a real spread
    assert max(r.shot_std for r in res) > 0

    one = model.score(frames[0])
    assert one.xt_delta == 0.0 and one.pitch_control is not None
    assert abs(one.shot_prob - res[0].shot_prob) < 1e-4
    f = frames[0]
    mover = next(p for p in f.players if p.team_id == f.possession_team_id and p.player_id != f.carrier_id)
    moved = model.score(f, Intent(moves={mover.player_id: (mover.x + 6, mover.y + 3)}))
    assert moved.xt_delta is not None
    assert abs(moved.xt_delta - (moved.value - one.value)) < 1e-4
    assert moved.embedding != one.embedding  # the intent changed the scene


def test_propose_respects_locks_and_speed(exported, demo_match):
    model = learned.LearnedModel.load(exported["dir"])
    f = _frames(demo_match)[1]
    team = f.possession_team_id
    mates = [p.player_id for p in f.players if p.team_id == team]
    lock = frozenset(mates[:4])
    by_id = {p.player_id: p for p in f.players}
    for target in ("shot", "retain", f"receiver:{mates[5]}"):
        for both in (False, True):
            props = model.propose(f, team, target, k=3, max_move_m=7.0, locked=lock, both_teams=both)
            assert len(props) <= 3
            for pr in props:
                assert pr.gain["objective"] > 0 and pr.score.model_version == model.version
                for pid, (x, y) in pr.moves.items():
                    p = by_id[pid]
                    assert pid not in lock or (p.team_id != team)
                    if p.team_id == team:
                        assert np.hypot(x - p.x, y - p.y) <= 7.0 + 1e-6
                        assert not p.is_gk or np.hypot(x - p.x, y - p.y) <= 6.0 + 1e-6
                    assert 0 <= x <= 105 and 0 <= y <= 68
    defending = next(t for t in {p.team_id for p in f.players} if t != team)
    model.propose(f, defending, "press", k=2)  # the defending side works too
    with pytest.raises(ValueError):
        model.propose(f, team, "nonsense")


def test_propose_without_cvae_uses_the_baseline(exported, demo_match, tmp_path):
    import shutil

    d = tmp_path / "w"
    shutil.copytree(exported["dir"], d)
    manifest = json.loads((d / "manifest.json").read_text())
    manifest["cvae"] = None
    (d / "manifest.json").write_text(json.dumps(manifest))
    (d / "cvae.npz").unlink()
    model = learned.LearnedModel.load(d)
    assert model.cvae is None and model.similar(_frames(demo_match)[0]) == []
    f = _frames(demo_match)[0]
    props = model.propose(f, f.possession_team_id, "shot", k=2)
    assert all(p.score.model_version.startswith("baseline") for p in props)


def test_similar_uses_a_matching_critic_index(exported, demo_match):
    from matchlens.tacticsml.retrieval import RetrievalIndex

    model = learned.LearnedModel.load(exported["dir"])
    frames = _frames(demo_match, 6)
    _, batch = _batch(frames)
    raw = model.embed_batch(batch)
    idx = RetrievalIndex.from_raw("critic", raw, ["demo"] * 6, [f"m{i}" for i in range(6)], np.arange(6) * 60.0,
                                  ["shot"] * 6, model.weights_hash)
    idx.save(exported["dir"] / "retrieval.npz")
    try:
        hits = learned.LearnedModel.load(exported["dir"]).similar(frames[2], k=3)
        assert hits and hits[0].match_label == "demo:m2" and hits[0].similarity > 0.99
        stale = RetrievalIndex.from_raw("critic", raw, ["demo"] * 6, [f"m{i}" for i in range(6)],
                                        np.arange(6) * 60.0, ["shot"] * 6, "0" * 64)
        stale.save(exported["dir"] / "retrieval.npz")
        assert learned.LearnedModel.load(exported["dir"]).similar(frames[2]) == []  # built from other weights
    finally:
        (exported["dir"] / "retrieval.npz").unlink()


# ---------------------------------------------------------------- (c) registry

def test_registry_returns_baseline_without_weights(clean_registry):
    from matchlens.tacticsml.baseline import BaselineModel

    assert not learned.weights_available()
    assert isinstance(registry.get_tactical_model(), BaselineModel)
    clean_registry.setenv("MATCHLENS_EVALUATOR", "learned")
    registry.get_tactical_model.cache_clear()
    assert isinstance(registry.get_tactical_model(), BaselineModel)


def test_registry_returns_learned_when_weights_present(exported, clean_registry):
    clean_registry.setenv(learned.ENV_WEIGHTS, str(exported["dir"]))
    clean_registry.setenv("MATCHLENS_EVALUATOR", "learned")
    registry.get_tactical_model.cache_clear()
    assert isinstance(registry.get_tactical_model(), learned.LearnedModel)
    clean_registry.setenv("MATCHLENS_EVALUATOR", "baseline")
    registry.get_tactical_model.cache_clear()
    from matchlens.tacticsml.baseline import BaselineModel

    assert isinstance(registry.get_tactical_model(), BaselineModel)
    clean_registry.setenv("MATCHLENS_EVALUATOR", "auto")
    registry.get_tactical_model.cache_clear()
    assert isinstance(registry.get_tactical_model(), learned.LearnedModel)


# ---------------------------------------------------------------- (d) no torch

_NO_TORCH = textwrap.dedent("""
    import sys
    sys.modules["torch"] = None  # any `import torch` now raises ImportError
    from matchlens.tacticsml import infer_numpy, learned, registry, retrieval
    from matchlens.tacticsml.contract import TBall, TFrame, TPlayer
    out = {"imported": True}
    d = sys.argv[1] if len(sys.argv) > 1 else ""
    if d:
        model = learned.LearnedModel.load(d)
        players = [TPlayer(player_id=f"{t}{i}", team_id=t, x=20.0 + 5 * i + (40 if t == "b" else 0),
                           y=10.0 + 4 * i, is_gk=i == 0) for t in "ab" for i in range(11)]
        frame = TFrame(players=players, ball=TBall(x=30.0, y=20.0), carrier_id="a3", possession_team_id="a",
                       attacking_right={"a": True, "b": False})
        res = model.score(frame)
        props = model.propose(frame, "a", "shot", k=2)
        out.update(version=model.version, shot=res.shot_prob, n_props=len(props), torch="torch" in sys.modules
                   and sys.modules["torch"] is not None)
    print(__import__("json").dumps(out))
""")


def _run_no_torch(*args: str) -> dict:
    proc = subprocess.run([sys.executable, "-c", _NO_TORCH, *args], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_serving_modules_import_without_torch():
    assert _run_no_torch()["imported"]


def test_learned_model_runs_without_torch(exported):
    out = _run_no_torch(str(exported["dir"]))
    assert out["version"].startswith("learned-gat-v1/") and 0 <= out["shot"] <= 1 and not out["torch"]


def test_propose_ranks_sampled_moves_with_the_critic(exported, demo_match, monkeypatch):
    """With a stand-in critic that likes players further upfield, the cVAE samples are kept, pruned and ranked."""
    from matchlens.tacticsml.contract import ScoreResult

    model = learned.LearnedModel.load(exported["dir"])

    def fake(frames, with_control=False):
        out = []
        for f in frames:
            xs = [p.x if f.attacking_right[f.possession_team_id] else 105 - p.x
                  for p in f.players if p.team_id == f.possession_team_id]
            q = float(np.mean(xs)) / 105
            out.append(ScoreResult(receiver_probs={}, shot_prob=q, value=q, turnover_prob=1 - q, model_version="fake"))
        return out

    monkeypatch.setattr(model, "_score_frames", fake)
    f = _frames(demo_match)[0]
    team = f.possession_team_id
    for both in (False, True):
        props = model.propose(f, team, "shot", k=3, max_move_m=8.0, both_teams=both)
        assert props, "some sampled moves must beat the unmoved frame under this critic"
        gains = [p.gain["objective"] for p in props]
        assert gains == sorted(gains, reverse=True) and gains[0] > 0
        assert all(1 <= len(p.moves) <= 7 for p in props)
