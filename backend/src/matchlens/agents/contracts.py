"""Output contracts for the agent layer (BRD §8.4, FR-020 to FR-041).

Two groups of models live here:

* LLM-facing models (suffix ``Draft`` / ``Plan``): what an agent must return.
  They are strict-schema friendly: no dicts, no defaults, every field
  required (nullable where optional), ``extra="forbid"``.
* Published models (``Insight``, ``Overlay``, ``Catchup``, ``Recap``): what the
  API and UI consume. They are assembled and validated in code after the
  Fact-Checker has run, never returned by a model directly.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from matchlens.factcheck.verify import (
    Claim,
    ClaimResult,
    EventClaim,
    MomentumClaim,
    ScoreClaim,
    StatClaim,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------- fan profile


class Mode(StrEnum):
    CASUAL = "casual"
    ANALYST = "analyst"
    PLAYER_FOCUS = "player_focus"


class Language(StrEnum):
    EN = "en"
    HI = "hi"
    ES = "es"


LANGUAGE_NAMES = {Language.EN: "English", Language.HI: "Hindi", Language.ES: "Spanish"}


class FanProfile(BaseModel):
    mode: Mode = Mode.CASUAL
    language: Language = Language.EN
    club_id: str | None = None
    player_id: str | None = None
    metric: str | None = Field(default=None, description="Headline metric key, e.g. 'xg' or 'pass_accuracy'")


# --------------------------------------------------------------- claims (LLM side)


class AgentClaim(_Strict):
    """A factual statement the agent made, in checkable form.

    Flat on purpose: strict structured outputs do not support discriminated
    unions with optional members, so unused fields are null.
    """

    kind: Literal["player_stat", "team_stat", "event", "score", "momentum"]
    text: str = Field(description="The phrase in your output this claim supports")
    subject_id: str | None = Field(description="player_id or team_id for stat and momentum claims")
    stat: str | None = Field(description="Stat key exactly as in the provided stats")
    value: float | None = Field(description="Claimed numeric value")
    event_id: str | None = Field(description="event_id for event claims")
    player_id: str | None = Field(description="Player claimed to be involved in the event")
    home: int | None = Field(description="Claimed home score")
    away: int | None = Field(description="Claimed away score")

    def to_claim(self) -> Claim | None:
        match self.kind:
            case "player_stat" | "team_stat" if self.subject_id and self.stat and self.value is not None:
                return StatClaim(
                    subject_type="player" if self.kind == "player_stat" else "team",
                    subject_id=self.subject_id, stat=self.stat, value=self.value,
                )
            case "event" if self.event_id:
                return EventClaim(event_id=self.event_id, player_id=self.player_id)
            case "score" if self.home is not None and self.away is not None:
                return ScoreClaim(home=self.home, away=self.away)
            case "momentum" if self.subject_id:
                return MomentumClaim(team_on_top=self.subject_id)
        return None


class KeyNumber(_Strict):
    label: str = Field(description="Short label, max 24 characters")
    value: str = Field(description="Value exactly as in the data, formatted for display")
    unit: str | None


# --------------------------------------------------------------- specialist drafts


class TacticalDraft(_Strict):
    """Tactical agent: why the moment happened and why it matters."""

    headline: str = Field(description="Max 70 characters")
    explanation: str = Field(description="2-3 sentences: the cause, grounded in the evidence")
    why_it_matters: str = Field(description="1 sentence on consequence for the match")
    key_numbers: list[KeyNumber] = Field(description="2-4 numbers from the data that prove the point")
    claims: list[AgentClaim]


class NarrativeDraft(_Strict):
    """Narrator agent: the story beat, as a broadcaster would tell it."""

    headline: str = Field(description="Max 70 characters, vivid but factual")
    narrative: str = Field(description="2-3 sentences of commentary")
    storyline: str | None = Field(description="Bigger thread this continues, e.g. 'comeback', 'milestone'")
    claims: list[AgentClaim]


class OverlayKind(StrEnum):
    LOWER_THIRD = "lower_third"
    STAT_CARD = "stat_card"
    PLAYER_CARD = "player_card"
    MOMENTUM_BAR = "momentum_bar"
    SHOT_CARD = "shot_card"
    CORRECTION_BANNER = "correction_banner"
    MILESTONE_BANNER = "milestone_banner"


class OverlayPlan(_Strict):
    """Producer agent: how the moment should appear on screen."""

    kind: OverlayKind
    title: str = Field(description="Max 32 characters")
    subtitle: str | None = Field(description="Max 60 characters")
    stats: list[KeyNumber] = Field(description="0-3 numbers to show")
    duration_s: int = Field(description="4 to 12 seconds")
    focus_player_id: str | None


class Variant(_Strict):
    mode: Mode
    language: Language
    headline: str = Field(description="Max 70 characters")
    body: str = Field(description="Casual: 1 short sentence. Analyst: 2 sentences with numbers. "
                                   "Player focus: 1-2 sentences centred on the key player")


class ClubAngle(_Strict):
    team_id: str
    language: Language
    text: str = Field(description="One sentence for a fan of this club")


class PersonalisedDraft(_Strict):
    """Personaliser agent: every mode x language, plus a club angle per team."""

    variants: list[Variant]
    club_angles: list[ClubAngle]


class CatchupDraft(_Strict):
    headline: str = Field(description="Max 80 characters")
    bullets: list[str] = Field(description="3-5 bullets, most important first, each under 140 characters")
    momentum_summary: str = Field(description="One sentence on who has been on top in this window")
    spotlight_player_id: str | None
    claims: list[AgentClaim]


class RecapDraft(_Strict):
    title: str = Field(description="Max 80 characters")
    summary: str = Field(description="One paragraph, 4-6 sentences")
    key_moments: list[str] = Field(description="3-6 moments, each '<minute> <what happened>'")
    turning_point: str = Field(description="One sentence naming the turning point and why")
    player_of_the_match_id: str | None
    claims: list[AgentClaim]


class RewriteDraft(_Strict):
    """Fact-Checker rewrite: the same text with unsupported statements fixed or removed."""

    headline: str
    body: str
    claims: list[AgentClaim]


# --------------------------------------------------------------- published


class FactCheckStatus(StrEnum):
    VERIFIED = "verified"      # every claim supported, no unaccounted numbers
    REWRITTEN = "rewritten"    # issues found, fixed by a rewrite, then verified
    BLOCKED = "blocked"        # issues remained; agent text withheld, data-only text shown
    DATA_ONLY = "data_only"    # no agent text was generated (fallback or low priority)


class FactCheckReport(BaseModel):
    status: FactCheckStatus
    results: list[ClaimResult] = Field(default_factory=list)
    unaccounted_numbers: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)


class InsightText(BaseModel):
    headline: str
    body: str


class Insight(BaseModel):
    insight_id: str  # == signal_id, so revisions and upgrades replace in place
    signal_id: str
    signal_type: str
    revision: int
    stage: Literal["data", "agent", "retracted"]
    elapsed: float
    minute_label: str
    period: int
    team_id: str | None
    player_ids: list[str]
    priority: int
    headline: str
    explanation: str | None = None
    why_it_matters: str | None = None
    narrative: str | None = None
    storyline: str | None = None
    key_numbers: list[KeyNumber] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    variants: dict[str, dict[str, InsightText]] = Field(default_factory=dict)  # mode -> lang -> text
    club_angles: dict[str, dict[str, str]] = Field(default_factory=dict)       # team -> lang -> text
    fact_check: FactCheckReport
    agents: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    supersedes_revision: int | None = None


class Overlay(BaseModel):
    """Timed broadcast graphic (FR-030 to FR-034). Times are match elapsed seconds."""

    overlay_id: str
    insight_id: str
    revision: int
    kind: OverlayKind
    title: str
    subtitle: str | None
    stats: list[KeyNumber]
    team_id: str | None
    focus_player_id: str | None
    accent_colour: str | None
    start_elapsed: float
    duration_s: float
    priority: int
    slot: Literal["lower", "upper_right", "banner"]
    localized: dict[str, InsightText] = Field(default_factory=dict)
    stage: Literal["data", "agent", "retracted"] = "data"


class Catchup(BaseModel):
    window_minutes: int
    from_minute: str
    to_minute: str
    score_before: dict[str, int]
    score_now: dict[str, int]
    headline: str
    bullets: list[str]
    momentum_summary: str
    spotlight_player_id: str | None
    insight_ids: list[str]
    profile: FanProfile
    fact_check: FactCheckReport
    source: Literal["agent", "data"]


class Recap(BaseModel):
    phase: Literal["half_time", "full_time", "live"]
    title: str
    summary: str
    key_moments: list[str]
    turning_point: str | None
    player_of_the_match_id: str | None
    profile: FanProfile
    fact_check: FactCheckReport
    source: Literal["agent", "data"]
