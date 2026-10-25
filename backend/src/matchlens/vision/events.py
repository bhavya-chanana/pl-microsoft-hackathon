"""Ball events from tracking: who is on the ball, and what happened between touches.

A player is on the ball when they are its nearest player and close enough to
play it. Consecutive touches give the events: the ball moving to a team-mate is
a completed pass, to an opponent a misplaced pass and an interception (or a
tackle, when it never travelled), and a player running with it is a carry. The
first touch after a dead ball is a restart, named by where it was taken. A
ball struck at goal from shooting range is a shot; a kick-off straight after it
means it went in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH, EventType, Outcome
from matchlens.domain.pitch import GOAL_HALF_WIDTH, GOAL_Y, expected_goals

from .teams import LEFT, RIGHT
from .tracks import smooth

CONTROL_M = 2.0  # ball within this of a player's feet (projection error is about 1.8 m)
MIN_TOUCH_FRAMES = 4  # brushing past the ball for less than this is not a touch
KEEP_M = 3.0  # the player on the ball keeps it while this close...
SWITCH_M = 1.0  # ...unless someone else is this much closer
CONTEST_S = 0.8  # a shorter spell between two of the other team's is a contest, not a turnover
FLIGHT_MPS = 9.0  # a ball moving faster than a sprinter is a pass or shot in flight
TRAVEL_M = 5.0  # the ball must travel this far to be a pass rather than a duel
CARRY_M = 5.0
SHOT_RANGE_M = 35.0
SHOT_FLIGHT_S = 2.5
SHOT_MOUTH_M = 6.0  # beside the posts still counts as a shot, off target
GOAL_LINE_M = 2.0
SPOT_M = 3.0  # a restart this close to the centre spot is a kick-off
TOUCHLINE_M = 2.0
GOAL_AREA_M = 5.5
CORNER_M = 5.0  # a goal-line restart this close to a touchline is a corner


@dataclass(frozen=True)
class Touch:
    track: int  # track column
    first: int  # frame
    last: int


@dataclass(frozen=True)
class Detected:
    """One event in track terms; the feed turns tracks into players."""

    type: EventType
    frame: int
    track: int | None
    team: int  # LEFT or RIGHT
    other: int | None = None  # receiver, tackled or shooting player
    start: tuple[float, float] | None = None
    end: tuple[float, float] | None = None
    outcome: Outcome = Outcome.NONE
    attrs: dict[str, float | int | str | bool] = field(default_factory=dict)


def _owners(gap: np.ndarray, flying: np.ndarray) -> np.ndarray:
    """Who is on the ball each frame (track column, or -1), with hysteresis: whoever has
    it keeps it until they lose touch or someone is clearly closer, so a duel does not
    flicker between two players."""
    owner = np.full(len(gap), -1)
    current = -1
    for f in range(len(gap)):
        nearest = int(gap[f].argmin())
        if not np.isfinite(gap[f, nearest]) or flying[f]:
            current = -1
        elif current >= 0 and gap[f, current] <= KEEP_M and gap[f, nearest] >= gap[f, current] - SWITCH_M:
            pass
        else:
            current = nearest if gap[f, nearest] <= CONTROL_M else -1
        owner[f] = current
    return owner


def touches(players: np.ndarray, ball: np.ndarray, in_play: np.ndarray, teams: np.ndarray,
            fps: float = 25.0) -> list[Touch]:
    """Spells on the ball, (frames, tracks, 2) players and (frames, 2) ball in pitch metres.
    Tracks outside both teams (referees) never touch it; nobody controls a ball in flight."""
    gap = np.linalg.norm(players - ball[:, None], axis=2)
    gap[:, teams < 0] = np.inf
    gap[~in_play] = np.inf
    gap = np.where(np.isfinite(gap), gap, np.inf)
    ball_speed = np.hypot(*np.gradient(smooth(ball[:, None], int(fps) // 2 | 1)[:, 0], axis=0).T) * fps
    owner = _owners(gap, ball_speed > FLIGHT_MPS)

    spells: list[Touch] = []
    start = 0
    for f in range(1, len(owner) + 1):
        if f == len(owner) or owner[f] != owner[start]:
            if owner[start] >= 0 and f - start >= MIN_TOUCH_FRAMES:
                spells.append(Touch(int(owner[start]), start, f - 1))
            start = f

    return _settle(_merge(spells, in_play), teams, in_play, int(CONTEST_S * fps))


def _merge(spells: list[Touch], in_play: np.ndarray) -> list[Touch]:
    merged: list[Touch] = []
    for t in spells:  # the same player again, with the ball in play throughout: one spell
        if merged and merged[-1].track == t.track and in_play[merged[-1].last:t.first + 1].all():
            merged[-1] = Touch(t.track, merged[-1].first, t.last)
        else:
            merged.append(t)
    return merged


def _settle(spells: list[Touch], teams: np.ndarray, in_play: np.ndarray, contest: int) -> list[Touch]:
    """Drop a brief spell by one team between two of the other's: a contested ball or a
    deflection the team kept, not two changes of possession."""
    while True:
        drop = {i for i in range(1, len(spells) - 1)
                if spells[i].last - spells[i].first < contest
                and teams[spells[i - 1].track] == teams[spells[i + 1].track] != teams[spells[i].track]
                and in_play[spells[i - 1].last:spells[i + 1].first + 1].all()}
        if not drop:
            return spells
        spells = _merge([t for i, t in enumerate(spells) if i not in drop], in_play)


def _xy(p: np.ndarray) -> tuple[float, float]:
    return float(np.clip(p[0], 0, PITCH_LENGTH)), float(np.clip(p[1], 0, PITCH_WIDTH))


def _towards(team: int, x: float, y: float) -> tuple[float, float]:
    """Position relative to the goal ``team`` attacks (LEFT attacks x=105)."""
    return (x, y) if team == LEFT else (PITCH_LENGTH - x, PITCH_WIDTH - y)


def _restart(team: int, at: tuple[float, float]) -> tuple[EventType, int]:
    """What a restart taken at ``at`` was, and whose. On a goal line the place says whose
    it is (a corner for the attackers, a goal kick for the defenders), whoever touched first."""
    x, y = at
    if np.hypot(x - PITCH_LENGTH / 2, y - PITCH_WIDTH / 2) <= SPOT_M:
        return EventType.KICKOFF, team
    attackers = LEFT if x > PITCH_LENGTH / 2 else RIGHT  # the team attacking this end
    from_line = min(x, PITCH_LENGTH - x)
    if from_line <= GOAL_LINE_M and (y <= CORNER_M or y >= PITCH_WIDTH - CORNER_M):
        return EventType.CORNER, attackers
    if from_line <= GOAL_AREA_M + 1.0 and abs(y - GOAL_Y) <= GOAL_HALF_WIDTH + GOAL_AREA_M + 1.0:
        return EventType.GOAL_KICK, 1 - attackers
    if y <= TOUCHLINE_M or y >= PITCH_WIDTH - TOUCHLINE_M:
        return EventType.THROW_IN, team
    return EventType.FREE_KICK, team


def _shot(team: int, ball: np.ndarray, frames: range, fps: float) -> dict[str, float | int | str | bool] | None:
    """A shot when the ball, after leaving the player, reaches the goal line they attack
    near the goal mouth, struck from shooting range."""
    kicked = _towards(team, *ball[frames.start])
    if PITCH_LENGTH - kicked[0] > SHOT_RANGE_M:
        return None
    for f in frames:
        rx, ry = _towards(team, *ball[f])
        if rx >= PITCH_LENGTH - GOAL_LINE_M:
            if abs(ry - GOAL_Y) > GOAL_HALF_WIDTH + SHOT_MOUTH_M:
                return None
            seconds = max(f - frames.start, 1) / fps
            distance = float(np.hypot(rx - kicked[0], ry - kicked[1]))
            return {"xg": expected_goals(*kicked), "distance_m": round(PITCH_LENGTH - kicked[0], 1),
                    "shot_speed_kmh": round(distance / seconds * 3.6, 1),
                    "on_target": abs(ry - GOAL_Y) <= GOAL_HALF_WIDTH}
    return None


def detect(players: np.ndarray, ball: np.ndarray, in_play: np.ndarray, teams: np.ndarray,
           keepers: frozenset[int], fps: float) -> list[Detected]:
    """Events, in frame order, from tracked players and ball."""
    spells = touches(players, ball, in_play, teams, fps)

    def resumed(i: int) -> int | None:
        """The frame play restarted before spell ``i``, if it was stopped since the last touch."""
        since = spells[i - 1].last if i else 0
        dead = np.flatnonzero(~in_play[since:spells[i].first + 1])
        return since + int(dead[-1]) + 1 if len(dead) else (spells[i].first if i == 0 else None)

    out: list[Detected] = []
    for i, a in enumerate(spells):
        team = int(teams[a.track])
        restart = resumed(i)
        if restart is not None:  # placed where the ball was when play resumed, before anyone is near it
            at = _xy(ball[restart])
            kind, taker = _restart(team, at)
            out.append(Detected(kind, restart, a.track if taker == team else None, taker, start=at))
        moved = float(np.linalg.norm(players[a.last, a.track] - players[a.first, a.track]))
        if moved >= CARRY_M:
            out.append(Detected(EventType.CARRY, a.last, a.track, team, start=_xy(ball[a.first]),
                                end=_xy(ball[a.last]), outcome=Outcome.SUCCESS,
                                attrs={"duration_s": round((a.last - a.first) / fps, 2)}))
        b = spells[i + 1] if i + 1 < len(spells) else None
        stop = b.first if b else len(ball) - 1
        dead = np.flatnonzero(~in_play[a.last:stop + 1])
        ends = a.last + int(dead[0]) if len(dead) else stop  # where the ball got to: the next touch, or dead
        start, end = _xy(ball[a.last]), _xy(ball[ends])

        # Follow a shot's flight even once the ball is called dead (it is, the moment it is struck wide).
        shot = _shot(team, ball, range(a.last, min(stop, a.last + int(SHOT_FLIGHT_S * fps)) + 1), fps)
        if shot is not None:
            on_target = bool(shot.pop("on_target"))
            restart = resumed(i + 1) if b is not None else None
            scored = (restart is not None and teams[b.track] != team  # type: ignore[union-attr]
                      and _restart(int(teams[b.track]), _xy(ball[restart]))[0] == EventType.KICKOFF)  # type: ignore[union-attr]
            saved = b is not None and not len(dead) and b.track in keepers and teams[b.track] != team
            outcome = (Outcome.GOAL if scored else Outcome.SAVED if saved or on_target else Outcome.OFF_TARGET)
            out.append(Detected(EventType.SHOT, a.last, a.track, team, start=start, end=end, outcome=outcome,
                                attrs=shot))
            if scored:
                out.append(Detected(EventType.GOAL, ends, a.track, team, start=start, attrs=dict(shot)))
            elif saved and b is not None:
                out.append(Detected(EventType.SAVE, b.first, b.track, int(teams[b.track]), other=a.track,
                                    start=_xy(ball[b.first]), attrs={"xg_faced": shot["xg"]}))
            continue
        if b is None:
            continue
        travel = float(np.linalg.norm(ball[ends] - ball[a.last]))
        if len(dead):  # the ball went out of play off this player
            if travel >= TRAVEL_M:
                out.append(Detected(EventType.PASS, a.last, a.track, team, start=start, end=end, outcome=Outcome.FAIL))
            continue
        other = int(teams[b.track])
        if other == team:
            out.append(Detected(EventType.PASS, a.last, a.track, team, other=b.track, start=start, end=end,
                                outcome=Outcome.SUCCESS))
            continue
        if travel >= TRAVEL_M:
            out.append(Detected(EventType.PASS, a.last, a.track, team, start=start, end=end, outcome=Outcome.FAIL))
            out.append(Detected(EventType.INTERCEPTION, b.first, b.track, other, other=a.track, start=end,
                                outcome=Outcome.SUCCESS))
        else:
            out.append(Detected(EventType.TACKLE, b.first, b.track, other, other=a.track, start=end,
                                outcome=Outcome.SUCCESS))
        out.append(Detected(EventType.POSSESSION_CHANGE, b.first, b.track, other, start=end))
    out.sort(key=lambda d: d.frame)
    return out

