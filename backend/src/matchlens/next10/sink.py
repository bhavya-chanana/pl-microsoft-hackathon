"""Batched writes of frame documents to the next10 store, plus fan-out to the live hub."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from matchlens.next10.hub import Hub, hub as default_hub
from matchlens.store.next10 import Next10Store

log = logging.getLogger(__name__)

MAX_BUFFER = 20000  # frames kept while the store is unreachable (about an hour at 5 Hz)


class FrameSink:
    """``add`` documents from the event loop; they go to the hub at once and to the store every ``flush_s``.

    ``store=None`` runs without persistence (hub only). A failed write keeps the batch for the next flush.
    """

    def __init__(self, match_id: str, store: Next10Store | None, *, hub: Hub = default_hub, flush_s: float = 1.0,
                 container: str = "frames") -> None:
        self.match_id, self.store, self.hub, self.flush_s, self.container = match_id, store, hub, flush_s, container
        self.written = 0
        self.failures = 0
        self._buffer: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> FrameSink:
        self._task = asyncio.create_task(self._loop())
        return self

    def add(self, doc: dict[str, Any], kind: str = "frame") -> None:
        self.hub.publish(self.match_id, kind, doc)
        if self.store is not None and kind == "frame":
            if len(self._buffer) >= MAX_BUFFER:
                self._buffer.pop(0)
            self._buffer.append(doc)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.flush_s)
            await self.flush()

    async def flush(self) -> None:
        if not self._buffer or self.store is None:
            return
        batch, self._buffer = self._buffer, []
        try:
            self.written += await self.store.upsert(self.container, batch)
        except Exception as exc:
            self.failures += 1
            log.warning("writing %d frames failed (%s); keeping them for the next flush", len(batch), exc)
            self._buffer = (batch + self._buffer)[-MAX_BUFFER:]

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        await self.flush()
