"""Similar-moment retrieval over shards (numpy only)."""

from __future__ import annotations

import numpy as np
import pytest

from matchlens.tacticsml import features as F
from matchlens.tacticsml import retrieval
from matchlens.tacticsml.contract import from_tracking
from matchlens.tacticsml.retrieval import RetrievalIndex, build_index, descriptor, load_index, outcome_label
from matchlens.tacticsml.train import schema

N = 24


@pytest.fixture(scope="module")
def samples(demo_match):
    out = []
    for j in range(N):
        i = 10 + 25 * j
        team = demo_match.meta.home.team_id if j % 2 == 0 else demo_match.meta.away.team_id
        out.append(F.build_sample(from_tracking(demo_match.tracking[i - 4:i + 1], demo_match.meta, team)))
    return out


def _labels(n: int) -> dict[str, list[int]]:
    shot = [1 if i % 4 == 0 else 0 for i in range(n)]
    return {"shot10": shot, "goal10": [0] * n, "concede10": [0] * n, "turn5": [1 if i % 4 == 1 else 0 for i in range(n)]}


@pytest.fixture(scope="module")
def shard_root(samples, tmp_path_factory):
    root = tmp_path_factory.mktemp("shards")
    for name, part in (("m1", samples[:12]), ("m2", samples[12:])):
        tp, valid = schema.target_positions(part, [None] * len(part))
        arrays = schema.pack(part, [11] * len(part), _labels(len(part)), [schema.KIND_OPEN] * len(part), tp, valid)
        schema.write_shard("synthetic", name, arrays, root=root)
    return root


def test_outcome_labels():
    assert outcome_label(1, 0, 0) == "shot" and outcome_label(1, 1, 0) == "goal"
    assert outcome_label(0, 0, 1) == "turnover" and outcome_label(0, 0, 0) == "retained"
    assert outcome_label(-1, -1, -1) == "unknown"


def test_descriptor_is_d2_invariant(samples):
    batch = F.stack(samples)
    base = descriptor(batch)
    assert base.shape == (N, 39) and np.isfinite(base).all()
    for g in F.D2:
        pos, vel = F.apply_d2(g, batch["pos"], batch["vel"])
        assert np.abs(descriptor({**batch, "pos": pos, "vel": vel}) - base).max() < 1e-9
    assert base.std(0).max() > 0.01  # it tells moments apart


def test_build_search_save_load(samples, shard_root, tmp_path):
    index = build_index(None, root=shard_root)
    assert index.kind == "descriptor" and len(index) == N
    path = tmp_path / "retrieval.npz"
    index.save(path)
    back = load_index(path)
    assert back is not None and len(back) == N and np.allclose(back.emb, index.emb)
    assert np.allclose(np.linalg.norm(back.emb, axis=1), 1.0, atol=1e-5)

    q = descriptor(F.stack([samples[7]]))[0]
    hits = back.search(q, k=3)
    assert hits and hits[0].similarity > 0.999
    assert hits[0].match_label == "synthetic:m1" and hits[0].source == "synthetic"
    assert hits[0].outcome == "retained"  # sample 7: no shot, turn5 label only at i % 4 == 1
    assert [h.similarity for h in hits] == sorted((h.similarity for h in hits), reverse=True)
    assert len({(h.match_label, round(h.elapsed / retrieval.DEDUP_SECONDS)) for h in hits}) == len(hits)

    # a mirrored copy of the scene finds the same moments
    pos, vel = F.apply_d2("fxy", *(F.stack([samples[7]])[k] for k in ("pos", "vel")))
    mirrored = descriptor({**F.stack([samples[7]]), "pos": pos, "vel": vel})[0]
    assert [h.elapsed for h in back.search(mirrored, k=3)] == [h.elapsed for h in hits]

    # excluding the query's own match
    other = back.search(q, k=3, exclude=("synthetic", "m1"))
    assert other and all(h.match_label == "synthetic:m2" for h in other)


def test_outcomes_follow_the_labels(samples, shard_root):
    index = build_index(None, root=shard_root)
    first = index.outcome[index.match_id == "m1"]
    assert list(first[:5]) == ["shot", "turnover", "retained", "retained", "shot"]


def test_missing_or_broken_index_degrades(tmp_path):
    assert load_index(tmp_path / "nope.npz") is None
    bad = tmp_path / "bad.npz"
    bad.write_bytes(b"not an npz")
    assert load_index(bad) is None
    empty = build_index(None, root=tmp_path / "no-shards")
    assert len(empty) == 0 and empty.search(np.zeros(39), k=3) == []


def test_critic_index_uses_the_model_embedding(samples, shard_root):
    class Fake:
        n_node_feat = F.F_BASE
        weights_hash = "ab" * 32

        def embed_batch(self, batch):
            return batch["pos"][:, -1, :22].reshape(len(batch["pos"]), -1)[:, :8].astype(np.float64)

    index = build_index(Fake(), root=shard_root)
    assert index.kind == "critic" and index.model_hash == Fake.weights_hash and len(index) == N
    skipped = build_index(type("Wide", (Fake,), {"n_node_feat": 9})(), root=shard_root)
    assert len(skipped) == 0  # shards with another feature width are not mixed in


def test_max_samples_caps_the_index(shard_root):
    assert len(build_index(None, root=shard_root, max_samples=15)) == 15
