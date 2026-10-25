"""Live match session: runs a feed through the stats engine and fans results
out to subscribers (UI sockets, the agent pipeline). One active match at a
time (FR-003).

Everything the session ingests or publishes is recorded to the store through
its ``RunRecorder``; ``insights`` and ``overlays`` are the in-process cache of
the stored collections, so a restarted backend can ``hydrate`` and resume."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from matchlens.domain.models import Event, EventType, TrackingFrame
from matchlens.ingest.adapters import FeedAdapter, FeedItem, feed_order
from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.signals import Signal
from matchlens.runtime.fans import FanZone
from matchlens.store.repository import Repository, RunRecorder

log = logging.getLogger(__name__)

# Event types shown on the fan timeline (FR-038). Other events still update stats.
TIMELINE_TYPES = {
    EventType.KICKOFF, EventType.GOAL, EventType.SHOT, EventType.SAVE, EventType.CARD,
    EventType.SUBSTITUTION, EventType.CORNER, EventType.HALF_TIME, EventType.FULL_TIME,
}

TALLY_INTERVAL_S = 0.5

SignalHandler = Callable[[Signal, "MatchSession"], Awaitable[None]]


def event_payload(engine: MatchEngine, e: Event) -> dict[str, Any]:
    data = e.model_dump(mode="json")
    data["minute_label"] = e.minute_label
    q = engine.pass_quality(e.event_id)
    if q:
        data["pass_quality"] = q.model_dump()
    return data


class MatchSession:
    def __init__(self, adapter: FeedAdapter, recorder: RunRecorder | None = None) -> None:
        self.adapter = adapter
        self.meta = adapter.meta()
        self.engine = MatchEngine(self.meta)
        self.recorder = recorder or Repository.detached().recorder(f"local-{self.meta.match_id}")
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._signal_handlers: list[SignalHandler] = []
        self._task: asyncio.Task[None] | None = None
        self.finished = False
        self.last_frame: dict[str, Any] | None = None
        # Published outputs of later pipeline stages, kept for late joiners.
        self.insights: dict[str, dict[str, Any]] = {}
        self.overlays: dict[str, dict[str, Any]] = {}
        self.latency_ms: list[float] = []
        # Feed items already ingested (by hydrate) are skipped when the feed delivers them again.
        self._resume_frame_elapsed: float | None = None
        # Fan polls and ratings; votes are stored with the run.
        self.fans = FanZone(self.meta, salt=self.run_id, sink=self.recorder.fan_vote)
        self._tally_pending = False

    @property
    def run_id(self) -> str:
        return self.recorder.run_id

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

    def _already_ingested(self, item: FeedItem) -> bool:
        if isinstance(item, TrackingFrame):
            return self._resume_frame_elapsed is not None and item.elapsed <= self._resume_frame_elapsed
        return self.engine.has_event(item.event_id)

    async def _run(self) -> None:
        rec = self.recorder
        try:
            async for item in self.adapter.stream():
                if self._already_ingested(item):
                    continue
                started = time.perf_counter()
                if isinstance(item, TrackingFrame):
                    signals = self.engine.ingest_frame(item)
                    self.last_frame = item.model_dump(mode="json")
                    self.publish({"type": "frame", "frame": self.last_frame})
                    rec.frame(self.last_frame)
                else:
                    signals = self.engine.ingest_event(item)
                    q = self.engine.pass_quality(item.event_id)
                    rec.event(item.model_dump(mode="json"), item.minute_label, q.model_dump() if q else None)
                    if item.type == EventType.CORRECTION:
                        self.publish({"type": "correction", "event": item.model_dump(mode="json")})
                    else:
                        effective = self.engine.event(item.event_id) or item
                        self.publish({"type": "event", "event": event_payload(self.engine, effective)})
                        for poll in self.fans.observe(effective, self.engine):
                            self.publish({"type": "poll", "poll": poll.public()})
                    state = self.engine.state()
                    self.publish({"type": "state", "state": state})
                    rec.progress(state)
                    if item.type in (EventType.HALF_TIME, EventType.FULL_TIME):
                        self._record_stats(item.type.value)
                self.latency_ms.append((time.perf_counter() - started) * 1000)
                for sig in signals:
                    payload = sig.model_dump(mode="json")
                    self.publish({"type": "signal", "signal": payload})
                    rec.signal(payload)
                    for handler in self._signal_handlers:
                        asyncio.create_task(self._safe(handler, sig))
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - logged and surfaced to clients
            log.exception("feed failed")
            rec.update(status="error")
            self.publish({"type": "feed_status", "status": "error"})
        else:
            self.finished = True
            rec.update(status="finished", finished_at=rec.run.get("updated_at"))
            self.publish({"type": "feed_status", "status": "finished"})

    def _record_stats(self, phase: str) -> None:
        teams = {t.team_id: self.engine.team_stats(t.team_id) for t in (self.meta.home, self.meta.away)}
        players = {pid: self.engine.player_stats(pid) for pid in self.meta.players}
        self.recorder.stats(phase, teams, players)

    def hydrate(self, events: list[Event], frames: list[TrackingFrame]) -> None:
        """Rebuild the engine from a stored run (events and frames in feed order), without
        publishing or re-recording anything. The feed then skips what is already here."""
        for item in feed_order(events, frames):
            if isinstance(item, TrackingFrame):
                self.engine.ingest_frame(item)
            else:
                self.engine.ingest_event(item)
                if item.type != EventType.CORRECTION:
                    self.fans.observe(self.engine.event(item.event_id) or item, self.engine)
        if frames:
            self.last_frame = frames[-1].model_dump(mode="json")
            self._resume_frame_elapsed = frames[-1].elapsed

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

    def fans_changed(self) -> None:
        """Publish fan tallies after a vote, at most twice a second."""
        if self._tally_pending:
            return
        self._tally_pending = True
        asyncio.get_running_loop().call_later(TALLY_INTERVAL_S, self._publish_tally)

    def _publish_tally(self) -> None:
        self._tally_pending = False
        self.publish({"type": "fan_tally", "polls": self.fans.public_polls(),
                      "ratings": self.fans.rating_tallies()})

    def publish_insight(self, insight: dict[str, Any]) -> None:
        self.insights[insight["insight_id"]] = insight
        self.recorder.insight(insight)
        self.publish({"type": "insight", "insight": insight})

    def publish_overlay(self, overlay: dict[str, Any]) -> None:
        self.overlays[overlay["overlay_id"]] = overlay
        self.recorder.overlay(overlay)
        self.publish({"type": "overlay", "overlay": overlay})

    # ---------------------------------------------------------- snapshots

    def timeline(self) -> list[dict[str, Any]]:
        return [event_payload(self.engine, e) for e in self.engine.events(types=TIMELINE_TYPES)]

    def snapshot(self) -> dict[str, Any]:
        """Everything a client joining mid-match needs (FR-007)."""
        return {
            "type": "hello",
            "run_id": self.run_id,
            "meta": self.meta.model_dump(mode="json"),
            "state": self.engine.state(),
            "timeline": self.timeline(),
            "signals": [s.model_dump(mode="json") for s in self.engine.signals()],
            "insights": list(self.insights.values()),
            "overlays": list(self.overlays.values()),
            "momentum": [vars(p) for p in self.engine.momentum_series()],
            "frame": self.last_frame,
            "finished": self.finished,
            "polls": self.fans.public_polls(),
        }
