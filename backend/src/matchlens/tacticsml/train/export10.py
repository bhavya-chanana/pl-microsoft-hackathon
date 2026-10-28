"""Export Next10 checkpoints to numpy weights: ``python -m matchlens.tacticsml.train.export10``.

Reads ``backend/data/local/tacticsml/next10/ckpt/`` (``next10_seed*.pt``, ``trajectory_seed0.pt``) and writes
``backend/data/local/tacticsml/next10/weights/``: ``critic_seed{S}.npz``, ``trajectory.npz`` and ``manifest.json``
(sha256 of every file; ``infer_next10`` refuses a set whose hashes differ). Never touches ``weights/`` of the
deployed Tactics Board model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from matchlens.tacticsml import next10_common as C
from matchlens.tacticsml.infer_next10 import NEXT10_FORMAT, TRAJ_FORMAT
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.next10 import FORMAT as CKPT_FORMAT
from matchlens.tacticsml.train.trajectory import FORMAT as TRAJ_CKPT_FORMAT

CKPT10 = schema.NEXT10 / "ckpt"
WEIGHTS10 = schema.NEXT10 / "weights"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arrays(sd: dict) -> dict[str, np.ndarray]:
    return {k: v.detach().cpu().float().contiguous().numpy() for k, v in sd.items()}


def write_critic(blob: dict, path: Path) -> str:
    if blob.get("format") != CKPT_FORMAT:
        raise ValueError(f"unexpected checkpoint format {blob.get('format')!r} (want {CKPT_FORMAT})")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **_arrays(blob["state_dict"]), team_names=np.array(list(blob["teams"]["team_names"]), dtype="<U64"),
             __format__=np.str_(NEXT10_FORMAT), __config__=np.str_(json.dumps(blob["config"])),
             __seed__=np.int64(blob.get("seed", 0)))
    return _sha(path)


def write_trajectory(blob: dict, path: Path) -> str:
    if blob.get("format") != TRAJ_CKPT_FORMAT:
        raise ValueError(f"unexpected checkpoint format {blob.get('format')!r} (want {TRAJ_CKPT_FORMAT})")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **_arrays(blob["state_dict"]), __format__=np.str_(TRAJ_FORMAT),
             __config__=np.str_(json.dumps(blob["config"])))
    return _sha(path)


def export(ckpt_dir: Path | None = None, out_dir: Path | None = None, tag: str = "", shot_mode: str = "blend",
           version: str = "next10-v1", log=print) -> dict:
    ckpt_dir, out_dir = Path(ckpt_dir or CKPT10), Path(out_dir or WEIGHTS10)
    files = sorted(p.name for p in ckpt_dir.glob(f"next10{tag}_seed*.pt"))
    if not files:
        raise SystemExit(f"no next10{tag}_seed*.pt under {ckpt_dir}; train with: "
                         "python -m matchlens.tacticsml.train.fit10")
    members = []
    for name in files:
        blob = torch.load(ckpt_dir / name, map_location="cpu", weights_only=False)
        out = out_dir / name.replace("next10", "critic", 1).replace(".pt", ".npz")
        members.append({"file": out.name, "seed": int(blob.get("seed", 0)), "sha256": write_critic(blob, out)})
        log(f"exported {out.name} ({out.stat().st_size / 1e6:.2f} MB)")
    traj = None
    tp = ckpt_dir / "trajectory_seed0.pt"
    if tp.exists():
        out = out_dir / "trajectory.npz"
        traj = {"file": out.name, "sha256": write_trajectory(torch.load(tp, map_location="cpu", weights_only=False), out)}
        log(f"exported {out.name} ({out.stat().st_size / 1e6:.2f} MB)")
    ident = hashlib.sha256("|".join(sorted([m["sha256"] for m in members] + ([traj["sha256"]] if traj else []))
                                    ).encode()).hexdigest()
    manifest = {"format": "tacticsml-next10-weights-v1", "version": f"{version}+{ident[:8]}", "members": members,
                "trajectory": traj, "serve": {"shot": shot_mode}, "hash": ident,
                "grid": {"cols": C.GRID_COLS, "rows": C.GRID_ROWS}, "style_keys": list(C.STYLE_KEYS)}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log(f"manifest {out_dir / 'manifest.json'} version {manifest['version']}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train.export10", description=__doc__)
    p.add_argument("--ckpt-dir", default=str(CKPT10))
    p.add_argument("--out-dir", default=str(WEIGHTS10))
    p.add_argument("--tag", default="")
    p.add_argument("--shot-mode", default="blend", choices=("blend", "direct", "decomposed"))
    a = p.parse_args(argv)
    export(Path(a.ckpt_dir), Path(a.out_dir), a.tag, a.shot_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
