"""Next10 training data: shards (with future labels) -> an in-memory dataset with the Next10 labels.

Reads the shards the sources write when built with ``--root backend/data/local/tacticsml/next10/shards``
(``fut_pos``, ``fut_valid``, ``since_dead``, ``poss10``, ``action_next``, ``poss_team``, ``opp_team``) and, for the
single-frame StatsBomb 360 source that has no future, the older shards (new labels filled with "unknown").

Labels derived here
-------------------
* ``zone_cell``  flat 16 x 12 cell of the ball 10 s later (``-1`` when the tracking does not cover it);
* ``open_play``  bool. Tracking sources: no set-piece flag and the ball has been alive for at least
  ``OPEN_AFTER_DEAD`` seconds. Flagged sources (StatsBomb 360): flag is "open play". Everything else is excluded
  from Next10 training and from the open-play tables (the "all" tables keep them);
* ``team_ids``   (B, 2) FiLM ids of [possession team, opponent] from the ``TeamTable`` (0 = unknown).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from matchlens.tacticsml import features as F
from matchlens.tacticsml import next10_common as C
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.schema import edge_feat_from

OPEN_AFTER_DEAD = 10.0
TRACKING_SOURCES = ("sportec", "skillcorner", "metrica")
LEGACY_SOURCES = ("statsbomb360",)
KEEP = ("pos", "vel", "vel_mask", "node_feat", "node_mask", "carrier_index", "receiver_idx", "shot10", "goal10",
        "concede10", "turn5", "poss10", "action_next", "set_piece", "kind", "elapsed", "source", "match_id", "split",
        "poss_team", "opp_team", "since_dead", "fut_pos", "fut_valid", "period")
LABELS10 = ("receiver_idx", "shot10", "goal10", "concede10", "turn5", "poss10", "action_next", "zone_cell")


@dataclass
class Dataset10:
    a: dict[str, np.ndarray]
    table: C.TeamTable

    def __len__(self) -> int:
        return int(self.a["pos"].shape[0])

    def subset(self, sel: np.ndarray) -> "Dataset10":
        return Dataset10({k: v[sel] for k, v in self.a.items()}, self.table)

    def where(self, **eq) -> np.ndarray:
        m = np.ones(len(self), dtype=bool)
        for k, v in eq.items():
            m &= np.isin(self.a[k], v if isinstance(v, (list, tuple, set)) else [v])
        return m

    def batch(self, idx: np.ndarray, edges: bool = True) -> dict[str, np.ndarray]:
        b = {k: v[idx] for k, v in self.a.items()}
        for k in ("pos", "vel", "node_feat", "node_mask", "vel_mask"):
            b[k] = b[k].astype(np.float32)
        if edges:
            b["edge_feat"] = edge_feat_from(b["pos"][:, -1], b["node_mask"], dtype=np.float32)
        return b

    def source_names(self) -> list[str]:
        return sorted({str(s) for s in np.unique(self.a["source"])})


def _fill(d: dict[str, np.ndarray], n: int) -> dict[str, np.ndarray]:
    """Defaults for shards written before the Next10 labels existed."""
    d.setdefault("poss10", np.full(n, -1, np.int8))
    d.setdefault("action_next", np.full(n, -1, np.int8))
    d.setdefault("since_dead", np.full(n, np.nan, np.float32))
    d.setdefault("poss_team", np.full(n, "", "<U48"))
    d.setdefault("opp_team", np.full(n, "", "<U48"))
    d.setdefault("period", np.ones(n, np.int8))
    d.setdefault("fut_pos", np.zeros((n, schema.HORIZONS, F.N_NODES, 2), np.float16))
    d.setdefault("fut_valid", np.zeros((n, schema.HORIZONS, F.N_NODES), bool))
    return d


def _read(root: Path, sources: Sequence[str], split: str) -> list[dict[str, np.ndarray]]:
    parts = []
    index = schema.ShardIndex.load(root)
    for entry in index.select(sources, split):
        d = schema.read_shard(root / entry.path, with_edges=False)
        n = d["pos"].shape[0]
        parts.append({k: v for k, v in _fill(d, n).items() if k in KEEP})
    return parts


def _open_play(d: dict[str, np.ndarray]) -> np.ndarray:
    sp = d["set_piece"].astype(int)
    flagged_open = sp == 0
    unknown = sp == schema.SET_PIECE_UNKNOWN
    sd = d["since_dead"]
    tracked = ~np.isnan(sd)
    return np.where(tracked, (sd >= OPEN_AFTER_DEAD) & (flagged_open | unknown), flagged_open)


def load_split(split: str, sources: Sequence[str] = TRACKING_SOURCES + LEGACY_SOURCES, root: Path | None = None,
               legacy_root: Path | None = None) -> dict[str, np.ndarray] | None:
    root = Path(root or schema.NEXT10_SHARDS)
    legacy_root = Path(legacy_root) if legacy_root else None
    parts = _read(root, [s for s in sources if s not in LEGACY_SOURCES], split)
    if legacy_root is not None:
        parts += _read(legacy_root, [s for s in sources if s in LEGACY_SOURCES], split)
    if not parts:
        return None
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    out["open_play"] = _open_play(out)
    ball10 = out["fut_pos"][:, -1, F.BALL].astype(np.float32)
    out["zone_cell"] = np.where(out["fut_valid"][:, -1, F.BALL], C.cell_of(ball10), -1).astype(np.int64)
    for k in ("receiver_idx", "carrier_index"):
        out[k] = out[k].astype(np.int64)
    return out


def load_next10(sources: Sequence[str] = TRACKING_SOURCES + LEGACY_SOURCES, root: Path | None = None,
                legacy_root: Path | None = None, table: C.TeamTable | None = None, min_team_count: int = 1000
                ) -> dict[str, Dataset10]:
    """``{"train": ..., "val": ..., "test": ...}``; the team table is built from the train split unless given."""
    raw = {s: load_split(s, sources, root, legacy_root) for s in schema.SPLITS}
    if raw["train"] is None:
        raise schema.DataUnavailable(f"no Next10 train shards under {root or schema.NEXT10_SHARDS}; build them with "
                                     "python -m matchlens.tacticsml.train shards --root <next10/shards>")
    if table is None:
        names = np.concatenate([raw["train"]["poss_team"], raw["train"]["opp_team"]])
        keys, counts = np.unique([C.normalise_team(n) for n in names], return_counts=True)
        table = C.build_team_table(list(keys), counts=dict(zip(keys, counts)), min_count=min_team_count)
    out: dict[str, Dataset10] = {}
    for s, d in raw.items():
        if d is None:
            continue
        d["team_ids"] = C.team_ids_for(d["poss_team"], d["opp_team"], table)
        out[s] = Dataset10(d, table)
    return out


def to_tensors10(batch: dict, device=None):
    """numpy batch (``Dataset10.batch``) -> torch dict for ``Next10GAT`` and ``heads.next10_losses``."""
    import torch

    from matchlens.tacticsml.train.model import to_tensors

    out = to_tensors({k: batch[k] for k in batch if k in ("pos", "vel", "vel_mask", "node_feat", "edge_feat",
                                                          "node_mask", "receiver_idx", "shot10", "goal10",
                                                          "concede10", "turn5")}, device)
    for k in ("poss10", "action_next", "zone_cell", "team_ids", "carrier_index"):
        if k in batch:
            out[k] = torch.as_tensor(np.asarray(batch[k]), dtype=torch.long, device=device)
    if "fut_pos" in batch:
        out["fut_pos"] = torch.as_tensor(np.asarray(batch["fut_pos"], dtype=np.float32), device=device)
        out["fut_valid"] = torch.as_tensor(np.asarray(batch["fut_valid"]), device=device)
    return out
