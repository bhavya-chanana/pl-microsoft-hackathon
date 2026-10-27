"""P2-d: signal handlers see the match as of their signal, not as of a later event."""

from __future__ import annotations

import asyncio

from matchlens.domain.models import TrackingFrame
from matchlens.interpret.signals import SignalType
from matchlens.runtime.session import MatchSession

from .conftest import replay_order


class _InstantAdapter:
    """Delivers the feed without ever yielding to the event loop, like a very fast producer."""

    def __init__(self, match) -> None:
        self._match = match

    def meta(self):
        return self._match.meta

    async def stream(self):
        for _, item in replay_order(self._match):
            yield item


def test_handler_sees_the_score_before_a_following_goal(demo_match):
    seen: list[tuple[str, float, dict[str, int], float]] = []

    async def handler(sig, session) -> None:
        # First line: record the engine as the handler finds it.
        seen.append((sig.signal_id, sig.elapsed, dict(session.engine.score()), session.engine.c.elapsed))
        await asyncio.sleep(0)

    async def main() -> MatchSession:
        session = MatchSession(_InstantAdapter(demo_match))
        session.on_signal(handler)
        session.start()
        await session._task
        await asyncio.sleep(0.05)
        return session

    session = asyncio.run(main())
    goals = [s for s in session.engine.signals() if s.type == SignalType.GOAL]
    assert goals and seen
    # Every handler started while the engine was still at its own signal's moment (never later).
    bad = [(sid, e, ee) for sid, e, _, ee in seen if ee > e + 1.0]  # frame signals are stamped up to a second ahead
    assert not bad, bad[:5]
    # In particular, whatever signal precedes a goal never quotes the goal's score.
    first_goal = goals[0]
    before = [score for sid, elapsed, score, _ in seen if elapsed < first_goal.elapsed]
    assert before and all(sum(score.values()) == 0 for score in before), (first_goal.elapsed, before[-3:])
    assert not session._handler_tasks  # finished tasks are released
