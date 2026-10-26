"""Published models of the Tactics Board (what the API returns and the UI renders).

These are assembled in code from simulator rollouts and evaluator scores, never
returned by a model directly. The LLM-facing model is ``intent.TacticIntent``.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from matchlens.domain.models import Event, TrackingFrame
from matchlens.simulator.style import SimStyle
from matchlens.tactics.intent import TacticIntent


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlayerSpot(_Model):
    player_id: str
    name: str
    team_id: str
    role: str
    x: float
    y: float
    tags: list[str] = Field(default_factory=list)  # real-data role tags, e.g. "aerial threat"


class Situation(_Model):
    """The frozen moment a board is built from."""

    match_id: str
    elapsed: float
    minute_label: str
    period: int
    score: dict[str, int]
    possession_team_id: str
    carrier_id: str | None
    ball: tuple[float, float]
    ball_zone: str  # tactics.intent zone of the ball, for the possession team
    phase: Literal["build_up", "progression", "final_third", "transition", "set_piece", "defending"]
    coaching_team_id: str
    attacking_right: dict[str, bool] = Field(default_factory=dict)  # team_id -> attacks towards x=105
    players: list[PlayerSpot]
    frame: TrackingFrame


class Interval(_Model):
    value: float
    low: float
    high: float


class OutcomeStats(_Model):
    """Monte-Carlo summary of k rollouts of one tactic (or the baseline)."""

    rollouts: int
    horizon_s: float
    p_shot: Interval
    mean_xg: float
    p_goal: Interval
    p_box_entry: Interval
    p_turnover: Interval
    p_concede_shot: Interval
    xt_delta: float  # mean evaluator value change over the rollout
    time_to_shot_s: float | None
    terminals: dict[str, int]  # terminal kind -> count


class TacticResult(_Model):
    tactic_id: str
    persona: str  # "recipe", "positional", "gegenpress", "direct", "wide", "maverick"
    intent: TacticIntent
    outcome: OutcomeStats | None = None
    lift: dict[str, float] = Field(default_factory=dict)  # metric -> change against the baseline
    utility: float | None = None
    rank: int | None = None
    status: Literal["thinking", "evaluating", "ready", "rejected"] = "thinking"
    problems: list[str] = Field(default_factory=list)  # validation issues when rejected
    confidence: Literal["low", "medium", "high"] | None = None


class Verdict(_Model):
    headline: str
    text: str
    recommended_tactic_id: str | None
    cited_metrics: list[str]


class TacticsBoard(_Model):
    board_id: str
    status: Literal["thinking", "evaluating", "done", "failed"]
    situation: Situation
    prompt: str | None
    risk: Literal["safe", "balanced", "bold"]
    horizon_s: float
    baseline: OutcomeStats | None = None
    tactics: list[TacticResult] = Field(default_factory=list)
    verdict: Verdict | None = None
    evaluator: str  # TacticalModel.version
    model_label: str
    recorded: bool = False  # answered from the demo cassette
    errors: list[str] = Field(default_factory=list)


class Drawing(_Model):
    kind: Literal["path", "zone", "lane", "shot", "arrow"]
    team_id: str | None
    points: list[tuple[float, float]]
    label: str | None = None


class RolloutView(_Model):
    tactic_id: str  # "baseline" for the do-nothing branch
    variant: Literal["typical", "best", "worst"]
    seed: int
    frames: list[TrackingFrame]  # 5 Hz
    events: list[Event]
    drawings: list[Drawing]
    terminal: str


class StyleProvider(Protocol):
    """Where team styles and player context come from (derived from play, or real league data)."""

    def style(self, team_id: str) -> SimStyle: ...

    def player_tags(self, player_id: str) -> list[str]: ...

    def context(self, team_id: str) -> dict[str, object]:
        """Playbook, mentality and similar-match context for the tactician (JSON-serialisable)."""
        ...
