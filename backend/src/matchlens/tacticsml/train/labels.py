"""Training labels from a time-ordered event stream (pure Python, provider-neutral).

Every source converts its events to ``LEvent`` first, so the label rules live in one place:

* ``receiver``: the player who receives the possession team's next pass (or keeps the ball on a carry);
  "lost" when the pass fails or the ball changes team before the next action; "unknown" otherwise.
* ``shot10`` / ``goal10`` / ``concede10``: the possession team shoots / scores / concedes in the next
  10 seconds (events strictly after the sample time, up to and including t + 10).
* ``turn5``: the possession team loses the ball within 5 seconds (a change of possession; the restart
  after a goal is not a turnover).

Labels are 0/1, or -1 when unknown: a window that runs past the end of the data (or the period) and has
no positive event in the part that was seen.

Event kinds (anything else is ignored):

========  =====================================================================================
pass      ``team_id`` passes; ``outcome`` "success" / "fail" / "none" (unknown); ``receiver_id``
          the receiver of a successful pass (or the intended one of a failed pass)
carry     ``team_id`` carries / dribbles; the carrier is ``player_id``
shot      ``team_id`` shoots; ``outcome`` "goal" when it scored
goal      ``team_id`` scored (use when the source has a separate goal event)
gain      ``team_id`` wins the ball (recovery, interception, restart awarded)
========  =====================================================================================
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

SHOT_WINDOW = 10.0
TURN_WINDOW = 5.0
MAX_ACTION_GAP = 6.0  # the "next action" must come this soon, else the receiver is unknown
_ACTIONS = ("pass", "carry")
_HOLDS_BALL = ("pass", "carry", "shot", "gain")

RECEIVER_PLAYER = "player"
RECEIVER_LOST = "lost"
RECEIVER_UNKNOWN = "unknown"


@dataclass(frozen=True)
class LEvent:
    t: float  # seconds on a time axis that is monotonic within a period
    team_id: str
    player_id: str | None
    kind: str
    receiver_id: str | None = None
    outcome: str = "none"
    period: int = 1


@dataclass(frozen=True)
class Labels:
    receiver_id: str | None
    receiver_state: str  # RECEIVER_PLAYER | RECEIVER_LOST | RECEIVER_UNKNOWN
    shot10: int
    goal10: int
    concede10: int
    turn5: int


UNKNOWN = Labels(None, RECEIVER_UNKNOWN, -1, -1, -1, -1)


def receiver_index(labels: Labels, poss_node_ids: list[str] | tuple[str, ...]) -> int:
    """Node index 0..10 of the receiver among the possession team, 11 = lost, -1 = unknown."""
    if labels.receiver_state == RECEIVER_LOST:
        return 11
    if labels.receiver_state == RECEIVER_PLAYER and labels.receiver_id in poss_node_ids[:11]:
        return list(poss_node_ids[:11]).index(labels.receiver_id)
    return -1


def _scores(e: LEvent) -> bool:
    return e.kind == "goal" or (e.kind == "shot" and e.outcome == "goal")


class EventStream:
    """A match's events, indexed for label queries.

    ``ends`` maps a period to the last second the source covers, used to tell "nothing happened" from
    "we do not know" near the end of a period.
    """

    def __init__(self, events: list[LEvent], ends: dict[int, float] | None = None) -> None:
        self.events = sorted(events, key=lambda e: (e.period, e.t))
        self.ends = ends or {}
        self.teams = sorted({e.team_id for e in self.events})
        self._keys = [(e.period, e.t) for e in self.events]
        # possession switches: (period, t, new team, reason)
        self._switches: list[tuple[int, float, str, str]] = []
        poss: str | None = None
        for e in self.events:
            new: str | None = None
            if e.kind in _HOLDS_BALL:
                new = e.team_id
            if new is not None and new != poss:
                self._switches.append((e.period, e.t, new, "start" if poss is None else "play"))
                poss = new
            if _scores(e):
                other = self.other(e.team_id)
                if other is not None and other != poss:
                    self._switches.append((e.period, e.t, other, "goal"))
                    poss = other
        self._sw_keys = [(s[0], s[1]) for s in self._switches]

    def other(self, team_id: str) -> str | None:
        rest = [t for t in self.teams if t != team_id]
        return rest[0] if len(rest) == 1 else None

    # ------------------------------------------------------------ possession

    def possession_at(self, t: float, period: int) -> str | None:
        """The team in possession just after time ``t`` (events at ``t`` included)."""
        i = bisect_right(self._sw_keys, (period, t))
        return self._switches[i - 1][2] if i else None

    # ------------------------------------------------------------ labels

    def _known_until(self, period: int, t: float) -> bool:
        end = self.ends.get(period)
        return end is None or t <= end + 1e-9

    def _window(self, t0: float, period: int, horizon: float):
        lo = bisect_right(self._keys, (period, t0))  # strictly after t0
        hi = bisect_right(self._keys, (period, t0 + horizon))
        return self.events[lo:hi]

    def _flag(self, hit: bool, t0: float, period: int, horizon: float) -> int:
        if hit:
            return 1
        return 0 if self._known_until(period, t0 + horizon) else -1

    def labels_at(self, t: float, period: int, team_id: str, event: LEvent | None = None) -> Labels:
        """Labels of a moment at ``t`` for ``team_id`` in possession.

        ``event`` is the pass/carry that starts at ``t`` when the sample is anchored on one; otherwise
        the receiver is taken from the next pass or carry after ``t``.
        """
        window = self._window(t, period, SHOT_WINDOW)
        shot = any(e.kind == "shot" and e.team_id == team_id for e in window)
        goal = any(_scores(e) and e.team_id == team_id for e in window)
        concede = any(_scores(e) and e.team_id != team_id for e in window)
        turn = any(s[2] != team_id and s[3] == "play" and period == s[0] and t < s[1] <= t + TURN_WINDOW
                   for s in self._switches)
        rid, state = self._receiver(t, period, team_id, event)
        return Labels(
            receiver_id=rid, receiver_state=state,
            shot10=self._flag(shot, t, period, SHOT_WINDOW),
            goal10=self._flag(goal, t, period, SHOT_WINDOW),
            concede10=self._flag(concede, t, period, SHOT_WINDOW),
            turn5=self._flag(turn, t, period, TURN_WINDOW),
        )

    def _receiver(self, t: float, period: int, team_id: str, event: LEvent | None) -> tuple[str | None, str]:
        if event is None:
            nxt = next((e for e in self._window(t, period, MAX_ACTION_GAP) if e.kind in _ACTIONS), None)
            if nxt is None:
                return None, RECEIVER_UNKNOWN
            if nxt.team_id != team_id:
                return None, RECEIVER_LOST
            event = nxt
            lost_switch = any(s[2] != team_id and t < s[1] < event.t and s[0] == period
                              for s in self._switches)
            if lost_switch:
                return None, RECEIVER_LOST
        if event.team_id != team_id:
            return None, RECEIVER_UNKNOWN
        if event.kind == "carry":
            return (event.player_id, RECEIVER_PLAYER) if event.player_id else (None, RECEIVER_UNKNOWN)
        if event.kind != "pass":
            return None, RECEIVER_UNKNOWN
        if event.outcome == "fail":
            return None, RECEIVER_LOST
        if event.outcome == "success" and event.receiver_id:
            return event.receiver_id, RECEIVER_PLAYER
        return None, RECEIVER_UNKNOWN
