"""Split tracks into two teams by kit colour.

Each track's shirt colour is the median, over sampled frames, of its torso
pixels that are not grass (CIE Lab, so distances follow what the eye sees).
Two-means over those colours gives the teams. A track far from both kits is a
goalkeeper when it stays near a goal, and otherwise someone else (a referee) to
leave out. The team that starts on the left attacks right: it becomes home.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from matchlens.domain.models import PITCH_LENGTH

from .tracks import Tracks

LEFT, RIGHT, OTHER = 0, 1, -1
# Torso: below the head, above the shorts, away from the box edges.
TORSO_ROWS, TORSO_COLS = (0.15, 0.55), (0.2, 0.8)
GRASS_HUE, GRASS_MIN_SAT = (30, 90), 40  # OpenCV hue runs 0-180
MIN_PIXELS = 6
# A kit further than this from its team's (Lab units, and times the team's spread) is not that kit.
OUTLIER_LAB, OUTLIER_SPREAD = 25.0, 3.0
KEEPER_ZONE_M = 25.0  # a keeper's median position stays this close to a goal line


@dataclass(frozen=True)
class TeamSplit:
    labels: np.ndarray  # (tracks,) LEFT, RIGHT or OTHER
    keepers: frozenset[int]  # track columns
    kits: tuple[str, str]  # hex colour of the left and the right team's shirts


def shirt_colour(crop: np.ndarray) -> np.ndarray | None:
    """Median Lab colour of a player crop's torso (BGR image), or None when only grass shows."""
    h, w = crop.shape[:2]
    rows = slice(int(h * TORSO_ROWS[0]), max(int(h * TORSO_ROWS[1]), 1))
    torso = crop[rows, int(w * TORSO_COLS[0]):max(int(w * TORSO_COLS[1]), 1)]
    if torso.size == 0:
        return None
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    grass = (hsv[:, 0] >= GRASS_HUE[0]) & (hsv[:, 0] <= GRASS_HUE[1]) & (hsv[:, 1] >= GRASS_MIN_SAT)
    lab = cv2.cvtColor(torso, cv2.COLOR_BGR2LAB).reshape(-1, 3)[~grass]
    return np.median(lab, axis=0).astype(np.float64) if len(lab) >= MIN_PIXELS else None


def sample_frames(frames: int, samples: int) -> list[int]:
    return sorted({int(f) for f in np.linspace(0, frames - 1, samples)})


def track_colours(video: Path, tracks: Tracks, frames: Sequence[int]) -> np.ndarray:
    """Median shirt colour per track over the given frames, (tracks, 3) Lab; NaN if never seen."""
    found: list[list[np.ndarray]] = [[] for _ in tracks.ids]
    capture = cv2.VideoCapture(str(video))
    try:
        for f in frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, image = capture.read()
            if not ok:
                continue
            for t, (x, y, w, h) in enumerate(tracks.boxes[f]):
                if not np.isfinite(x):
                    continue
                x0, y0 = max(int(x), 0), max(int(y), 0)
                colour = shirt_colour(image[y0:int(y + h), x0:int(x + w)])
                if colour is not None:
                    found[t].append(colour)
    finally:
        capture.release()
    return np.array([np.median(c, axis=0) if c else np.full(3, np.nan) for c in found])


def two_means(points: np.ndarray, rounds: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic 2-means: start from the two most different points."""
    gaps = np.linalg.norm(points[:, None] - points[None], axis=2)
    a, b = np.unravel_index(np.argmax(gaps), gaps.shape)
    centres = points[[a, b]].copy()
    labels = np.zeros(len(points), dtype=int)
    for _ in range(rounds):
        labels = np.linalg.norm(points[:, None] - centres[None], axis=2).argmin(axis=1)
        updated = np.array([points[labels == k].mean(axis=0) if (labels == k).any() else centres[k] for k in (0, 1)])
        if np.allclose(updated, centres):
            break
        centres = updated
    return labels, centres


def _hex(lab: np.ndarray) -> str:
    bgr = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8).reshape(1, 1, 3), cv2.COLOR_LAB2BGR)[0, 0]
    return "#{:02x}{:02x}{:02x}".format(*bgr[::-1])


def split_teams(colours: np.ndarray, median_x: np.ndarray) -> TeamSplit:
    """Teams from each track's shirt colour (tracks, 3) and median pitch x (tracks,)."""
    labels = np.full(len(colours), OTHER)
    usable = np.flatnonzero(np.isfinite(colours).all(axis=1) & np.isfinite(median_x))
    if len(usable) < 2:
        raise ValueError("not enough tracks with a visible shirt to split teams")
    kit = usable
    for _ in range(3):  # drop odd kits (keepers, referees) and split again
        team, centres = two_means(colours[kit])
        dist = np.linalg.norm(colours[kit] - centres[team], axis=1)
        spread = np.array([np.median(dist[team == k]) if (team == k).any() else 0.0 for k in (0, 1)])
        odd = dist > np.maximum(OUTLIER_LAB, OUTLIER_SPREAD * spread[team])
        if not odd.any():
            break
        kit = kit[~odd]
    team, centres = two_means(colours[kit])
    if np.median(median_x[kit][team == 0]) > np.median(median_x[kit][team == 1]):
        team, centres = 1 - team, centres[::-1]  # LEFT is the team on the left
    labels[kit] = team
    keepers = set()
    for t in np.setdiff1d(usable, kit):
        if median_x[t] <= KEEPER_ZONE_M:
            labels[t] = LEFT
        elif median_x[t] >= PITCH_LENGTH - KEEPER_ZONE_M:
            labels[t] = RIGHT
        else:
            continue  # a referee, or a track too mixed to call
        keepers.add(int(t))
    return TeamSplit(labels=labels, keepers=frozenset(keepers), kits=(_hex(centres[0]), _hex(centres[1])))
