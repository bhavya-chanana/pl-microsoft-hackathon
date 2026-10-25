"""Player tracks: boxes per frame, feet on the pitch, smoothed positions and speeds.

Arrays are indexed (frame, track): a track is one tracked person, not yet a
player or a team. NaN marks a frame where the track was not seen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .pitch import PitchProjector

MAX_SPEED_MPS = 12.0  # faster than any sprinter: a projection glitch, not a run
SMOOTH_FRAMES = 9  # about a third of a second at 25 fps, for positions
MAX_GAP_FRAMES = 25  # fill a track's gaps up to a second long


@dataclass(frozen=True)
class Tracks:
    fps: float
    ids: list[int]  # track ids, in column order
    boxes: np.ndarray  # (frames, tracks, 4): x, y, w, h in clip pixels

    @property
    def frames(self) -> int:
        return int(self.boxes.shape[0])

    def feet(self) -> np.ndarray:
        """Bottom centre of each box, (frames, tracks, 2)."""
        b = self.boxes
        return np.stack([b[..., 0] + b[..., 2] / 2, b[..., 1] + b[..., 3]], axis=-1)


def from_rows(rows: np.ndarray, fps: float, frames: int | None = None) -> Tracks:
    """Rows of ``frame, id, x, y, w, h`` (frames counted from 0) to a dense array."""
    rows = np.asarray(rows, dtype=np.float64)
    ids = sorted({int(i) for i in rows[:, 1]})
    column = {track_id: c for c, track_id in enumerate(ids)}
    count = int(rows[:, 0].max()) + 1 if frames is None else frames
    boxes = np.full((count, len(ids), 4), np.nan)
    for f, track_id, *box in rows[:, :6]:
        if 0 <= f < count:
            boxes[int(f), column[int(track_id)]] = box
    return Tracks(fps=fps, ids=ids, boxes=boxes)


def load_mot(path: Path, fps: float = 25.0, first_frame: int = 0) -> Tracks:
    """A MOT-format text file (``frame,id,x,y,w,h,...``). ``first_frame`` is the
    file's first frame number: 1 in the MOT challenge, 0 in SoccerTrack."""
    rows = np.loadtxt(path, delimiter=",", ndmin=2)
    rows[:, 0] -= first_frame
    return from_rows(rows, fps)


def project(tracks: Tracks, projector: PitchProjector) -> np.ndarray:
    """Each track's feet in pitch metres, (frames, tracks, 2)."""
    feet = tracks.feet()
    return projector.to_pitch(feet.reshape(-1, 2)).reshape(feet.shape)


def fill_gaps(positions: np.ndarray, max_gap: int = MAX_GAP_FRAMES) -> np.ndarray:
    """Linearly fill each track's gaps of up to ``max_gap`` frames between two sightings."""
    out = positions.copy()
    frames = np.arange(len(positions))
    for t in range(positions.shape[1]):
        seen = np.isfinite(positions[:, t, 0])
        if seen.sum() < 2:
            continue
        known = frames[seen]
        prev = known[np.clip(np.searchsorted(known, frames, side="right") - 1, 0, len(known) - 1)]
        nxt = known[np.clip(np.searchsorted(known, frames, side="left"), 0, len(known) - 1)]
        fill = ~seen & (prev < frames) & (nxt > frames) & (nxt - prev <= max_gap + 1)
        for axis in (0, 1):
            out[fill, t, axis] = np.interp(frames[fill], known, positions[seen, t, axis])
    return out


def smooth(positions: np.ndarray, window: int = SMOOTH_FRAMES) -> np.ndarray:
    """Centred moving average along time that ignores unseen frames."""
    seen = np.isfinite(positions)
    values = np.where(seen, positions, 0.0)
    kernel = np.ones(window)

    def run(a: np.ndarray) -> np.ndarray:
        return np.apply_along_axis(lambda s: np.convolve(s, kernel, mode="same"), 0, a)

    total, count = run(values), run(seen.astype(np.float64))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = total / count
    out[~seen] = np.nan
    return out


def speeds(positions: np.ndarray, fps: float) -> np.ndarray:
    """Speed in m/s per frame and track, from positions smoothed over a second
    (box jitter makes anything shorter read as 40 km/h sprints)."""
    step = np.gradient(smooth(positions, int(fps) | 1), axis=0) * fps
    speed = np.hypot(step[..., 0], step[..., 1])
    return np.where(speed > MAX_SPEED_MPS, np.nan, speed)


def per_second(positions: np.ndarray, fps: float) -> tuple[np.ndarray, np.ndarray]:
    """Positions at each whole second (seconds, tracks, 2) and the speed over the
    second before it (seconds, tracks). The stats engine adds one frame's speed per
    second as distance, so the speed is the distance actually covered."""
    smoothed = smooth(positions)
    step = int(round(fps))
    at = smoothed[::step]
    speed = np.hypot(*np.moveaxis(np.diff(at, axis=0, prepend=at[:1]), -1, 0)) * fps / step
    return at, np.where(speed > MAX_SPEED_MPS, np.nan, speed)
