"""The per-frame video chain shared by the live runner and the video jobs.

    frame -> main-camera gate (vision/views.py) -> camera calibration (vision/camera.py, or a whole-match
    solution from vision/bundle.py) -> person and ball detection (vision/detect.py) -> tracking in pitch
    coordinates (vision/tracker.py) -> team split by kit colour (vision/teams.py) -> frame document

Frames go in at the sampling rate (5 Hz); each comes out as a :class:`Result`. Needs the ``vision`` dependency group.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from matchlens.vision.broadcast import MIN_ANCHOR_POINTS, WORK_WIDTH, read_labels
from matchlens.vision.camera import CameraTracker, plausible, project, scaled, to_pitch
from matchlens.vision.teams import LEFT, OTHER, OUTLIER_LAB, RIGHT, shirt_colour, split_teams
from matchlens.vision.tracker import HIGH, Tracker
from matchlens.vision.views import CUT_DISTANCE, FrameLook, look

from .frames import PITCH_L, PITCH_W, FrameStat, RollingStats, centre_origin, entity, frame_doc, utc_now

log = logging.getLogger(__name__)

MARGIN_M = 3.0  # feet this far outside the lines still count as on the pitch (throw-ins, staff on the line)
BOX_M = 4.0  # a player's box in the tracker (pitch metres): big enough to overlap one 5 Hz step later
HEAD_PX = 110  # room above the far touchline for a player's head, at detection size
MIN_BAND_PX = 160
BALL_MIN_SCORE = 0.3
MIN_SHOT_FRAMES = 2  # a cut this soon after another is a flash (6 frames at 25 fps in vision/views.py)
SHOT_WINDOW = 10
SAMPLES_PER_TRACK = 12
MIN_SAMPLES = 3
MIN_TRACKS_FOR_SPLIT = 8
RESPLIT_EVERY_S = 10.0
MAX_WARMUP_S = 30.0  # frames wait this long at most for the first team split
Detections = tuple[np.ndarray, np.ndarray]  # people (N,5), balls (M,5): xyxy + score, detection-frame pixels


class Detector(Protocol):
    tiling: Any

    def __call__(self, image: np.ndarray) -> Detections: ...


class Calibrator(Protocol):
    def step(self, bgr960: np.ndarray, *, t_s: float, main: bool, cut: bool) -> np.ndarray | None:
        """Pitch metres (corner origin) -> pixels of the 960-wide frame, or None."""


# ---------------------------------------------------------------------------------------------- the main-camera gate


class ViewGate:
    """``vision/views.py`` frame by frame: cuts are jumps in the colour histogram; a shot takes the majority view
    of its frames so far (at most the last ``SHOT_WINDOW``)."""

    def __init__(self) -> None:
        self._prev: FrameLook | None = None
        self._votes: deque[bool] = deque(maxlen=SHOT_WINDOW)
        self._since_cut = 10**9

    def update(self, bgr960: np.ndarray) -> tuple[bool, bool]:
        """(is the main camera, a new shot started here)."""
        now = look(bgr960)
        cut = False
        if self._prev is not None:
            jump = cv2.compareHist(self._prev.hist, now.hist, cv2.HISTCMP_BHATTACHARYYA)
            cut = jump > CUT_DISTANCE and self._since_cut >= MIN_SHOT_FRAMES
        if cut:
            self._votes.clear()
            self._since_cut = 0
        self._since_cut += 1
        self._prev = now
        self._votes.append(now.main)
        return sum(self._votes) * 2 > len(self._votes), cut


# ---------------------------------------------------------------------------------------------------- calibrators


class TrackerCalibrator:
    """Per-frame calibration with ``CameraTracker``. It starts from keyframes (labelled frames of the clip) or from
    venue guesses for its first main-camera frame."""

    def __init__(self, anchors: list[tuple[np.ndarray, np.ndarray]] | None = None, *, venue: str | None = None) -> None:
        self.tracker = CameraTracker()
        self.venue = venue
        self._guessed = venue is None
        for image, h in anchors or []:
            self.tracker.seed(image, h)

    def _guess(self, bgr960: np.ndarray) -> list[np.ndarray]:
        try:
            from matchlens.vision import bundle  # phd-student's module; absent or without the venue => no guess
            return list(bundle.venue_guesses(self.venue, bgr960))
        except Exception as exc:
            log.warning("no venue guess for %s: %s", self.venue, exc)
            return []

    def step(self, bgr960: np.ndarray, *, t_s: float, main: bool, cut: bool) -> np.ndarray | None:
        anchor = None
        if main and not self._guessed and self.tracker._last is None:
            guesses = self._guess(bgr960)
            if guesses:
                anchor = guesses[0]
            self._guessed = bool(guesses)
        cal = self.tracker.step(bgr960, main=main, cut=cut, anchor=anchor)
        return None if cal is None else cal.homography


class BundleCalibrator:
    """A whole-match solution (``vision.bundle.solve_match`` / ``solve_video``): homographies by video time."""

    def __init__(self, solution: Any) -> None:
        self.solution = solution

    def step(self, bgr960: np.ndarray, *, t_s: float, main: bool, cut: bool) -> np.ndarray | None:
        if not main:
            return None
        return self.solution.homography_at(t_s)

    def image_to_pitch(self, t_s: float, pixels960: np.ndarray) -> np.ndarray | None:
        """Raw pixels (960 wide) to pitch metres with the solution's lens distortion, when it has one."""
        fn = getattr(self.solution, "image_to_pitch_at", None)
        return None if fn is None else np.asarray(fn(t_s, pixels960), np.float64).reshape(-1, 2)


def anchors_from_labels(labels: Path, video: Path, work_width: int = WORK_WIDTH) -> list[tuple[np.ndarray, np.ndarray]]:
    """Labelled main-camera frames of ``video`` (``python -m matchlens.vision.label``) as (image, homography) at
    ``work_width``: the way the calibration is started on a known clip."""
    capture = cv2.VideoCapture(str(video))
    try:
        size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        if size[0] <= 0:
            return []
        factor = work_width / size[0]
        out = []
        for lab in read_labels(labels):
            if not lab.main or len(lab.names) < MIN_ANCHOR_POINTS:
                continue
            h, _ = cv2.findHomography(lab.pitch, lab.pixels, 0)
            if h is None or not plausible(h, (size[1], size[0])):
                continue
            capture.set(cv2.CAP_PROP_POS_FRAMES, lab.frame)
            ok, image = capture.read()
            if ok:
                out.append((cv2.resize(image, (work_width, round(size[1] * factor)), interpolation=cv2.INTER_AREA),
                            scaled(h, factor)))
        return out
    finally:
        capture.release()


# ------------------------------------------------------------------------------------------------------- teams


class TeamModel:
    """Kit colours per track -> home / away / ref, refreshed as tracks accumulate samples.

    The team on the left of the pitch is ``home`` (``vision/teams.py``), unless ``swap``."""

    def __init__(self, *, swap: bool = False, centres: np.ndarray | None = None) -> None:
        self.names = ("away", "home") if swap else ("home", "away")
        self.colours: dict[int, list[np.ndarray]] = defaultdict(list)
        self.xs: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self.team: dict[int, str] = {}
        self.kits: tuple[str, str] | None = None
        self.centres = centres  # (2, 3) Lab, LEFT then RIGHT
        self._last = -1e9
        self.ready = False

    def observe(self, track: int, x_m: float, crop: np.ndarray | None) -> None:
        self.xs[track].append(x_m)
        if crop is not None and len(self.colours[track]) < SAMPLES_PER_TRACK:
            colour = shirt_colour(crop)
            if colour is not None:
                self.colours[track].append(colour)

    def refresh(self, t_s: float) -> None:
        if t_s - self._last < RESPLIT_EVERY_S and self.ready:
            return
        tracks = [t for t, c in self.colours.items() if len(c) >= MIN_SAMPLES and len(self.xs[t]) >= MIN_SAMPLES]
        if len(tracks) < MIN_TRACKS_FOR_SPLIT:
            return
        self._last = t_s
        colours = np.array([np.median(self.colours[t], axis=0) for t in tracks])
        median_x = np.array([np.median(self.xs[t]) for t in tracks])
        try:
            split = split_teams(colours, median_x)
        except ValueError:
            return
        labels, kits = split.labels.copy(), split.kits
        outfield = ~np.isin(np.arange(len(tracks)), list(split.keepers))
        centres = np.array([np.median(colours[(labels == side) & outfield], axis=0)
                            if ((labels == side) & outfield).any() else np.full(3, np.nan) for side in (LEFT, RIGHT)])
        old = self.centres
        if old is not None and np.isfinite(old).all() and np.isfinite(centres).all():
            keep = np.linalg.norm(centres - old, axis=1).sum()
            flip = np.linalg.norm(centres[::-1] - old, axis=1).sum()
            if flip < keep:  # the same two kits, ordered the other way round by who happens to be on the left
                labels = np.where(labels == LEFT, RIGHT, np.where(labels == RIGHT, LEFT, labels))
                centres, kits = centres[::-1], (kits[1], kits[0])
        self.kits, self.centres = kits, centres
        for i, t in enumerate(tracks):
            self.team[t] = self._name(int(labels[i]))
        self.ready = True

    def _name(self, side: int) -> str:
        return "ref" if side == OTHER else self.names[0] if side == LEFT else self.names[1]

    def team_of(self, track: int) -> str:
        if track in self.team:
            return self.team[track]
        samples = self.colours.get(track)
        if self.centres is not None and samples and np.isfinite(self.centres).all():
            colour = np.median(samples, axis=0)
            d = np.linalg.norm(self.centres - colour, axis=1)
            return self._name(int(d.argmin())) if d.min() <= OUTLIER_LAB else "ref"
        return "unk"


# -------------------------------------------------------------------------------------------------------- chain


@dataclass
class Result:
    t_s: float
    stat: FrameStat
    doc: dict[str, Any] | None  # None for other views, and while the first team split is pending


@dataclass
class _Pending:
    t_s: float
    people: list[tuple[int, float, float, float]]  # track, x, y (corner origin), conf
    ball: tuple[float, float, float] | None
    calibrated: bool
    wall: str


class FrameChain:
    def __init__(self, match_id: str, calibrator: Calibrator, *, detector: Callable[[], Detector] | None = None,
                 fps: float = 5.0, video_id: str | None = None, swap_teams: bool = False,
                 team_centres: np.ndarray | None = None) -> None:
        """``detector`` builds the person/ball detector on first use (loading RT-DETR takes seconds, and a run that
        never finds the main camera does not need it)."""
        self.match_id, self.calibrator, self.fps, self.video_id = match_id, calibrator, fps, video_id
        self._detector: Detector | None = None
        self._detector_factory = detector
        self.gate = ViewGate()
        self.tracker = Tracker()
        self.teams = TeamModel(swap=swap_teams, centres=team_centres)
        self.stats = RollingStats()
        self._pending: list[_Pending] = []
        self.homography: np.ndarray | None = None  # latest, at 960 px (for overlays and tests)

    def _to_pitch(self, h_det: np.ndarray, pixels: np.ndarray, t_s: float, width: int) -> np.ndarray:
        """Detection-frame pixels to corner-origin pitch metres: the calibrator's own mapping if it has one (a
        whole-match solution undistorts the lens), else the frame's homography."""
        own = getattr(self.calibrator, "image_to_pitch", None)
        if own is not None:
            metres = own(t_s, pixels * (WORK_WIDTH / width))
            if metres is not None and len(metres) == len(pixels):
                return metres
        return to_pitch(h_det, pixels)

    def _detect(self) -> Detector | None:
        if self._detector is None and self._detector_factory is not None:
            self._detector = self._detector_factory()
        return self._detector

    @staticmethod
    def _band(h_det: np.ndarray, shape: tuple[int, ...]) -> tuple[int, int] | None:
        """Rows of the frame the pitch (and a standing player on its far line) can occupy."""
        edge = np.linspace(0, 1, 40)
        m = MARGIN_M
        pts = np.concatenate([np.c_[edge * (PITCH_L + 2 * m) - m, np.full(40, -m)],
                              np.c_[edge * (PITCH_L + 2 * m) - m, np.full(40, PITCH_W + m)],
                              np.c_[np.full(40, -m), edge * (PITCH_W + 2 * m) - m],
                              np.c_[np.full(40, PITCH_L + m), edge * (PITCH_W + 2 * m) - m]])
        px, front = project(h_det, pts)
        usable = front & np.isfinite(px).all(axis=1)
        if not usable.any():
            return None
        # A zoomed camera puts part of the pitch's outline behind it: that part reaches the bottom of the frame.
        top = int(max(px[usable, 1].min() - HEAD_PX * shape[1] / 1920, 0))
        bottom = shape[0] if not usable.all() else int(min(px[:, 1].max() + 30, shape[0]))
        top = min(top, bottom)
        return (top, bottom) if bottom - top >= MIN_BAND_PX else None

    def process(self, bgr: np.ndarray, t_s: float) -> list[Result]:
        """One frame at the sampling rate (any width; calibration works on a 960-px copy). Returns the results now
        ready, in time order: usually this frame's, none while waiting for the first team split."""
        started = time.monotonic()
        h, w = bgr.shape[:2]
        small = bgr if w == WORK_WIDTH else cv2.resize(bgr, (WORK_WIDTH, round(h * WORK_WIDTH / w)),
                                                       interpolation=cv2.INTER_AREA)
        main, cut = self.gate.update(small)
        h960 = self.calibrator.step(small, t_s=t_s, main=main, cut=cut)
        stat = FrameStat(main=main, calibrated=h960 is not None)
        self.homography = h960
        wall = utc_now()
        if not main:
            return self._done([Result(t_s, stat, None)], started, stat)
        people: list[tuple[int, float, float, float]] = []
        ball = None
        if h960 is not None:
            people, ball = self._see(bgr, scaled(h960, w / WORK_WIDTH), t_s, stat)
        self._pending.append(_Pending(t_s, people, ball, h960 is not None, wall))
        out = self._release(t_s)
        return self._done(out, started, stat)

    def _done(self, results: list[Result], started: float, stat: FrameStat) -> list[Result]:
        self.stats.record(stat, time.monotonic() - started)
        return results

    def _see(self, bgr: np.ndarray, h_det: np.ndarray, t_s: float,
             stat: FrameStat) -> tuple[list[tuple[int, float, float, float]], tuple[float, float, float] | None]:
        detector = self._detect()
        band = self._band(h_det, bgr.shape)
        if detector is None or band is None:
            return [], None
        top, bottom = band
        tile = int(min(bottom - top, bgr.shape[1]))
        detector.tiling = type(detector.tiling)(top=top, bottom=bottom, width=bgr.shape[1], tile=tile,
                                                overlap=min(100, tile // 4))
        people, balls = detector(bgr)
        people = people[people[:, 4] >= HIGH] if len(people) else people
        out: list[tuple[int, float, float, float]] = []
        if len(people):
            feet = np.c_[(people[:, 0] + people[:, 2]) / 2, people[:, 3]]
            metres = self._to_pitch(h_det, feet, t_s, bgr.shape[1])
            with np.errstate(invalid="ignore"):
                on = ((metres[:, 0] >= -MARGIN_M) & (metres[:, 0] <= PITCH_L + MARGIN_M)
                      & (metres[:, 1] >= -MARGIN_M) & (metres[:, 1] <= PITCH_W + MARGIN_M))
            stat.people, stat.on_pitch = len(people), int(on.sum())
            keep = np.flatnonzero(on)
            if len(keep):
                boxes = np.c_[metres[keep] - BOX_M / 2, metres[keep] + BOX_M / 2, people[keep, 4]]
                index = {tuple(np.round(b[:4], 4)): int(k) for b, k in zip(boxes, keep, strict=True)}
                frame_no = round(t_s * self.fps)
                for track, box in self.tracker.update(frame_no, boxes):
                    k = index.get(tuple(np.round(box, 4)))
                    if k is None:
                        continue
                    x, y = float(metres[k, 0]), float(metres[k, 1])
                    x0, y0, x1, y1 = (int(v) for v in people[k, :4])
                    self.teams.observe(track, x, bgr[max(y0, 0):y1, max(x0, 0):x1])
                    out.append((track, x, y, float(people[k, 4])))
        ball = None
        if len(balls):
            best = balls[balls[:, 4].argmax()]
            if best[4] >= BALL_MIN_SCORE:
                bx, by = self._to_pitch(h_det, np.array([[(best[0] + best[2]) / 2, (best[1] + best[3]) / 2]]), t_s,
                                        bgr.shape[1])[0]
                if -MARGIN_M <= bx <= PITCH_L + MARGIN_M and -MARGIN_M <= by <= PITCH_W + MARGIN_M:
                    ball = (float(bx), float(by), float(best[4]))
        self.teams.refresh(t_s)
        return out, ball

    def _release(self, now_s: float, force: bool = False) -> list[Result]:
        if not self._pending:
            return []
        waiting = not self.teams.ready and any(p.people for p in self._pending)
        if waiting and not force and now_s - self._pending[0].t_s < MAX_WARMUP_S:
            return []
        out = [self._result(p) for p in self._pending]
        self._pending = []
        return out

    def _result(self, p: _Pending) -> Result:
        ents = []
        for track, x, y, conf in p.people:
            cx, cy = centre_origin(x, y)
            ents.append(entity(f"p{track}", self.teams.team_of(track), cx, cy, conf=conf))
        if p.ball is not None:
            cx, cy = centre_origin(p.ball[0], p.ball[1])
            ents.append(entity("ball", "ball", cx, cy, conf=p.ball[2]))
        stat = FrameStat(main=True, calibrated=p.calibrated)
        return Result(p.t_s, stat, frame_doc(self.match_id, p.t_s, ents, p.calibrated, wall_utc=p.wall,
                                             video_id=self.video_id))

    def flush(self) -> list[Result]:
        """Release whatever still waits for a team split (end of the input)."""
        return self._release(0.0, force=True)
