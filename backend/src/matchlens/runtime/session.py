"""Live match session: runs a feed through the stats engine and fans results
out to subscribers (UI sockets, the agent pipeline). One active match at a
time (FR-003)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from matchlens.domain.models import Event, EventType, TrackingFrame
from matchlens.ingest.adapters import FeedAdapter
from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.signals import Signal

log = logging.getLogger(__name__)

# Event types shown on the fan timeline (FR-038). Other events still update stats.
TIMELINE_TYPES = {
    EventType.KICKOFF, EventType.GOAL, EventType.SHOT, EventType.SAVE, EventType.CARD,
    EventType.SUBSTITUTION, EventType.CORNER, EventType.HALF_TIME, EventType.FULL_TIME,
}

SignalHandler = Callable[[Signal, "MatchSession"], Awaitable[None]]


def event_payload(engine: MatchEngine, e: Event) -> dict[str, Any]:
    data = e.model_dump(mode="json")
    data["minute_label"] = e.minute_label
    q = engine.pass_quality(e.event_id)
    if q:
        data["pass_quality"] = q.model_dump()
    return data


class MatchSession:
    def __init__(self, adapter: FeedAdapter) -> None:
        self.adapter = adapter
        self.meta = adapter.meta()
        self.engine = MatchEngine(self.meta)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._signal_handlers: list[SignalHandler] = []
        self._task: asyncio.Task[None] | None = None
        self.finished = False
        self.last_frame: dict[str, Any] | None = None
        # Published outputs of later pipeline stages, kept for late joiners.
        self.insights: dict[str, dict[str, Any]] = {}
        self.overlays: dict[str, dict[str, Any]] = {}
        self.latency_ms: list[float] = []

    # ---------------------------------------------------------- lifecycle

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"match:{self.meta.match_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def on_signal(self, handler: SignalHandler) -> None:
        self._signal_handlers.append(handler)

    async def _run(self) -> None:
        try:
            async for item in self.adapter.stream():
                started = time.perf_counter()
                if isinstance(item, TrackingFrame):
                    signals = self.engine.ingest_frame(item)
                    self.last_frame = item.model_dump(mode="json")
                    self.publish({"type": "frame", "frame": self.last_frame})
                else:
                    signals = self.engine.ingest_event(item)
                    if item.type == EventType.CORRECTION:
                        self.publish({"type": "correction", "event": item.model_dump(mode="json")})
                    else:
                        effective = self.engine.event(item.event_id) or item
                        self.publish({"type": "event", "event": event_payload(self.engine, effective)})
                    self.publish({"type": "state", "state": self.engine.state()})
                self.latency_ms.append((time.perf_counter() - started) * 1000)
                for sig in signals:
                    self.publish({"type": "signal", "signal": sig.model_dump(mode="json")})
                    for handler in self._signal_handlers:
                        asyncio.create_task(self._safe(handler, sig))
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - logged and surfaced to clients
            log.exception("feed failed")
            self.publish({"type": "feed_status", "status": "error"})
        else:
            self.finished = True
            self.publish({"type": "feed_status", "status": "finished"})

    async def _safe(self, handler: SignalHandler, sig: Signal) -> None:
        try:
            await handler(sig, self)
        except Exception:
            # Agent failures must never stop the score, clock or timeline (BRD §8.3).
            log.exception("signal handler failed for %s", sig.signal_id)

    # ---------------------------------------------------------- pub/sub

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def publish(self, message: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # A slow client: drop its oldest message rather than block the feed.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                q.put_nowait(message)

    def publish_insight(self, insight: dict[str, Any]) -> None:
        self.insights[insight["insight_id"]] = insight
        self.publish({"type": "insight", "insight": insight})

    def publish_overlay(self, overlay: dict[str, Any]) -> None:
        self.overlays[overlay["overlay_id"]] = overlay
        self.publish({"type": "overlay", "overlay": overlay})

    # ---------------------------------------------------------- snapshots

    def timeline(self) -> list[dict[str, Any]]:
        return [event_payload(self.engine, e) for e in self.engine.events(types=TIMELINE_TYPES)]

    def snapshot(self) -> dict[str, Any]:
        """Everything a client joining mid-match needs (FR-007)."""
        return {
            "type": "hello",
            "meta": self.meta.model_dump(mode="json"),
            "state": self.engine.state(),
            "timeline": self.timeline(),
            "signals": [s.model_dump(mode="json") for s in self.engine.signals()],
            "insights": list(self.insights.values()),
            "overlays": list(self.overlays.values()),
            "momentum": [vars(p) for p in self.engine.momentum_series()],
            "frame": self.last_frame,
            "finished": self.finished,
        }
