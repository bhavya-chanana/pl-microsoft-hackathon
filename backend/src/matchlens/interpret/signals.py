"""Signals: key moments detected by the stats engine (FR-016).

A signal is a deterministic, evidence-backed observation. Agents turn signals
into explanations and narratives; they never create signals themselves.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SignalType(StrEnum):
    GOAL = "goal"
    BIG_CHANCE = "big_chance"
    BIG_SAVE = "big_save"
    CARD = "card"
    SUBSTITUTION = "substitution"
    MOMENTUM_SWING = "momentum_swing"
    PRESSURE_SPELL = "pressure_spell"
    CHAOS_SPELL = "chaos_spell"
    CONTROL_SPELL = "control_spell"
    MILESTONE = "milestone"
    SPEED_BADGE = "speed_badge"
    DISTANCE_BADGE = "distance_badge"
    SHOT_SPEED_BADGE = "shot_speed_badge"
    HARD_PASS = "hard_pass"
    CORRECTION = "correction"
    PERIOD = "period"  # kick-off, half-time, full-time


class Signal(BaseModel):
    signal_id: str  # deterministic, so a rebuild produces the same ids
    type: SignalType
    elapsed: float
    period: int
    clock: float
    minute_label: str
    team_id: str | None = None
    player_ids: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    facts: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    priority: int = 3  # 1 (highest) .. 5
    revision: int = 0  # incremented when a correction changes the signal
