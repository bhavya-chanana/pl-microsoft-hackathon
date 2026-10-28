"""In-process fan-out of new documents to WebSocket clients.

Writers (the shadow writer, the live video runner) call :func:`publish` after they store a document. Clients
subscribe per match. Publishing is thread-safe and never blocks: a subscriber whose queue is full loses its oldest
message. Writers in other processes are covered by the API polling the store (``api/next10_routes.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

QUEUE_SIZE = 500


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]] = defaultdict(list)
        self._lock = threading.Lock()

    def publish(self, match_id: str, kind: str, doc: dict[str, Any]) -> int:
        """Send ``{"type": kind, "doc": doc}`` to every subscriber of ``match_id``; returns how many."""
        message = {"type": kind, "doc": doc}
        with self._lock:
            subs = list(self._subs.get(match_id, ()))
        for loop, queue in subs:
            with contextlib.suppress(RuntimeError):  # the subscriber's loop is closing
                loop.call_soon_threadsafe(self._put, queue, message)
        return len(subs)

    @staticmethod
    def _put(queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(message)

    @contextlib.asynccontextmanager
    async def subscribe(self, match_id: str) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        entry = (asyncio.get_running_loop(), asyncio.Queue[dict[str, Any]](QUEUE_SIZE))
        with self._lock:
            self._subs[match_id].append(entry)
        try:
            yield entry[1]
        finally:
            with self._lock:
                self._subs[match_id].remove(entry)
                if not self._subs[match_id]:
                    del self._subs[match_id]

    def subscribers(self, match_id: str) -> int:
        with self._lock:
            return len(self._subs.get(match_id, ()))


hub = Hub()
