"""Producer scheduling: on-screen caps for broadcast overlays (FR-030 to FR-034).

At most one graphic per screen slot at a time. A new graphic waits briefly
for a busy slot, pre-empts a lower-priority one, or is dropped, so the
screen never stacks graphics on top of play.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from matchlens.agents.contracts import Overlay, OverlayKind

SLOT_FOR = {
    OverlayKind.LOWER_THIRD: "lower", OverlayKind.SHOT_CARD: "lower", OverlayKind.PLAYER_CARD: "lower",
    OverlayKind.MOMENTUM_BAR: "upper_right", OverlayKind.STAT_CARD: "upper_right",
    OverlayKind.MILESTONE_BANNER: "banner", OverlayKind.CORRECTION_BANNER: "banner",
}
MAX_WAIT_S = 10.0
MIN_DURATION_S, MAX_DURATION_S = 4.0, 12.0


@dataclass
class _Booking:
    overlay_id: str
    start: float
    end: float
    priority: int


@dataclass
class OverlayScheduler:
    bookings: dict[str, list[_Booking]] = field(default_factory=dict)
    trimmed: dict[str, float] = field(default_factory=dict)  # overlay_id -> new duration after pre-emption

    def snapshot(self) -> dict[str, Any]:
        return {"bookings": {slot: [asdict(b) for b in books] for slot, books in self.bookings.items()},
                "trimmed": dict(self.trimmed)}

    def restore(self, state: dict[str, Any]) -> None:
        self.bookings = {slot: [_Booking(**b) for b in books] for slot, books in state.get("bookings", {}).items()}
        self.trimmed = dict(state.get("trimmed", {}))

    def take_trimmed(self) -> dict[str, float]:
        out, self.trimmed = self.trimmed, {}
        return out

    def place(self, overlay: Overlay, now: float) -> Overlay | None:
        """Return the overlay with its final timing, or None if it should not air.

        An agent upgrade of a graphic that is on screen keeps its slot and start.
        If the data card already finished, important moments (priority 1-2) air
        again with the richer text; minor ones are updated as a record only.
        """
        slot = SLOT_FOR[overlay.kind]
        duration = min(MAX_DURATION_S, max(MIN_DURATION_S, overlay.duration_s))
        books = self.bookings.setdefault(slot, [])
        existing = next((b for b in books if b.overlay_id == overlay.overlay_id), None)
        if existing is not None:
            if now <= existing.end:
                later = [b.start for b in books if b is not existing and b.start >= existing.start]
                end = min([existing.start + duration, *later])
                existing.end = end
                return overlay.model_copy(update={"start_elapsed": existing.start,
                                                  "duration_s": end - existing.start, "slot": slot})
            if overlay.priority > 2:
                return overlay.model_copy(update={"start_elapsed": existing.start,
                                                  "duration_s": existing.end - existing.start, "slot": slot})
            books.remove(existing)
            overlay = overlay.model_copy(update={"start_elapsed": now})
        start = max(overlay.start_elapsed, now - 2)
        books[:] = [b for b in books if b.end > start - 30]
        for b in sorted(books, key=lambda b: b.start):
            if b.start < start + duration and start < b.end:  # overlap
                if overlay.priority < b.priority and b.start <= start:
                    b.end = start  # pre-empt the less important graphic
                    self.trimmed[b.overlay_id] = max(0.0, b.end - b.start)
                elif b.end - overlay.start_elapsed <= MAX_WAIT_S:
                    start = b.end
                else:
                    return None
        books.append(_Booking(overlay.overlay_id, start, start + duration, overlay.priority))
        return overlay.model_copy(update={"start_elapsed": start, "duration_s": duration, "slot": slot})
