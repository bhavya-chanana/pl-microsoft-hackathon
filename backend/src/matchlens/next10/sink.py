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

    Documents are stored by kind: ``frame`` goes to ``container`` (frames) and ``next10`` to ``predictions``; other
    kinds (``stats``) only reach the hub. ``store=None`` runs without persistence (hub only). A failed write keeps
    the batch for the next flush.
    """

    def __init__(self, match_id: str, store: Next10Store | None, *, hub: Hub = default_hub, flush_s: float = 1.0,
                 container: str = "frames") -> None:
        self.match_id, self.store, self.hub, self.flush_s, self.container = match_id, store, hub, flush_s, container
        self.written = 0
        self.failures = 0
        self.routes = {"frame": container, "next10": "predictions"}
        self._buffers: dict[str, list[dict[str, Any]]] = {k: [] for k in self.routes}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> FrameSink:
        self._task = asyncio.create_task(self._loop())
        return self

    def add(self, doc: dict[str, Any], kind: str = "frame") -> None:
        self.hub.publish(self.match_id, kind, doc)
        if self.store is not None and kind in self.routes:
            buffer = self._buffers[kind]
            if len(buffer) >= MAX_BUFFER:
                buffer.pop(0)
            buffer.append(doc)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.flush_s)
            await self.flush()

    async def flush(self) -> None:
        if self.store is None:
            return
        for kind, container in self.routes.items():
            if not self._buffers[kind]:
                continue
            batch, self._buffers[kind] = self._buffers[kind], []
            try:
                self.written += await self.store.upsert(container, batch)
            except Exception as exc:
                self.failures += 1
                log.warning("writing %d %s docs failed (%s); keeping them for the next flush", len(batch), kind, exc)
                self._buffers[kind] = (batch + self._buffers[kind])[-MAX_BUFFER:]

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        await self.flush()
