"""How well detected events and tracks match hand-labelled ones.

A labelled action is found when a detection of the same kind, by the same team,
falls within the tolerance of it; each detection matches at most one label.
Carries are compared at their start, since a label marks when the run began.

Tracks are scored the MOT way: boxes matched one-to-one per frame at IoU 0.5,
misses, false positives and identity switches folded into MOTA.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from matchlens.domain.models import EventType

from .events import Detected
from .tracker import iou
from .tracks import Tracks

# SoccerTrack ball-action labels -> Matchlens event types. Labels with no
# counterpart (OUT, HEADER, BALL PLAYER BLOCK, GOAL) are not scored.
LABELS = {
    "PASS": EventType.PASS, "HIGH PASS": EventType.PASS, "CROSS": EventType.PASS,
    "DRIVE": EventType.CARRY, "SHOT": EventType.SHOT, "THROW IN": EventType.THROW_IN,
    "FREE KICK": EventType.FREE_KICK, "PLAYER SUCCESSFUL TACKLE": EventType.TACKLE,
}


@dataclass(frozen=True)
class Labelled:
    frame: int
    type: EventType
    team: int


@dataclass(frozen=True)
class Score:
    type: EventType
    labelled: int
    detected: int
    matched: int

    @property
    def recall(self) -> float:
        return self.matched / self.labelled if self.labelled else 0.0

    @property
    def precision(self) -> float:
        return self.matched / self.detected if self.detected else 0.0


def _when(d: Detected, fps: float) -> float:
    return d.frame - float(d.attrs.get("duration_s", 0.0)) * fps if d.type == EventType.CARRY else d.frame


def score(detected: Iterable[Detected], labelled: Iterable[Labelled], fps: float,
          tolerance_s: float = 1.0) -> list[Score]:
    detected, labelled = list(detected), list(labelled)
    scores = []
    for kind in dict.fromkeys(LABELS.values()):
        mine = sorted((d for d in detected if d.type == kind), key=lambda d: _when(d, fps))
        theirs = [lab for lab in labelled if lab.type == kind]
        used: set[int] = set()
        matched = 0
        for lab in sorted(theirs, key=lambda lab: lab.frame):
            best = min(((abs(_when(d, fps) - lab.frame), i) for i, d in enumerate(mine)
                        if i not in used and d.team == lab.team), default=None)
            if best is not None and best[0] <= tolerance_s * fps:
                used.add(best[1])
                matched += 1
        scores.append(Score(kind, len(theirs), len(mine), matched))
    return scores


@dataclass(frozen=True)
class TrackScore:
    frames: int
    truth: int  # labelled boxes
    matched: int
    missed: int
    false: int
    switches: int
    purity: float  # mean share of each labelled player's matched frames under their main track id

    @property
    def recall(self) -> float:
        return self.matched / self.truth if self.truth else 0.0

    @property
    def precision(self) -> float:
        return self.matched / (self.matched + self.false) if self.matched + self.false else 0.0

    @property
    def mota(self) -> float:
        return 1 - (self.missed + self.false + self.switches) / self.truth if self.truth else 0.0


def _xyxy(boxes: np.ndarray) -> np.ndarray:
    return np.c_[boxes[:, :2], boxes[:, :2] + boxes[:, 2:]]


def score_tracks(found: Tracks, truth: Tracks, frames: Iterable[int], min_iou: float = 0.5) -> TrackScore:
    """Score tracks against labelled boxes on the given frames (the ones the detector ran on)."""
    totals = Counter()
    last: dict[int, int] = {}
    owners: dict[int, Counter] = {}
    count = 0
    for f in frames:
        count += 1
        t_seen = np.flatnonzero(np.isfinite(truth.boxes[f, :, 0]))
        f_seen = np.flatnonzero(np.isfinite(found.boxes[f, :, 0]))
        totals["truth"] += len(t_seen)
        pairs: list[tuple[int, int]] = []
        if len(t_seen) and len(f_seen):
            overlap = iou(_xyxy(truth.boxes[f, t_seen]), _xyxy(found.boxes[f, f_seen]))
            rows, cols = linear_sum_assignment(-overlap)
            pairs = [(int(t_seen[r]), int(f_seen[c])) for r, c in zip(rows, cols, strict=True)
                     if overlap[r, c] >= min_iou]
        totals["matched"] += len(pairs)
        totals["missed"] += len(t_seen) - len(pairs)
        totals["false"] += len(f_seen) - len(pairs)
        for t, c in pairs:
            track_id = found.ids[c]
            if t in last and last[t] != track_id:
                totals["switches"] += 1
            last[t] = track_id
            owners.setdefault(t, Counter())[track_id] += 1
    purity = float(np.mean([c.most_common(1)[0][1] / sum(c.values()) for c in owners.values()])) if owners else 0.0
    return TrackScore(frames=count, truth=totals["truth"], matched=totals["matched"], missed=totals["missed"],
                      false=totals["false"], switches=totals["switches"], purity=purity)
