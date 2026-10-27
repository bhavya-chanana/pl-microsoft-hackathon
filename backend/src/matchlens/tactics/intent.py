"""The tactic language an agent writes and the simulator executes.

A ``TacticIntent`` describes what one team tries in the next 5 to 10 seconds:
which players go where (directives), how the ball should travel (ball plan),
how to press, and small style changes. The agent decides what to try; the
simulator decides what happens.

It is an LLM-facing model, so it follows the strict-schema rules of
``agents/contracts.py``: no dicts, no defaults, every field required (nullable
where optional), ``extra="forbid"``. ``tactics.validate.check_intent`` checks
it against the match before it is simulated.

Zones are named relative to the team's attacking direction: lanes run from the
team's left touchline (``left``) to its right (``right``), thirds from its own
goal (``def``) to the opposition goal (``final``), as seen by the team.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH
from matchlens.domain.pitch import to_absolute


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


LANES = ("left", "left_half", "centre", "right_half", "right")
THIRDS = ("def", "mid", "att", "final")

# Lane centres (team-relative y; y=68 is the team's left touchline) and third centres (x).
_LANE_Y = {"left": 61.0, "left_half": 47.0, "centre": 34.0, "right_half": 21.0, "right": 7.0}
_THIRD_X = {"def": 14.0, "mid": 42.0, "att": 68.0, "final": 92.0}

SPOTS: dict[str, tuple[float, float]] = {
    "box": (96.0, 34.0),
    "six_yard": (101.5, 34.0),
    "near_post": (101.0, 30.0),  # near post when attacking down the right
    "far_post": (101.0, 40.0),
    "penalty_spot": (94.0, 34.0),
    "cutback_spot": (91.0, 34.0),
    "edge_of_box": (86.0, 34.0),
}

ZONES: tuple[str, ...] = tuple(f"{lane}:{third}" for lane in LANES for third in THIRDS) + tuple(SPOTS)


def zone_center(zone: str, attacking_right: bool) -> tuple[float, float]:
    """Absolute pitch coordinates of a named zone for a team."""
    if zone in SPOTS:
        x, y = SPOTS[zone]
    else:
        lane, third = zone.split(":")
        x, y = _THIRD_X[third], _LANE_Y[lane]
    x = min(max(x, 0.0), PITCH_LENGTH)
    y = min(max(y, 0.0), PITCH_WIDTH)
    return to_absolute(x, y, attacking_right)


class Action(StrEnum):
    RUN_TO = "run_to"  # sprint into zone
    OVERLAP = "overlap"  # go round the outside of the ball carrier
    UNDERLAP = "underlap"  # run inside the ball carrier
    HOLD_WIDTH = "hold_width"
    DROP_DEEP = "drop_deep"  # come short to receive
    PIN_DEFENDER = "pin_defender"  # stay on target_player to fix him
    ATTACK_BOX = "attack_box"
    PRESS_PLAYER = "press_player"
    MAN_MARK = "man_mark"
    SCREEN_LANE = "screen_lane"  # block the passing lane through zone
    REST_DEFENCE = "rest_defence"  # stay behind the ball as cover


class BallAction(StrEnum):
    PASS = "pass"
    LONG_BALL = "long_ball"
    SWITCH = "switch"
    CROSS = "cross"
    CUTBACK = "cutback"
    CARRY = "carry"
    SHOOT = "shoot"


class Mechanism(StrEnum):
    OVERLOAD = "overload"
    ISOLATION = "isolation"
    THIRD_MAN = "third_man"
    SWITCH = "switch"
    RUN_IN_BEHIND = "run_in_behind"
    PRESS_TRAP = "press_trap"
    CUTBACK = "cutback"
    SET_PIECE = "set_piece"
    COUNTER = "counter"
    LOW_BLOCK = "low_block"


class PressTrigger(StrEnum):
    IMMEDIATE = "immediate"
    BACK_PASS = "back_pass"
    TOUCHLINE = "touchline"
    HEAVY_TOUCH = "heavy_touch"
    NONE = "none"


class RoleDirective(_Strict):
    player_id: str
    action: Action
    zone: str | None  # one of ZONES
    target_player_id: str | None  # for pin_defender, press_player, man_mark
    start_s: float = Field(ge=0, le=10)  # seconds after the moment
    end_s: float = Field(ge=0, le=10)


class BallStep(_Strict):
    action: BallAction
    from_player_id: str | None
    to_player_id: str | None
    to_zone: str | None


class PressPlan(_Strict):
    trigger: PressTrigger
    cover_shadow_on: str | None  # opponent player whose passing lane is screened
    intensity: float = Field(ge=0.3, le=2.0)  # the press_intensity knob while the plan is active


class StyleChange(_Strict):
    knob: str  # a SimStyle field name
    delta: float  # added to the team's style, clamped to the knob's bounds


class TacticIntent(_Strict):
    team_id: str
    title: str = Field(max_length=60)
    idea: str = Field(max_length=280)  # one or two sentences, for the card
    mechanism: Mechanism
    directives: list[RoleDirective] = Field(max_length=6)
    ball_plan: list[BallStep] = Field(max_length=4)
    press: PressPlan | None
    style_changes: list[StyleChange] = Field(max_length=4)
    rest_defence: int = Field(ge=0, le=6)  # players kept behind the ball
    cites: list[str]  # context keys the idea relies on (player tags, playbook patterns, stats)
