"""Follow detections from frame to frame, after ByteTrack (Zhang et al., 2022).

Each track predicts its next box from its last movement. Confident detections
are matched to tracks first (IoU, optimal assignment); weak ones, often a player
half hidden behind another, may then continue tracks left unmatched rather than
start new ones. A track not seen for a second is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

HIGH, LOW = 0.5, 0.25  # detection scores: confident, and the least worth keeping
MATCH_IOU = 0.2  # small boxes at 25 fps barely overlap when players sprint
LOST_FRAMES = 25


def iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU of xyxy boxes, (len(a), len(b))."""
    ix = np.clip(np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0]), 0, None)
    iy = np.clip(np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1]), 0, None)
    inter = ix * iy
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None] - inter, 1e-9)


@dataclass
class _Track:
    track_id: int
    box: np.ndarray  # xyxy
    velocity: np.ndarray  # xyxy change per frame
    seen: int  # last frame it was matched

    def predict(self, frame: int) -> np.ndarray:
        return self.box + self.velocity * (frame - self.seen)


class Tracker:
    def __init__(self) -> None:
        self._tracks: list[_Track] = []
        self._next_id = 1

    def update(self, frame: int, detections: np.ndarray) -> list[tuple[int, np.ndarray]]:
        """Detections (N, 5) xyxy + score at ``frame``; returns (track id, box) for each matched or new track."""
        self._tracks = [t for t in self._tracks if frame - t.seen <= LOST_FRAMES]
        out: list[tuple[int, np.ndarray]] = []
        detections = detections[detections[:, 4] >= LOW] if len(detections) else np.zeros((0, 5))
        strong = detections[detections[:, 4] >= HIGH]
        weak = detections[detections[:, 4] < HIGH]
        unmatched = list(range(len(self._tracks)))
        for pool, may_start in ((strong, True), (weak, False)):
            if not len(pool):
                continue
            left = self._match(frame, pool, unmatched, out)
            if may_start:
                for d in left:
                    self._tracks.append(_Track(self._next_id, pool[d, :4].copy(), np.zeros(4), frame))
                    out.append((self._next_id, pool[d, :4]))
                    self._next_id += 1
        return out

    def _match(self, frame: int, pool: np.ndarray, unmatched: list[int],
               out: list[tuple[int, np.ndarray]]) -> list[int]:
        """Match ``pool`` to the still unmatched tracks; returns the detections left over."""
        if not unmatched:
            return list(range(len(pool)))
        predicted = np.array([self._tracks[t].predict(frame) for t in unmatched])
        overlap = iou(predicted, pool[:, :4])
        rows, cols = linear_sum_assignment(-overlap)
        used = set()
        for r, c in zip(rows, cols, strict=True):
            if overlap[r, c] < MATCH_IOU:
                continue
            track = self._tracks[unmatched[r]]
            gap = max(frame - track.seen, 1)
            track.velocity = 0.5 * track.velocity + 0.5 * (pool[c, :4] - track.box) / gap
            track.box, track.seen = pool[c, :4].copy(), frame
            out.append((track.track_id, pool[c, :4]))
            used.add(int(c))
        matched = {unmatched[r] for r, c in zip(rows, cols, strict=True) if int(c) in used}
        unmatched[:] = [t for t in unmatched if t not in matched]
        return [d for d in range(len(pool)) if d not in used]
