"""The video clock: broadcast-clock anchors, the ClockMap built from them, and applying it to stored docs.

    uv run python -m matchlens.next10.clock clip:liv-bha-2627 --anchors anchors.json

``anchors.json`` is a list of pairs of a video time and what the broadcast clock showed then::

    [{"video_s": 14.0, "clock": "00:03", "period": 1}, {"video_s": 2790.0, "clock": "45:12", "period": 2},
     {"video_s": 310.5, "match_s": 296.5, "kind": "goal", "label": "..."}]

``clock`` is the on-screen clock ("mm:ss", or seconds); ``match_s`` is match seconds on FotMob's minute scale;
``kind`` is any ``vision.align`` anchor kind (default ``clock``).

Applying the map to a match rewrites ``match_clock_s`` on its frames and predictions (null in the half-time break and
outside the two halves), and turns the frames of the second half round by 180 degrees (``flipped: true``) so home
keeps attacking towards +x for the whole match (the other way round when the job was run with ``--swap-teams``); frames that stop being second-half are turned back. Nothing is
detected again, so it takes seconds. After it, run ``next10.predict`` again for the predictions, whose input
depends on the orientation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from matchlens.store.next10 import Next10Settings, Next10Store

log = logging.getLogger("matchlens.next10.clock")

PAGE_S = 600.0  # frames fetched per query: 10 minutes of video at 5 Hz is 3000 documents


def load_anchors(path: Path) -> list[Any]:
    """Anchors from a JSON file (see the module docstring)."""
    from matchlens.vision.align import Anchor

    rows = json.loads(Path(path).read_text())
    if isinstance(rows, dict):
        rows = rows.get("anchors", [])
    anchors = []
    for r in rows:
        period = r.get("period")
        label = str(r.get("label", ""))
        if "clock" in r:
            anchors.append(Anchor.from_clock(float(r["video_s"]), r["clock"], period, label))
        elif "match_s" in r:
            anchors.append(Anchor(float(r["video_s"]), float(r["match_s"]), r.get("kind", "event"), period,
                                  r.get("sigma_s"), label))
        elif "minute" in r:
            anchors.append(Anchor.from_minute(float(r["video_s"]), int(r["minute"]), r.get("added"),
                                              r.get("kind", "goal"), period, label))
        else:
            raise ValueError(f"anchor needs clock, match_s or minute: {r}")
    return anchors


def build_clock(anchors: list[Any]) -> Any | None:
    """A ClockMap from anchors (None when there are none or ``vision.align`` is absent)."""
    if not anchors:
        return None
    try:
        from matchlens.vision.align import align
    except ImportError:
        log.warning("matchlens.vision.align is not available; no match clock")
        return None
    return align(anchors)


def load_clock(path: Path) -> Any:
    from matchlens.vision.align import ClockMap

    return ClockMap.from_dict(json.loads(Path(path).read_text()))


def clock_function(clock_map: Any | None) -> Callable[[float], tuple[float | None, int]] | None:
    """``video seconds -> (match clock seconds or None, period 0/1/2)`` for ``FrameChain(clock=...)``."""
    if clock_map is None:
        return None

    def at(t_s: float) -> tuple[float | None, int]:
        m = float(clock_map.to_match(t_s))
        period = int(clock_map.period_at(t_s))
        return (None if period == 0 or not math.isfinite(m) else round(m, 2)), period

    return at


def finite(value: Any) -> Any:
    """JSON-safe copy: non-finite numbers become None (Cosmos rejects NaN and Infinity)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    return value


def apply_to_frame(doc: dict[str, Any], at: Callable[[float], tuple[float | None, int]], swap: bool = False) -> bool:
    """Set ``match_clock_s`` and the orientation on one frame doc; True if it changed. ``swap``: home started on the
    right (the frames are turned in the first half, and not in the second)."""
    clock, period = at(float(doc["t_s"]))
    changed = doc.get("match_clock_s") != clock
    doc["match_clock_s"] = clock
    want = (period == 2) != swap
    if bool(doc.get("flipped")) != want:
        for e in doc.get("entities", []):
            e["x"], e["y"] = -e["x"], -e["y"]
        changed = True
    if want:
        doc["flipped"] = True
    else:
        doc.pop("flipped", None)
    return changed


async def apply_clock(store: Next10Store, match_id: str, clock_map: Any, *, containers: tuple[str, ...] = (
        "frames", "predictions"), batch: int = 500, swap: bool = False) -> dict[str, int]:
    """Rewrite the clock of a stored match. Returns the number of documents changed per container."""
    at = clock_function(clock_map)
    assert at is not None
    changed = {c: 0 for c in containers}
    for container in containers:
        end = await store.query(container, "SELECT VALUE MAX(c.t_s) FROM c WHERE c.match_id = @m",
                                [{"name": "@m", "value": match_id}], partition=match_id)
        top = float(end[0]) if end and end[0] is not None else -1.0
        t = 0.0
        while t <= top:
            rows = await store.query(
                container, "SELECT * FROM c WHERE c.match_id = @m AND c.t_s >= @a AND c.t_s < @b",
                [{"name": "@m", "value": match_id}, {"name": "@a", "value": t}, {"name": "@b", "value": t + PAGE_S}],
                partition=match_id)
            todo = []
            for doc in rows:
                doc = {k: v for k, v in doc.items() if not k.startswith("_")}
                if container == "frames":
                    ok = apply_to_frame(doc, at, swap)
                else:
                    clock = at(float(doc["t_s"]))[0]
                    ok = doc.get("match_clock_s") != clock
                    doc["match_clock_s"] = clock
                if ok:
                    todo.append(doc)
            for i in range(0, len(todo), batch):
                await store.upsert(container, todo[i:i + batch])
            changed[container] += len(todo)
            t += PAGE_S
    return changed


async def _main(args: argparse.Namespace) -> None:
    anchors = load_anchors(args.anchors)
    clock_map = build_clock(anchors)
    if clock_map is None:
        raise SystemExit("no anchors")
    async with await Next10Store(Next10Settings.from_env()).open() as store:
        job = None
        rows = await store.query("video_jobs", "SELECT * FROM c WHERE c.match_id = @m",
                                 [{"name": "@m", "value": args.match_id}])
        if rows:
            job = rows[0]
            folder = Path(args.jobs_dir) / job["job_id"]
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "anchors.json").write_text(Path(args.anchors).read_text())
            (folder / "clockmap.json").write_text(json.dumps(clock_map.to_dict()))
        changed = await apply_clock(store, args.match_id, clock_map, swap=bool(job and job.get("swap_teams")))
        report = finite({k: v for k, v in clock_map.report.to_dict().items() if k != "anchors"})
        if job is not None:
            job["clock"] = {"anchors": len(anchors), "map": str(folder / "clockmap.json"), "report": report}
            await store.upsert("video_jobs", [job])
    print(json.dumps({"match_id": args.match_id, "anchors": len(anchors), "changed": changed, "report": report}))


def main() -> None:
    from matchlens.vision.jobs import JOBS

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("match_id")
    p.add_argument("--anchors", type=Path, required=True)
    p.add_argument("--jobs-dir", type=Path, default=JOBS)
    logging.basicConfig(level=logging.INFO)
    for noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.cosmos", "azure.identity"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
