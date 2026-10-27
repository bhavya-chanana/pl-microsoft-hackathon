"""Configurable weights and thresholds for the stats engine (FR-015, FR-017)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MomentumWeights(BaseModel):
    goal: float = 10.0
    big_chance: float = 6.0
    shot_on_target: float = 5.0
    shot: float = 3.0
    corner: float = 2.0
    box_entry: float = 1.5
    final_third_pass: float = 0.5
    high_turnover: float = 1.5  # possession won in the attacking third


class Thresholds(BaseModel):
    momentum_window_s: float = 300.0  # rolling five-minute window
    momentum_swing_delta: float = 55.0  # change in momentum index (-100..100)
    momentum_even_band: float = 10.0  # |index| below this reads as "Even" (matches the bar)
    sprint_mps: float = 7.0  # 25.2 km/h
    speed_badge_kmh: float = 32.0
    distance_badge_km: list[float] = Field(default_factory=lambda: [5.0, 10.0])
    shot_speed_badge_kmh: float = 105.0
    hard_pass_difficulty: float = 70.0
    pressure_spell_window_s: float = 180.0
    pressure_spell_count: int = 12  # pressures + high turnovers in the window
    chaos_window_s: float = 300.0
    milestone_goal_every: int = 50
    milestone_appearance_every: int = 100


class EngineConfig(BaseModel):
    momentum: MomentumWeights = Field(default_factory=MomentumWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
