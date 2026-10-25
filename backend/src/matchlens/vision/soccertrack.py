"""SoccerTrack v2 (CC BY 4.0) files for one match, as downloaded under
``backend/data/local/soccertrack/<match>/``.

Only the arrays and JSON are read: the camera-intrinsics ``.npz`` is pickled and
is never loaded. Real team and player names in the metadata are never read
either; a video run gets fictional names (BRD §9.3).

Frames: a clip starts at its half's first frame (verified on 128057: the players
stand in the kick-off shape at frame 0). The ball file and the action labels
count frames from 1, the MOT tracks from 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH

from .pitch import PitchProjector
from .tracks import Tracks, load_mot

FPS = 25.0
# Ball status, inferred from the action labels (each team's actions fall in its own status):
DEAD, RIGHT_TEAM, LEFT_TEAM, LOOSE = 0, 1, 2, 3


@dataclass(frozen=True)
class Ball:
    positions: np.ndarray  # (frames, 2) pitch metres
    status: np.ndarray  # (frames,) DEAD, RIGHT_TEAM, LEFT_TEAM or LOOSE


@dataclass(frozen=True)
class Action:
    frame: int  # clip frame, counted from 0
    label: str  # "PASS", "DRIVE", "SHOT", ...
    side: str  # "left" or "right": the team attacking from that side of the clip
    player_id: str


@dataclass(frozen=True)
class SoccerTrackMatch:
    root: Path
    match: str

    def _path(self, *parts: str) -> Path:
        return self.root / self.match / Path(*parts)

    @property
    def clip(self) -> Path:
        return self._path("mot", "clips", f"{self.match}.mp4")

    def projector(self) -> PitchProjector:
        raw = ("raw", self.match)
        return PitchProjector.from_fixed_maps(
            np.load(self._path(*raw, f"{self.match}_mapx.npy")),
            np.load(self._path(*raw, f"{self.match}_mapy.npy")),
            np.load(self._path(*raw, f"{self.match}_homography.npy")),
        )

    def tracks(self) -> Tracks:
        """Ground-truth player boxes for the clip."""
        return load_mot(self._path("mot", f"{self.match}.txt"), fps=FPS, first_frame=0)

    def ball(self, frames: int, half: int = 1) -> Ball:
        name = "1st" if half == 1 else "2nd"
        with np.load(self._path("ball", f"{self.match}_{name}_ball.npz"), allow_pickle=False) as z:
            x, y, status = z["x"][:frames], z["y"][:frames], z["status"][:frames]
        positions = np.c_[x + PITCH_LENGTH / 2, y + PITCH_WIDTH / 2].astype(np.float64)
        return Ball(positions=positions, status=status.astype(np.int8))

    def actions(self, frames: int, half: int = 1) -> list[Action]:
        """Hand-labelled ball actions inside the clip."""
        path = self._path("bas", self.match, f"{self.match}_12_class_events.json")
        raw = json.loads(path.read_text())["actions"]
        return [Action(frame=a["frame"] - 1, label=a["label"], side=a["team"], player_id=str(a.get("player_id", "")))
                for a in raw if a["gameTime"].startswith(f"{half} ") and 0 <= a["frame"] - 1 < frames]
