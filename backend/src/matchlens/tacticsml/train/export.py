"""Export torch checkpoints to numpy weights: ``python -m matchlens.tacticsml.train export``.

TRAINING ONLY (imports torch). Reads ``backend/data/local/tacticsml/ckpt/`` (``gat_seed{S}.pt``,
``ensemble.json``, ``cvae.pt``) and writes ``backend/data/local/tacticsml/weights/``:

* ``gat_seed{S}.npz``  every ``state_dict`` tensor (float32) plus ``__config__`` / ``__profile_keys__`` /
  ``__format__`` / ``__seed__``
* ``cvae.npz``         the cVAE-only tensors plus ``__config__`` / ``__encoder_member__``
* ``manifest.json``    version, seeds, feature config, sha256 of each npz and the metrics of the ensemble
* ``retrieval.npz``    optional similar-moment index over the shards (``--sources``; see ``retrieval``)

``tacticsml.learned`` loads this directory in numpy (no torch) and checks the hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Sequence

import numpy as np
import torch

from matchlens.tacticsml import features as F
from matchlens.tacticsml.infer_numpy import (
    CVAE_FORMAT, DISP_SCALE, GAT_FORMAT, LOST, NEG, POS_SCALE, REL_SCALE, VEL_SCALE,
)
from matchlens.tacticsml.train.fit import CKPT_DIR

WEIGHTS_DIR = CKPT_DIR.parent / "weights"
MANIFEST_FORMAT = "tacticsml-weights-v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def combined_hash(hashes: Sequence[str]) -> str:
    """Identity of a weight set: hash of the member and cVAE file hashes (order independent)."""
    return hashlib.sha256("|".join(sorted(hashes)).encode()).hexdigest()


def _arrays(state_dict: dict) -> dict[str, np.ndarray]:
    return {k: v.detach().cpu().float().contiguous().numpy() for k, v in state_dict.items()}


def write_member(blob: dict, path: Path) -> str:
    """One ``gat_seed{S}.pt`` blob (as written by ``fit.save_member``) -> npz. Returns its sha256."""
    if blob.get("format") != GAT_FORMAT:
        raise ValueError(f"unexpected checkpoint format {blob.get('format')!r} (want {GAT_FORMAT})")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **_arrays(blob["state_dict"]), __format__=np.str_(GAT_FORMAT),
             __config__=np.str_(json.dumps(blob["config"])),
             __profile_keys__=np.array([str(k) for k in blob.get("profile_keys", [])], dtype="<U64"),
             __seed__=np.int64(blob.get("seed", 0)))
    return sha256_file(path)


def write_cvae(blob: dict, path: Path, encoder_member: str) -> str:
    """``cvae.pt`` blob -> npz (``encoder_member`` is the exported file name of the frozen encoder)."""
    if blob.get("format") != CVAE_FORMAT:
        raise ValueError(f"unexpected checkpoint format {blob.get('format')!r} (want {CVAE_FORMAT})")
    sd = {k: v for k, v in blob["state_dict"].items() if not k.startswith("encoder.")}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **_arrays(sd), __format__=np.str_(CVAE_FORMAT), __config__=np.str_(json.dumps(blob["config"])),
             __encoder_member__=np.str_(encoder_member))
    return sha256_file(path)


def feature_config(model_cfg: dict, profile_keys: Sequence[str]) -> dict:
    """Everything the numpy side must agree on with ``features`` / ``model``."""
    return {"t_steps": model_cfg.get("steps", F.T_STEPS), "n_nodes": F.N_NODES, "n_edge": F.N_EDGE,
            "n_node_feat": model_cfg.get("n_node_feat", F.F_BASE), "profile_keys": [str(k) for k in profile_keys],
            "n_slots": F.N_TEAM, "lost_index": LOST, "pos_scale": POS_SCALE, "vel_scale": VEL_SCALE,
            "rel_scale": REL_SCALE, "neg": NEG, "disp_scale": DISP_SCALE, "d2": list(F.D2)}


def export(ckpt_dir: Path | None = None, out_dir: Path | None = None, version: str = GAT_FORMAT,
           log=print) -> dict:
    """Export every ensemble member (and the cVAE when present); returns the manifest."""
    ckpt_dir, out_dir = Path(ckpt_dir or CKPT_DIR), Path(out_dir or WEIGHTS_DIR)
    info_path = ckpt_dir / "ensemble.json"
    info = json.loads(info_path.read_text()) if info_path.exists() else {}
    files = [m["file"] for m in info.get("members", [])] or sorted(p.name for p in ckpt_dir.glob("gat_seed*.pt"))
    if not files:
        raise SystemExit(f"no gat_seed*.pt checkpoints under {ckpt_dir}; train first: "
                         "uv run --group tactics-ml python -m matchlens.tacticsml.train fit")
    members, cfg, keys = [], None, []
    for name in files:
        blob = torch.load(ckpt_dir / name, map_location="cpu", weights_only=False)
        out = out_dir / Path(name).with_suffix(".npz").name
        digest = write_member(blob, out)
        if cfg is not None and blob["config"] != cfg:
            raise SystemExit(f"{name}: model config differs from the first member")
        cfg, keys = blob["config"], blob.get("profile_keys", [])
        members.append({"file": out.name, "seed": int(blob.get("seed", 0)), "sha256": digest})
        log(f"exported {out.name} ({out.stat().st_size / 1e6:.2f} MB)")
    cvae = None
    if (ckpt_dir / "cvae.pt").exists():
        blob = torch.load(ckpt_dir / "cvae.pt", map_location="cpu", weights_only=False)
        enc = Path(blob.get("encoder_member", files[0])).with_suffix(".npz").name
        if enc not in {m["file"] for m in members}:
            enc = members[0]["file"]
        digest = write_cvae(blob, out_dir / "cvae.npz", enc)
        cvae = {"file": "cvae.npz", "sha256": digest, "encoder_member": enc, "config": blob["config"]}
        log("exported cvae.npz")
    manifest = {
        "format": MANIFEST_FORMAT, "version": version, "seeds": [m["seed"] for m in members], "members": members,
        "cvae": cvae, "model_config": cfg, "feature": feature_config(cfg, keys),
        "hash": combined_hash([m["sha256"] for m in members] + ([cvae["sha256"]] if cvae else [])),
        "metrics": info.get("ensemble_val", {}),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=float))
    log(f"manifest {out_dir / 'manifest.json'} hash {manifest['hash'][:8]}")
    return manifest


def build_retrieval(out_dir: Path, sources: Sequence[str] | None, split: str | None, descriptor: bool,
                    root: Path | None, log=print) -> int:
    from matchlens.tacticsml.learned import LearnedModel
    from matchlens.tacticsml.retrieval import INDEX_FILE, build_index

    model = None if descriptor else LearnedModel.load(out_dir)
    index = build_index(model, sources, split, root, progress=lambda m: log(m))
    if len(index) == 0:
        log("no shards found: retrieval index not written")
        return 0
    index.save(out_dir / INDEX_FILE)
    log(f"retrieval index {out_dir / INDEX_FILE}: {len(index)} moments ({index.kind})")
    return len(index)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train export", description=__doc__)
    p.add_argument("--ckpt-dir", default=str(CKPT_DIR))
    p.add_argument("--out-dir", default=str(WEIGHTS_DIR))
    p.add_argument("--version", default=GAT_FORMAT)
    p.add_argument("--sources", nargs="+", default=None, help="shard sources for the retrieval index (default all)")
    p.add_argument("--index-split", default=None, choices=("train", "val", "test"))
    p.add_argument("--descriptor-index", action="store_true", help="index handcrafted descriptors, not the critic")
    p.add_argument("--no-index", action="store_true", help="skip the retrieval index")
    args = p.parse_args(argv)
    export(Path(args.ckpt_dir), Path(args.out_dir), args.version)
    if not args.no_index:
        build_retrieval(Path(args.out_dir), args.sources, args.index_split, args.descriptor_index, None)
    return 0
