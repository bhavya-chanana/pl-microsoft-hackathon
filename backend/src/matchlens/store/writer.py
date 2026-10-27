"""Write-behind queue between the live match and the store.

The feed must never wait for the database (score, clock and timeline within
one second, BRD §10.1), so writes are queued and flushed by one background
task every ``interval`` seconds, grouped per container and run into batches.
Flushes run in order, so a stored event implies every earlier event of the
run is stored too, which is what resuming a run relies on. Several writes of
the same item between flushes collapse into the latest one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from matchlens.store.base import Store

log = logging.getLogger(__name__)

MAX_BATCH_OPS = 100  # Cosmos DB transactional batch limit
MAX_BATCH_BYTES = 1_500_000  # under the 2 MB request limit
RETRIES = 3


@dataclass
class WriterStats:
    written: int = 0
    batches: int = 0
    dropped: int = 0
    last_error: str | None = None


class WriteBehind:
    def __init__(self, store: Store, interval: float = 0.25, wake_at: int = 500) -> None:
        self.store = store
        self.interval = interval
        self.wake_at = wake_at
        self.stats = WriterStats()
        self._pending: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._count = 0
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closing = False

    @property
    def pending(self) -> int:
        return self._count

    def put(self, container: str, run_id: str, doc: dict[str, Any]) -> None:
        group = self._pending.setdefault((container, run_id), {})
        if doc["id"] not in group:
            self._count += 1
        group[doc["id"]] = doc
        if self._count >= self.wake_at:
            self._wake.set()

    def start(self) -> None:
        if self._task is None:
            self._closing = False
            self._task = asyncio.create_task(self._loop(), name="store-writer")

    async def close(self) -> None:
        if self._task is not None:
            self._closing = True
            self._wake.set()
            await self._task
            self._task = None
        await self.flush()

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.interval)
            self._wake.clear()
            try:
                await self.flush()
            except Exception:  # pragma: no cover - flush already logs per batch
                log.exception("store flush failed")
            if self._closing:
                return

    async def flush(self) -> None:
        async with self._lock:
            pending, self._pending, self._count = self._pending, {}, 0
            for (container, run_id), docs in pending.items():
                for chunk in _chunks(list(docs.values())):
                    await self._write(container, run_id, chunk)

    async def _write(self, container: str, run_id: str, docs: list[dict[str, Any]]) -> None:
        for attempt in range(RETRIES):
            try:
                await self.store.upsert_many(container, run_id, docs)
                self.stats.written += len(docs)
                self.stats.batches += 1
                return
            except Exception as exc:  # noqa: BLE001 - a store outage must not stop the match
                self.stats.last_error = f"{container}: {type(exc).__name__}: {exc}"[:300]
                if attempt < RETRIES - 1:
                    await asyncio.sleep(0.5 * 2 ** attempt)
        self.stats.dropped += len(docs)
        log.error("store: dropped %d %s items for run %s (%s)", len(docs), container, run_id,
                  self.stats.last_error)


def _chunks(docs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for doc in docs:
        n = len(json.dumps(doc, separators=(",", ":")))
        if current and (len(current) >= MAX_BATCH_OPS or size + n > MAX_BATCH_BYTES):
            out.append(current)
            current, size = [], 0
        current.append(doc)
        size += n
    if current:
        out.append(current)
    return out
