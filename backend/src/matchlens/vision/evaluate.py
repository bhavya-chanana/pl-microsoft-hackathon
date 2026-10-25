"""How well detected events match hand-labelled ones.

A labelled action is found when a detection of the same kind, by the same team,
falls within the tolerance of it; each detection matches at most one label.
Carries are compared at their start, since a label marks when the run began.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from matchlens.domain.models import EventType

from .events import Detected

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
