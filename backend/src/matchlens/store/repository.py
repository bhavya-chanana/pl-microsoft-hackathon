"""Matchlens data access: the store behind an in-process read-through cache.

Reads hit the cache first and fall through to the store on a miss; writes
update the cache at once and reach the store through the write-behind queue.
The backend runs a single replica, so the cache never goes stale.

``RunRecorder`` is the per-run write API the live session, the agent
pipeline and the fan services use. A detached repository (no store) keeps
only the cache, for tools and tests that run a match without a database.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from matchlens.store.base import Store, from_doc, to_doc
from matchlens.store.writer import WriteBehind

ACTIVE = ("_active", "active")  # (partition, id) of the pointer to the run the backend is playing


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Repository:
    def __init__(self, store: Store | None, writer: WriteBehind | None, cache_size: int = 20_000) -> None:
        self.store = store
        self.writer = writer
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @classmethod
    def detached(cls) -> Repository:
        return cls(None, None)

    @property
    def label(self) -> str:
        return self.store.label if self.store else "detached"

    # ------------------------------------------------------------ cache

    def _remember(self, container: str, run_id: str, doc_id: str, payload: dict[str, Any]) -> None:
        key = (container, run_id, doc_id)
        self._cache[key] = payload
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def put(self, container: str, run_id: str, doc_id: str, payload: dict[str, Any], cache: bool = True) -> None:
        if cache:
            self._remember(container, run_id, doc_id, payload)
        if self.writer is not None:
            self.writer.put(container, run_id, to_doc(doc_id, run_id, payload))

    async def get(self, container: str, run_id: str, doc_id: str) -> dict[str, Any] | None:
        key = (container, run_id, doc_id)
        if key in self._cache:
            self.hits += 1
            self._cache.move_to_end(key)
            return self._cache[key]
        self.misses += 1
        if self.store is None:
            return None
        doc = await self.store.read(container, run_id, doc_id)
        if doc is None:
            return None
        payload = from_doc(doc)
        self._remember(container, run_id, doc_id, payload)
        return payload

    async def load(self, container: str, run_id: str, order_by: str | None = None,
                   where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """A whole collection of one run, in order (used to resume a run)."""
        if self.store is None:
            return []
        if self.writer is not None:
            await self.writer.flush()
        return [from_doc(d) for d in await self.store.query(container, run_id, where=where, order_by=order_by)]

    async def flush(self) -> None:
        if self.writer is not None:
            await self.writer.flush()

    def stats(self) -> dict[str, Any]:
        w = self.writer.stats if self.writer else None
        return {
            "backend": self.label,
            "pending_writes": self.writer.pending if self.writer else 0,
            "written": w.written if w else 0,
            "dropped": w.dropped if w else 0,
            "last_error": w.last_error if w else None,
            "cache_entries": len(self._cache),
            "cache_hits": self.hits,
            "cache_misses": self.misses,
        }

    # ------------------------------------------------------------ runs

    def recorder(self, run_id: str) -> RunRecorder:
        return RunRecorder(self, run_id)

    async def active_run(self) -> dict[str, Any] | None:
        pointer = await self.get("runs", *ACTIVE)
        if not pointer:
            return None
        run_id = pointer["active_run_id"]
        run = await self.get("runs", run_id, run_id)
        return {"run_id": run_id, **run} if run else None

    async def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        rows = await self.store.query("runs", None, where={"doc_type": "run"}, order_by="created_at",
                                      descending=True, limit=limit)
        return [{"run_id": d["run_id"], **from_doc(d)} for d in rows]


class RunRecorder:
    """Writes one run's data. Every method is non-blocking."""

    def __init__(self, repo: Repository, run_id: str) -> None:
        self.repo = repo
        self.run_id = run_id
        self.run: dict[str, Any] = {}

    def _put(self, container: str, doc_id: str, payload: dict[str, Any], cache: bool = True) -> None:
        self.repo.put(container, self.run_id, doc_id, payload, cache)

    # run document --------------------------------------------------------

    def begin(self, *, match_id: str, seed: int | None, storyline: str | None, speed: float,
              meta: dict[str, Any], resumed_from: float | None = None,
              existing: dict[str, Any] | None = None) -> None:
        stamp = now_iso()
        self.run = existing or {
            "doc_type": "run", "match_id": match_id, "seed": seed, "storyline": storyline,
            "status": "live", "speed": speed, "paused": False, "position_elapsed": 0.0,
            "minute_label": "0'", "score": {}, "created_at": stamp, "finished_at": None, "resumes": 0,
        }
        if resumed_from is not None:
            self.run["resumes"] = int(self.run.get("resumes", 0)) + 1
            self.run["resumed_at"] = stamp
            self.run["status"] = "live"
        self.update()
        self._put("runs", "meta", {"doc_type": "meta", "meta": meta})
        self.repo.put("runs", ACTIVE[0], ACTIVE[1], {"doc_type": "pointer", "active_run_id": self.run_id})

    def update(self, **fields: Any) -> None:
        if not self.run:
            return
        self.run.update(fields, updated_at=now_iso())
        self._put("runs", self.run_id, dict(self.run))

    def progress(self, state: dict[str, Any]) -> None:
        self.update(position_elapsed=state["elapsed"], minute_label=state["minute_label"], score=state["score"],
                    match_status=state["status"], state=state)

    # feed ----------------------------------------------------------------

    def event(self, event: dict[str, Any], minute_label: str, pass_quality: dict[str, Any] | None) -> None:
        payload = {**event, "minute_label": minute_label}
        if pass_quality:
            payload["pass_quality"] = pass_quality
        self._put("events", event["event_id"], payload, cache=False)

    def frame(self, frame: dict[str, Any]) -> None:
        self._put("frames", f"{frame['elapsed']:.1f}", frame, cache=False)

    # outputs -------------------------------------------------------------

    def signal(self, signal: dict[str, Any]) -> None:
        self._put("signals", f"{signal['signal_id']}@r{signal['revision']}", signal, cache=False)

    def insight(self, insight: dict[str, Any]) -> None:
        self._put("insights", insight["insight_id"], insight)

    def overlay(self, overlay: dict[str, Any]) -> None:
        self._put("overlays", overlay["overlay_id"], overlay)

    def decision(self, seq: int, decision: dict[str, Any]) -> None:
        self._put("decisions", f"{seq:05d}", {"seq": seq, **decision, "at": now_iso()}, cache=False)

    def agent_run(self, record: dict[str, Any]) -> None:
        self._put("agent_runs", uuid.uuid4().hex, {**record, "at": now_iso()}, cache=False)

    def pipeline_state(self, coordinator: dict[str, Any], scheduler: dict[str, Any]) -> None:
        self._put("runs", "pipeline", {"doc_type": "pipeline", "coordinator": coordinator, "scheduler": scheduler})

    def stats(self, phase: str, teams: dict[str, Any], players: dict[str, Any]) -> None:
        self._put("runs", f"stats:{phase}", {"doc_type": "stats", "phase": phase, "teams": teams,
                                             "players": players}, cache=False)

    def fan_output(self, key: str, kind: str, result: dict[str, Any]) -> None:
        self._put("fan_outputs", key, {"kind": kind, "result": result, "at": now_iso()})

    def fan_vote(self, doc_id: str, payload: dict[str, Any]) -> None:
        self._put("fan_votes", doc_id, {**payload, "at": now_iso()}, cache=False)

    async def fan_cached(self, key: str) -> dict[str, Any] | None:
        doc = await self.repo.get("fan_outputs", self.run_id, key)
        return doc["result"] if doc else None

    async def load(self, container: str, order_by: str | None = None) -> list[dict[str, Any]]:
        return await self.repo.load(container, self.run_id, order_by)

    async def get(self, container: str, doc_id: str) -> dict[str, Any] | None:
        return await self.repo.get(container, self.run_id, doc_id)
