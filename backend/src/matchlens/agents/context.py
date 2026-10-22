"""Context packs: the grounded, compact input every agent receives.

The Coordinator assembles a pack from the match-state tools before calling
specialists. Pre-fetching keeps live insights inside the 10 s budget (no
tool-call round trips) and fixes the set of facts the Fact-Checker accepts.
"""

from __future__ import annotations

from typing import Any

from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.signals import Signal, SignalType

PLAYER_KEYS = (
    "goals", "assists", "shots", "shots_on_target", "xg", "passes", "pass_accuracy",
    "avg_pass_difficulty", "key_passes", "progressive_passes", "tackles", "interceptions",
    "saves", "yellow_cards", "red_card", "distance_km", "top_speed_kmh", "sprints", "max_shot_speed_kmh",
)
TEAM_KEYS = (
    "goals", "possession_pct", "shots", "shots_on_target", "big_chances", "xg", "passes",
    "pass_accuracy", "box_entries", "corners", "fouls", "high_turnovers", "players_on_pitch",
)


def _slim_event(engine: MatchEngine, event_id: str) -> dict[str, Any] | None:
    e = engine.event(event_id)
    if e is None:
        return None
    out: dict[str, Any] = {
        "event_id": e.event_id, "minute": e.minute_label, "type": e.type.value,
        "team_id": e.team_id, "player_id": e.player_id, "outcome": e.outcome.value,
    }
    if e.related_player_id:
        out["related_player_id"] = e.related_player_id
    for k in ("xg", "shot_speed_kmh", "big_chance", "distance_m", "set_piece", "body_part", "reason"):
        if k in e.attrs:
            out[k] = e.attrs[k]
    q = engine.pass_quality(e.event_id)
    if q:
        out["pass_difficulty"] = q.difficulty
        out["pass_band"] = q.band
    return out


def player_brief(engine: MatchEngine, player_id: str) -> dict[str, Any]:
    p = engine.meta.players[player_id]
    stats = engine.player_stats(player_id)
    return {
        "player_id": player_id, "name": p.name, "short_name": p.short_name, "team_id": stats["team_id"],
        "role": p.role, "shirt": p.shirt, "career_goals_before_match": p.career_goals,
        "on_pitch": stats["on_pitch"],
        "stats": {k: stats[k] for k in PLAYER_KEYS if stats.get(k) is not None},
    }


def match_header(engine: MatchEngine) -> dict[str, Any]:
    st = engine.state()
    meta = engine.meta
    return {
        "home": {"team_id": meta.home.team_id, "name": meta.home.name},
        "away": {"team_id": meta.away.team_id, "name": meta.away.name},
        "score": st["score"], "minute": st["minute_label"], "period": st["period"], "status": st["status"],
        "players_on_pitch": st["players_on_pitch"],
    }


def momentum_brief(engine: MatchEngine, minutes: float = 10, step_minutes: int = 2) -> dict[str, Any]:
    now = engine.c.elapsed
    series = [p for p in engine.momentum_series() if p.elapsed >= now - minutes * 60]
    sampled = series[::step_minutes] + (series[-1:] if series and (len(series) - 1) % step_minutes else [])
    rhythm = engine.rhythm_series()[-1:] or [None]
    r = rhythm[0]
    return {
        "scale": "-100 = away fully on top, +100 = home fully on top",
        "now": round(engine.momentum_now(), 1),
        "recent": [{"elapsed_min": round(p.elapsed / 60, 1), "index": round(p.index, 1)} for p in sampled],
        "rhythm": None if r is None else {
            "state": r.state, "turnovers_per_min": r.turnovers_per_min, "controlling_team": r.controlling_team,
        },
    }


def recent_moments(engine: MatchEngine, minutes: float = 15, exclude: str | None = None) -> list[dict[str, Any]]:
    now = engine.c.elapsed
    out = []
    for s in engine.signals():
        if s.elapsed < now - minutes * 60 or s.signal_id == exclude or s.priority > 3:
            continue
        if s.type in (SignalType.CORRECTION, SignalType.PERIOD):
            continue
        out.append({"minute": s.minute_label, "type": s.type.value, "team_id": s.team_id,
                    "player_ids": s.player_ids})
    return out[-8:]


def signal_pack(engine: MatchEngine, signal: Signal) -> dict[str, Any]:
    """Everything a specialist needs to explain one signal."""
    player_ids = list(dict.fromkeys(
        [p for p in signal.player_ids if p in engine.meta.players]
        + [str(v) for k, v in signal.facts.items() if k in ("scorer", "assist", "previous", "value", "on", "off")
           and str(v) in engine.meta.players]
    ))
    evidence = [x for x in (_slim_event(engine, eid) for eid in signal.evidence_event_ids if eid) if x]
    teams = {
        tid: {k: v for k, v in engine.team_stats(tid).items() if k in TEAM_KEYS and v is not None}
        for tid in (engine.meta.home.team_id, engine.meta.away.team_id)
    }
    return {
        "match": match_header(engine),
        "signal": {
            "signal_id": signal.signal_id, "type": signal.type.value, "minute": signal.minute_label,
            "team_id": signal.team_id, "player_ids": signal.player_ids, "facts": signal.facts,
            "revision": signal.revision,
        },
        "evidence": evidence[:8],
        "players": [player_brief(engine, p) for p in player_ids[:4]],
        "teams": teams,
        "momentum": momentum_brief(engine),
        "recent_moments": recent_moments(engine, exclude=signal.signal_id),
    }


def window_pack(engine: MatchEngine, minutes: int, insights: list[dict[str, Any]]) -> dict[str, Any]:
    """Context for catch-ups (the last N minutes) and recaps (minutes = whole match)."""
    now = engine.c.elapsed
    since = now - minutes * 60
    signals = [s for s in engine.signals() if s.elapsed >= since and s.priority <= 3
               and s.type not in (SignalType.PERIOD, SignalType.CORRECTION)]
    goals_before = {tid: 0 for tid in engine.score()}
    for s in engine.signals():
        if s.type == SignalType.GOAL and s.elapsed < since and s.team_id:
            goals_before[s.team_id] += 1
    involved = list(dict.fromkeys(p for s in signals for p in s.player_ids if p in engine.meta.players))
    by_signal = {i["signal_id"]: i for i in insights}
    return {
        "match": match_header(engine),
        "window": {"minutes": minutes, "score_at_start": goals_before},
        "moments": [
            {"minute": s.minute_label, "elapsed": s.elapsed, "type": s.type.value, "team_id": s.team_id, "player_ids": s.player_ids,
             "facts": s.facts, "insight": by_signal[s.signal_id]["headline"] if s.signal_id in by_signal else None}
            for s in sorted(signals, key=lambda s: (s.priority, -s.elapsed))[:12]
        ],
        "players": [player_brief(engine, p) for p in involved[:8]],
        "teams": {
            tid: {k: v for k, v in engine.team_stats(tid).items() if k in TEAM_KEYS and v is not None}
            for tid in (engine.meta.home.team_id, engine.meta.away.team_id)
        },
        "momentum": momentum_brief(engine, minutes=min(minutes, 30), step_minutes=3),
    }


def name_of(engine: MatchEngine, player_id: str | None) -> str:
    if not player_id or player_id not in engine.meta.players:
        return "a player"
    return engine.meta.players[player_id].name


def team_name(engine: MatchEngine, team_id: str | None) -> str:
    if team_id is None:
        return "Both sides"
    return engine.meta.team(team_id).name
