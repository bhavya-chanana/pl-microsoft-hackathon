"""Data-driven player ratings on the familiar 0-10 scale (fan ratings, BRD §6.2).

Every player starts at 6.0 and moves with what the stats engine has counted:
end product (goals, assists, xG), chance creation, defending, passing, saves,
discipline and goals conceded. The weights are deliberately simple and
published with each rating as ``parts``, so a fan can see why the data
disagrees with them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

BASE = 6.0
FLOOR, CEILING = 3.0, 10.0
MIN_PASSES = 10  # pass accuracy counts only with a real sample
# Goals conceded weigh on the keeper and the back line.
CONCEDED_WEIGHT = {"GK": 0.25, "DEF": 0.15}
CLEAN_SHEET = {"GK": 0.4, "DEF": 0.3}


@dataclass(frozen=True)
class Rating:
    player_id: str
    value: float
    # Each component's contribution, largest first (keys are labels for the UI).
    parts: list[tuple[str, float]]

    def as_dict(self) -> dict[str, Any]:
        return {"player_id": self.player_id, "data_rating": self.value,
                "parts": [{"key": k, "delta": d} for k, d in self.parts]}


def has_played(stats: Mapping[str, Any]) -> bool:
    return bool(stats.get("on_pitch") or stats.get("touches") or stats.get("passes"))


def data_rating(stats: Mapping[str, Any], *, conceded: int = 0, finished: bool = False) -> Rating:
    """Rate one player from ``MatchEngine.player_stats`` output. ``conceded`` is their
    team's goals against; ``finished`` turns a zero into a clean sheet."""
    position = str(stats.get("position", ""))
    passes = int(stats.get("passes") or 0)
    accuracy = stats.get("pass_accuracy")
    parts = {
        "goals": 1.0 * int(stats.get("goals") or 0),
        "assists": 0.7 * int(stats.get("assists") or 0),
        "xg": 0.6 * float(stats.get("xg") or 0.0),
        "chance_creation": 0.25 * int(stats.get("key_passes") or 0) + 0.04 * int(stats.get("progressive_passes") or 0)
        + 0.1 * int(stats.get("shots_on_target") or 0),
        "defending": 0.12 * (int(stats.get("tackles") or 0) + int(stats.get("interceptions") or 0))
        + 0.02 * int(stats.get("pressures") or 0),
        "passing": (float(accuracy) - 80.0) / 100.0 * 1.5 if passes >= MIN_PASSES and accuracy is not None else 0.0,
        "saves": 0.35 * int(stats.get("saves") or 0),
        "discipline": -0.3 * int(stats.get("yellow_cards") or 0) - (1.5 if stats.get("red_card") else 0.0)
        - 0.05 * int(stats.get("fouls") or 0),
        "conceded": -CONCEDED_WEIGHT.get(position, 0.0) * conceded
        + (CLEAN_SHEET.get(position, 0.0) if finished and conceded == 0 else 0.0),
    }
    value = min(CEILING, max(FLOOR, BASE + sum(parts.values())))
    shown = sorted(((k, round(v, 2)) for k, v in parts.items() if abs(v) >= 0.01), key=lambda kv: -abs(kv[1]))
    return Rating(str(stats["player_id"]), round(value, 1), shown)
