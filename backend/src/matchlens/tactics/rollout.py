"""Monte-Carlo rollouts of a tactic from a frozen moment.

``run_monte_carlo`` forks the simulator ``k`` times from one snapshot, each with its own deterministic
seed, optionally with a ``TacticIntent`` executing on the coached team, and returns the traces. The
same arguments always give the same traces, so a board can be rebuilt, cached and replayed.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections import OrderedDict

from matchlens.simulator.fork import RolloutTrace, SimSnapshot
from matchlens.simulator.style import NEUTRAL, SimStyle
from matchlens.tactics.intent import TacticIntent

CACHE_SIZE = 64
BASELINE_ID = "baseline"
MAX_PARALLEL = 2  # simulation threads at once, across all boards: a rollout run is pure CPU

_slots = threading.BoundedSemaphore(MAX_PARALLEL)


class RolloutCancelled(Exception):
    """The board that asked for these rollouts was cancelled or ran out of time."""

_cache: OrderedDict[tuple, list[RolloutTrace]] = OrderedDict()
_lock = threading.Lock()


def _digest(data: str) -> str:
    return hashlib.blake2b(data.encode(), digest_size=8).hexdigest()


def snapshot_digest(snap: SimSnapshot) -> str:
    """A stable fingerprint of the frozen moment: who is where, who has the ball, the score and the random stream."""
    s = snap.state
    positions = sorted((p, round(x, 2), round(y, 2)) for p, (x, y) in s.positions.items())
    rng = hashlib.blake2b(repr(snap.rng_state).encode(), digest_size=8).hexdigest()
    return _digest(repr((snap.meta.match_id, round(snap.elapsed, 3), s.possession, s.carrier,
                         tuple(round(v, 2) for v in s.ball), sorted(s.score.items()), positions,
                         sorted(s.sent_off), snap.seed, rng)))


def intent_hash(intent: TacticIntent | None) -> str:
    return BASELINE_ID if intent is None else _digest(intent.model_dump_json())


def tactic_id_of(intent: TacticIntent | None) -> str:
    return BASELINE_ID if intent is None else f"t-{intent_hash(intent)[:8]}"


def seed_for(base_seed: str, tactic_id: str, i: int) -> int:
    return int.from_bytes(hashlib.blake2b(f"{base_seed}|{tactic_id}|{i}".encode()).digest()[:4])


def apply_style_changes(
    styles: dict[str, SimStyle] | None, intent: TacticIntent | None
) -> dict[str, SimStyle]:
    """The styles with the intent's ``style_changes`` added to the coached team's (clamped to bounds)."""
    out = dict(styles or {})
    if intent is not None and intent.style_changes:
        base = out.get(intent.team_id, NEUTRAL)
        out[intent.team_id] = base.with_delta({c.knob: c.delta for c in intent.style_changes})
    return out


def _styles_key(styles: dict[str, SimStyle] | None) -> str:
    return repr(sorted((t, s.model_dump_json()) for t, s in (styles or {}).items()))


def run_monte_carlo(
    snap: SimSnapshot,
    intent: TacticIntent | None,
    styles: dict[str, SimStyle] | None,
    *,
    k: int = 24,
    horizon_s: float = 10.0,
    frame_dt: float = 0.2,
    base_seed: str,
    tactic_id: str | None = None,
    use_cache: bool = True,
    cancel: threading.Event | None = None,
) -> list[RolloutTrace]:
    """``k`` rollouts of ``horizon_s`` seconds from ``snap``; ``intent=None`` is the do-nothing baseline.

    Seed ``i`` is ``blake2b(base_seed|tactic_id|i)``. ``tactic_id`` defaults to ``baseline`` or a hash of the
    intent. Results are cached (an LRU of 64 runs) and must be treated as read-only. At most ``MAX_PARALLEL``
    runs compute at once; ``cancel`` (when set) stops a run between rollouts with ``RolloutCancelled``.
    """
    tid = tactic_id or tactic_id_of(intent)
    key = (snapshot_digest(snap), intent_hash(intent), _styles_key(styles), base_seed, tid, k,
           round(horizon_s, 3), round(frame_dt, 3))
    if use_cache:
        with _lock:
            hit = _cache.get(key)
            if hit is not None:
                _cache.move_to_end(key)
                return hit

    run_styles = apply_style_changes(styles, intent)
    attacking_right = {snap.meta.home.team_id: True, snap.meta.away.team_id: False}
    traces: list[RolloutTrace] = []
    with _slots:
        _simulate(snap, intent, run_styles, attacking_right, traces, k, horizon_s, frame_dt, base_seed, tid, cancel)

    if use_cache:
        with _lock:
            _cache[key] = traces
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
    return traces


def _simulate(snap: SimSnapshot, intent: TacticIntent | None, run_styles: dict[str, SimStyle],
              attacking_right: dict[str, bool], traces: list[RolloutTrace], k: int, horizon_s: float,
              frame_dt: float, base_seed: str, tid: str, cancel: threading.Event | None) -> None:
    from matchlens.simulator.engine import MatchSimulator
    from matchlens.simulator.intent_exec import IntentController

    for i in range(k):
        if cancel is not None and cancel.is_set():
            raise RolloutCancelled()
        controller = (IntentController(intent, snap.meta, attacking_right, snap.elapsed)
                      if intent is not None else None)
        sim = MatchSimulator.resume(snap, seed=seed_for(base_seed, tid, i), styles=run_styles,
                                    intent=controller, frame_dt=frame_dt, id_prefix=f"r{i}-")
        traces.append(sim.rollout(horizon_s))


async def run_monte_carlo_async(
    snap: SimSnapshot,
    intent: TacticIntent | None,
    styles: dict[str, SimStyle] | None,
    *,
    k: int = 24,
    horizon_s: float = 10.0,
    frame_dt: float = 0.2,
    base_seed: str,
    tactic_id: str | None = None,
    use_cache: bool = True,
    cancel: threading.Event | None = None,
) -> list[RolloutTrace]:
    """``run_monte_carlo`` in a worker thread, so a board can evaluate several tactics without blocking the loop."""
    return await asyncio.to_thread(run_monte_carlo, snap, intent, styles, k=k, horizon_s=horizon_s,
                                   frame_dt=frame_dt, base_seed=base_seed, tactic_id=tactic_id, use_cache=use_cache,
                                   cancel=cancel)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
