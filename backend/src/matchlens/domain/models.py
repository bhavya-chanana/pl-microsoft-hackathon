"""Provider-neutral match data schema (BRD §9).

Every feed adapter converts provider data into these models, so everything
downstream (stats engine, agents, UI) is independent of the data source.

Pitch coordinates are metres on a 105 x 68 pitch. x runs from the home goal
(x=0) to the away goal (x=105); y runs from the bottom touchline (y=0) to the
top touchline (y=68). For simplicity the home team attacks towards x=105 in
both halves.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Point(_Model):
    x: float = Field(ge=0, le=PITCH_LENGTH)
    y: float = Field(ge=0, le=PITCH_WIDTH)


class Side(StrEnum):
    HOME = "home"
    AWAY = "away"


class Position(StrEnum):
    GK = "GK"
    DEF = "DEF"
    MID = "MID"
    FWD = "FWD"


class Player(_Model):
    player_id: str
    name: str
    short_name: str
    shirt: int
    position: Position
    role: str  # e.g. "LB", "CM", "RW" - slot in the formation
    nationality: str
    top_speed: float = 8.8  # m/s, the player's maximum sprint speed
    # Optional career context, used for milestone detection.
    career_appearances: int = 0
    career_goals: int = 0


class Team(_Model):
    team_id: str
    name: str
    short_name: str
    side: Side
    colour: str  # hex, used by the UI
    formation: str
    starting_xi: list[str]  # player_ids, in formation-slot order
    bench: list[str]


class MatchMeta(_Model):
    match_id: str
    competition: str
    venue: str
    kickoff_utc: str
    home: Team
    away: Team
    players: dict[str, Player]
    storyline_title: str | None = None

    def team(self, team_id: str) -> Team:
        if team_id == self.home.team_id:
            return self.home
        if team_id == self.away.team_id:
            return self.away
        raise KeyError(team_id)

    def opponent(self, team_id: str) -> Team:
        return self.away if team_id == self.home.team_id else self.home


class EventType(StrEnum):
    KICKOFF = "kickoff"
    PASS = "pass"
    CARRY = "carry"
    PRESSURE = "pressure"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    POSSESSION_CHANGE = "possession_change"
    SHOT = "shot"
    SAVE = "save"
    GOAL = "goal"
    FOUL = "foul"
    CARD = "card"
    FREE_KICK = "free_kick"
    CORNER = "corner"
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    SUBSTITUTION = "substitution"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"
    CORRECTION = "correction"


class Outcome(StrEnum):
    SUCCESS = "success"
    FAIL = "fail"
    # Shot outcomes
    GOAL = "goal"
    SAVED = "saved"
    OFF_TARGET = "off_target"
    BLOCKED = "blocked"
    # Card outcomes
    YELLOW = "yellow"
    SECOND_YELLOW = "second_yellow"
    RED = "red"
    NONE = "none"


class Event(_Model):
    event_id: str
    match_id: str
    sequence: int  # monotonically increasing in generation order
    period: int  # 1 or 2
    # Display match clock in seconds. Period 2 starts at 2700, so first-half
    # stoppage time (clock > 2700 in period 1) is shown as 45+N'.
    clock: float
    # Monotonic seconds since kickoff, including stoppage time but excluding
    # the half-time interval. Used for ordering, replay timing and windows.
    elapsed: float
    type: EventType
    team_id: str | None = None
    player_id: str | None = None
    related_player_id: str | None = None  # receiver, tackled player, sub coming on...
    start: Point | None = None
    end: Point | None = None
    outcome: Outcome = Outcome.NONE
    attrs: dict[str, float | int | str | bool] = Field(default_factory=dict)
    # For CORRECTION events: the event being amended and the replacement fields.
    corrects_event_id: str | None = None

    @property
    def minute(self) -> int:
        """Broadcast-style minute (1-based), e.g. clock 0-59s -> 1'."""
        return display_minute(self.period, self.clock)[0]

    @property
    def minute_label(self) -> str:
        return minute_label(self.period, self.clock)


def display_minute(period: int, clock: float) -> tuple[int, int]:
    """Return (minute, added_minutes) as shown on a broadcast clock."""
    limit = 2700 if period == 1 else 5400
    if clock >= limit:
        return limit // 60, int((clock - limit) // 60) + 1
    return int(clock // 60) + 1, 0


def minute_label(period: int, clock: float) -> str:
    minute, added = display_minute(period, clock)
    return f"{minute}+{added}'" if added else f"{minute}'"


class EntityPosition(_Model):
    entity_id: str  # player_id or "ball"
    x: float
    y: float
    speed: float  # m/s


class TrackingFrame(_Model):
    match_id: str
    clock: float
    elapsed: float
    period: int
    entities: list[EntityPosition]


class SyntheticMatch(_Model):
    """A complete generated match: metadata, events and tracking."""

    meta: MatchMeta
    events: list[Event]
    tracking: list[TrackingFrame]
    seed: int
