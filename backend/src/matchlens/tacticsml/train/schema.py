"""Shard format of the tactical-model training data.

One ``.npz`` per source and match under ``backend/data/local/tacticsml/shards/<source>/<match_id>.npz``,
plus a ``manifest.json`` (``ShardIndex``) listing every shard. A shard holds ``B`` samples as stacked
``features.GraphSample`` arrays and their labels:

=============  =============  =====================================================================
key            shape / dtype  meaning
=============  =============  =====================================================================
pos, vel       (B,5,25,2) f4  centred canonical metres (possession team attacks +x), m/s
vel_mask       (B,25) f4      1 where the last-step velocity is known
node_feat      (B,25,F) f4    see ``features`` (observed flag in column 6; 0 for unseen 360 players)
node_mask      (B,25) f4      1 for real nodes (players present, ball, goals)
node_ids       (B,25) <U      player ids, "ball", "goal_own", "goal_target", "pad"
carrier_index  (B,) i8        node index of the ball carrier, -1 if none
receiver_idx   (B,) i8        0..10 receiver (node index), 11 lost / no receiver, -1 unknown
shot10 goal10  (B,) i1        possession team shoots / scores / concedes within 10 s (-1 unknown)
concede10
turn5          (B,) i1        possession lost within 5 s (-1 unknown)
kind           (B,) i1        KIND_OPEN (random open-play frame), KIND_PASS or KIND_CARRY start
set_piece      (B,) i1        0 open play, 1 corner, 2 free kick, 3 throw-in, 4 goal kick, -1 unknown
                              (known for synthetic and statsbomb360 only)
elapsed        (B,) f4        match seconds of the sample (source clock)
target_pos     (B,25,2) f4    canonical positions about ``TARGET_DT`` s later (same node order)
target_valid   (B,) bool      whether ``target_pos`` is real (False for single-frame sources)
profile_keys   (P,) <U        profile feature names (``F = 7 + 2 * P``)
source,        0-d <U         provenance (``read_shards`` expands them to (B,) arrays)
match_id, split
=============  =============  =====================================================================

Next10 shards (``NEXT10_SHARDS``, tracking sources only) add, all canonical like ``pos``:

=============  =============  =====================================================================
fut_pos        (B,10,25,2) f2 node positions 1..10 s later (0 where not ``fut_valid``)
fut_valid      (B,10,25) bool whether the node is tracked at that horizon
since_dead     (B,) f4        seconds since the last dead-ball frame (NaN unknown); open play >= 10 s
poss10         (B,) i1        possession team has the ball 10 s later (-1 unknown)
action_next    (B,) i1        next on-ball action 0 pass, 1 carry, 2 shot, 3 other (-1 unless the source has carries)
poss_team,     (B,) <U48      club names of the possession team and its opponent (FiLM team ids)
opp_team
period         (B,) i1        match period of the sample
=============  =============  =====================================================================

``edge_feat`` and ``poss_slots`` / ``opp_slots`` are functions of the stored arrays and are not stored;
``read_shards(..., with_edges=True)`` rebuilds ``edge_feat`` (float16, distance / 10 is all that varies).

Splits are deterministic per match (a hash of ``source:match_id``: 80 / 10 / 10 %). Held-out test sets
override the hash: PFF knockout matches, ``HELD_OUT_SPORTEC`` (2 DFL matches) and Euro 2024 (StatsBomb 360).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml.features import GraphSample

KIND_OPEN, KIND_PASS, KIND_CARRY = 0, 1, 2
SET_PIECE_CODES = {None: 0, "corner": 1, "free_kick": 2, "throw_in": 3, "goal_kick": 4}
SET_PIECE_UNKNOWN = -1
TARGET_DT = 3.0  # seconds ahead of the sample for ``target_pos``
SPLITS = ("train", "val", "test")
LABEL_KEYS = ("receiver_idx", "shot10", "goal10", "concede10", "turn5")
# Two of the seven IDSSE (DFL) matches kept out of training as an unseen-league test set.
HELD_OUT_SPORTEC = frozenset({"J03WMX", "J03WR9"})

ROOT = Path(__file__).resolve().parents[4] / "data" / "local" / "tacticsml"
SHARDS = ROOT / "shards"
# Raw downloads. ``MATCHLENS_TACTICSML_RAW`` points the builders at another copy (e.g. a sibling worktree's
# ``raw/``) without moving 3 GB; the sources only read it.
RAW_ROOT = Path(os.environ.get("MATCHLENS_TACTICSML_RAW", "") or ROOT / "raw")
# Next10 artefacts (shards with future labels, checkpoints, weights, reports) never mix with the above.
NEXT10 = ROOT / "next10"
NEXT10_SHARDS = NEXT10 / "shards"
HORIZONS = 10  # seconds of future stored per sample for the Next10 labels (1 Hz)
MANIFEST = "manifest.json"


class DataUnavailable(RuntimeError):
    """A data source cannot be read (not downloaded, or needs a manual step); the message says what to do."""


def shards_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else SHARDS


def assign_split(source: str, match_id: str, override: str | None = None) -> str:
    """Deterministic split of a match: ``override`` (a held-out set) wins, else a hash of the id."""
    if override is not None:
        if override not in SPLITS:
            raise ValueError(f"unknown split {override!r}")
        return override
    if source == "sportec" and match_id in HELD_OUT_SPORTEC:
        return "test"
    bucket = int.from_bytes(hashlib.blake2b(f"{source}:{match_id}".encode(), digest_size=4).digest(), "big") % 100
    return "train" if bucket < 80 else "val" if bucket < 90 else "test"


# ---------------------------------------------------------------- manifest

@dataclass
class ShardEntry:
    source: str
    match_id: str
    split: str
    path: str  # relative to the shards root
    n: int
    rates: dict[str, float] = field(default_factory=dict)  # label base rates over known labels
    note: str = ""


@dataclass
class ShardIndex:
    shards: list[ShardEntry] = field(default_factory=list)
    profile_keys: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path | None = None) -> "ShardIndex":
        path = shards_root(root) / MANIFEST
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(shards=[ShardEntry(**s) for s in raw.get("shards", [])], profile_keys=raw.get("profile_keys", []))

    def save(self, root: Path | None = None) -> None:
        path = shards_root(root) / MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"profile_keys": self.profile_keys, "shards": [asdict(s) for s in self.shards]},
                                  indent=1))
        tmp.replace(path)

    def upsert(self, entry: ShardEntry) -> None:
        self.shards = [s for s in self.shards if (s.source, s.match_id) != (entry.source, entry.match_id)]
        self.shards.append(entry)
        self.shards.sort(key=lambda s: (s.source, s.match_id))

    def select(self, sources: Iterable[str] | None = None, split: str | None = None) -> list[ShardEntry]:
        wanted = None if sources is None else set(sources)
        return [s for s in self.shards if (wanted is None or s.source in wanted) and (split is None or s.split == split)]

    def counts(self) -> dict[str, dict[str, int]]:
        """samples per source and split"""
        out: dict[str, dict[str, int]] = {}
        for s in self.shards:
            out.setdefault(s.source, {k: 0 for k in SPLITS})[s.split] += s.n
        return out


# ---------------------------------------------------------------- arrays

def edge_feat_from(pos_last: np.ndarray, node_mask: np.ndarray, dtype=np.float16) -> np.ndarray:
    """Vectorised ``features._edges`` for a batch: pos_last (B,N,2), node_mask (B,N) -> (B,N,N,5)."""
    B, n, _ = pos_last.shape
    idx = np.arange(n)
    is_player = idx < F.BALL
    team = np.where(idx < F.N_TEAM, 0, 1)
    both = is_player[:, None] & is_player[None, :]
    goal = idx >= F.GOAL_OWN
    ball = idx == F.BALL
    e = np.zeros((B, n, n, F.N_EDGE), dtype=np.float32)
    e[..., F.E_SAME_TEAM] = (both & (team[:, None] == team[None, :]))[None]
    e[..., F.E_OPPONENT] = (both & (team[:, None] != team[None, :]))[None]
    e[..., F.E_INVOLVES_BALL] = (ball[:, None] | ball[None, :])[None]
    e[..., F.E_INVOLVES_GOAL] = (goal[:, None] | goal[None, :])[None]
    e[..., F.E_DISTANCE] = np.linalg.norm(pos_last[:, :, None] - pos_last[:, None], axis=-1) / 10.0
    e *= (node_mask[:, :, None] * node_mask[:, None, :])[..., None]
    return e.astype(dtype)


def target_positions(samples: list[GraphSample], futures: list[dict[str, tuple[float, float]] | None]
                     ) -> tuple[np.ndarray, np.ndarray]:
    """(B,N,2) canonical future positions per node (current position where unknown) and a validity flag.

    ``futures[i]`` maps a player id (and "ball") to its canonical position ``TARGET_DT`` s later.
    """
    N = samples[0].n_nodes
    tp = np.zeros((len(samples), N, 2), dtype=np.float32)
    valid = np.zeros(len(samples), dtype=bool)
    for i, (s, fut) in enumerate(zip(samples, futures)):
        tp[i] = s.pos[-1]
        if not fut:
            continue
        hit = 0
        for j, nid in enumerate(s.node_ids[: F.GOAL_OWN]):
            if nid in fut:
                tp[i, j] = fut[nid]
                hit += 1
        valid[i] = hit >= 12
    return tp, valid


def pack(samples: list[GraphSample], receiver_idx: list[int], labels: dict[str, list[int]], kind: list[int],
         target_pos: np.ndarray, target_valid: np.ndarray, set_piece: list[int] | None = None,
         extra: dict[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    """GraphSamples and labels -> the arrays of a shard (without provenance)."""
    st = F.stack(samples)
    elapsed = np.array([s.elapsed for s in samples], dtype=np.float32)
    keys = np.array(samples[0].profile_keys, dtype="<U32")
    out: dict[str, np.ndarray] = {
        "pos": st["pos"], "vel": st["vel"], "vel_mask": st["vel_mask"], "node_feat": st["node_feat"],
        "node_mask": st["node_mask"], "node_ids": st["node_ids"].astype("<U24"),
        "carrier_index": st["carrier_index"].astype(np.int64),
        "receiver_idx": np.array(receiver_idx, dtype=np.int64),
        "kind": np.array(kind, dtype=np.int8), "elapsed": elapsed,
        "set_piece": np.array(set_piece if set_piece is not None else [SET_PIECE_UNKNOWN] * len(samples),
                              dtype=np.int8),
        "target_pos": target_pos.astype(np.float32), "target_valid": target_valid.astype(bool),
        "profile_keys": keys,
    }
    for k in LABEL_KEYS[1:]:
        out[k] = np.array(labels[k], dtype=np.int8)
    out.update(extra or {})
    return out


def _rates(arrays: dict[str, np.ndarray]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in ("shot10", "goal10", "concede10", "turn5"):
        v = arrays[k]
        known = v >= 0
        out[k] = round(float(v[known].mean()), 5) if known.any() else float("nan")
    r = arrays["receiver_idx"]
    out["receiver_known"] = round(float((r >= 0).mean()), 5)
    out["receiver_lost"] = round(float((r == 11).sum() / max((r >= 0).sum(), 1)), 5)
    return out


def write_shard(source: str, match_id: str, arrays: dict[str, np.ndarray], split: str | None = None,
                root: Path | None = None, note: str = "", update_manifest: bool = True) -> ShardEntry:
    """Write one match's arrays (from ``pack``) and register it in the manifest."""
    split = assign_split(source, match_id, split)
    base = shards_root(root)
    rel = f"{source}/{match_id}.npz"
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez_compressed(tmp, source=np.str_(source), match_id=np.str_(match_id), split=np.str_(split), **arrays)
    tmp.replace(path)
    entry = ShardEntry(source=source, match_id=match_id, split=split, path=rel, n=int(arrays["pos"].shape[0]),
                       rates=_rates(arrays), note=note)
    if update_manifest:
        index = ShardIndex.load(base)
        index.profile_keys = [str(k) for k in arrays["profile_keys"]]
        index.upsert(entry)
        index.save(base)
    return entry


def read_shard(path: Path | str, with_edges: bool = True) -> dict[str, np.ndarray]:
    """One shard as a batch dict in ``features.stack`` layout plus labels (see the module docstring)."""
    with np.load(path, allow_pickle=False) as z:
        d = {k: z[k] for k in z.files}
    n = d["pos"].shape[0]
    for k in ("source", "match_id", "split"):
        d[k] = np.full(n, str(d[k]))
    d.setdefault("set_piece", np.full(n, SET_PIECE_UNKNOWN, dtype=np.int8))  # shards written before the flag
    d["poss_slots"] = np.tile(np.arange(F.N_TEAM), (n, 1))
    d["opp_slots"] = np.tile(np.arange(F.N_TEAM, 2 * F.N_TEAM), (n, 1))
    if with_edges:
        d["edge_feat"] = edge_feat_from(d["pos"][:, -1], d["node_mask"])
    return d


def iter_shards(sources: Iterable[str] | None = None, split: str | None = None, root: Path | None = None,
                with_edges: bool = True) -> Iterator[dict[str, np.ndarray]]:
    """Shards of the given sources / split, one batch dict each (deterministic order)."""
    base = shards_root(root)
    for entry in ShardIndex.load(base).select(sources, split):
        yield read_shard(base / entry.path, with_edges=with_edges)


def read_shards(sources: Iterable[str] | None = None, split: str | None = None, root: Path | None = None,
                with_edges: bool = True) -> dict[str, np.ndarray]:
    """All samples of the given sources / split concatenated into one batch dict.

    ``profile_keys`` is returned once (shape (P,)); every other array has the sample count as its first
    axis. Raises ``DataUnavailable`` when nothing matches.
    """
    parts = list(iter_shards(sources, split, root, with_edges))
    if not parts:
        raise DataUnavailable(f"no shards for sources={sources!r} split={split!r} under {shards_root(root)}; "
                              "build them with: uv run --group tactics-ml python -m matchlens.tacticsml.train shards")
    keys = parts[0]["profile_keys"]
    if any(not np.array_equal(p["profile_keys"], keys) for p in parts):
        raise ValueError("shards disagree on profile_keys")
    width = max(p["node_feat"].shape[2] for p in parts)
    out: dict[str, np.ndarray] = {}
    for k in parts[0]:
        if k == "profile_keys":
            out[k] = keys
            continue
        arrs = [p[k] for p in parts]
        if k == "node_feat" and any(a.shape[2] != width for a in arrs):
            arrs = [np.pad(a, ((0, 0), (0, 0), (0, width - a.shape[2]))) for a in arrs]
        out[k] = np.concatenate(arrs)
    return out
