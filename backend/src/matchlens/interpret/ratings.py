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


# ---------------------------------------------------------------- match cards
#
# FIFA-style card attributes (30-99) from this match's own data, never from any
# game's ratings: the players are fictional. They grow as the match goes on, like
# an in-form card. Shooting and defending start from a role baseline (a defender
# is a defender before the first tackle); pace and physical need tracking and are
# None without it.

OUTFIELD = ("PAC", "SHO", "PAS", "DRI", "DEF", "PHY")
GOALKEEPER = ("DIV", "HAN", "KIC", "REF", "SPD", "POS")
WEIGHTS = {
    "FWD": {"PAC": 0.2, "SHO": 0.35, "PAS": 0.1, "DRI": 0.2, "PHY": 0.15},
    "MID": {"PAC": 0.1, "SHO": 0.15, "PAS": 0.35, "DRI": 0.2, "DEF": 0.1, "PHY": 0.1},
    "DEF": {"PAC": 0.15, "PAS": 0.2, "DRI": 0.05, "DEF": 0.45, "PHY": 0.15},
    "GK": {"DIV": 0.2, "HAN": 0.2, "KIC": 0.1, "REF": 0.25, "SPD": 0.05, "POS": 0.2},
}

# Role baselines for shooting and defending.
SHO_BASE = {"FWD": 48.0, "MID": 40.0, "DEF": 34.0}
DEF_BASE = {"FWD": 30.0, "MID": 40.0, "DEF": 64.0}


def _attr(value: float | None) -> int | None:
    return None if value is None else int(round(min(99.0, max(30.0, value))))


def _num(stats: Mapping[str, Any], key: str) -> float:
    return float(stats.get(key) or 0)


def _passing(stats: Mapping[str, Any]) -> float:
    if _num(stats, "passes") < 5:
        return 45.0
    return (20 + 0.55 * _num(stats, "pass_accuracy") + 0.15 * _num(stats, "avg_pass_difficulty")
            + 3 * _num(stats, "key_passes") + 0.6 * _num(stats, "progressive_passes"))


def _pace(stats: Mapping[str, Any]) -> float | None:
    speed = stats.get("top_speed_kmh")
    return None if speed is None else 45 + (float(speed) - 24) * 50 / 12 + min(4.0, 0.4 * _num(stats, "sprints"))


def match_card(stats: Mapping[str, Any], rating: Rating, *, conceded: int = 0) -> dict[str, Any]:
    """Overall and six attributes for one player, outfield or goalkeeper."""
    position = str(stats.get("position", ""))
    saves = _num(stats, "saves")
    if position == "GK":
        kind, values = "goalkeeper", {
            "DIV": 48 + 7 * saves,
            "HAN": 55 + 5 * saves - 3 * conceded,
            "KIC": _passing(stats),
            "REF": 50 + 9 * saves,
            "SPD": _pace(stats),
            "POS": 60 + 3 * saves - 5 * conceded,
        }
    else:
        distance = stats.get("distance_km")
        kind, values = "outfield", {
            "PAC": _pace(stats),
            "SHO": SHO_BASE.get(position, 40.0) + 22 * _num(stats, "xg") + 6 * _num(stats, "shots_on_target")
            + 8 * _num(stats, "goals") + 1.5 * _num(stats, "shots"),
            "PAS": _passing(stats),
            "DRI": 40 + 1.2 * _num(stats, "carries") + 0.12 * _num(stats, "touches"),
            "DEF": DEF_BASE.get(position, 40.0) + 6 * _num(stats, "tackles") + 5 * _num(stats, "interceptions")
            + 0.4 * _num(stats, "pressures"),
            "PHY": None if distance is None else 30 + 3.5 * float(distance) + 0.1 * _num(stats, "sprints"),
        }
    attrs = {k: _attr(v) for k, v in values.items()}
    weights = {k: w for k, w in WEIGHTS.get(position, WEIGHTS["MID"]).items() if attrs.get(k) is not None}
    total = sum(weights.values())
    weighted = sum(attrs[k] * w for k, w in weights.items()) / total if total else 60.0  # type: ignore[operator]
    # Half the attributes, half the match rating (6.0 -> 65, 10.0 -> 99), so the two never disagree badly.
    from_rating = 40 + (rating.value - FLOOR) * 59 / (CEILING - FLOOR)
    keys = GOALKEEPER if kind == "goalkeeper" else OUTFIELD
    return {"kind": kind, "overall": _attr(0.5 * weighted + 0.5 * from_rating),
            "attributes": [{"key": k, "value": attrs[k]} for k in keys]}
