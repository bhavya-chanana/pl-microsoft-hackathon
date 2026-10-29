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
from matchlens.vision.teams import LEFT, OTHER, OUTLIER_LAB, RIGHT, shirt_colour
from matchlens.vision.tracker import HIGH, Tracker
from matchlens.vision.views import CUT_DISTANCE, FrameLook, look

from .frames import PITCH_L, PITCH_W, FrameStat, RollingStats, centre_origin, entity, frame_doc, utc_now
from .kits import split_kits

log = logging.getLogger(__name__)

LINE_M = 0.5  # a person further than this outside the lines is staff, a sub or a ball boy: not a player
MARGIN_M = 3.0  # feet this far outside the lines still count as on the pitch (throw-ins, staff on the line)
BOX_M = 4.0  # a player's box in the tracker (pitch metres): big enough to overlap one 5 Hz step later
HEAD_PX = 110  # room above the far touchline for a player's head, at detection size
MIN_BAND_PX = 160
BALL_MIN_SCORE = 0.3
BALL_MARGIN_M = 1.0  # a ball further than this outside the lines is an ad board, a graphic or a spectator's: not the ball
STATIC_PX = 4.0  # a "ball" this still in the image (at 960 px wide) frame after frame ...
STATIC_MIN = 6  # ... seen this many times before ...
STATIC_SPAN_S = 3.0  # ... over at least this long ...
STATIC_MOVED_M = 3.0  # ... while the camera carried that pixel this far over the pitch, is part of the picture
STATIC_MEMORY_S = 120.0
MIN_SHOT_FRAMES = 2  # a cut this soon after another is a flash (6 frames at 25 fps in vision/views.py)
SHOT_WINDOW = 10
SAMPLES_PER_TRACK = 12
MIN_SAMPLES = 3
MIN_TRACKS_FOR_SPLIT = 8
RESPLIT_EVERY_S = 10.0
MAX_WARMUP_S = 30.0  # frames wait this long at most for the first team split
# A per-frame ("frame") calibration can drift to a degenerate homography that throws every player onto one line of
# the pitch (or off it). Such a frame is dropped (status "none", no entities) instead of drawn.
JUDGE_MIN_PEOPLE = 4  # fewer detections than this say nothing about the calibration
MIN_ON_PITCH_SHARE = 0.8  # of the people detected, at least this share must project onto the pitch (give or take MARGIN_M)
MIN_SPREAD_M = 1.5  # std of the on-pitch people along each axis: a real main-camera view is never a line
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
        return sum(self._votes) * 2 > len(self._votes) and not now.overlay, cut


# ---------------------------------------------------------------------------------------------------- calibrators


class TrackerCalibrator:
    """Per-frame calibration with ``CameraTracker``. It starts from keyframes (labelled frames of the clip) or from
    venue guesses for its first main-camera frame."""

    status: str | None = None  # "frame" while the last step gave a calibration

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
        self.status = None if cal is None else "frame"
        return None if cal is None else cal.homography

    def reject(self) -> None:
        self.status = "none"
        self.tracker.reset()


def implausible(metres: np.ndarray, on: np.ndarray) -> str | None:
    """Why a frame's calibration cannot be right, from where it puts the people it detected (None when it can be).

    ``metres`` are corner-origin pitch metres of every detected person, ``on`` which of them are on the pitch
    (within ``MARGIN_M``). Needs ``JUDGE_MIN_PEOPLE`` people. A degenerate homography either throws them off the pitch
    or collapses them onto a line (all at one x, say), which still counts as "on" because the line lies on the pitch."""
    if len(metres) < JUDGE_MIN_PEOPLE:
        return None
    if not np.isfinite(metres).all():
        return "non-finite positions"
    share = float(on.mean())
    if share < MIN_ON_PITCH_SHARE:
        return f"{1 - share:.0%} of people off the pitch"
    kept = metres[on]
    if len(kept) >= JUDGE_MIN_PEOPLE and float(kept.std(axis=0).min()) < MIN_SPREAD_M:
        return "people collapsed onto a line"
    return None


class BundleCalibrator:
    """A whole-match solution (``vision.bundle.solve_match`` / ``solve_video``): homographies by video time.

    Where the solution has nothing for a frame the chain's gate called main camera (status ``none``, or a frame the
    solution's own filter dropped), ``fallback`` (a :class:`TrackerCalibrator`, per-frame calibration) is tried.
    ``status`` says which calibration the last frame got: ``fitted``, ``interpolated``, ``frame`` or ``none``."""

    def __init__(self, solution: Any, fallback: TrackerCalibrator | None = None) -> None:
        self.solution, self.fallback = solution, fallback
        self.status: str | None = None
        self._own = False  # the last answer came from the solution (so lens distortion applies)

    def step(self, bgr960: np.ndarray, *, t_s: float, main: bool, cut: bool) -> np.ndarray | None:
        self.status, self._own = None, False
        if not main:
            return None
        frame = self.solution.frame_at(t_s) if hasattr(self.solution, "frame_at") else None
        h = None
        if frame is not None and frame.usable:
            h = frame.homography
        elif frame is None and not hasattr(self.solution, "frame_at"):
            h = self.solution.homography_at(t_s)
        if h is not None:
            self.status = frame.status if frame is not None else "fitted"
            self._own = True
            if self.fallback is not None:
                self.fallback.tracker.reset()  # a later fallback starts fresh from the keyframes
            return h
        if self.fallback is not None:
            h = self.fallback.step(bgr960, t_s=t_s, main=main, cut=cut)
            self.status = self.fallback.status
            return h
        self.status = "none"
        return None

    def reject(self) -> None:
        """The chain found the last answer implausible: forget it, so the fallback re-acquires from its keyframes."""
        self.status, self._own = "none", False
        if self.fallback is not None:
            self.fallback.reject()

    def image_to_pitch(self, t_s: float, pixels960: np.ndarray) -> np.ndarray | None:
        """Raw pixels (960 wide) to pitch metres with the solution's lens distortion (None for fallback frames)."""
        if not self._own:
            return None
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


def _kit_lab(hex_colour: str | None) -> np.ndarray | None:
    """``#rrggbb`` to the Lab colour ``shirt_colour`` measures (OpenCV's 8-bit Lab)."""
    if not hex_colour:
        return None
    h = hex_colour.lstrip("#")
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return cv2.cvtColor(np.uint8([[rgb[::-1]]]), cv2.COLOR_BGR2LAB)[0, 0].astype(np.float64)


class TeamModel:
    """Kit colours per track -> home / away / ref, refreshed as tracks accumulate samples.

    Which kit is home: ``home_lab`` (the home shirt colour in Lab) says so by colour; without it the team on the left
    of the pitch is ``home`` (``vision/teams.py``), unless ``swap``. The left-team rule is unreliable when the view
    shows one half only (both teams then have almost the same median x), so name the kit when it is known."""

    def __init__(self, *, swap: bool = False, centres: np.ndarray | None = None,
                 home_lab: np.ndarray | None = None) -> None:
        self.home_lab = home_lab
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
            split = split_kits(colours, median_x, np.array([len(self.colours[t]) for t in tracks], float))
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
        if self.home_lab is not None and np.isfinite(centres).all():
            home_is_left = np.linalg.norm(centres[0] - self.home_lab) <= np.linalg.norm(centres[1] - self.home_lab)
            self.names = ("home", "away") if home_is_left else ("away", "home")
        for i, t in enumerate(tracks):
            self.team[t] = self._name(int(labels[i]))
        self.ready = True

    def _name(self, side: int) -> str:
        return "ref" if side == OTHER else self.names[0] if side == LEFT else self.names[1]

    def confidence(self, track: int) -> float | None:
        """How sure the kit colour is of the team: 0.5 equally close to both kits, towards 1 when it is one kit's."""
        samples = self.colours.get(track)
        if self.centres is None or not samples or not np.isfinite(self.centres).all():
            return None
        d = np.linalg.norm(self.centres - np.median(samples, axis=0), axis=1)
        near, far = float(d.min()), float(d.max())
        return round(far / (near + far), 2) if near + far > 0 else None

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
    status: str | None = None


class FrameChain:
    def __init__(self, match_id: str, calibrator: Calibrator, *, detector: Callable[[], Detector] | None = None,
                 fps: float = 5.0, video_id: str | None = None, swap_teams: bool = False,
                 team_centres: np.ndarray | None = None,
                 clock: Callable[[float], tuple[float | None, int]] | None = None,
                 home_kit: str | None = None) -> None:
        """``detector`` builds the person/ball detector on first use (loading RT-DETR takes seconds, and a run that
        never finds the main camera does not need it)."""
        self.match_id, self.calibrator, self.fps, self.video_id = match_id, calibrator, fps, video_id
        self._detector: Detector | None = None
        self._detector_factory = detector
        self.clock = clock  # video seconds -> (match clock seconds or None, period 0/1/2)
        self.gate = ViewGate()
        self.tracker = Tracker()
        self.swap_teams = swap_teams  # home starts on the right: the frames are turned so home still attacks +x
        self.teams = TeamModel(swap=swap_teams, centres=team_centres, home_lab=_kit_lab(home_kit))
        self.stats = RollingStats()
        self._pending: list[_Pending] = []
        self._ball_marks: deque[tuple[float, float, float, float, float]] = deque()  # t, px, py (960 px), x, y (m)
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

    def _fixture(self, t_s: float, pixel: tuple[float, float], metres: tuple[float, float]) -> bool:
        """True when a ball candidate has sat on the same image pixel while the camera moved: it belongs to the picture
        (an advertising board, a scoreboard graphic), not to the game. Records the candidate either way."""
        while self._ball_marks and t_s - self._ball_marks[0][0] > STATIC_MEMORY_S:
            self._ball_marks.popleft()
        same = [m for m in self._ball_marks if abs(m[1] - pixel[0]) <= STATIC_PX and abs(m[2] - pixel[1]) <= STATIC_PX]
        self._ball_marks.append((t_s, pixel[0], pixel[1], metres[0], metres[1]))
        if len(same) < STATIC_MIN or t_s - same[0][0] < STATIC_SPAN_S:
            return False
        moved = max(float(np.hypot(m[3] - metres[0], m[4] - metres[1])) for m in same)
        return moved >= STATIC_MOVED_M

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
            seen = self._see(bgr, scaled(h960, w / WORK_WIDTH), t_s, stat)
            if seen is None:  # the per-frame calibration put the people nowhere sensible: no entities, status none
                reject = getattr(self.calibrator, "reject", None)
                if reject is not None:
                    reject()
                h960, stat.calibrated, stat.people, stat.on_pitch = None, False, 0, 0
                self.homography = None
            else:
                people, ball = seen
        self._pending.append(_Pending(t_s, people, ball, h960 is not None, wall,
                                      getattr(self.calibrator, "status", None) if h960 is not None else "none"))
        out = self._release(t_s)
        return self._done(out, started, stat)

    def _done(self, results: list[Result], started: float, stat: FrameStat) -> list[Result]:
        self.stats.record(stat, time.monotonic() - started)
        return results

    def _see(self, bgr: np.ndarray, h_det: np.ndarray, t_s: float, stat: FrameStat
             ) -> tuple[list[tuple[int, float, float, float]], tuple[float, float, float] | None] | None:
        """People and ball in pitch metres, or None when a per-frame calibration is implausible (see ``implausible``)."""
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
            if getattr(self.calibrator, "status", None) == "frame" and (why := implausible(metres, on)):
                log.info("t=%.1fs: per-frame calibration rejected (%s)", t_s, why)
                return None
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
                    inside = -LINE_M <= x <= PITCH_L + LINE_M and -LINE_M <= y <= PITCH_W + LINE_M
                    self.teams.observe(track, x, bgr[max(y0, 0):y1, max(x0, 0):x1] if inside else None)
                    out.append((track, x, y, float(people[k, 4])))
        ball = None
        for cand in sorted(balls, key=lambda b: -b[4]) if len(balls) else []:
            if cand[4] < BALL_MIN_SCORE:
                break
            pixel = np.array([[(cand[0] + cand[2]) / 2, (cand[1] + cand[3]) / 2]])
            bx, by = self._to_pitch(h_det, pixel, t_s, bgr.shape[1])[0]
            scale = WORK_WIDTH / bgr.shape[1]
            fixed = self._fixture(t_s, (float(pixel[0, 0]) * scale, float(pixel[0, 1]) * scale), (float(bx), float(by)))
            if (not fixed and -BALL_MARGIN_M <= bx <= PITCH_L + BALL_MARGIN_M
                    and -BALL_MARGIN_M <= by <= PITCH_W + BALL_MARGIN_M):
                ball = (float(bx), float(by), float(cand[4]))
                break
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
        clock, period = self.clock(p.t_s) if self.clock is not None else (None, 0)
        # home attacks +x: turn the pitch round in the second half (the teams change ends) and when home attacked -x
        # at the start (``swap_teams``)
        sign = -1.0 if (period == 2) != self.swap_teams else 1.0
        for track, x, y, conf in p.people:
            cx, cy = centre_origin(x, y)
            off = abs(cx) > PITCH_L / 2 + LINE_M or abs(cy) > PITCH_W / 2 + LINE_M
            team = "ref" if off else self.teams.team_of(track)
            e = entity(f"p{track}", team, sign * cx, sign * cy, conf=conf)
            if team in ("home", "away") and (tc := self.teams.confidence(track)) is not None:
                e["team_conf"] = tc
            ents.append(e)
        if p.ball is not None:
            cx, cy = centre_origin(p.ball[0], p.ball[1])
            ents.append(entity("ball", "ball", sign * cx, sign * cy, conf=p.ball[2]))
        stat = FrameStat(main=True, calibrated=p.calibrated)
        doc = frame_doc(self.match_id, p.t_s, ents, p.calibrated, match_clock_s=clock, wall_utc=p.wall,
                        video_id=self.video_id, calib_status=p.status)
        if sign < 0:
            doc["flipped"] = True
        return Result(p.t_s, stat, doc)

    def flush(self) -> list[Result]:
        """Release whatever still waits for a team split (end of the input)."""
        return self._release(0.0, force=True)
