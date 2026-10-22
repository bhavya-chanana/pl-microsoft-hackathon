"""Pass difficulty rating (FR-013).

Difficulty is a 0-100 score built from observable factors, each reported
separately so that explanations can say *why* a pass was hard.
"""

from __future__ import annotations

from pydantic import BaseModel

from matchlens.domain.pitch import distance, point_to_segment_distance


class PassQuality(BaseModel):
    distance_m: float
    progression_m: float  # metres gained towards the opponent goal
    pressured: bool
    lane_opponents: int  # opponents within 2.5 m of the passing lane
    lofted: bool
    difficulty: float  # 0..100
    band: str  # easy | medium | hard | elite
    components: dict[str, float]


def band_for(difficulty: float) -> str:
    if difficulty >= 75:
        return "elite"
    if difficulty >= 55:
        return "hard"
    if difficulty >= 30:
        return "medium"
    return "easy"


def rate_pass(
    start: tuple[float, float],
    end: tuple[float, float],
    attacking_right: bool,
    pressured: bool,
    lofted: bool,
    opponent_positions: list[tuple[float, float]] | None,
) -> PassQuality:
    d = distance(start, end)
    progression = (end[0] - start[0]) if attacking_right else (start[0] - end[0])
    lane = 0
    if opponent_positions:
        lane = sum(1 for o in opponent_positions if point_to_segment_distance(o, start, end) < 2.5)
    components = {
        "distance": round(min(d / 50, 1.0) * 35, 1),
        "progression": round(min(max(progression / 40, 0.0), 1.0) * 20, 1),
        "pressure": 15.0 if pressured else 0.0,
        "lane_opponents": round(min(lane, 3) / 3 * 20, 1),
        "lofted": 10.0 if lofted else 0.0,
    }
    difficulty = round(min(sum(components.values()), 100.0), 1)
    return PassQuality(
        distance_m=round(d, 1),
        progression_m=round(progression, 1),
        pressured=pressured,
        lane_opponents=lane,
        lofted=lofted,
        difficulty=difficulty,
        band=band_for(difficulty),
        components=components,
    )
