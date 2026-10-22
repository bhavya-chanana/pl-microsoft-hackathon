"""Storylines: scripted match narratives that the simulator plays out (FR-009).

A storyline sets *what* happens at key moments (beats) and *who is on top*
over time (phases). The simulator fills in everything between beats with
football-realistic play. Storylines can be hand-written or produced by an
LLM, which must return JSON matching :class:`Storyline`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BeatType(StrEnum):
    GOAL = "goal"
    BIG_CHANCE = "big_chance"  # a high-quality chance that does not go in
    YELLOW_CARD = "yellow_card"
    RED_CARD = "red_card"  # straight red, or second yellow if already booked
    SUBSTITUTION = "substitution"


class Beat(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    minute: int = Field(ge=1, le=95, description="Match minute; 46-95 is the second half")
    type: BeatType
    team_id: str
    player_id: str
    # Goal: assisting player. Substitution: player coming on.
    related_player_id: str | None = None
    # Goal only: the feed first credits this player, then corrects it (FR-006).
    initially_credited_to: str | None = None
    note: str | None = None


class Phase(BaseModel):
    """Period where one team is on top. dominance: -1 (away) .. +1 (home)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_minute: int = Field(ge=0, le=95)
    end_minute: int = Field(ge=1, le=96)
    home_dominance: float = Field(ge=-1, le=1)
    label: str | None = None


class Storyline(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    summary: str
    phases: list[Phase] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)
    # If True, the only goals are the scripted ones; other shots never score.
    scripted_goals_only: bool = True

    @model_validator(mode="after")
    def _beats_sorted(self) -> Storyline:
        object.__setattr__(self, "beats", sorted(self.beats, key=lambda b: b.minute))
        return self

    def dominance_at(self, minute: float) -> float:
        for phase in self.phases:
            if phase.start_minute <= minute < phase.end_minute:
                return phase.home_dominance
        return 0.0


def comeback_storyline() -> Storyline:
    """The hero demo match: Harbour City go 2-0 up, Northbridge come back to win 3-2."""
    return Storyline(
        title="The Kestrel Park Comeback",
        summary=(
            "Harbour City dominate the first half and lead 2-0. Northbridge switch to a "
            "high press after the break, a red card tilts the game, and Elias Brandvold's "
            "100th career goal completes a late comeback."
        ),
        phases=[
            Phase(start_minute=0, end_minute=15, home_dominance=0.0, label="Even opening"),
            Phase(start_minute=15, end_minute=40, home_dominance=-0.6, label="Harbour control"),
            Phase(start_minute=40, end_minute=46, home_dominance=-0.2),
            Phase(start_minute=46, end_minute=58, home_dominance=0.3, label="Northbridge press"),
            Phase(start_minute=58, end_minute=72, home_dominance=0.55, label="Momentum shift"),
            Phase(start_minute=72, end_minute=96, home_dominance=0.75, label="Siege"),
        ],
        beats=[
            Beat(minute=11, type=BeatType.BIG_CHANCE, team_id="NBR", player_id="NBR-10"),
            Beat(minute=23, type=BeatType.GOAL, team_id="HBC", player_id="HBC-11", related_player_id="HBC-09"),
            Beat(minute=31, type=BeatType.YELLOW_CARD, team_id="NBR", player_id="NBR-07"),
            Beat(minute=38, type=BeatType.GOAL, team_id="HBC", player_id="HBC-09", related_player_id="HBC-05"),
            Beat(minute=52, type=BeatType.YELLOW_CARD, team_id="HBC", player_id="HBC-04"),
            Beat(minute=58, type=BeatType.SUBSTITUTION, team_id="NBR", player_id="NBR-08", related_player_id="NBR-16"),
            Beat(
                minute=64,
                type=BeatType.GOAL,
                team_id="NBR",
                player_id="NBR-09",
                related_player_id="NBR-16",
                initially_credited_to="NBR-10",
            ),
            Beat(minute=67, type=BeatType.SUBSTITUTION, team_id="HBC", player_id="HBC-08", related_player_id="HBC-14"),
            Beat(minute=71, type=BeatType.RED_CARD, team_id="HBC", player_id="HBC-04"),
            Beat(minute=74, type=BeatType.SUBSTITUTION, team_id="HBC", player_id="HBC-10", related_player_id="HBC-13"),
            Beat(minute=79, type=BeatType.GOAL, team_id="NBR", player_id="NBR-10", related_player_id="NBR-05"),
            Beat(minute=84, type=BeatType.BIG_CHANCE, team_id="HBC", player_id="HBC-11"),
            Beat(minute=86, type=BeatType.SUBSTITUTION, team_id="NBR", player_id="NBR-06", related_player_id="NBR-15"),
            Beat(minute=92, type=BeatType.GOAL, team_id="NBR", player_id="NBR-11", related_player_id="NBR-09"),
        ],
    )
