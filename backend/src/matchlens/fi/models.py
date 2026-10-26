"""Ingest contracts. Real devices and the simulator post exactly these shapes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Side = Literal["home", "away"]
Source = Literal["sensor", "mock", "llm_vision", "operator"]


class EventRef(BaseModel):
    """Optional link from an observation to the canonical event it creates or revises."""

    id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=32)
    team: Side | None = None
    player_ids: list[str] = []
    critical: bool = False
    attributes: dict = {}


class VestFrame(BaseModel):
    player_id: str
    media_ms: int = Field(ge=0)
    x: float = Field(ge=-5, le=105, description="0–100 along the pitch towards the opponent goal")
    y: float = Field(ge=-5, le=105)
    speed_mps: float = Field(ge=0, le=13)
    heart_rate: int | None = Field(default=None, ge=30, le=230)
    on_pitch: bool = True


class VestBatch(BaseModel):
    source: Source = "sensor"
    frames: list[VestFrame] = Field(max_length=20_000)


BallKind = Literal["pass", "shot", "goal_line_cross", "ball_out", "duel", "recovery", "dribble", "tackle", "interception", "clearance", "press_win"]


class BallEvent(BaseModel):
    """A ball-sensor impact, fused with the nearest vest (who touched it)."""

    id: str = Field(min_length=1, max_length=64)
    kind: BallKind
    media_ms: int = Field(ge=0)
    available_ms: int = Field(ge=0)
    team: Side | None = None
    player_id: str | None = None
    target_player_id: str | None = None
    attributes: dict = {}
    event: EventRef | None = None


class BallBatch(BaseModel):
    source: Source = "sensor"
    events: list[BallEvent] = Field(max_length=20_000)


VisionKind = Literal["foul", "yellow", "red", "offside", "substitution", "var_overturn", "goal_confirmed", "corner", "free_kick",
                     "throw_in", "save", "celebration", "other"]


class VisionObservation(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    kind: VisionKind
    media_from_ms: int = Field(ge=0)
    media_to_ms: int = Field(ge=0)
    available_ms: int = Field(ge=0)
    team: Side | None = None
    player_ids: list[str] = []
    confidence: float = Field(ge=0, le=1)
    description: str = ""
    event: EventRef | None = None


class VisionBatch(BaseModel):
    source: Source = "llm_vision"
    observations: list[VisionObservation] = Field(max_length=2_000)


class OperatorEvent(BaseModel):
    """Match operator input: period changes and manual corrections."""

    media_ms: int = Field(ge=0)
    available_ms: int = Field(ge=0)
    status: Literal["provisional", "confirmed", "overturned"] = "confirmed"
    note: str | None = None
    event: EventRef


class OperatorBatch(BaseModel):
    source: Source = "operator"
    events: list[OperatorEvent] = Field(max_length=500)


class Commentary(BaseModel):
    event_id: str
    available_ms: int = Field(ge=0)
    headline: str
    copy_by_persona: dict[str, str]
    model: str = "mock"


class CommentaryBatch(BaseModel):
    items: list[Commentary] = Field(max_length=500)


class ClockSync(BaseModel):
    source: Literal["vest", "ball", "video"]
    offset_ms: int
    method: Literal["kickoff", "manual", "pdt"] = "kickoff"


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=4, max_length=200)


class Preferences(BaseModel):
    persona: Literal["diehard", "casual", "tactical", "fantasy"] = "casual"
    favoriteTeam: str | None = Field(default=None, max_length=8)
    followedPlayers: list[str] = Field(default=[], max_length=50)
    spoilersVisible: bool = False
    language: str = Field(default="en", max_length=8)
    onboarded: bool = False
