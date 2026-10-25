"""Interface of the tactical models (the evaluator and proposer the Tactics Board uses).

Two implementations sit behind ``TacticalModel``:

* ``baseline.BaselineModel``: pitch control plus an expected-threat grid,
  numpy only, always available.
* ``learned.LearnedModel``: a D2-equivariant graph attention network trained
  on open tracking data, run in numpy from exported weights.

``registry.get_tactical_model()`` returns the learned model when weights are
present and the baseline otherwise, so callers never import torch.

Coordinates are absolute metres on the 105 x 68 pitch (``domain.models``).
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from matchlens.domain.models import MatchMeta, Position, TrackingFrame


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TPlayer(_Model):
    player_id: str
    team_id: str
    x: float
    y: float
    vx: float | None = None  # m/s; None when unknown (single frame without history)
    vy: float | None = None
    is_gk: bool = False
    top_speed: float = 8.8
    # Optional real-data profile features (e.g. "pace", "height_cm", "aerial", "xt90"), see leaguedata.
    profile: dict[str, float] | None = None


class TBall(_Model):
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0


class TFrame(_Model):
    """One moment of play, the input of every model call."""

    players: list[TPlayer]
    ball: TBall
    carrier_id: str | None
    possession_team_id: str
    attacking_right: dict[str, bool]  # team_id -> attacks towards x=105
    elapsed: float = 0.0
    history: list[TFrame] = Field(default_factory=list)  # up to 4 earlier frames, oldest first
    set_piece: Literal["corner", "free_kick", "throw_in", "goal_kick"] | None = None


class Intent(_Model):
    """Positional change to score: players moved before the model runs."""

    moves: dict[str, tuple[float, float]] = Field(default_factory=dict)  # player_id -> new (x, y)
    target: str = "shot"  # "shot" | "retain" | "press" | "receiver:<player_id>"


class ScoreResult(_Model):
    receiver_probs: dict[str, float]  # next ball receiver among the possession team
    shot_prob: float  # possession team shoots within 10 s
    shot_std: float = 0.0
    goal_prob: float = 0.0  # possession team scores within 10 s
    concede_prob: float = 0.0  # possession team concedes within 10 s
    value: float = 0.0  # goal_prob - concede_prob (VAEP-style)
    xt_delta: float | None = None  # value change against the reference frame
    turnover_prob: float = 0.0  # possession lost within 5 s
    turnover_std: float = 0.0
    pitch_control: list[list[float]] | None = None  # rows of y, columns of x; possession team share
    embedding: list[float] = Field(default_factory=list)
    model_version: str


class Proposal(_Model):
    moves: dict[str, tuple[float, float]]
    score: ScoreResult
    gain: dict[str, float]  # metric -> change against the unmodified frame


class RetrievedMoment(_Model):
    source: str  # e.g. "statsbomb360", "pff", "synthetic"
    match_label: str
    elapsed: float
    similarity: float
    outcome: str  # what happened next, e.g. "shot", "turnover"


class TacticalModel(Protocol):
    version: str

    def score(self, frame: TFrame, intent: Intent | None = None,
              reference: TFrame | None = None) -> ScoreResult:
        """Score a moment for ``frame.possession_team_id``. ``intent.moves`` are applied first;
        ``xt_delta`` compares against ``reference`` (default: the frame without the intent)."""
        ...

    def score_batch(self, frames: list[TFrame]) -> list[ScoreResult]: ...

    def propose(self, frame: TFrame, team_id: str, target: str, k: int = 3, max_move_m: float = 8.0,
                locked: frozenset[str] = frozenset(), both_teams: bool = False) -> list[Proposal]:
        """Suggest repositionings of ``team_id`` that move ``target`` the most."""
        ...

    def similar(self, frame: TFrame, k: int = 5) -> list[RetrievedMoment]: ...


def from_tracking(frames: list[TrackingFrame], meta: MatchMeta, possession_team_id: str,
                  carrier_id: str | None = None) -> TFrame:
    """Build a ``TFrame`` from the last of ``frames`` (earlier ones become history and velocities)."""
    if not frames:
        raise ValueError("from_tracking needs at least one frame")
    last = frames[-1]
    prev = frames[-2] if len(frames) > 1 else None
    dt = (last.elapsed - prev.elapsed) if prev else 0.0
    before = {e.entity_id: e for e in prev.entities} if prev else {}

    def velocity(eid: str, x: float, y: float) -> tuple[float | None, float | None]:
        p = before.get(eid)
        if p is None or dt <= 0:
            return None, None
        return (x - p.x) / dt, (y - p.y) / dt

    players: list[TPlayer] = []
    ball = TBall(x=52.5, y=34.0)
    for e in last.entities:
        vx, vy = velocity(e.entity_id, e.x, e.y)
        if e.entity_id == "ball":
            ball = TBall(x=e.x, y=e.y, vx=vx or 0.0, vy=vy or 0.0)
            continue
        info = meta.players.get(e.entity_id)
        if info is None:
            continue
        team_id = meta.home.team_id if e.entity_id in _squad(meta, meta.home.team_id) else meta.away.team_id
        players.append(TPlayer(player_id=e.entity_id, team_id=team_id, x=e.x, y=e.y, vx=vx, vy=vy,
                               is_gk=info.position == Position.GK, top_speed=info.top_speed))
    if carrier_id is None:
        mates = [p for p in players if p.team_id == possession_team_id]
        if mates:
            carrier_id = min(mates, key=lambda p: (p.x - ball.x) ** 2 + (p.y - ball.y) ** 2).player_id
    history = [from_tracking(frames[: i + 1], meta, possession_team_id) for i in range(max(0, len(frames) - 5),
                                                                                        len(frames) - 1)]
    return TFrame(players=players, ball=ball, carrier_id=carrier_id, possession_team_id=possession_team_id,
                  attacking_right={meta.home.team_id: True, meta.away.team_id: False},
                  elapsed=last.elapsed, history=[h.model_copy(update={"history": []}) for h in history])


def _squad(meta: MatchMeta, team_id: str) -> set[str]:
    team = meta.team(team_id)
    return set(team.starting_xi) | set(team.bench)
