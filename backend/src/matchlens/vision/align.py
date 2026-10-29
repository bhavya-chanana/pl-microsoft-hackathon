"""Map video time to the match clock from anchors: kick-offs, goals, cards.

The data feed (FotMob) timestamps events on the match clock in whole minutes; the video has its own clock. Anchors
pair the two: ``Anchor(video_s=1834.2, match_s=1350.0, kind="goal")`` says "the goal on the 23rd minute is at 30:34
into this video".

Match seconds are on the FotMob minute scale: the first half runs from 0, and the second half from 2700 (46:00)
whatever happened in first-half stoppage time, so a 45+3' event has ``match_s`` near 2850 *in period 1* and the
second-half kick-off is 2700 *in period 2*. That is why anchors carry a period and every half gets its own map.

Method, per period:

1. Candidate lines ``match = a * video + b`` come from every pair of anchors (exhaustive RANSAC: with a handful of
   anchors every minimal sample is tried), slope held to ``slope_bounds`` (video runs at the speed of the match,
   so about 1).
2. An anchor is an inlier of a line if it sits within ``tolerance = 2 * sigma + min_tol`` seconds of it. Sigma
   comes from the anchor kind (a kick-off is exact to a second or so; a goal or card is known to the minute, so
   about 20-30 s), or from ``Anchor.sigma_s``.
3. The line with most inliers wins (ties go to the smaller weighted residual) and is refitted to its inliers by
   weighted least squares. A wrong anchor, a replay timestamp for instance, is an outlier and is ignored.
4. If two or more outliers agree on a second line (an edited clip with a jump in it), that becomes a further piece
   of the map; each piece is valid up to halfway to the next one.

``ClockMap.to_match`` / ``to_video`` convert in both directions; ``report`` lists every anchor's residual in seconds.
Video time in the break between halves has no match time (NaN).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

HALF_S = 2700.0  # the second half starts at 46:00 on the minute scale
SLOPE_SIGMA = 0.005  # prior spread of the video-to-match slope around 1
DEFAULT_SIGMA_S = {"clock": 1.5, "kickoff_1": 1.5, "kickoff_2": 1.5, "whistle_1": 3.0, "whistle_2": 3.0, "goal": 20.0, "card": 30.0,
                   "substitution": 30.0, "event": 30.0}
STOPPAGE_CAP_S = 480.0  # first-half stoppage is not believed beyond this when the half's end is not anchored
PRE_ROLL_S = 900.0  # video before the first kick-off that still maps (negative match time)
POST_ROLL_S = 600.0  # video after the second half's 90:00 that still maps (stoppage time)


def minute_to_seconds(minute: int, added: int | None = None) -> float:
    """FotMob's minute (with stoppage ``added``) to match seconds, aimed at the middle of that minute."""
    if added:
        return minute * 60.0 + (added - 1) * 60.0 + 30.0
    return (max(minute, 1) - 1) * 60.0 + 30.0


@dataclass(frozen=True)
class Anchor:
    """One pairing of a video timestamp with a match time. ``period`` is 1 or 2 (inferred from ``match_s`` when
    left out: before 2700 s is the first half)."""

    video_s: float
    match_s: float
    kind: str = "event"
    period: int | None = None
    sigma_s: float | None = None
    label: str = ""

    @property
    def half(self) -> int:
        if self.kind.endswith("_1"):
            return 1
        if self.kind.endswith("_2"):
            return 2
        if self.period in (1, 2):
            return int(self.period)
        return 1 if self.match_s <= HALF_S else 2

    @property
    def sigma(self) -> float:
        return float(self.sigma_s if self.sigma_s is not None else DEFAULT_SIGMA_S.get(self.kind, 30.0))

    @classmethod
    def kickoff(cls, half: int, video_s: float) -> Anchor:
        return cls(video_s, 0.0 if half == 1 else HALF_S, f"kickoff_{half}", half)

    @classmethod
    def from_clock(cls, video_s: float, clock: str | float, period: int | None = None, label: str = "") -> Anchor:
        """An on-screen match clock reading, ``"14:41"`` or seconds, at ``video_s``. The broadcast clock runs on the
        same scale as the minute scale (the second half starts at 45:00 = 2700 s), good to a second or two. Pass
        ``period`` for readings in stoppage time (45:00 and over can belong to either half)."""
        if isinstance(clock, str):
            minutes, _, seconds = clock.strip().partition(":")
            clock = int(minutes) * 60.0 + float(seconds or 0.0)
        return cls(video_s, float(clock), "clock", period, None, label)

    @classmethod
    def from_minute(cls, video_s: float, minute: int, added: int | None = None, kind: str = "goal",
                    period: int | None = None, label: str = "") -> Anchor:
        period = period or (1 if minute <= 45 else 2)
        return cls(video_s, minute_to_seconds(minute, added), kind, period, None, label)


@dataclass(frozen=True)
class Piece:
    period: int
    video_lo: float
    video_hi: float
    slope: float
    intercept: float  # match_s = slope * video_s + intercept
    n_anchors: int

    def to_match(self, v):
        return self.slope * v + self.intercept

    def to_video(self, m):
        return (m - self.intercept) / self.slope


@dataclass
class AnchorResidual:
    anchor: Anchor
    period: int
    inlier: bool
    residual_s: float  # match_s minus the map's prediction at the anchor's video time (nan if the period has no map)


@dataclass
class AlignReport:
    residuals: list[AnchorResidual]
    rms_s: float  # over inliers
    max_s: float  # over inliers
    outliers: int
    slopes: dict[int, float]

    def to_dict(self) -> dict:
        return {"rms_s": self.rms_s, "max_s": self.max_s, "outliers": self.outliers,
                "slopes": {str(k): v for k, v in self.slopes.items()},
                "anchors": [{"video_s": r.anchor.video_s, "match_s": r.anchor.match_s, "kind": r.anchor.kind,
                             "period": r.period, "inlier": r.inlier, "residual_s": r.residual_s,
                             "label": r.anchor.label} for r in self.residuals]}


@dataclass
class ClockMap:
    pieces: list[Piece]
    report: AlignReport = field(default_factory=lambda: AlignReport([], math.nan, math.nan, 0, {}))

    def _piece_at(self, video_s: float) -> Piece | None:
        for p in self.pieces:
            if p.video_lo <= video_s < p.video_hi:
                return p
        return None

    def to_match(self, video_s):
        """Match seconds (FotMob minute scale) at video time(s) ``video_s``; NaN outside the two halves."""
        arr = np.atleast_1d(np.asarray(video_s, np.float64))
        out = np.full(arr.shape, np.nan)
        for i, v in enumerate(arr):
            p = self._piece_at(float(v))
            if p is not None:
                out[i] = p.to_match(v)
        return float(out[0]) if np.isscalar(video_s) or np.ndim(video_s) == 0 else out

    def period_at(self, video_s: float) -> int:
        """1 or 2 while a half is being played at that video time, else 0."""
        p = self._piece_at(float(video_s))
        return 0 if p is None else p.period

    def to_video(self, match_s: float, period: int | None = None) -> float:
        """Video seconds at match time ``match_s`` in ``period`` (1 or 2). Without a period, times from 2700 s on
        mean the second half when it is mapped, since that is where the clock is read from then on."""
        have = {p.period for p in self.pieces}
        if period is None:
            period = 2 if match_s >= HALF_S and 2 in have else 1
        cands = [p for p in self.pieces if p.period == period]
        if not cands:
            return math.nan
        # Prefer the piece whose own match range contains the time, else the nearest piece.
        def distance(p: Piece) -> float:
            lo, hi = sorted((p.to_match(p.video_lo), p.to_match(p.video_hi)))
            return 0.0 if lo <= match_s <= hi else min(abs(match_s - lo), abs(match_s - hi))
        best = min(cands, key=distance)
        return float(best.to_video(match_s))

    def to_dict(self) -> dict:
        return {"pieces": [p.__dict__ for p in self.pieces], "report": self.report.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> ClockMap:
        return cls([Piece(**p) for p in d["pieces"]])


def _fit_line(video: np.ndarray, match: np.ndarray, sigma: np.ndarray, bounds: tuple[float, float]):
    """Weighted least-squares line with the slope clipped to ``bounds`` (refitting the intercept)."""
    w = 1.0 / sigma**2
    if len(video) == 1:
        return 1.0, float(match[0] - video[0])
    vm, mm = np.average(video, weights=w), np.average(match, weights=w)
    var = float(np.sum(w * (video - vm) ** 2))
    # A weak pull to slope 1: live video runs at the speed of the match, and minute-level events alone would
    # let the slope wander by a percent over a half.
    pull = 1.0 / SLOPE_SIGMA**2
    slope = (float(np.sum(w * (video - vm) * (match - mm))) + pull) / (var + pull)
    slope = min(max(slope, bounds[0]), bounds[1])
    return slope, float(mm - slope * vm)


def _ransac(video: np.ndarray, match: np.ndarray, sigma: np.ndarray, min_tol: float, bounds: tuple[float, float],
            min_inliers: int):
    """Best (slope, intercept, inlier mask) over every pair of anchors, or None."""
    n = len(video)
    tol = 2.0 * sigma + min_tol
    best = None
    pairs = list(itertools.combinations(range(n), 2)) if n >= 2 else []
    candidates = []
    for i, j in pairs:
        dv = video[j] - video[i]
        if abs(dv) < 1e-6:
            continue
        slope = (match[j] - match[i]) / dv
        if not bounds[0] <= slope <= bounds[1]:
            continue
        candidates.append((slope, match[i] - slope * video[i]))
    if n == 1:
        candidates.append((1.0, match[0] - video[0]))
    for slope, intercept in candidates:
        res = match - (slope * video + intercept)
        inl = np.abs(res) <= tol
        if inl.sum() < min_inliers:
            continue
        # Refit on the inliers, then recount: the two-point line is only a proposal.
        s2, b2 = _fit_line(video[inl], match[inl], sigma[inl], bounds)
        res = match - (s2 * video + b2)
        inl = np.abs(res) <= tol
        if inl.sum() < min_inliers:
            continue
        s2, b2 = _fit_line(video[inl], match[inl], sigma[inl], bounds)
        res = match - (s2 * video + b2)
        score = (int(inl.sum()), -float(np.sum((res[inl] / sigma[inl]) ** 2)))
        if best is None or score > best[0]:
            best = (score, s2, b2, inl)
    return None if best is None else (best[1], best[2], best[3])


def align(anchors: Sequence[Anchor], *, slope_bounds: tuple[float, float] = (0.97, 1.03), min_tol_s: float = 3.0) -> ClockMap:
    """Video time to match clock from ``anchors``. See the module docstring."""
    if not anchors:
        raise ValueError("no anchors")
    pieces: list[Piece] = []
    residuals: list[AnchorResidual] = []
    for period in (1, 2):
        group = [a for a in anchors if a.half == period]
        if not group:
            continue
        video = np.array([a.video_s for a in group], np.float64)
        match = np.array([a.match_s for a in group], np.float64)
        sigma = np.array([a.sigma for a in group], np.float64)
        left = np.arange(len(group))
        found: list[tuple[float, float, np.ndarray]] = []
        for round_ in range(3):
            if not len(left):
                break
            got = _ransac(video[left], match[left], sigma[left], min_tol_s, slope_bounds,
                          min_inliers=1 if round_ == 0 else 2)
            if got is None:
                break
            slope, intercept, inl = got
            found.append((slope, intercept, left[inl]))
            left = left[~inl]
        if not found:  # no consistent line at all (e.g. every pair has an impossible slope): fall back to slope 1
            k = int(np.argmin(sigma))
            found = [(1.0, float(match[k] - video[k]), np.array([k]))]
            left = np.array([i for i in range(len(group)) if i != k])
        # Pieces, ordered by video time.
        info = []
        for slope, intercept, idx in found:
            info.append((float(video[idx].min()), float(video[idx].max()), slope, intercept, len(idx), idx))
        info.sort(key=lambda t: t[0])
        for k, (vlo, vhi, slope, intercept, n, idx) in enumerate(info):
            lo = -math.inf if k == 0 else (info[k - 1][1] + vlo) / 2.0
            hi = math.inf if k == len(info) - 1 else (vhi + info[k + 1][0]) / 2.0
            pieces.append(Piece(period, lo, hi, slope, intercept, n))
        inlier_idx = {int(i) for _, _, _, _, _, idx in info for i in idx}
        for i, a in enumerate(group):
            p = next(q for q in pieces if q.period == period and q.video_lo <= a.video_s < q.video_hi)
            residuals.append(AnchorResidual(a, period, i in inlier_idx, float(a.match_s - p.to_match(a.video_s))))

    # Bound each half in video time: period 1 begins PRE_ROLL_S before its zero and ends at its stoppage;
    # period 2 begins at its zero (or a little before, for the kick-off anchor's slack).
    p1 = [p for p in pieces if p.period == 1]
    p2 = [p for p in pieces if p.period == 2]
    start1 = p1[0].to_video(-PRE_ROLL_S) if p1 else math.nan
    start2 = p2[0].to_video(HALF_S - 10.0) if p2 else math.nan
    end1_match = HALF_S + STOPPAGE_CAP_S
    ends = [a.video_s for a in anchors if a.kind == "whistle_1"]
    if p1:
        end1 = max(ends) if ends else p1[-1].to_video(end1_match)
        if p2:
            end1 = min(end1, start2 - 1.0)
        pieces = [Piece(p.period, max(p.video_lo, start1) if p is p1[0] else p.video_lo,
                        min(p.video_hi, end1) if p is p1[-1] else p.video_hi, p.slope, p.intercept, p.n_anchors)
                  if p.period == 1 else p for p in pieces]
    if p2:
        end2 = p2[-1].to_video(2 * HALF_S + POST_ROLL_S)
        pieces = [Piece(p.period, max(p.video_lo, start2) if p is p2[0] else p.video_lo,
                        min(p.video_hi, end2) if p is p2[-1] else p.video_hi, p.slope, p.intercept, p.n_anchors)
                  if p.period == 2 else p for p in pieces]
    inl = np.array([abs(r.residual_s) for r in residuals if r.inlier]) if residuals else np.array([])
    report = AlignReport(residuals, float(np.sqrt(np.mean(inl**2))) if inl.size else math.nan,
                         float(inl.max()) if inl.size else math.nan, sum(not r.inlier for r in residuals),
                         {p.period: p.slope for p in pieces})
    return ClockMap(pieces, report)
