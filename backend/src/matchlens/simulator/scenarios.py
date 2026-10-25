"""Scenario Lab: preset storylines, storyline checks and "script vs reality".

A fan (or a judge) picks a preset or describes a match in words; the scenario
agent turns words into a ``Storyline``. Either way the storyline is checked
here before kick-off, so the simulator never gets a script it cannot play: a
player who is not on the pitch, a substitute who is already on, a player who
scores after being sent off. At full time, ``script_vs_reality`` shows that
every scripted moment happened.
"""

from __future__ import annotations

from typing import Any

from matchlens.domain.models import EventType, MatchMeta
from matchlens.interpret.engine import MatchEngine
from matchlens.simulator.storyline import Beat, BeatType, Phase, Storyline, comeback_storyline

MAX_GOALS = 7
MAX_SUBS = 5
MAX_REDS = 2
# A scripted moment counts as delivered within this many minutes of its slot.
TOLERANCE_MIN = 2


def _b(minute: int, kind: BeatType, team: str, player: str, related: str | None = None) -> Beat:
    return Beat(minute=minute, type=kind, team_id=team, player_id=player, related_player_id=related)


def ten_man_heist() -> Storyline:
    return Storyline(
        title="The Ten-Man Heist",
        summary="Harbour City lose a centre-back to a red card before the half hour, defend for their lives "
                "and steal it on the counter.",
        phases=[
            Phase(start_minute=0, end_minute=28, home_dominance=0.1, label="Cagey start"),
            Phase(start_minute=28, end_minute=60, home_dominance=0.65, label="Northbridge siege"),
            Phase(start_minute=60, end_minute=78, home_dominance=0.5, label="Wave after wave"),
            Phase(start_minute=78, end_minute=96, home_dominance=0.8, label="Last stand"),
        ],
        beats=[
            _b(12, BeatType.YELLOW_CARD, "HBC", "HBC-04"),
            _b(28, BeatType.RED_CARD, "HBC", "HBC-04"),
            _b(31, BeatType.SUBSTITUTION, "HBC", "HBC-10", "HBC-13"),
            _b(39, BeatType.BIG_CHANCE, "NBR", "NBR-10"),
            _b(57, BeatType.BIG_CHANCE, "NBR", "NBR-11"),
            _b(63, BeatType.SUBSTITUTION, "NBR", "NBR-08", "NBR-15"),
            _b(69, BeatType.BIG_CHANCE, "NBR", "NBR-15"),
            _b(77, BeatType.GOAL, "HBC", "HBC-11", "HBC-09"),
            _b(84, BeatType.SUBSTITUTION, "NBR", "NBR-04", "NBR-16"),
            _b(88, BeatType.BIG_CHANCE, "NBR", "NBR-16"),
            _b(91, BeatType.YELLOW_CARD, "HBC", "HBC-11"),
        ],
    )


def late_late_show() -> Storyline:
    return Storyline(
        title="The Late, Late Show",
        summary="Eighty-eight minutes of chances and nerves, then two goals in stoppage time.",
        phases=[
            Phase(start_minute=0, end_minute=30, home_dominance=0.2, label="Northbridge probe"),
            Phase(start_minute=30, end_minute=60, home_dominance=-0.3, label="Harbour respond"),
            Phase(start_minute=60, end_minute=85, home_dominance=0.0, label="End to end"),
            Phase(start_minute=85, end_minute=96, home_dominance=0.4, label="Frantic finish"),
        ],
        beats=[
            _b(17, BeatType.BIG_CHANCE, "NBR", "NBR-09"),
            _b(34, BeatType.BIG_CHANCE, "HBC", "HBC-09"),
            _b(48, BeatType.YELLOW_CARD, "NBR", "NBR-07"),
            _b(61, BeatType.BIG_CHANCE, "HBC", "HBC-11"),
            _b(70, BeatType.SUBSTITUTION, "NBR", "NBR-09", "NBR-16"),
            _b(76, BeatType.SUBSTITUTION, "HBC", "HBC-08", "HBC-16"),
            _b(82, BeatType.BIG_CHANCE, "NBR", "NBR-10"),
            _b(91, BeatType.GOAL, "NBR", "NBR-16", "NBR-11"),
            _b(94, BeatType.GOAL, "HBC", "HBC-16", "HBC-05"),
        ],
    )


def goal_fest() -> Storyline:
    return Storyline(
        title="Seven-Goal Thriller",
        summary="Neither defence turns up: the lead changes hands three times before Brandvold settles a 4-3.",
        phases=[
            Phase(start_minute=0, end_minute=20, home_dominance=0.35, label="Northbridge fast start"),
            Phase(start_minute=20, end_minute=46, home_dominance=-0.35, label="Harbour hit back"),
            Phase(start_minute=46, end_minute=70, home_dominance=0.2, label="Chaos"),
            Phase(start_minute=70, end_minute=96, home_dominance=0.1, label="Anyone's game"),
        ],
        beats=[
            _b(9, BeatType.GOAL, "NBR", "NBR-10", "NBR-11"),
            _b(21, BeatType.GOAL, "HBC", "HBC-11", "HBC-09"),
            _b(33, BeatType.GOAL, "NBR", "NBR-11", "NBR-06"),
            _b(44, BeatType.GOAL, "HBC", "HBC-09", "HBC-10"),
            _b(55, BeatType.YELLOW_CARD, "HBC", "HBC-07"),
            _b(58, BeatType.GOAL, "NBR", "NBR-09", "NBR-10"),
            _b(66, BeatType.SUBSTITUTION, "HBC", "HBC-06", "HBC-15"),
            _b(71, BeatType.GOAL, "HBC", "HBC-15", "HBC-11"),
            _b(80, BeatType.BIG_CHANCE, "HBC", "HBC-11"),
            _b(88, BeatType.GOAL, "NBR", "NBR-10", "NBR-09"),
        ],
    )


# id -> (factory, seed). The comeback keeps seed 7, so it replays the recorded demo agents.
PRESETS: dict[str, tuple[Any, int]] = {
    "comeback": (comeback_storyline, 7),
    "ten-man-heist": (ten_man_heist, 11),
    "late-late-show": (late_late_show, 23),
    "goal-fest": (goal_fest, 31),
}


def presets() -> list[dict[str, Any]]:
    return [{"preset_id": pid, "seed": seed, "storyline": factory().model_dump(mode="json")}
            for pid, (factory, seed) in PRESETS.items()]


def check_storyline(meta: MatchMeta, story: Storyline) -> list[str]:
    """Everything that would stop the simulator playing the script as written."""
    problems: list[str] = []
    teams = {meta.home.team_id: meta.home, meta.away.team_id: meta.away}
    on_pitch = {tid: set(t.starting_xi) for tid, t in teams.items()}
    bench = {tid: set(t.bench) for tid, t in teams.items()}
    yellows: dict[str, int] = {}
    goals = 0
    subs = {tid: 0 for tid in teams}
    reds = 0
    for b in story.beats:
        where = f"{b.minute}' {b.type.value}"
        if b.team_id not in teams:
            problems.append(f"{where}: unknown team {b.team_id}")
            continue
        if b.type == BeatType.SUBSTITUTION:
            if b.player_id not in on_pitch[b.team_id]:
                problems.append(f"{where}: {b.player_id} is not on the pitch for {b.team_id}")
            if not b.related_player_id or b.related_player_id not in bench[b.team_id]:
                problems.append(f"{where}: {b.related_player_id} is not on {b.team_id}'s bench")
            else:
                bench[b.team_id].discard(b.related_player_id)
                on_pitch[b.team_id].discard(b.player_id)
                on_pitch[b.team_id].add(b.related_player_id)
            subs[b.team_id] += 1
            continue
        if b.player_id not in on_pitch[b.team_id]:
            problems.append(f"{where}: {b.player_id} is not on the pitch for {b.team_id}")
        if b.type == BeatType.GOAL:
            goals += 1
            if b.related_player_id and (b.related_player_id == b.player_id
                                        or b.related_player_id not in on_pitch[b.team_id]):
                problems.append(f"{where}: assist {b.related_player_id} is not an on-pitch team-mate")
        if b.type == BeatType.YELLOW_CARD:
            yellows[b.player_id] = yellows.get(b.player_id, 0) + 1
        if b.type == BeatType.RED_CARD or yellows.get(b.player_id, 0) >= 2:
            reds += 1
            on_pitch[b.team_id].discard(b.player_id)
    if goals > MAX_GOALS:
        problems.append(f"{goals} goals; at most {MAX_GOALS}")
    if reds > MAX_REDS:
        problems.append(f"{reds} red cards; at most {MAX_REDS}")
    problems += [f"{tid}: {n} substitutions; at most {MAX_SUBS}" for tid, n in subs.items() if n > MAX_SUBS]
    for p in story.phases:
        if p.end_minute <= p.start_minute:
            problems.append(f"phase {p.label or p.start_minute}: ends before it starts")
    return problems


_DELIVERED_BY = {
    BeatType.GOAL: {EventType.GOAL},
    BeatType.BIG_CHANCE: {EventType.SHOT},
    BeatType.YELLOW_CARD: {EventType.CARD},
    BeatType.RED_CARD: {EventType.CARD},
    BeatType.SUBSTITUTION: {EventType.SUBSTITUTION},
}


def script_vs_reality(engine: MatchEngine, story: Storyline) -> dict[str, Any]:
    """For every scripted beat: did it happen, and when. Beats still to come are pending."""
    # Beats are scripted on the match clock (period and minute), not on elapsed time.
    now = (engine.c.period, engine.c.clock)
    finished = engine.state()["status"] == "full_time"
    rows = []
    for b in story.beats:
        period = 1 if b.minute <= 45 else 2
        lo, hi = (b.minute - 1 - TOLERANCE_MIN) * 60, (b.minute + TOLERANCE_MIN) * 60
        match = next((e for e in engine.events(player_id=b.player_id, types=_DELIVERED_BY[b.type])
                      if e.player_id == b.player_id and e.team_id == b.team_id and e.period == period
                      and lo <= e.clock <= hi), None)
        passed = now[0] > period or (now[0] == period and now[1] > hi)
        status = "delivered" if match else ("missed" if finished or passed else "pending")
        rows.append({"minute": b.minute, "type": b.type.value, "team_id": b.team_id, "player_id": b.player_id,
                     "related_player_id": b.related_player_id, "status": status,
                     "event_id": match.event_id if match else None,
                     "minute_label": match.minute_label if match else None})
    return {
        "title": story.title,
        "summary": story.summary,
        "finished": finished,
        "beats": rows,
        "delivered": sum(r["status"] == "delivered" for r in rows),
        "total": len(rows),
    }
