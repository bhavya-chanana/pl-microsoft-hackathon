"""Match Pulse: the match's heartbeat, minute by minute (no language model).

One row per minute the engine sampled: who was on top (momentum), how the game
felt (control, balanced or chaos, from the rhythm sample), how hard each side
pressed and how fast the ball moved. Pins mark the moments that mattered. The
UI draws it as one strip; every value is read straight from the stats engine.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from matchlens.domain.models import EventType, minute_label
from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.signals import SignalType

PIN_TYPES = {
    SignalType.GOAL, SignalType.CARD, SignalType.BIG_CHANCE, SignalType.BIG_SAVE, SignalType.MOMENTUM_SWING,
    SignalType.PRESSURE_SPELL, SignalType.CHAOS_SPELL, SignalType.CONTROL_SPELL, SignalType.MILESTONE,
    SignalType.SUBSTITUTION,
}


def pulse(engine: MatchEngine) -> dict[str, Any]:
    home, away = engine.meta.home.team_id, engine.meta.away.team_id
    pressure: Counter[tuple[int, str]] = Counter()
    passes: Counter[int] = Counter()
    for e in engine.events(types={EventType.PRESSURE, EventType.PASS}):
        minute = int(e.elapsed // 60)
        if e.type == EventType.PRESSURE and e.team_id:
            pressure[(minute, e.team_id)] += 1
        elif e.type == EventType.PASS:
            passes[minute] += 1

    rhythm = {int(r.elapsed // 60): r for r in engine.rhythm_series()}
    rows = []
    for m in engine.momentum_series():
        minute = int(m.elapsed // 60)
        r = rhythm.get(minute)
        # The sample at minute N describes the minute that just ended.
        prev = minute - 1
        rows.append({
            "elapsed": m.elapsed,
            "minute_label": minute_label(m.period, m.clock),
            "period": m.period,
            "momentum": m.index,
            "state": r.state if r else "balanced",
            "controlling_team": r.controlling_team if r else None,
            "turnovers_per_min": r.turnovers_per_min if r else 0.0,
            "fouls_per_min": r.fouls_per_min if r else 0.0,
            "pass_share": r.pass_share if r else {},
            "pressure": {home: pressure[(prev, home)], away: pressure[(prev, away)]},
            "passes": passes[prev],
        })

    pins = [
        {"insight_id": s.signal_id, "type": s.type.value, "elapsed": s.elapsed, "minute_label": s.minute_label,
         "team_id": s.team_id, "player_ids": s.player_ids, "priority": s.priority,
         "card": s.facts.get("card") if s.type == SignalType.CARD else None}
        for s in engine.signals()
        if s.type in PIN_TYPES and (s.priority <= 2 or s.type in (SignalType.CARD, SignalType.SUBSTITUTION))
    ]

    # Consecutive minutes in the same state, so the strip can label spells ("Harbour City in control").
    spells: list[dict[str, Any]] = []
    for row in rows:
        last = spells[-1] if spells else None
        if last and last["state"] == row["state"] and last["team_id"] == row["controlling_team"]:
            last["to_elapsed"] = row["elapsed"]
            last["minutes"] += 1
        else:
            spells.append({"state": row["state"], "team_id": row["controlling_team"], "from_elapsed": row["elapsed"],
                           "to_elapsed": row["elapsed"], "minutes": 1})

    state = engine.state()
    return {
        "home_team_id": home,
        "away_team_id": away,
        "now": {"elapsed": state["elapsed"], "minute_label": state["minute_label"],
                "momentum": state["momentum_index"], "state": state["rhythm"],
                "controlling_team": state["controlling_team"]},
        "rows": rows,
        "pins": pins,
        "spells": [s for s in spells if s["state"] != "balanced" and s["minutes"] >= 2],
    }
