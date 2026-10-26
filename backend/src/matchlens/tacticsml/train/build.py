"""Build training shards: ``python -m matchlens.tacticsml.train shards --sources synthetic statsbomb360``.

``limit`` caps the number of matches per source. A source that needs a manual download (PFF) reports what
to do and is skipped, so one missing dataset does not stop the rest.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.schema import (  # noqa: F401  (re-exported for the trainer)
    DataUnavailable, ShardIndex, iter_shards, read_shard, read_shards,
)
from matchlens.tacticsml.train.sources import SOURCES, load


def build(sources: Sequence[str] | None = None, limit: int | None = None, root: Path | None = None,
          progress: bool = True, **options: Any) -> dict[str, list[schema.ShardEntry]]:
    """Write the shards of each source and return its entries. ``options`` go to the source's
    ``build_shards`` (e.g. ``random_hz``, ``seeds``)."""
    out: dict[str, list[schema.ShardEntry]] = {}
    for name in sources or SOURCES:
        mod = load(name)
        accepted = inspect.signature(mod.build_shards).parameters
        kw = {k: v for k, v in options.items() if k in accepted and v is not None}
        try:
            out[name] = mod.build_shards(limit=limit, root=root, progress=progress, **kw)
        except DataUnavailable as exc:
            print(f"[{name}] skipped: {exc}", file=sys.stderr)
            out[name] = []
    return out


def summary(root: Path | None = None) -> str:
    """Sample counts per source and split and label base rates (over known labels)."""
    index = ShardIndex.load(root)
    lines = []
    for source, counts in index.counts().items():
        entries = index.select([source])
        total = sum(e.n for e in entries)
        rates: dict[str, float] = {}
        for k in ("shot10", "goal10", "concede10", "turn5", "receiver_known", "receiver_lost"):
            vals = [(e.rates.get(k, float("nan")), e.n) for e in entries if not np.isnan(e.rates.get(k, float("nan")))]
            rates[k] = sum(v * n for v, n in vals) / max(sum(n for _, n in vals), 1)
        lines.append(f"{source}: {len(entries)} matches, {total} samples "
                     + " ".join(f"{s}={counts[s]}" for s in schema.SPLITS))
        lines.append("    rates " + " ".join(f"{k}={v:.4f}" for k, v in rates.items()))
    return "\n".join(lines) or "no shards"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train shards", description=__doc__)
    parser.add_argument("--sources", nargs="+", choices=SOURCES, default=["synthetic"])
    parser.add_argument("--limit", type=int, default=None, help="max matches per source")
    parser.add_argument("--root", type=Path, default=None, help="shards directory (default: backend/data/local/...)")
    parser.add_argument("--random-hz", type=float, default=None, help="open-play frames per second (tracking sources)")
    parser.add_argument("--summary", action="store_true", help="only print the manifest summary")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.summary:
        built = build(args.sources, args.limit, args.root, random_hz=args.random_hz)
        if not any(built.values()):
            return 1
    print(summary(args.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
