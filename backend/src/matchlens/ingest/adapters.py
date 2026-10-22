"""Feed adapters: the pluggable Ingest boundary (FR-001, FR-005).

Every adapter yields :class:`Event` and :class:`TrackingFrame` objects in the
common schema. The rest of Matchlens never sees provider formats, so a
licensed live provider can replace the synthetic feed by implementing one
adapter.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Protocol

from matchlens.domain.models import Event, MatchMeta, SyntheticMatch, TrackingFrame

FeedItem = Event | TrackingFrame


class FeedAdapter(Protocol):
    """Contract every feed provider implements."""

    def meta(self) -> MatchMeta: ...

    def stream(self) -> AsyncIterator[FeedItem]: ...


def feed_order(events: Iterable[Event], frames: Iterable[TrackingFrame]) -> list[FeedItem]:
    """Interleave events and frames in the order a live feed would deliver them."""
    items: list[tuple[float, int, int, FeedItem]] = [(e.elapsed, 1, e.sequence, e) for e in events]
    items += [(f.elapsed, 0, 0, f) for f in frames]
    items.sort(key=lambda t: (t[0], t[1], t[2]))
    return [item for *_, item in items]


class ReplayControl:
    """Live-adjustable replay settings shared with the API."""

    def __init__(self, speed: float = 1.0, start_elapsed: float = 0.0) -> None:
        self.speed = speed
        self.start_elapsed = start_elapsed
        self.paused = False
        self._resume = asyncio.Event()
        self._resume.set()

    def pause(self) -> None:
        self.paused = True
        self._resume.clear()

    def resume(self) -> None:
        self.paused = False
        self._resume.set()

    async def wait_if_paused(self) -> None:
        await self._resume.wait()


class SyntheticReplayAdapter:
    """Replays a generated match in (scaled) real time.

    Items before ``control.start_elapsed`` are delivered immediately, which
    lets a viewer join a match already in progress.
    """

    def __init__(self, match: SyntheticMatch, control: ReplayControl | None = None) -> None:
        self._match = match
        self.control = control or ReplayControl()
        self._items = feed_order(match.events, match.tracking)

    def meta(self) -> MatchMeta:
        return self._match.meta

    async def stream(self) -> AsyncIterator[FeedItem]:
        loop = asyncio.get_running_loop()
        anchor_wall = loop.time()
        anchor_elapsed = self.control.start_elapsed
        speed = self.control.speed
        for item in self._items:
            if item.elapsed > self.control.start_elapsed:
                await self.control.wait_if_paused()
                if self.control.speed != speed or self.control.paused:
                    # Re-anchor after a speed change or a pause.
                    anchor_wall, anchor_elapsed, speed = loop.time(), item.elapsed, self.control.speed
                due = anchor_wall + (item.elapsed - anchor_elapsed) / max(speed, 0.01)
                delay = due - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            yield item


class JsonLinesAdapter:
    """Reads a recorded feed: one JSON object per line with a "kind" field
    ("event" or "frame"). This is the shape a provider bridge would write after
    mapping its own format to the Matchlens schema."""

    def __init__(self, meta: MatchMeta, path: Path, speed: float = 1.0) -> None:
        self._meta = meta
        self._path = path
        self._speed = speed

    def meta(self) -> MatchMeta:
        return self._meta

    async def stream(self) -> AsyncIterator[FeedItem]:
        loop = asyncio.get_running_loop()
        start_wall: float | None = None
        start_elapsed = 0.0
        with self._path.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                raw = json.loads(line)
                kind = raw.pop("kind")
                item: FeedItem = Event.model_validate(raw) if kind == "event" else TrackingFrame.model_validate(raw)
                if start_wall is None:
                    start_wall, start_elapsed = loop.time(), item.elapsed
                delay = start_wall + (item.elapsed - start_elapsed) / self._speed - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                yield item


def write_json_lines(match: SyntheticMatch, path: Path) -> None:
    """Export a match as a JSON-lines feed (also the open dataset format)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for item in feed_order(match.events, match.tracking):
            kind = "event" if isinstance(item, Event) else "frame"
            fh.write(json.dumps({"kind": kind, **item.model_dump(mode="json")}) + "\n")
