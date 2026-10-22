"""Match-state tools (BRD §8.2).

Plain functions over the active session, exposed to agents through the MCP
server. Every agent reads match data only through these tools, so all
generated content is grounded in the same state.
"""

from __future__ import annotations

from typing import Any

from matchlens.domain.models import EventType
from matchlens.factcheck.verify import Claim, verify_claims
from matchlens.interpret.engine import MatchEngine
from matchlens.runtime.manager import manager

MAX_EVENTS = 60


def _engine() -> MatchEngine:
    return manager.require().engine


def _slim_event(engine: MatchEngine, e: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "event_id": e.event_id,
        "minute": e.minute_label,
        "type": e.type.value,
        "team_id": e.team_id,
        "player_id": e.player_id,
        "outcome": e.outcome.value,
    }
    if e.related_player_id:
        out["related_player_id"] = e.related_player_id
    keep = ("xg", "shot_speed_kmh", "big_chance", "ball_speed_kmh", "set_piece", "reason", "home_score", "away_score")
    attrs = {k: v for k, v in e.attrs.items() if k in keep}
    if attrs:
        out["attrs"] = attrs
    q = engine.pass_quality(e.event_id)
    if q:
        out["pass_difficulty"] = q.difficulty
        out["pass_distance_m"] = q.distance_m
    return out


def get_match_state() -> dict[str, Any]:
    """Current score, clock, status, possession, momentum index (-100 away .. +100 home) and rhythm."""
    engine = _engine()
    meta = engine.meta
    return {
        **engine.state(),
        "home": {"team_id": meta.home.team_id, "name": meta.home.name},
        "away": {"team_id": meta.away.team_id, "name": meta.away.name},
        "competition": meta.competition,
        "venue": meta.venue,
    }


def list_players(team_id: str | None = None) -> list[dict[str, Any]]:
    """Squad list with ids, names, roles and whether each player is on the pitch."""
    engine = _engine()
    out = []
    for pid, p in engine.meta.players.items():
        if team_id and not pid.startswith(team_id):
            continue
        stats = engine.c.players.get(pid)
        out.append({
            "player_id": pid, "name": p.name, "short_name": p.short_name, "shirt": p.shirt,
            "role": p.role, "nationality": p.nationality,
            "career_goals_before_match": p.career_goals,
            "on_pitch": bool(stats and stats.on_pitch),
        })
    return out


def get_events(
    last_minutes: float | None = None,
    team_id: str | None = None,
    player_id: str | None = None,
    types: list[str] | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Events (newest last), optionally limited to the last N minutes, a team, a player or event types
    such as goal, shot, save, card, substitution, pass, tackle, interception, pressure."""
    engine = _engine()
    since = engine.c.elapsed - last_minutes * 60 if last_minutes else None
    type_set = {EventType(t) for t in types} if types else None
    rows = engine.events(since=since, team_id=team_id, player_id=player_id, types=type_set)
    return [_slim_event(engine, e) for e in rows[-min(limit, MAX_EVENTS):]]


def get_player_stats(player_id: str) -> dict[str, Any]:
    """Live statistics for one player. A value of null means unavailable, not zero."""
    return _engine().player_stats(player_id)


def get_team_stats(team_id: str) -> dict[str, Any]:
    """Live statistics for one team, including possession, xG and the shot map."""
    return _engine().team_stats(team_id)


def get_momentum(last_minutes: float = 15) -> dict[str, Any]:
    """Momentum index per minute for the window (positive = home on top) and rhythm states
    (control / balanced / chaos) with turnovers per minute and pass share."""
    engine = _engine()
    since = engine.c.elapsed - last_minutes * 60
    momentum = [
        {"elapsed": p.elapsed, "index": p.index, "home_pressure": p.home, "away_pressure": p.away}
        for p in engine.momentum_series() if p.elapsed >= since
    ]
    rhythm = [
        {"elapsed": r.elapsed, "state": r.state, "turnovers_per_min": r.turnovers_per_min,
         "pass_share": r.pass_share, "controlling_team": r.controlling_team}
        for r in engine.rhythm_series() if r.elapsed >= since
    ]
    return {"momentum": momentum, "rhythm": rhythm}


def get_signal(signal_id: str) -> dict[str, Any] | None:
    """One detected key moment, with its facts and evidence event ids."""
    for s in _engine().signals():
        if s.signal_id == signal_id:
            return s.model_dump(mode="json")
    return None


def get_recent_signals(last_minutes: float = 10) -> list[dict[str, Any]]:
    """Key moments detected in the last N minutes."""
    engine = _engine()
    since = engine.c.elapsed - last_minutes * 60
    return [s.model_dump(mode="json") for s in engine.signals() if s.elapsed >= since]


def verify(claims: list[Claim]) -> list[dict[str, Any]]:
    """Check factual claims (stat, event, score, momentum) against match state.
    Returns a verdict per claim: supported, contradicted or unverifiable."""
    return [r.model_dump(mode="json") for r in verify_claims(_engine(), claims)]


ALL_TOOLS = [
    get_match_state, list_players, get_events, get_player_stats, get_team_stats,
    get_momentum, get_signal, get_recent_signals, verify,
]
