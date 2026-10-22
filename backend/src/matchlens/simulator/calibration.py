"""Calibration report: compare simulator output with public match averages (FR-008).

Run: uv run python -m matchlens.simulator.calibration --matches 20
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

from matchlens.domain.models import EventType, Outcome
from matchlens.simulator.engine import generate_match
from matchlens.simulator.storyline import Storyline

# Approximate per-match figures (both teams combined unless noted) for a top
# European league, rounded from widely published season averages. They are
# ranges for plausibility checks, not exact targets.
REFERENCE: dict[str, tuple[float, float]] = {
    "passes": (850, 1150),
    "pass_accuracy_pct": (80, 87),
    "shots": (20, 30),
    "goals": (2.2, 3.4),
    "shots_on_target_pct": (30, 45),
    "corners": (6, 12),
    "fouls": (16, 26),
    "yellow_cards": (2.5, 5.0),
    "outfield_distance_km": (9.5, 11.5),
    "top_speed_kmh": (31, 36),
}


def measure(seed: int, storyline: Storyline | None) -> dict[str, float]:
    m = generate_match(seed=seed, storyline=storyline)
    ev = m.events
    passes = [e for e in ev if e.type == EventType.PASS]
    shots = [e for e in ev if e.type == EventType.SHOT]
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
        "passes": len(passes),
        "pass_accuracy_pct": sum(e.outcome == Outcome.SUCCESS for e in passes) / len(passes) * 100,
        "shots": len(shots),
        "goals": sum(e.type == EventType.GOAL for e in ev),
        "shots_on_target_pct": sum(e.outcome in (Outcome.SAVED, Outcome.GOAL) for e in shots) / max(len(shots), 1) * 100,
        "corners": sum(e.type == EventType.CORNER for e in ev),
        "fouls": sum(e.type == EventType.FOUL for e in ev),
        "yellow_cards": sum(e.type == EventType.CARD and e.outcome == Outcome.YELLOW for e in ev),
        "outfield_distance_km": statistics.mean(outfield_full) / 1000,
        "top_speed_kmh": max(top.values()) * 3.6,
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
    for key, (lo, hi) in REFERENCE.items():
        mean = statistics.mean(r[key] for r in rows)
        out.append((key, round(mean, 1), lo, hi, lo <= mean <= hi))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", type=int, default=20)
    args = parser.parse_args()
    print(f"{'metric':<24}{'simulated':>10}{'reference':>16}  ok")
    for key, mean, lo, hi, ok in report(args.matches):
        print(f"{key:<24}{mean:>10}{f'{lo}-{hi}':>16}  {'yes' if ok else 'NO'}")


if __name__ == "__main__":
    main()
