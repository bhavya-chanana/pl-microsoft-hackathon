"""Calibration report: compare simulator output with real match statistics (FR-008).

Event metrics are checked against the real-data profile built by reference.py (aggregate statistics of real
top-flight matches). That data has no tracking, so running distance and top speed are checked against rounded
published ranges instead.

Run: uv run python -m matchlens.simulator.calibration --matches 20
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from typing import Any

from matchlens.domain.models import SyntheticMatch
from matchlens.simulator.engine import generate_match
from matchlens.simulator.reference import METRICS, load_profile, measure_simulated
from matchlens.simulator.storyline import Storyline

# Tracking metrics: rounded from widely published season averages (no tracking in the real-data profile).
REFERENCE: dict[str, tuple[float, float]] = {
    "outfield_distance_km": (9.5, 11.5),
    "top_speed_kmh": (31, 36),
}
# How far the simulated mean may sit from the real mean, in real match-to-match standard deviations.
TOLERANCE_SD = 1.0


def measure(seed: int, storyline: Storyline | None) -> dict[str, float]:
    return measure_match(generate_match(seed=seed, storyline=storyline))


def measure_match(m: SyntheticMatch) -> dict[str, float]:
    """Every real-data metric (reference.py) plus outfield distance and top speed from tracking."""
    dist: dict[str, float] = defaultdict(float)
    top: dict[str, float] = defaultdict(float)
    prev_period = None
    for f in m.tracking:
        same = prev_period == f.period
        prev_period = f.period
        for en in f.entities:
            if en.entity_id == "ball" or not same:
                continue
            dist[en.entity_id] += en.speed
            top[en.entity_id] = max(top[en.entity_id], en.speed)
    outfield_full = [
        d for pid, d in dist.items() if m.meta.players[pid].role != "GK" and d > 7000
    ]
    return {
        **{k: v for k, v in measure_simulated(m).items() if v is not None},
        "outfield_distance_km": statistics.mean(outfield_full) / 1000,
        "top_speed_kmh": max(top.values()) * 3.6,
    }


def targets() -> dict[str, tuple[float, float]]:
    """Accepted range for the mean of each metric: real mean +/- TOLERANCE_SD real sd, plus tracking ranges."""
    real = load_profile()["metrics"]
    out = {k: (round(v["mean"] - TOLERANCE_SD * v["sd"], 2), round(v["mean"] + TOLERANCE_SD * v["sd"], 2))
           for k, v in real.items()}
    return {**out, **REFERENCE}


def calibration_reference() -> dict[str, Any]:
    """What the published dataset records as its calibration target."""
    profile = load_profile()
    return {
        "source": profile["source"],
        "scope": profile["scope"],
        "matches": profile["matches"],
        "tolerance_sd": TOLERANCE_SD,
        "metrics": {k: {**v, "definition": METRICS[k]} for k, v in profile["metrics"].items()},
        "tracking_ranges": {k: list(v) for k, v in REFERENCE.items()},
    }


NEUTRAL = Storyline(
    title="Calibration",
    summary="Unscripted match: no beats, goals come from the xG model.",
    scripted_goals_only=False,
)


def report(matches: int = 20, storyline: Storyline | None = None) -> list[tuple[str, float, float, float, bool]]:
    """Calibrate on unscripted play by default, so storyline beats do not skew averages."""
    rows = [measure(seed, storyline or NEUTRAL) for seed in range(1, matches + 1)]
    out = []
    for key, (lo, hi) in targets().items():
        mean = statistics.mean(r[key] for r in rows)
        out.append((key, round(mean, 2), lo, hi, lo <= mean <= hi))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", type=int, default=20)
    args = parser.parse_args()
    real = load_profile()["metrics"]
    print(f"{'metric':<26}{'simulated':>10}{'real':>9}{'accepted':>18}  ok")
    for key, mean, lo, hi, ok in report(args.matches):
        ref = f"{real[key]['mean']:.2f}" if key in real else "-"
        print(f"{key:<26}{mean:>10}{ref:>9}{f'{lo}-{hi}':>18}  {'yes' if ok else 'NO'}")


if __name__ == "__main__":
    main()
