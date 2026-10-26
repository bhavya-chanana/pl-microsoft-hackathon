"""Normalised rows of the league dataset (one model per table).

Every source adapter in ``leaguedata/sources`` converts provider data into
these rows; ``build.py`` writes them to Parquet tables and ``curate.py`` turns
the tables into the versioned curated dataset.

Conventions:
* ``match_key`` = ``"{league}:{season}:{date}:{home_slug}-{away_slug}"`` (see ids.py).
* Seasons are ``"2023-24"``. Dates are ISO ``YYYY-MM-DD``.
* Coordinates are metres on the 105 x 68 pitch with the shooting/acting team
  attacking towards x=105 (``domain.models`` conventions).
* Ids are our own slugs; provider ids are kept in ``source_ids``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class League(StrEnum):
    EPL = "EPL"
    LA_LIGA = "LA_LIGA"
    SERIE_A = "SERIE_A"
    BUNDESLIGA = "BUNDESLIGA"
    LIGUE_1 = "LIGUE_1"
    UCL = "UCL"  # Champions League: no Understat xG


DEFAULT_LEAGUES = (League.EPL, League.LA_LIGA, League.SERIE_A, League.UCL)
SEASONS = ("2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26")

# Provider codes per league.
UNDERSTAT_LEAGUE = {League.EPL: "EPL", League.LA_LIGA: "La_liga", League.SERIE_A: "Serie_A",
                    League.BUNDESLIGA: "Bundesliga", League.LIGUE_1: "Ligue_1"}
FOOTBALL_DATA_CODE = {League.EPL: "E0", League.LA_LIGA: "SP1", League.SERIE_A: "I1",
                      League.BUNDESLIGA: "D1", League.LIGUE_1: "F1"}
TRANSFERMARKT_CODE = {League.EPL: "GB1", League.LA_LIGA: "ES1", League.SERIE_A: "IT1",
                      League.BUNDESLIGA: "L1", League.LIGUE_1: "FR1", League.UCL: "CL"}

SourceName = Literal["understat", "transfermarkt", "football_data", "fpl", "clubelo", "statsbomb",
                     "wikidata", "wikipedia", "openfootball", "espn", "fotmob"]


class SourceIds(_Row):
    understat: str | None = None
    transfermarkt: str | None = None
    fpl: str | None = None
    wikidata: str | None = None
    statsbomb: str | None = None
    football_data: str | None = None
    openfootball: str | None = None


class Match(_Row):
    match_key: str
    league: League
    season: str
    date: str
    kickoff_utc: str | None = None
    stage: str | None = None  # UCL: "group", "round_of_16", ..., "final"
    home_id: str
    away_id: str
    home_goals: int
    away_goals: int
    home_goals_ht: int | None = None
    away_goals_ht: int | None = None
    home_xg: float | None = None
    away_xg: float | None = None
    home_formation: str | None = None
    away_formation: str | None = None
    home_manager_id: str | None = None
    away_manager_id: str | None = None
    referee: str | None = None
    venue: str | None = None
    attendance: int | None = None
    # Closing odds-implied probabilities (football-data), normalised to sum to 1.
    p_home: float | None = None
    p_draw: float | None = None
    p_away: float | None = None
    home_elo: float | None = None
    away_elo: float | None = None
    source_ids: SourceIds = Field(default_factory=SourceIds)


class LineupEntry(_Row):
    match_key: str
    team_id: str
    player_id: str
    starter: bool
    position: str | None  # provider position code, e.g. "DC", "AML", "Left Winger"
    shirt: int | None = None
    minute_on: int = 0
    minute_off: int | None = None  # None = played to the end
    captain: bool = False


class Shot(_Row):
    shot_id: str
    match_key: str
    team_id: str
    player_id: str
    assist_player_id: str | None
    minute: int
    x: float  # metres, attacking towards x=105
    y: float
    xg: float
    result: Literal["goal", "saved", "blocked", "missed", "post", "own_goal"]
    situation: Literal["open_play", "corner", "set_piece", "direct_free_kick", "penalty", "fast_break"]
    body_part: Literal["right_foot", "left_foot", "head", "other"]
    last_action: str | None  # e.g. "Cross", "Pass", "TakeOn", "Throughball", "Rebound"
    home_score_before: int
    away_score_before: int


class MatchEvent(_Row):
    """Non-shot timeline events: goals, cards, substitutions."""

    event_id: str
    match_key: str
    team_id: str
    minute: int
    kind: Literal["goal", "own_goal", "penalty_goal", "yellow", "second_yellow", "red", "sub"]
    player_id: str | None
    related_player_id: str | None = None  # assist, or the player coming on


class TeamMatchStats(_Row):
    match_key: str
    team_id: str
    is_home: bool
    goals: int
    xg: float | None = None
    npxg: float | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    corners: int | None = None
    fouls: int | None = None
    yellows: int | None = None
    reds: int | None = None
    ppda: float | None = None  # opponent passes allowed per defensive action (Understat)
    ppda_allowed: float | None = None
    deep: int | None = None  # completed passes within ~20 m of goal (Understat)
    deep_allowed: int | None = None
    xg_chain: float | None = None  # sum over players (Understat)
    xg_buildup: float | None = None
    possession: float | None = None  # 0..1, only when ESPN/FotMob enabled


class PlayerMatch(_Row):
    match_key: str
    player_id: str
    team_id: str
    minutes: int
    goals: int = 0
    assists: int = 0
    shots: int = 0
    key_passes: int = 0
    xg: float | None = None
    xa: float | None = None
    xg_chain: float | None = None
    xg_buildup: float | None = None


class Player(_Row):
    player_id: str
    name: str
    birth_date: str | None = None
    nationality: str | None = None
    height_cm: int | None = None
    foot: Literal["left", "right", "both"] | None = None
    main_position: str | None = None
    sub_position: str | None = None
    source_ids: SourceIds = Field(default_factory=SourceIds)


class PlayerValuation(_Row):
    player_id: str
    date: str
    market_value_eur: int


class Club(_Row):
    team_id: str
    name: str
    short_name: str | None = None
    country: str | None = None
    founded: int | None = None
    stadium: str | None = None
    stadium_capacity: int | None = None
    source_ids: SourceIds = Field(default_factory=SourceIds)


class Manager(_Row):
    manager_id: str
    name: str
    nationality: str | None = None
    birth_date: str | None = None
    source_ids: SourceIds = Field(default_factory=SourceIds)


class ManagerSpell(_Row):
    manager_id: str
    team_id: str
    start: str  # ISO date
    end: str | None  # None = current
    source: SourceName


class Honour(_Row):
    team_id: str
    competition: str
    season: str
    source: SourceName


class TextDoc(_Row):
    """Encyclopedic text (Wikipedia summary) kept with its attribution."""

    entity_kind: Literal["club", "manager", "player"]
    entity_id: str
    title: str
    text: str
    url: str
    licence: str  # e.g. "CC BY-SA 4.0"
    retrieved: str  # ISO date


class IdLink(_Row):
    """A cross-source identity link and how sure we are about it."""

    entity_kind: Literal["club", "player", "manager"]
    entity_id: str
    source: SourceName
    source_id: str
    confidence: float = Field(ge=0, le=1)
    method: Literal["exact_id", "name_club_season", "name_birth", "override", "fuzzy"]


TABLES: dict[str, type[_Row]] = {
    "matches": Match,
    "lineups": LineupEntry,
    "shots": Shot,
    "match_events": MatchEvent,
    "team_match_stats": TeamMatchStats,
    "player_matches": PlayerMatch,
    "players": Player,
    "player_valuations": PlayerValuation,
    "clubs": Club,
    "managers": Manager,
    "manager_spells": ManagerSpell,
    "honours": Honour,
    "text_docs": TextDoc,
    "id_links": IdLink,
}
