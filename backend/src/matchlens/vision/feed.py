"""A vision run as a Matchlens match: fictional meta, events and 1 Hz tracking.

Footage of real players never carries their names into Matchlens (BRD §9.3):
teams and players get made-up names, shirt numbers by role, and the kit colour
seen on video. The result is a :class:`SyntheticMatch`, the same container a
generated match uses, so it replays through the usual adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from matchlens.domain.models import (
    PITCH_LENGTH,
    PITCH_WIDTH,
    EntityPosition,
    Event,
    EventType,
    MatchMeta,
    Player,
    Point,
    Position,
    Side,
    SyntheticMatch,
    Team,
    TrackingFrame,
)

from .events import Detected
from .teams import LEFT, TeamSplit
from .tracks import per_second

# (team_id, name, short name) for the team starting on the left (home) and right.
TEAMS = (("WSM", "Westmoor", "Westmoor"), ("EBK", "Eastbrook", "Eastbrook"))
LINES = ((Position.DEF, 0.4, "B"), (Position.MID, 0.4, "M"), (Position.FWD, 0.2, "F"))
SHIRTS = {"GK": [1], "RB": [2], "LB": [3], "CB": [5, 4, 6, 15], "RM": [7], "LM": [11], "CM": [8, 6, 10, 14, 16],
          "CF": [9, 10, 19], "RF": [7, 17], "LF": [11, 18]}


@dataclass(frozen=True)
class Roster:
    meta: MatchMeta
    player_of: dict[int, str]  # track column -> player_id
    team_of: dict[int, str]  # LEFT/RIGHT -> team_id


def _relative(side: int, x: float, y: float) -> tuple[float, float]:
    """Team-relative position: x towards the goal they attack, y from their right touchline."""
    return (x, y) if side == LEFT else (PITCH_LENGTH - x, PITCH_WIDTH - y)


def roster(match_id: str, split: TeamSplit, players: np.ndarray, *, competition: str = "Matchlens Video Lab",
           venue: str = "Westmoor Training Ground", kickoff_utc: str = "2026-10-25T10:00:00Z") -> Roster:
    """Teams and players for the tracks of each side, lines read from where each track played."""
    median = np.nanmedian(players, axis=0)
    teams: list[Team] = []
    everyone: dict[str, Player] = {}
    player_of: dict[int, str] = {}
    for side, (team_id, name, short) in enumerate(TEAMS):
        tracks = [int(t) for t in np.flatnonzero(split.labels == side)]
        keepers = [t for t in tracks if t in split.keepers]
        outfield = sorted((t for t in tracks if t not in split.keepers),
                          key=lambda t: _relative(side, *median[t])[0])
        slots: list[tuple[int, Position, str]] = [(t, Position.GK, "GK") for t in keepers]
        start = 0
        for i, (position, share, letter) in enumerate(LINES):
            count = len(outfield) - start if i == len(LINES) - 1 else round(share * len(outfield))
            line = sorted(outfield[start:start + count], key=lambda t: -_relative(side, *median[t])[1])
            start += count
            for k, t in enumerate(line):  # left to right as the team faces; wide players at the ends
                flank = "L" if k == 0 and len(line) > 2 else "R" if k == len(line) - 1 and len(line) > 2 else "C"
                slots.append((t, position, flank + letter))
        taken: set[int] = set()
        xi = []
        for t, position, role in slots:
            spare = (n for n in range(12, 100) if n not in taken)
            shirt = next((n for n in SHIRTS[role] if n not in taken), None) or next(spare)
            taken.add(shirt)
            player_id = f"{team_id}-{shirt:02d}"
            everyone[player_id] = Player(player_id=player_id, name=f"{name} No. {shirt}", short_name=f"#{shirt}",
                                         shirt=shirt, position=position, role=role, nationality="")
            player_of[t] = player_id
            xi.append(player_id)
        lines = [sum(1 for _, p, _ in slots if p == position) for position, _, _ in LINES]
        teams.append(Team(team_id=team_id, name=name, short_name=short, side=Side.HOME if side == LEFT else Side.AWAY,
                          colour=split.kits[side], formation="-".join(str(n) for n in lines if n),
                          starting_xi=xi, bench=[]))
    meta = MatchMeta(match_id=match_id, competition=competition, venue=venue, kickoff_utc=kickoff_utc,
                     home=teams[0], away=teams[1], players=everyone)
    return Roster(meta=meta, player_of=player_of, team_of={LEFT: TEAMS[0][0], 1 - LEFT: TEAMS[1][0]})


def _point(xy: tuple[float, float] | None) -> Point | None:
    if xy is None:
        return None
    return Point(x=float(np.clip(xy[0], 0, PITCH_LENGTH)), y=float(np.clip(xy[1], 0, PITCH_WIDTH)))


def to_events(detected: list[Detected], cast: Roster, fps: float) -> list[Event]:
    events = []
    for seq, d in enumerate(detected, start=1):
        seconds = round(d.frame / fps, 2)
        events.append(Event(
            event_id=f"{cast.meta.match_id}-v{seq:05d}", match_id=cast.meta.match_id, sequence=seq, period=1,
            clock=seconds, elapsed=seconds, type=d.type, team_id=cast.team_of[d.team],
            player_id=cast.player_of.get(d.track) if d.track is not None else None,
            related_player_id=cast.player_of.get(d.other) if d.other is not None else None,
            start=_point(d.start), end=_point(d.end), outcome=d.outcome, attrs=dict(d.attrs),
        ))
    return events


def to_tracking(players: np.ndarray, ball: np.ndarray, cast: Roster, match_id: str, fps: float) -> list[TrackingFrame]:
    """One frame a second: where everyone was, and how fast they covered the second before."""
    at, speed = per_second(players, fps)
    ball_at, ball_speed = per_second(ball[:, None], fps)
    frames = []
    for s in range(len(at)):
        entities = [EntityPosition(entity_id=pid, x=float(np.clip(at[s, t, 0], 0, PITCH_LENGTH)),
                                   y=float(np.clip(at[s, t, 1], 0, PITCH_WIDTH)),
                                   speed=float(np.nan_to_num(speed[s, t])))
                    for t, pid in cast.player_of.items() if np.isfinite(at[s, t]).all()]
        if np.isfinite(ball_at[s, 0]).all():
            entities.append(EntityPosition(entity_id="ball", x=float(np.clip(ball_at[s, 0, 0], 0, PITCH_LENGTH)),
                                           y=float(np.clip(ball_at[s, 0, 1], 0, PITCH_WIDTH)),
                                           speed=float(np.nan_to_num(ball_speed[s, 0]))))
        frames.append(TrackingFrame(match_id=match_id, clock=float(s), elapsed=float(s), period=1, entities=entities))
    return frames


def build_match(match_id: str, split: TeamSplit, players: np.ndarray, ball: np.ndarray,
                detected: list[Detected], fps: float) -> SyntheticMatch:
    """The whole clip as a match. It ends with a full-time whistle at the clip's last frame, so
    polls resolve and ratings settle as they would at the end of a match."""
    cast = roster(match_id, split, players)
    events = to_events(detected, cast, fps)
    end = round((len(players) - 1) / fps, 2)
    events.append(Event(event_id=f"{match_id}-v{len(events) + 1:05d}", match_id=match_id, sequence=len(events) + 1,
                        period=1, clock=end, elapsed=end, type=EventType.FULL_TIME, attrs={"clip_end": True}))
    return SyntheticMatch(meta=cast.meta, events=events, tracking=to_tracking(players, ball, cast, match_id, fps),
                          seed=0)
