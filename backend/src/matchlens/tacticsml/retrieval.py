"""Similar real moments: a cosine index over the training shards (numpy only, saved as ``.npz``).

Two kinds of vectors, both standardised with the index's own mean / std and L2 normalised:

* ``critic``: the D2-averaged pooled embedding of ensemble member 0 (needs the learned weights; the index
  records the weight hash and is ignored when the weights change),
* ``descriptor``: a handcrafted vector made of distances and speeds only, so it is D2 invariant without any
  weights (used before a model is trained).

A missing or unreadable index never raises: ``load_index`` returns ``None`` and callers return ``[]``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml.contract import RetrievedMoment

log = logging.getLogger(__name__)

INDEX_FILE = "retrieval.npz"
INDEX_FORMAT = "tacticsml-retrieval-v1"
DEDUP_SECONDS = 6.0  # moments of one match closer than this count as the same situation
_FAR = 60.0  # distance standing in for a missing player


def outcome_label(shot10: int, goal10: int, turn5: int, concede10: int = -1) -> str:
    """What happened next, for ``RetrievedMoment.outcome``."""
    if goal10 == 1:
        return "goal"
    if shot10 == 1:
        return "shot"
    if turn5 == 1:
        return "turnover"
    if concede10 == 1:
        return "conceded"
    return "unknown" if shot10 < 0 and turn5 < 0 else "retained"


# ---------------------------------------------------------------- handcrafted descriptor

def _pair_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.linalg.norm(a[:, :, None] - b[:, None], axis=-1)


def descriptor(batch: dict[str, np.ndarray]) -> np.ndarray:
    """(B, 39) distance / speed features of a batch in ``features.stack`` layout (D2 invariant)."""
    pos = np.asarray(batch["pos"], dtype=np.float64)
    vel = np.asarray(batch["vel"], dtype=np.float64)
    mask = np.asarray(batch["node_mask"], dtype=np.float64) > 0
    B = pos.shape[0]
    last, v = pos[:, -1], vel[:, -1]
    ball, goal_t, goal_o = last[:, F.BALL], last[:, F.GOAL_TARGET], last[:, F.GOAL_OWN]
    ci = np.asarray(batch["carrier_index"]).astype(int)
    rows = np.arange(B)
    has_c = ci >= 0
    carrier = np.where(has_c[:, None], last[rows, np.maximum(ci, 0)], ball)
    poss, opp = last[:, :F.N_TEAM], last[:, F.N_TEAM:F.BALL]
    pm, om = mask[:, :F.N_TEAM], mask[:, F.N_TEAM:F.BALL]
    pa = np.where(pm, 1.0, np.nan)[..., None]
    oa = np.where(om, 1.0, np.nan)[..., None]

    def d(a, b):  # (B,) or (B,n) Euclidean distance
        return np.linalg.norm(a - b, axis=-1)

    def nearest(pts, m, ref, n):
        dist = np.where(m, d(pts, ref[:, None]), _FAR)
        return np.sort(dist, axis=1)[:, :n]

    def nanmean(x, axis=1):
        with np.errstate(all="ignore"):
            return np.nan_to_num(np.nanmean(x, axis=axis), nan=0.0)

    def nanstd(x, axis=1):
        with np.errstate(all="ignore"):
            return np.nan_to_num(np.nanstd(x, axis=axis), nan=0.0)

    gp = d(poss, goal_t[:, None]) * pa[..., 0]  # nan where absent
    go = d(opp, goal_t[:, None]) * oa[..., 0]
    c_goal = d(carrier, goal_t)
    p_to_c = np.where(pm, d(poss, carrier[:, None]), _FAR)
    o_to_c = np.where(om, d(opp, carrier[:, None]), _FAR)
    spread_p = _pair_dist(poss, poss) * (pm[:, :, None] & pm[:, None])
    spread_o = _pair_dist(opp, opp) * (om[:, :, None] & om[:, None])
    npm, nom = np.maximum(pm.sum(1), 1), np.maximum(om.sum(1), 1)
    allp = np.concatenate([poss, opp], axis=1)
    allm = np.concatenate([pm, om], axis=1)
    pair = _pair_dist(allp, allp)[:, np.triu_indices(2 * F.N_TEAM, 1)[0], np.triu_indices(2 * F.N_TEAM, 1)[1]]
    valid = (allm[:, :, None] & allm[:, None])[:, np.triu_indices(2 * F.N_TEAM, 1)[0], np.triu_indices(2 * F.N_TEAM, 1)[1]]
    hist = np.stack([((pair >= lo) & (pair < hi) & valid).sum(1) for lo, hi in
                     zip((0, 5, 10, 15, 20, 30, 40, 50), (5, 10, 15, 20, 30, 40, 50, 200))], axis=1)
    hist = hist / np.maximum(valid.sum(1, keepdims=True), 1)
    speed = np.linalg.norm(v, axis=-1)
    cols = [
        d(ball, goal_t)[:, None] / 50, d(ball, goal_o)[:, None] / 50, d(carrier, ball)[:, None] / 10,
        nearest(poss, pm, ball, 5) / 30, nearest(opp, om, ball, 5) / 30,
        nanmean(gp)[:, None] / 50, nanmean(go)[:, None] / 50, nanstd(gp)[:, None] / 20, nanstd(go)[:, None] / 20,
        (spread_p.sum((1, 2)) / (npm * np.maximum(npm - 1, 1)))[:, None] / 30,
        (spread_o.sum((1, 2)) / (nom * np.maximum(nom - 1, 1)))[:, None] / 30,
        (o_to_c < 5).sum(1)[:, None] / 5, (o_to_c < 10).sum(1)[:, None] / 11, (o_to_c < 15).sum(1)[:, None] / 11,
        ((p_to_c < 10) & pm).sum(1)[:, None] / 11, ((p_to_c < 20) & pm).sum(1)[:, None] / 11,
        speed[:, F.BALL][:, None] / 30, np.where(has_c, speed[rows, np.maximum(ci, 0)], 0.0)[:, None] / 8,
        nanmean(np.where(pm, speed[:, :F.N_TEAM], np.nan))[:, None] / 8,
        nanmean(np.where(om, speed[:, F.N_TEAM:F.BALL], np.nan))[:, None] / 8,
        np.sort(o_to_c, axis=1)[:, :1] / 10,
        (np.where(om, go, _FAR) < c_goal[:, None]).sum(1)[:, None] / 11,  # opponents between ball and goal
        (np.where(pm, gp, _FAR) < c_goal[:, None]).sum(1)[:, None] / 11,
        hist,
    ]
    out = np.concatenate([np.asarray(c, dtype=np.float64).reshape(B, -1) for c in cols], axis=1)
    return np.nan_to_num(out)


# ---------------------------------------------------------------- the index

@dataclass
class RetrievalIndex:
    kind: str  # "critic" | "descriptor"
    emb: np.ndarray  # (n, d) float32, standardised and L2 normalised
    mean: np.ndarray
    std: np.ndarray
    source: np.ndarray  # (n,) <U
    match_id: np.ndarray
    elapsed: np.ndarray  # (n,) f4
    outcome: np.ndarray  # (n,) <U
    model_hash: str = ""  # weights the critic vectors came from ("" for descriptors)

    def __len__(self) -> int:
        return int(self.emb.shape[0])

    @classmethod
    def from_raw(cls, kind: str, raw: np.ndarray, source, match_id, elapsed, outcome, model_hash: str = ""
                 ) -> "RetrievalIndex":
        raw = np.asarray(raw, dtype=np.float64)
        mean = raw.mean(0)
        std = np.maximum(raw.std(0), 1e-6)
        idx = cls(kind, np.zeros((0, raw.shape[1]), np.float32), mean.astype(np.float32), std.astype(np.float32),
                  np.asarray(source, "<U32"), np.asarray(match_id, "<U32"), np.asarray(elapsed, np.float32),
                  np.asarray(outcome, "<U12"), model_hash)
        idx.emb = idx.normalise(raw)
        return idx

    def normalise(self, raw: np.ndarray) -> np.ndarray:
        x = (np.atleast_2d(np.asarray(raw, dtype=np.float64)) - self.mean) / self.std
        return (x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-9)).astype(np.float32)

    def search(self, raw: np.ndarray, k: int = 5, exclude: tuple[str, str] | None = None) -> list[RetrievedMoment]:
        """Cosine top-``k`` for one raw (unstandardised) vector; one hit per situation (see ``DEDUP_SECONDS``).
        ``exclude`` = ``(source, match_id)`` drops that match (e.g. the query's own)."""
        if len(self) == 0 or k <= 0:
            return []
        q = self.normalise(raw)[0]
        sims = self.emb @ q
        if exclude is not None:
            sims = np.where((self.source == exclude[0]) & (self.match_id == exclude[1]), -np.inf, sims)
        take = min(len(self), max(8 * k, 32))
        order = np.argpartition(-sims, take - 1)[:take]
        order = order[np.argsort(-sims[order])]
        out: list[RetrievedMoment] = []
        chosen: list[int] = []
        for i in order:
            if not np.isfinite(sims[i]):
                continue
            if any(self.source[i] == self.source[j] and self.match_id[i] == self.match_id[j]
                   and abs(float(self.elapsed[i] - self.elapsed[j])) < DEDUP_SECONDS for j in chosen):
                continue
            chosen.append(int(i))
            out.append(RetrievedMoment(source=str(self.source[i]), match_label=f"{self.source[i]}:{self.match_id[i]}",
                                       elapsed=float(self.elapsed[i]), similarity=round(float(sims[i]), 4),
                                       outcome=str(self.outcome[i])))
            if len(out) == k:
                break
        return out

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp.npz")
        np.savez_compressed(tmp, __format__=np.str_(INDEX_FORMAT), kind=np.str_(self.kind), emb=self.emb,
                            mean=self.mean, std=self.std, source=self.source, match_id=self.match_id,
                            elapsed=self.elapsed, outcome=self.outcome, model_hash=np.str_(self.model_hash))
        tmp.replace(path)


def load_index(path: Path | str) -> RetrievalIndex | None:
    """The saved index, or ``None`` when it is missing or unreadable."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as z:
            if str(z["__format__"]) != INDEX_FORMAT:
                return None
            return RetrievalIndex(str(z["kind"]), z["emb"], z["mean"], z["std"], z["source"], z["match_id"],
                                  z["elapsed"], z["outcome"], str(z["model_hash"]))
    except Exception as exc:  # corrupt or truncated file: retrieval is optional
        log.warning("retrieval index %s unreadable: %s", path, exc)
        return None


# ---------------------------------------------------------------- building

def build_index(model: Any | None = None, sources: Iterable[str] | None = None, split: str | None = None,
                root: Path | None = None, max_samples: int | None = None,
                progress: Callable[[str], None] | None = None) -> RetrievalIndex:
    """Index the shards. With a ``LearnedModel`` the vectors are critic embeddings, else descriptors.

    Shards are read with ``train.schema.iter_shards`` (numpy only). Critic mode skips shards whose node
    feature width differs from the model's (a different profile-key set).
    """
    from matchlens.tacticsml.train.schema import iter_shards

    raws, src, mid, ela, out = [], [], [], [], []
    total = 0
    for shard in iter_shards(list(sources) if sources is not None else None, split, root, with_edges=model is not None):
        if model is not None and shard["node_feat"].shape[-1] != model.n_node_feat:
            if progress:
                progress(f"skip {shard['source'][0]}/{shard['match_id'][0]}: node_feat width differs from the model")
            continue
        n = shard["pos"].shape[0]
        if max_samples is not None and total + n > max_samples:
            n = max_samples - total
            if n <= 0:
                break
            shard = {k: (v[:n] if getattr(v, "ndim", 0) >= 1 and v.shape[0] >= n and k != "profile_keys" else v)
                     for k, v in shard.items()}
        raws.append(model.embed_batch(shard) if model is not None else descriptor(shard))
        src.append(shard["source"][:n])
        mid.append(shard["match_id"][:n])
        ela.append(shard["elapsed"][:n])
        out.append([outcome_label(int(a), int(b), int(c), int(e)) for a, b, c, e in
                    zip(shard["shot10"][:n], shard["goal10"][:n], shard["turn5"][:n], shard["concede10"][:n])])
        total += n
        if progress:
            progress(f"indexed {total} moments")
    if not raws:
        return RetrievalIndex("critic" if model is not None else "descriptor", np.zeros((0, 1), np.float32),
                              np.zeros(1, np.float32), np.ones(1, np.float32), np.zeros(0, "<U32"),
                              np.zeros(0, "<U32"), np.zeros(0, np.float32), np.zeros(0, "<U12"))
    return RetrievalIndex.from_raw("critic" if model is not None else "descriptor", np.concatenate(raws),
                                   np.concatenate(src), np.concatenate(mid), np.concatenate(ela),
                                   np.concatenate([np.asarray(o, "<U12") for o in out]),
                                   model.weights_hash if model is not None else "")
