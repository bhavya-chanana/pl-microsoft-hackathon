"""Dump a stored match's frames to NDJSON (one frame doc per line) for offline studies.

    uv run python -m matchlens.next10.export clip:liv-bha-2627 --out data/local/next10/frames_clip_liv-bha-2627.ndjson
    uv run python -m matchlens.next10.export clip:liv-bha-2627 --to-s 60 --out data/local/next10/frames_test_lb_bundle.ndjson

Read-only on the database. Output stays under ``backend/data/local/`` (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from matchlens.store.next10 import Next10Settings, Next10Store

PAGE_S = 600.0


async def export_frames(store: Next10Store, match_id: str, out: Path, *, from_s: float = 0.0,
                        to_s: float | None = None) -> int:
    """Write the match's frames (time order) to ``out``; returns the number written."""
    top = await store.query("frames", "SELECT VALUE MAX(c.t_s) FROM c WHERE c.match_id = @m",
                            [{"name": "@m", "value": match_id}], partition=match_id)
    end = float(top[0]) if top and top[0] is not None else -1.0
    end = end if to_s is None else min(end, to_s)
    out.parent.mkdir(parents=True, exist_ok=True)
    n, t = 0, from_s
    with out.open("w") as f:
        while t <= end:
            hi = min(t + PAGE_S, end + 1e-6)
            rows = await store.query(
                "frames", "SELECT * FROM c WHERE c.match_id = @m AND c.t_s >= @a AND c.t_s < @b ORDER BY c.t_s",
                [{"name": "@m", "value": match_id}, {"name": "@a", "value": t}, {"name": "@b", "value": hi}],
                partition=match_id)
            for r in rows:
                f.write(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, separators=(",", ":")) + "\n")
                n += 1
            t += PAGE_S
    return n


async def _main(args: argparse.Namespace) -> None:
    async with await Next10Store(Next10Settings.from_env()).open() as store:
        n = await export_frames(store, args.match_id, args.out, from_s=args.from_s, to_s=args.to_s)
    print(json.dumps({"match_id": args.match_id, "frames": n, "file": str(args.out)}))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("match_id")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--from-s", type=float, default=0.0)
    p.add_argument("--to-s", type=float)
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
