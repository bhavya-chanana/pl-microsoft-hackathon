"""Frame documents and rolling statistics for the video chain (no OpenCV needed).

Coordinates in a frame document are metres on a 105 x 68 pitch with the origin at the pitch centre: x in
[-52.5, 52.5] runs left to right as the main camera sees it, y in [-34, 34] runs from the far touchline to the near
one (down the screen). The chain works in the vision package's corner-origin metres (``vision/markings.py``) and
converts here.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

PITCH_L, PITCH_W = 105.0, 68.0
READY_CALIBRATED = 0.5  # share of main-camera frames with a calibration
READY_ON_PITCH = 0.8  # share of detected people that project onto the pitch
TEAMS = ("home", "away", "ref", "ball", "unk")


def centre_origin(x: float, y: float) -> tuple[float, float]:
    """Corner-origin metres (0..105, 0..68) to centre-origin metres."""
    return x - PITCH_L / 2, y - PITCH_W / 2


def entity(ident: str, team: str, x: float, y: float, *, observed: bool = True, conf: float = 1.0) -> dict[str, Any]:
    """An entity already in centre-origin metres."""
    return {"id": ident, "team": team, "x": round(float(x), 2), "y": round(float(y), 2), "observed": bool(observed),
            "conf": round(float(conf), 3)}


def frame_doc(match_id: str, t_s: float, entities: list[dict[str, Any]], calibrated: bool, *,
              match_clock_s: float | None = None, wall_utc: str | None = None, video_id: str | None = None,
              view: str = "main", calib_status: str | None = None) -> dict[str, Any]:
    t_s = round(float(t_s), 3)
    doc: dict[str, Any] = {"id": f"{match_id}:{round(t_s * 1000)}", "match_id": match_id, "t_s": t_s,
                           "match_clock_s": match_clock_s, "entities": entities, "calibrated": bool(calibrated),
                           "view": view}
    if calib_status:
        doc["calib_status"] = calib_status  # "fitted" | "interpolated" | "none" | "frame" (per-frame fallback)
    if wall_utc:
        doc["wall_utc"] = wall_utc
    if video_id:
        doc["video_id"] = video_id
    return doc


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass
class FrameStat:
    main: bool = False
    calibrated: bool = False
    people: int = 0  # detections in a calibrated frame
    on_pitch: int = 0  # of which project onto the pitch (give or take a metre or three)


@dataclass
class Counts:
    frames: int = 0
    main: int = 0
    calibrated: int = 0
    people: int = 0
    on_pitch: int = 0
    seconds: float = 0.0  # wall time spent

    def add(self, s: FrameStat, seconds: float) -> None:
        self.frames += 1
        self.main += s.main
        self.calibrated += s.calibrated
        self.people += s.people
        self.on_pitch += s.on_pitch
        self.seconds += seconds

    def report(self) -> dict[str, Any]:
        return {"frames": self.frames, "main_frames": self.main, "calibrated_frames": self.calibrated,
                "main_share": _ratio(self.main, self.frames),
                "calibrated_share": _ratio(self.calibrated, self.main),
                "people": self.people, "on_pitch": self.on_pitch,
                "on_pitch_rate": _ratio(self.on_pitch, self.people),
                "fps": round(self.frames / self.seconds, 2) if self.seconds > 0 else None}


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def is_ready(calibrated_share: float | None, on_pitch_rate: float | None) -> bool:
    return (calibrated_share is not None and on_pitch_rate is not None
            and calibrated_share >= READY_CALIBRATED and on_pitch_rate >= READY_ON_PITCH)


@dataclass
class RollingStats:
    """Per-window and cumulative numbers for a run; ``due`` says when a window of ``every_s`` is over."""

    every_s: float = 10.0
    window: Counts = field(default_factory=Counts)
    total: Counts = field(default_factory=Counts)
    _since: float = field(default_factory=time.monotonic)
    _history: deque[tuple[float | None, float | None]] = field(default_factory=lambda: deque(maxlen=3))

    def record(self, stat: FrameStat, seconds: float) -> None:
        self.window.add(stat, seconds)
        self.total.add(stat, seconds)

    def due(self) -> bool:
        return time.monotonic() - self._since >= self.every_s

    def snapshot(self, t_s: float) -> dict[str, Any]:
        """The window just ended plus the run so far; starts the next window."""
        w, t = self.window.report(), self.total.report()
        out = {"t_s": round(t_s, 3), "window_s": round(time.monotonic() - self._since, 1), **w,
               "live_ready": is_ready(w["calibrated_share"], w["on_pitch_rate"]),
               "total": t, "total_live_ready": is_ready(t["calibrated_share"], t["on_pitch_rate"])}
        self.window = Counts()
        self._since = time.monotonic()
        return out
