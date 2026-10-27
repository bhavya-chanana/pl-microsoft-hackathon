"""Turn Monte-Carlo rollouts into numbers a coach can compare.

``summarise`` reduces k rollouts to an ``OutcomeStats`` (probabilities with Wilson 95% intervals),
``rank`` orders tactics by a risk-adjusted utility against the baseline, and ``pick_variants`` chooses
the three rollouts the board replays: the typical one, the best one and how it breaks.

Meaning of the numbers, for the coached team:

* ``p_shot`` / ``p_goal`` / ``mean_xg``: the team shoots / scores / the xG it creates in the horizon.
* ``p_box_entry``: the ball enters the opponent's penalty area.
* ``p_turnover``: the team loses the ball it had at the start. It is 0 when the team starts without the
  ball; a regain then shows in ``terminals["turnover"]`` and in ``xt_delta``.
* ``p_concede_shot``: the opponent shoots (or scores) in the horizon.
* ``xt_delta``: mean change of the evaluator's value (goal chance minus conceding chance, from the
  team's side) between the first and last frame of each rollout.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Literal

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, EventType, MatchMeta
from matchlens.domain.pitch import to_relative
from matchlens.simulator.fork import RolloutTrace
from matchlens.tactics.models import Interval, OutcomeStats, TacticResult
from matchlens.tacticsml.contract import TacticalModel, TFrame, from_tracking

Risk = Literal["safe", "balanced", "bold"]

# Penalty area, team-relative: 16.5 m deep, 40.32 m wide.
BOX_X = PITCH_LENGTH - 16.5
BOX_Y = (34.0 - 20.16, 34.0 + 20.16)

# risk -> (weight on conceding a shot, weight on losing the ball)
RISK_WEIGHTS: dict[str, tuple[float, float]] = {"safe": (0.6, 0.25), "balanced": (0.3, 0.12), "bold": (0.12, 0.04)}
XT_WEIGHT = 0.5

Z95 = 1.96


def wilson(successes: int, n: int, z: float = Z95) -> Interval:
    """Observed proportion with its Wilson score interval."""
    if n <= 0:
        return Interval(value=0.0, low=0.0, high=1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return Interval(value=p, low=max(0.0, centre - half), high=min(1.0, centre + half))


# ------------------------------------------------------------------ per-trace facts


@dataclass(frozen=True)
class TraceFacts:
    team_had_ball: bool  # at the start
    shots: int
    xg: float
    goal: bool
    box_entry: bool
    lost_ball: bool
    concede_shot: bool
    first_shot_s: float | None


def restart_awarded_to(trace: RolloutTrace) -> str | None:
    """The team a rollout's ending restart (throw-in, goal kick, corner, free kick) is awarded to, if any."""
    parts = trace.terminal.split(":", 2)
    return parts[2] if len(parts) == 3 and parts[0] == "restart" else None


def possession_changed(trace: RolloutTrace, started: str) -> bool:
    """True when the side with the ball at the start no longer has it at the end: a tackle, an interception or a
    restart awarded to the other side (the ball ran out, or a foul)."""
    if trace.terminal == "turnover":
        return True
    awarded = restart_awarded_to(trace)
    return awarded is not None and awarded != started


def _start_possession(trace: RolloutTrace, team_id: str, meta: MatchMeta) -> str:
    if trace.start_possession:
        return trace.start_possession
    if trace.terminal == "turnover":
        return meta.opponent(trace.possession).team_id if trace.possession else team_id
    return trace.possession or team_id


def _ball_in_box(trace: RolloutTrace, attacking_right: bool) -> bool:
    for frame in trace.frames:
        for e in frame.entities:
            if e.entity_id == "ball":
                x, y = to_relative(e.x, e.y, attacking_right)
                if x >= BOX_X and BOX_Y[0] <= y <= BOX_Y[1]:
                    return True
                break
    return False


def facts(trace: RolloutTrace, team_id: str, meta: MatchMeta) -> TraceFacts:
    opp = meta.opponent(team_id).team_id
    start_t = trace.frames[0].elapsed if trace.frames else 0.0
    shots = [e for e in trace.events if e.type == EventType.SHOT and e.team_id == team_id]
    xg = sum(float(e.attrs.get("xg", 0.0) or 0.0) for e in shots)
    goal = any(e.type == EventType.GOAL and e.team_id == team_id for e in trace.events)
    conceded = any(e.type in (EventType.SHOT, EventType.GOAL) and e.team_id == opp for e in trace.events)
    started = _start_possession(trace, team_id, meta)
    return TraceFacts(
        team_had_ball=started == team_id,
        shots=len(shots),
        xg=xg,
        goal=goal,
        box_entry=_ball_in_box(trace, team_id == meta.home.team_id),
        lost_ball=started == team_id and possession_changed(trace, started),
        concede_shot=conceded,
        first_shot_s=(min(e.elapsed for e in shots) - start_t) if shots else None,
    )


# --------------------------------------------------------------------- summarise


def _value_frames(trace: RolloutTrace, meta: MatchMeta, started: str) -> tuple[TFrame, TFrame, str] | None:
    """(first, last, possession at the end) model frames of a trace, or None without frames. Both are built from
    two tracking frames so they carry velocities; a restart's awarded team has the ball at the end."""
    if not trace.frames:
        return None
    end_poss = restart_awarded_to(trace) or trace.possession or started
    f0 = from_tracking(trace.frames[:2], meta, started)
    f1 = from_tracking(trace.frames[-2:], meta, end_poss)
    return f0, f1, end_poss


def summarise(traces: list[RolloutTrace], team_id: str, evaluator: TacticalModel, meta: MatchMeta) -> OutcomeStats:
    """Reduce rollouts to outcome statistics for ``team_id``. ``horizon_s`` is the longest rollout seen."""
    n = len(traces)
    fs = [facts(t, team_id, meta) for t in traces]
    deltas: list[float] = []
    pairs: list[tuple[TFrame, TFrame, str, str]] = []
    for t, f in zip(traces, fs, strict=True):
        started = t.start_possession or (team_id if f.team_had_ball else meta.opponent(team_id).team_id)
        built = _value_frames(t, meta, started)
        if built is not None:
            pairs.append((*built[:2], started, built[2]))
    scores = evaluator.score_batch([x for p in pairs for x in p[:2]]) if pairs else []
    for i, (_, _, started, end_poss) in enumerate(pairs):
        s0, s1 = scores[2 * i], scores[2 * i + 1]
        deltas.append((s1.value if end_poss == team_id else -s1.value)
                      - (s0.value if started == team_id else -s0.value))
    horizon = max((t.frames[-1].elapsed - t.frames[0].elapsed for t in traces if t.frames), default=0.0)
    shot_times = [f.first_shot_s for f in fs if f.first_shot_s is not None]
    return OutcomeStats(
        rollouts=n,
        horizon_s=round(horizon, 2),
        p_shot=wilson(sum(f.shots > 0 for f in fs), n),
        mean_xg=float(np.mean([f.xg for f in fs])) if n else 0.0,
        p_goal=wilson(sum(f.goal for f in fs), n),
        p_box_entry=wilson(sum(f.box_entry for f in fs), n),
        p_turnover=wilson(sum(f.lost_ball for f in fs), n),
        p_concede_shot=wilson(sum(f.concede_shot for f in fs), n),
        xt_delta=float(np.mean(deltas)) if deltas else 0.0,
        time_to_shot_s=float(np.mean(shot_times)) if shot_times else None,
        terminals=dict(Counter(t.terminal for t in traces)),
    )


# -------------------------------------------------------------------------- rank

_LIFT_METRICS = ("p_shot", "mean_xg", "p_goal", "p_box_entry", "p_turnover", "p_concede_shot", "xt_delta")


def _value(stats: OutcomeStats, metric: str) -> float:
    v = getattr(stats, metric)
    return v.value if isinstance(v, Interval) else float(v)


def utility(stats: OutcomeStats, risk: Risk) -> float:
    lam, mu = RISK_WEIGHTS[risk]
    return stats.mean_xg + XT_WEIGHT * stats.xt_delta - lam * stats.p_concede_shot.value - mu * stats.p_turnover.value


def _disjoint(a: Interval, b: Interval) -> bool:
    return a.high < b.low or b.high < a.low


def confidence(stats: OutcomeStats, baseline: OutcomeStats) -> Literal["low", "medium", "high"]:
    """How far to trust the comparison: narrow intervals, and a metric clearly separated from the baseline."""
    pairs = [(stats.p_shot, baseline.p_shot), (stats.p_box_entry, baseline.p_box_entry),
             (stats.p_turnover, baseline.p_turnover), (stats.p_concede_shot, baseline.p_concede_shot)]
    width = float(np.mean([a.high - a.low for a, _ in pairs]))
    separated = any(_disjoint(a, b) for a, b in pairs)
    if separated and width <= 0.35:
        return "high"
    if separated or width <= 0.30:
        return "medium"
    return "low"


def clear_gain(stats: OutcomeStats, baseline: OutcomeStats) -> bool:
    """True when an idea beats doing nothing by more than the simulation noise: its shot chance is clearly
    higher, or its chance of conceding a shot clearly lower (the intervals do not overlap)."""
    return stats.p_shot.low > baseline.p_shot.high or stats.p_concede_shot.high < baseline.p_concede_shot.low


def clear_winner(ranked: list[TacticResult], baseline: OutcomeStats | None) -> TacticResult | None:
    """The best-ranked result with a clear gain over the baseline, or None when everything is within noise."""
    if baseline is None:
        return None
    return next((r for r in ranked if r.outcome is not None and clear_gain(r.outcome, baseline)), None)


def rank(results: list[TacticResult], baseline: OutcomeStats, risk: Risk) -> list[TacticResult]:
    """Results with lift, utility, rank and confidence filled in, best first.

    Results without an outcome (rejected or still thinking) keep ``rank=None`` and go last.
    """
    scored: list[TacticResult] = []
    unscored: list[TacticResult] = []
    for r in results:
        if r.outcome is None:
            unscored.append(r)
            continue
        lift = {m: _value(r.outcome, m) - _value(baseline, m) for m in _LIFT_METRICS}
        scored.append(r.model_copy(update={"lift": lift, "utility": utility(r.outcome, risk),
                                           "confidence": confidence(r.outcome, baseline)}))
    scored.sort(key=lambda r: (-(r.utility or 0.0), r.tactic_id))
    ranked = [r.model_copy(update={"rank": i}) for i, r in enumerate(scored, start=1)]
    return ranked + unscored


# ---------------------------------------------------------------------- variants


def score_trace(f: TraceFacts) -> float:
    """A per-rollout score used to choose the best and worst replay (not the ranking utility)."""
    return (f.xg + 0.3 * f.goal + 0.04 * f.box_entry + 0.02 * (f.shots > 0)
            - 0.08 * f.lost_ball - 0.12 * f.concede_shot)


def _failure_kind(f: TraceFacts) -> str | None:
    if f.concede_shot:
        return "concede_shot"
    if f.lost_ball:
        return "turnover"
    if f.team_had_ball and f.shots == 0 and not f.box_entry:
        return "stalled"
    return None


FAILURE_TEXT = {
    "concede_shot": "the opponent gets a shot away",
    "turnover": "the ball is lost",
    "stalled": "the move stalls before the box",
}


def dominant_failure(traces: list[RolloutTrace], team_id: str, meta: MatchMeta) -> str | None:
    """The most common way the rollouts go wrong ('turnover', 'concede_shot', 'stalled'), or None."""
    kinds = Counter(k for t in traces if (k := _failure_kind(facts(t, team_id, meta))))
    return kinds.most_common(1)[0][0] if kinds else None


def pick_variants(traces: list[RolloutTrace], team_id: str, meta: MatchMeta) -> dict[str, RolloutTrace]:
    """``typical`` (the rollout closest to the average outcome), ``best``, and ``worst``: the lowest-scoring
    rollout among those that fail in the most common way (how it breaks)."""
    if not traces:
        return {}
    fs = [facts(t, team_id, meta) for t in traces]
    scores = [score_trace(f) for f in fs]
    vec = np.array([[f.xg * 4, f.goal, f.box_entry, f.shots > 0, f.lost_ball, f.concede_shot] for f in fs],
                   dtype=float)
    typical = int(np.argmin(np.linalg.norm(vec - vec.mean(axis=0), axis=1)))
    best = int(np.argmax(scores))
    kinds = Counter(k for f in fs if (k := _failure_kind(f)))
    if kinds:
        top = kinds.most_common(1)[0][0]
        pool = [i for i, f in enumerate(fs) if _failure_kind(f) == top]
    else:
        pool = list(range(len(traces)))
    worst = min(pool, key=lambda i: (scores[i], i))
    return {"typical": traces[typical], "best": traces[best], "worst": traces[worst]}
