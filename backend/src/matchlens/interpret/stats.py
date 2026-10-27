"""Live team and player statistics (FR-012 to FR-014)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlayerStats:
    player_id: str
    team_id: str
    on_pitch: bool = False
    seconds_played: float = 0.0  # live match clock seconds spent on the pitch
    duels_won: int = 0  # see MatchEngine: a duel is one PRESSURE contest, presser against carrier
    duels_total: int = 0
    touches: int = 0
    passes: int = 0
    passes_completed: int = 0
    pass_difficulty_sum: float = 0.0
    hard_passes_completed: int = 0
    key_passes: int = 0
    progressive_passes: int = 0
    carries: int = 0
    shots: int = 0
    shots_on_target: int = 0
    goals: int = 0
    assists: int = 0
    xg: float = 0.0
    tackles: int = 0
    interceptions: int = 0
    pressures: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_card: bool = False
    saves: int = 0
    max_shot_speed_kmh: float | None = None
    # From tracking.
    distance_m: float = 0.0
    top_speed_mps: float = 0.0
    sprints: int = 0
    in_sprint: bool = False

    def as_dict(self) -> dict[str, object]:
        passes = self.passes
        return {
            "player_id": self.player_id,
            "team_id": self.team_id,
            "on_pitch": self.on_pitch,
            "minutes": int(self.seconds_played // 60),
            "duels_won": self.duels_won,
            "duels_total": self.duels_total,
            "touches": self.touches,
            "passes": passes,
            "passes_completed": self.passes_completed,
            "pass_accuracy": round(self.passes_completed / passes * 100, 1) if passes else None,
            "avg_pass_difficulty": round(self.pass_difficulty_sum / passes, 1) if passes else None,
            "hard_passes_completed": self.hard_passes_completed,
            "key_passes": self.key_passes,
            "progressive_passes": self.progressive_passes,
            "carries": self.carries,
            "shots": self.shots,
            "shots_on_target": self.shots_on_target,
            "goals": self.goals,
            "assists": self.assists,
            "xg": round(self.xg, 2),
            "tackles": self.tackles,
            "interceptions": self.interceptions,
            "pressures": self.pressures,
            "fouls": self.fouls,
            "yellow_cards": self.yellow_cards,
            "red_card": self.red_card,
            "saves": self.saves,
            "max_shot_speed_kmh": self.max_shot_speed_kmh,
            "distance_km": round(self.distance_m / 1000, 2),
            "top_speed_kmh": round(self.top_speed_mps * 3.6, 1) if self.top_speed_mps else None,
            "sprints": self.sprints,
        }


@dataclass
class TeamStats:
    team_id: str
    goals: int = 0
    possession_s: float = 0.0
    passes: int = 0
    passes_completed: int = 0
    final_third_passes: int = 0
    box_entries: int = 0
    shots: int = 0
    shots_on_target: int = 0
    big_chances: int = 0
    xg: float = 0.0
    corners: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    tackles: int = 0
    interceptions: int = 0
    pressures: int = 0
    high_turnovers: int = 0
    saves: int = 0
    players_on_pitch: int = 11
    shot_map: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self, total_possession_s: float) -> dict[str, object]:
        return {
            "team_id": self.team_id,
            "goals": self.goals,
            "possession_pct": round(self.possession_s / total_possession_s * 100, 1) if total_possession_s else None,
            "passes": self.passes,
            "passes_completed": self.passes_completed,
            "pass_accuracy": round(self.passes_completed / self.passes * 100, 1) if self.passes else None,
            "final_third_passes": self.final_third_passes,
            "box_entries": self.box_entries,
            "shots": self.shots,
            "shots_on_target": self.shots_on_target,
            "big_chances": self.big_chances,
            "xg": round(self.xg, 2),
            "corners": self.corners,
            "fouls": self.fouls,
            "yellow_cards": self.yellow_cards,
            "red_cards": self.red_cards,
            "tackles": self.tackles,
            "interceptions": self.interceptions,
            "pressures": self.pressures,
            "high_turnovers": self.high_turnovers,
            "saves": self.saves,
            "players_on_pitch": self.players_on_pitch,
        }
