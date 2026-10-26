"""Baseline tactical model: pitch control plus expected threat, numpy only (no learned weights).

Everything is scored for ``frame.possession_team_id``:

* receiver probabilities: teammate control x (1 + xT gain over the carrier) x lane-free factor, normalised;
* shot probability: expected goals of the places the ball can be shot from (carrier and likely receivers),
  weighted by local control, squashed to a 10 s horizon;
* turnover probability: how much of the carrier's cell the opponents control, pressure within 3 m and the
  risk of the likely passes;
* concede probability: the turnover times the opponent threat at the ball.

``propose`` searches repositionings by seeded random-restart hill climbing on these scores.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Any

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH
from matchlens.domain.pitch import GOAL_HALF_WIDTH, GOAL_Y
from matchlens.tacticsml.contract import (
    Intent,
    Proposal,
    RetrievedMoment,
    ScoreResult,
    TFrame,
)
from matchlens.tacticsml.pitch_control import (
    GRID_NX,
    GRID_NY,
    control_from_arrays,
    frame_arrays,
)
from matchlens.tacticsml.xt import XtGrid

VERSION = "baseline-pc-xt/1"

SHOT_RATE = 3.5  # shot_prob = 1 - exp(-SHOT_RATE * weighted chance quality)
XT_GAIN_SCALE = 12.0  # receiver weight 1 + scale * xT gain
PASS_DECAY_M = 30.0
LANE_CLEARANCE_M = 2.0
PRESSURE_RADIUS_M = 3.0
COUNTER_FACTOR = 1.5  # a turnover is worse than the raw opponent xT: the team is out of shape
XT_IN_GOAL = 0.3  # weight of the expected threat of where the ball can get to, in goal_prob
GK_MAX_MOVE_M = 6.0
PITCH_MARGIN = 0.5


@dataclass
class _State:
    """A frame as arrays, so that candidate moves can be evaluated without rebuilding models."""

    ids: list[str]
    x: np.ndarray
    y: np.ndarray
    vx: np.ndarray
    vy: np.ndarray
    speed: np.ndarray
    is_poss: np.ndarray
    is_gk: np.ndarray
    carrier: int | None  # index into the arrays
    ball: tuple[float, float]
    ar_poss: bool
    ar_opp: bool
    ball_on_carrier: bool

    @classmethod
    def from_frame(cls, frame: TFrame, moves: dict[str, tuple[float, float]] | None = None) -> _State:
        x, y, vx, vy, speed, is_poss = frame_arrays(frame)
        ids = [p.player_id for p in frame.players]
        is_gk = np.array([p.is_gk for p in frame.players], dtype=bool)
        carrier = ids.index(frame.carrier_id) if frame.carrier_id in ids else None
        if carrier is None and is_poss.any():
            d = np.hypot(x - frame.ball.x, y - frame.ball.y)
            d[~is_poss] = np.inf
            carrier = int(np.argmin(d))
        ball = (frame.ball.x, frame.ball.y)
        on_carrier = carrier is not None and np.hypot(x[carrier] - ball[0], y[carrier] - ball[1]) < 2.0
        poss = frame.possession_team_id
        ar_poss = frame.attacking_right.get(poss, True)
        other = [t for t in frame.attacking_right if t != poss]
        ar_opp = frame.attacking_right[other[0]] if other else (not ar_poss)
        state = cls(ids, x, y, vx, vy, speed, is_poss, is_gk, carrier, ball, ar_poss, ar_opp, on_carrier)
        if moves:
            state = state.moved({pid: xy for pid, xy in moves.items() if pid in ids})
        return state

    def moved(self, moves: dict[str, tuple[float, float]]) -> _State:
        x, y, vx, vy = self.x.copy(), self.y.copy(), self.vx.copy(), self.vy.copy()
        ball = self.ball
        for pid, (nx, ny) in moves.items():
            i = self.ids.index(pid)
            nx = float(np.clip(nx, 0.0, PITCH_LENGTH))
            ny = float(np.clip(ny, 0.0, PITCH_WIDTH))
            if i == self.carrier and self.ball_on_carrier:
                ball = (ball[0] + nx - x[i], ball[1] + ny - y[i])
            x[i], y[i], vx[i], vy[i] = nx, ny, 0.0, 0.0
        return _State(self.ids, x, y, vx, vy, self.speed, self.is_poss, self.is_gk, self.carrier, ball,
                      self.ar_poss, self.ar_opp, self.ball_on_carrier)


def _xg(x_abs: np.ndarray, y_abs: np.ndarray, attacking_right: bool) -> np.ndarray:
    """Vectorised ``domain.pitch.expected_goals`` (same logistic model, unrounded) in absolute coordinates."""
    xr = np.asarray(x_abs, dtype=float) if attacking_right else PITCH_LENGTH - np.asarray(x_abs, dtype=float)
    yr = np.asarray(y_abs, dtype=float) if attacking_right else PITCH_WIDTH - np.asarray(y_abs, dtype=float)
    dx = PITCH_LENGTH - xr
    dist = np.hypot(dx, yr - GOAL_Y)
    angle = np.abs(np.arctan2(GOAL_Y + GOAL_HALF_WIDTH - yr, dx) - np.arctan2(GOAL_Y - GOAL_HALF_WIDTH - yr, dx))
    return 1.0 / (1.0 + np.exp(-(-1.1 - 0.11 * dist + 1.6 * angle)))


def _cell_lookup(control: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    c = np.clip((np.asarray(x) / PITCH_LENGTH * GRID_NX).astype(int), 0, GRID_NX - 1)
    r = np.clip((np.asarray(y) / PITCH_WIDTH * GRID_NY).astype(int), 0, GRID_NY - 1)
    return control[r, c]


def _segment_clearance(ax: float, ay: float, bx: np.ndarray, by: np.ndarray,
                       ox: np.ndarray, oy: np.ndarray) -> np.ndarray:
    """Smallest opponent distance to each segment a -> b[j]; shape [len(b)] (inf with no opponents)."""
    if ox.size == 0 or bx.size == 0:
        return np.full(bx.shape, np.inf)
    dx, dy = bx - ax, by - ay
    len_sq = np.maximum(dx * dx + dy * dy, 1e-9)
    t = np.clip(((ox[None, :] - ax) * dx[:, None] + (oy[None, :] - ay) * dy[:, None]) / len_sq[:, None], 0.0, 1.0)
    d = np.hypot(ax + t * dx[:, None] - ox[None, :], ay + t * dy[:, None] - oy[None, :])
    return d.min(axis=1)


def _evaluate(s: _State, xt: XtGrid) -> dict[str, Any]:
    """All scores of one state, plus the pitch-control grid and embedding inputs."""
    control = control_from_arrays(s.x, s.y, s.vx, s.vy, s.speed, s.is_poss, s.ball)
    opp = ~s.is_poss
    ox, oy = s.x[opp], s.y[opp]
    out: dict[str, Any] = {"control": control}

    if s.carrier is None:  # nobody on the ball and no possession players
        out.update(shot_prob=0.0, goal_prob=0.0, concede_prob=0.0, turnover_prob=0.0, value=0.0,
                   receivers={}, ctrl_c=0.0, pressure=0.0, xg_c=0.0, xt_c=0.0)
        return out

    cx, cy = float(s.x[s.carrier]), float(s.y[s.carrier])
    mates = np.flatnonzero(s.is_poss & (np.arange(len(s.ids)) != s.carrier))
    ctrl_c = float(_cell_lookup(control, np.array([cx]), np.array([cy]))[0])
    xt_c = xt.value(cx, cy, s.ar_poss)
    xg_c = float(_xg(np.array([cx]), np.array([cy]), s.ar_poss)[0])

    # who receives next
    probs = np.zeros(0)
    ctrl_j = lane_j = xg_j = xt_j = np.zeros(0)
    if mates.size:
        mx, my = s.x[mates], s.y[mates]
        ctrl_j = _cell_lookup(control, mx, my)
        xt_j = xt.values_at(mx, my, s.ar_poss)
        xg_j = _xg(mx, my, s.ar_poss)
        clearance = _segment_clearance(cx, cy, mx, my, ox, oy)
        lane_j = 0.1 + 0.9 * np.clip(clearance / LANE_CLEARANCE_M, 0.0, 1.0)
        dist = np.hypot(mx - cx, my - cy)
        weight = (np.maximum(ctrl_j, 1e-3) * np.clip(1.0 + XT_GAIN_SCALE * (xt_j - xt_c), 0.3, 4.0) * lane_j
                  * np.exp(-dist / PASS_DECAY_M) * np.where(dist < 2.0, 0.5, 1.0))
        logits = np.log(weight)
        e = np.exp(logits - logits.max())
        probs = e / e.sum()

    # shot within 10 s: the carrier shoots or a likely receiver does
    if mates.size:
        w_src = np.concatenate(([0.5], 0.5 * probs))
        xg_src = np.concatenate(([xg_c], xg_j))
        ctrl_src = np.concatenate(([ctrl_c], ctrl_j * lane_j))
        xt_src = np.concatenate(([xt_c], xt_j))
    else:
        w_src, xg_src, ctrl_src, xt_src = np.array([1.0]), np.array([xg_c]), np.array([ctrl_c]), np.array([xt_c])
    quality = float((w_src * xg_src * ctrl_src).sum())
    shot_prob = 1.0 - float(np.exp(-SHOT_RATE * quality))
    mean_xg = float((w_src * ctrl_src * xg_src**2).sum()) / max(quality, 1e-9)
    reach_xt = float((w_src * ctrl_src * xt_src).sum())
    goal_prob = min(1.0, shot_prob * mean_xg + XT_IN_GOAL * reach_xt)

    # losing the ball within 5 s
    pressure = float(np.clip(1.0 - np.hypot(ox - cx, oy - cy) / PRESSURE_RADIUS_M, 0.0, 1.0).sum()) if ox.size else 0.0
    risk = float((probs * (1.0 - ctrl_j * lane_j)).sum()) if mates.size else 0.5
    z = -3.0 + 2.2 * (1.0 - ctrl_c) + 1.1 * pressure + 0.9 * risk
    turnover_prob = float(1.0 / (1.0 + np.exp(-z)))
    concede_prob = min(1.0, turnover_prob * COUNTER_FACTOR * xt.value(cx, cy, s.ar_opp))

    out.update(shot_prob=shot_prob, goal_prob=goal_prob, concede_prob=concede_prob, turnover_prob=turnover_prob,
               value=goal_prob - concede_prob, ctrl_c=ctrl_c, pressure=pressure, xg_c=xg_c, xt_c=xt_c,
               receivers={s.ids[m]: float(p) for m, p in zip(mates, probs, strict=True)})
    return out


def _embedding(s: _State, ev: dict[str, Any]) -> list[float]:
    """Fixed 17-vector: mean control in a 3 x 3 block (y-rows by x-thirds, in the possession team's attacking
    direction), then ball x/y (relative, 0..1), carrier xG, carrier xT, pressure, carrier control, shot and
    turnover probability."""
    control = ev["control"] if s.ar_poss else ev["control"][::-1, ::-1]
    rows = np.array_split(np.arange(GRID_NY), 3)
    cols = np.array_split(np.arange(GRID_NX), 3)
    block = [float(control[np.ix_(r, c)].mean()) for r in rows for c in cols]
    bx, by = s.ball if s.ar_poss else (PITCH_LENGTH - s.ball[0], PITCH_WIDTH - s.ball[1])
    extra = [bx / PITCH_LENGTH, by / PITCH_WIDTH, ev["xg_c"], ev["xt_c"], ev["pressure"], ev["ctrl_c"],
             ev["shot_prob"], ev["turnover_prob"]]
    return [round(v, 4) for v in block + extra]


def _objective(ev: dict[str, Any], target: str, team_is_poss: bool) -> float:
    """Value of one evaluation for the team asking; higher is better for that team."""
    sign = 1.0 if team_is_poss else -1.0
    if target == "shot":
        return sign * ev["shot_prob"]
    if target in ("retain", "press"):  # keeping the ball and winning it are the same quantity, signed by side
        return -sign * ev["turnover_prob"]
    if target.startswith("receiver:"):
        p = ev["receivers"].get(target.split(":", 1)[1], 0.0)
        return p if team_is_poss else -p
    raise ValueError(f"unknown target {target!r}")


class BaselineModel:
    version = VERSION

    def __init__(self) -> None:
        self.xt = XtGrid.load()

    # ---- scoring -------------------------------------------------------------------------------------------

    def _result(self, state: _State, ev: dict[str, Any], ref_value: float | None) -> ScoreResult:
        p = ev["shot_prob"]
        return ScoreResult(
            receiver_probs={k: round(v, 5) for k, v in ev["receivers"].items()},
            shot_prob=round(p, 5), shot_std=round(0.25 * float(np.sqrt(p * (1 - p))), 5),
            goal_prob=round(ev["goal_prob"], 5), concede_prob=round(ev["concede_prob"], 5),
            value=round(ev["value"], 5),
            xt_delta=None if ref_value is None else round(ev["value"] - ref_value, 5),
            turnover_prob=round(ev["turnover_prob"], 5),
            turnover_std=round(0.25 * float(np.sqrt(ev["turnover_prob"] * (1 - ev["turnover_prob"]))), 5),
            pitch_control=np.round(ev["control"], 3).tolist(),
            embedding=_embedding(state, ev), model_version=VERSION)

    def score(self, frame: TFrame, intent: Intent | None = None, reference: TFrame | None = None) -> ScoreResult:
        state = _State.from_frame(frame, intent.moves if intent else None)
        ev = _evaluate(state, self.xt)
        if reference is not None:
            ref = _evaluate(_State.from_frame(reference), self.xt)
        elif intent and intent.moves:
            ref = _evaluate(_State.from_frame(frame), self.xt)
        else:
            ref = ev
        return self._result(state, ev, ref["value"])

    def score_batch(self, frames: list[TFrame]) -> list[ScoreResult]:
        return [self.score(f) for f in frames]

    def similar(self, frame: TFrame, k: int = 5) -> list[RetrievedMoment]:
        return []  # retrieval needs the learned embeddings

    # ---- proposals -----------------------------------------------------------------------------------------

    def propose(self, frame: TFrame, team_id: str, target: str, k: int = 3, max_move_m: float = 8.0,
                locked: frozenset[str] = frozenset(), both_teams: bool = False) -> list[Proposal]:
        """Suggest up to ``k`` distinct repositionings of ``team_id`` that raise ``target``.

        ``target`` is ``shot``, ``retain``, ``press`` or ``receiver:<player_id>``, always read from the point
        of view of ``team_id`` (a defending team's ``shot`` target is to cut the possession team's shot
        chance, its ``press`` target is to force a turnover). Each proposal moves two to four players by at
        most ``max_move_m`` (goalkeepers 6 m). With ``both_teams`` the other side then replies, moving up to
        two of its players to hurt the target, and proposals are ranked after that reply.
        """
        base_state = _State.from_frame(frame)
        team_is_poss = team_id == frame.possession_team_id
        own = {p.player_id for p in frame.players if p.team_id == team_id}
        base_ev = _evaluate(base_state, self.xt)
        base_obj = _objective(base_ev, target, team_is_poss)
        index = {pid: i for i, pid in enumerate(base_state.ids)}
        mine = [pid for pid in sorted(own) if pid not in locked]
        theirs = [p.player_id for p in sorted(frame.players, key=lambda q: q.player_id)
                  if p.team_id != team_id and p.player_id not in locked] if both_teams else []
        if not mine:
            return []

        rng = np.random.default_rng(zlib.crc32(f"{team_id}|{target}|{frame.elapsed:.3f}".encode()))
        ball = np.array(base_state.ball)
        radius = {pid: min(max_move_m, GK_MAX_MOVE_M) if base_state.is_gk[index[pid]] else max_move_m
                  for pid in mine + theirs}

        def clamp(pid: str, nx: float, ny: float) -> tuple[float, float]:
            i = index[pid]
            ox, oy = base_state.x[i], base_state.y[i]
            dx, dy = nx - ox, ny - oy
            d = float(np.hypot(dx, dy))
            if d > radius[pid]:
                dx, dy = dx * radius[pid] / d, dy * radius[pid] / d
            return (float(np.clip(ox + dx, PITCH_MARGIN, PITCH_LENGTH - PITCH_MARGIN)),
                    float(np.clip(oy + dy, PITCH_MARGIN, PITCH_WIDTH - PITCH_MARGIN)))

        def objective(moves: dict[str, tuple[float, float]]) -> float:
            return _objective(_evaluate(base_state.moved(moves), self.xt), target, team_is_poss)

        # players nearer the ball matter more, goalkeepers rarely
        near = np.array([1.0 / (4.0 + float(np.hypot(base_state.x[index[p]] - ball[0],
                                                     base_state.y[index[p]] - ball[1]))) for p in mine])
        near = np.where([base_state.is_gk[index[p]] for p in mine], near * 0.15, near)
        near = near / near.sum()

        candidates: list[tuple[float, dict[str, tuple[float, float]], dict[str, tuple[float, float]]]] = []
        restarts = max(12, 4 * k)
        for _ in range(restarts):
            n_move = int(rng.integers(2, 5))
            chosen = list(rng.choice(len(mine), size=min(n_move, len(mine)), replace=False, p=near))
            subset = [mine[i] for i in chosen]
            moves: dict[str, tuple[float, float]] = {}
            for pid in subset:
                i = index[pid]
                ang, rad = rng.uniform(0, 2 * np.pi), rng.uniform(0.3, 1.0) * radius[pid]
                moves[pid] = clamp(pid, base_state.x[i] + rad * np.cos(ang), base_state.y[i] + rad * np.sin(ang))
            best = objective(moves)
            for step in range(60):
                pid = subset[int(rng.integers(len(subset)))]
                cur = moves[pid]
                sigma = max(0.8, 0.5 * radius[pid] * (1.0 - step / 70.0))
                trial = dict(moves)
                trial[pid] = clamp(pid, cur[0] + rng.normal(0, sigma), cur[1] + rng.normal(0, sigma))
                val = objective(trial)
                if val > best:
                    moves, best = trial, val
            for pid in list(moves):  # drop moves that do not pay for themselves
                without = {p: xy for p, xy in moves.items() if p != pid}
                if without and best - objective(without) < 2e-4 + 0.02 * abs(best - base_obj):
                    moves, best = without, objective(without)
            moves = {pid: xy for pid, xy in moves.items()
                     if float(np.hypot(xy[0] - base_state.x[index[pid]], xy[1] - base_state.y[index[pid]])) > 0.5}
            reply: dict[str, tuple[float, float]] = {}
            if both_teams and theirs and moves:
                reply = self._best_reply(base_state, moves, theirs, index, radius, clamp, rng, target,
                                         team_is_poss)
                best = objective({**moves, **reply})
            candidates.append((best, moves, reply))
        candidates.sort(key=lambda c: -c[0])

        chosen_moves: list[tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]] = []
        for val, moves, reply in candidates:
            if not moves or val - base_obj < 1e-4:
                continue
            if any(_moves_distance({**moves, **reply}, {**m, **r}, base_state, index) < 2.0
                   for m, r in chosen_moves):
                continue
            chosen_moves.append((moves, reply))
            if len(chosen_moves) == k:
                break

        proposals = []
        for moves, reply in chosen_moves:
            full_moves = {**moves, **reply}
            state = base_state.moved(full_moves)
            ev = _evaluate(state, self.xt)
            result = self._result(state, ev, base_ev["value"])
            gain = {name: round(ev[name] - base_ev[name], 5)
                    for name in ("shot_prob", "goal_prob", "concede_prob", "turnover_prob", "value")}
            gain["objective"] = round(_objective(ev, target, team_is_poss) - base_obj, 5)
            proposals.append(Proposal(moves={p: (round(x, 2), round(y, 2)) for p, (x, y) in full_moves.items()},
                                      score=result, gain=gain))
        proposals.sort(key=lambda p: -p.gain["objective"])
        return proposals

    # ---- the other side's reply (both_teams) -----------------------------------------------------------------

    def _best_reply(self, base: _State, moves: dict[str, tuple[float, float]], theirs: list[str],
                    index: dict[str, int], radius: dict[str, float], clamp: Any, rng: np.random.Generator,
                    target: str, team_is_poss: bool) -> dict[str, tuple[float, float]]:
        """Greedy reply: up to two opposing players near the ball move to push the target down."""
        after = base.moved(moves)
        reply: dict[str, tuple[float, float]] = {}
        best = _objective(_evaluate(after, self.xt), target, team_is_poss)
        ball = np.array(after.ball)
        order = sorted(theirs, key=lambda p: float(np.hypot(after.x[index[p]] - ball[0], after.y[index[p]] - ball[1])))
        for pid in order[:5]:
            i = index[pid]
            for _ in range(10):
                trial = dict(reply)
                trial[pid] = clamp(pid, after.x[i] + rng.normal(0, 0.4 * radius[pid]),
                                   after.y[i] + rng.normal(0, 0.4 * radius[pid]))
                val = _objective(_evaluate(after.moved(trial), self.xt), target, team_is_poss)
                if val < best:
                    reply, best = trial, val
            if len(reply) >= 2:
                break
        return reply


def _moves_distance(a: dict[str, tuple[float, float]], b: dict[str, tuple[float, float]], base: _State,
                    index: dict[str, int]) -> float:
    """Mean displacement difference between two move sets over the players either one moves."""
    ids = set(a) | set(b)
    total = 0.0
    for pid in ids:
        i = index[pid]
        pa = a.get(pid, (base.x[i], base.y[i]))
        pb = b.get(pid, (base.x[i], base.y[i]))
        total += float(np.hypot(pa[0] - pb[0], pa[1] - pb[1]))
    return total / max(len(ids), 1)
