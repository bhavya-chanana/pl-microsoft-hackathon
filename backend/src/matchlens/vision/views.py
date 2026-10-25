"""Which frames of a broadcast come from the main camera.

A broadcast cuts between the main (wide, high, side-on) camera, close-ups,
other angles, the crowd, graphics and replays. Only the main camera shows
enough of the pitch to calibrate and track players on, so the rest is set
aside.

Two passes over small frames. Cuts are jumps in the colour histogram between
consecutive frames. Each frame is then read for how much of it is grass and
how big the largest thing standing on the grass is: on the main camera most
of the frame is pitch and players are small; a close-up has a person filling
the frame, a crowd shot has no grass. A shot (the frames between two cuts)
takes the majority verdict of its frames, so a passing graphic or a player
running past the lens does not split it.

Replays from the main camera's angle look like live play and are not caught
here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise

import cv2
import numpy as np

from .lines import field_mask, grass_mask

SMALL_WIDTH = 320
HIST_BINS = (16, 8)  # hue, saturation
CUT_DISTANCE = 0.45  # Bhattacharyya distance between consecutive frames' histograms
MIN_SHOT_FRAMES = 6  # a cut this soon after another is a flash or a wipe, not a new shot
MIN_GRASS = 0.35  # share of the frame
MAX_FIGURE = 0.025  # share of the frame the largest non-grass blob on the field may cover


@dataclass(frozen=True)
class FrameLook:
    grass: float  # share of the frame that is grass
    figure: float  # share of the frame covered by the largest non-grass blob on the field
    hist: np.ndarray

    @property
    def main(self) -> bool:
        return self.grass >= MIN_GRASS and self.figure <= MAX_FIGURE


@dataclass(frozen=True)
class Views:
    main: np.ndarray  # (frames,) bool, per shot
    cuts: list[int]  # first frame of every shot after the first
    looks: list[FrameLook]

    def shots(self) -> list[range]:
        edges = [0, *self.cuts, len(self.main)]
        return [range(a, b) for a, b in pairwise(edges) if b > a]


def look(bgr: np.ndarray) -> FrameLook:
    h, w = bgr.shape[:2]
    small = cv2.resize(bgr, (SMALL_WIDTH, max(int(h * SMALL_WIDTH / w), 1)), interpolation=cv2.INTER_AREA)
    grass = grass_mask(small)
    field = field_mask(small, grass)
    standing = cv2.bitwise_and(field, cv2.bitwise_not(grass))
    standing = cv2.morphologyEx(standing, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(standing, connectivity=8)
    figure = float(stats[1:, cv2.CC_STAT_AREA].max()) / grass.size if n > 1 else 0.0
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, list(HIST_BINS), [0, 180, 0, 256])
    cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
    return FrameLook(grass=float(np.count_nonzero(grass)) / grass.size, figure=figure, hist=hist)


def cuts(looks: list[FrameLook]) -> list[int]:
    out: list[int] = []
    for i in range(1, len(looks)):
        jump = cv2.compareHist(looks[i - 1].hist, looks[i].hist, cv2.HISTCMP_BHATTACHARYYA)
        if jump > CUT_DISTANCE and i - (out[-1] if out else 0) >= MIN_SHOT_FRAMES:
            out.append(i)
    return out


def classify(frames: Iterable[np.ndarray]) -> Views:
    looks = [look(f) for f in frames]
    found = cuts(looks)
    main = np.zeros(len(looks), bool)
    views = Views(main=main, cuts=found, looks=looks)
    for shot in views.shots():
        votes = [looks[i].main for i in shot]
        main[shot.start:shot.stop] = sum(votes) * 2 > len(votes)
    return views
