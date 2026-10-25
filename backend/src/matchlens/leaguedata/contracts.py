"""Knowledge built from the league dataset, consumed by the agents and models.

* ``TeamStyle``, ``Mentality``, ``PlayerProfile``: computed in code (style.py,
  mentality.py, players.py), never by a model.
* ``ClubProfile``, ``ManagerProfile``: facts from Wikidata plus text from
  Wikipedia, with attribution (knowledge.py).
* ``MatchReasoningDraft``: what the reasoning model returns (strict schema:
  no dicts, no defaults). ``MatchReasoning`` is the published, fact-checked
  record assembled in code (reasoner.py).
* ``Playbook``: per team-season aggregation of reasonings (playbook.py).

Metric values carry ``is_proxy`` because free data has no pass-level events
for recent seasons; e.g. field tilt is approximated by deep-completion share.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from matchlens.leaguedata.schema import League, SourceIds


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatternTag(StrEnum):
    HIGH_PRESS = "high_press"
    MID_BLOCK = "mid_block"
    LOW_BLOCK = "low_block"
    COUNTER_ATTACK = "counter_attack"
    DIRECT_LONG_BALLS = "direct_long_balls"
    PATIENT_BUILDUP = "patient_buildup"
    CROSSING_WIDE_OVERLOAD = "crossing_wide_overload"
    THROUGH_BALLS = "through_balls"
    SET_PIECE_THREAT = "set_piece_threat"
    CORNER_THREAT = "corner_threat"
    AERIAL_THREAT = "aerial_threat"
    LONG_RANGE_SHOOTING = "long_range_shooting"
    REBOUNDS_SECOND_BALLS = "rebounds_second_balls"
    TERRITORIAL_DOMINANCE = "territorial_dominance"
    INDIVIDUAL_BRILLIANCE = "individual_brilliance"
    GOALKEEPER_HEROICS = "goalkeeper_heroics"
    FINISHING_OVER = "finishing_over"
    FINISHING_UNDER = "finishing_under"
    GAME_STATE_SIT_BACK = "game_state_sit_back"
    LATE_PUSH = "late_push"
    RED_CARD_IMPACT = "red_card_impact"
    SUBSTITUTION_IMPACT = "substitution_impact"
    DEFENSIVE_ERRORS = "defensive_errors"
    PENALTY_DECISIVE = "penalty_decisive"
    QUALITY_OVER_VOLUME = "quality_over_volume"
    COMEBACK_RESILIENCE = "comeback_resilience"


class Metric(_Model):
    key: str  # e.g. "ppda", "deep_share", "directness", "xg_per_shot"
    value: float | None
    pct: float | None = None  # league-season percentile 0..100
    z: float | None = None  # z-score within league-season
    is_proxy: bool = False
    source: str


class FormationShare(_Model):
    formation: str
    share: float  # of matches


class TeamStyle(_Model):
    team_id: str
    team_name: str
    league: League
    season: str
    as_of_match: str | None = None  # None = whole season; else built only from matches before it
    matches: int
    formations: list[FormationShare]
    manager_id: str | None = None
    metrics: list[Metric]

    def metric(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)

    def vector(self, keys: tuple[str, ...]) -> list[float]:
        """z-scores in a fixed key order (0.0 when missing), for model conditioning."""
        out = []
        for key in keys:
            m = self.metric(key)
            out.append(m.z if m is not None and m.z is not None else 0.0)
        return out


class Mentality(_Model):
    """Behaviour under pressure, computed from results and timelines only."""

    team_id: str
    season: str
    league: League | None = None  # None = all competitions
    matches: int
    comeback_rate: float | None  # share of matches where they trailed and did not lose
    points_from_losing: float | None  # per match in which they trailed
    lead_protection: float | None  # share of matches where they led and won
    late_goal_share: float | None  # goals scored 75'+ / all goals
    late_concede_share: float | None
    response_after_conceding: float | None  # shots (or xG) in the 15' after conceding vs. their rate
    big_game_ppg: float | None  # points per game vs. top-4 / Elo-top opponents
    knockout_record: str | None  # UCL knockout ties won-lost, e.g. "5-2"
    red_cards_per_match: float | None
    finishing_streak: float | None  # rolling goals - xG over the last 10
    notes: list[str] = Field(default_factory=list)  # computed observations, e.g. "6 comebacks from 1-0 down"


class PlayerProfile(_Model):
    player_id: str
    name: str
    ids: SourceIds
    team_id: str
    season: str
    league: League
    positions: list[str]
    minutes: int
    age: float | None = None
    height_cm: int | None = None
    foot: str | None = None
    market_value_eur: int | None = None
    per90: list[Metric]  # npxg, xa, shots, key_passes, xg_chain, xg_buildup
    shot_profile: list[Metric]  # box share, header share, xg_per_shot, situation shares
    role_tags: list[str]  # heuristic, e.g. "aerial threat", "creator", "ball-progressor", "poacher"


class ClubProfile(_Model):
    team_id: str
    name: str
    country: str | None
    founded: int | None
    stadium: str | None
    honours: list[str]  # "Premier League 2019-20", ...
    rivals: list[str]
    manager_timeline: list[str]  # "Name (2015-10 to 2024-05)", newest first
    history: str | None  # Wikipedia summary
    attribution: list[str]  # source URLs and licences


class ManagerProfile(_Model):
    manager_id: str
    name: str
    nationality: str | None
    tenures: list[str]  # "Club (start to end)"
    trophies: list[str]
    formations: list[FormationShare]  # from our match data
    philosophy: str | None  # Wikipedia-derived summary, attributed
    attribution: list[str]


# ------------------------------------------------------------------ reasoning


class TacticalPatternDraft(_Strict):
    tag: PatternTag
    side: Literal["home", "away"]
    description: str
    evidence: list[str]  # digest ids: S01 (shot), E03 (event), M:ppda (metric)
    strength: float = Field(ge=0, le=1)


class TurningPointDraft(_Strict):
    minute: int
    description: str
    evidence: list[str]
    xg_swing: float | None


class MatchReasoningDraft(_Strict):
    """What the reasoning model returns. Names are placeholders in blind mode: {H}, {A}, {P:<id>}."""

    verdict: Literal["dominant", "deserved", "even", "fortunate", "smash_and_grab"]
    headline: str
    why_won: str
    how: list[str]
    patterns: list[TacticalPatternDraft]
    turning_points: list[TurningPointDraft]
    home_note: str
    away_note: str
    confidence: float = Field(ge=0, le=1)
    limitations: list[str]


class Provenance(_Model):
    model: str
    prompt_version: str
    digest_hash: str
    fact_check: Literal["passed", "rewritten", "blocked", "template"]
    problems: list[str] = Field(default_factory=list)
    created_at: str


class MatchReasoning(_Model):
    match_key: str
    league: League
    season: str
    home_id: str
    away_id: str
    score: str  # "2-1"
    draft: MatchReasoningDraft  # names restored
    provenance: Provenance


class PatternFreq(_Model):
    tag: PatternTag
    share: float  # of the team's matches
    win_rate: float | None  # when the pattern was present
    example_keys: list[str]


class Playbook(_Model):
    team_id: str
    league: League
    season: str
    manager_id: str | None
    style: TeamStyle
    mentality: Mentality | None
    patterns_for: list[PatternFreq]
    patterns_against: list[PatternFreq]
    strengths: list[str]
    weaknesses: list[str]
    game_state: list[str]  # how they behave when leading / level / trailing
    key_players: list[str]  # player_ids
    summary: str
    evidence_matches: list[str]
