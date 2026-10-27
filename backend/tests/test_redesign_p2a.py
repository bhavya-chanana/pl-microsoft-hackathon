"""P2-a: restart kick-offs are flagged and kept off the timeline."""

from __future__ import annotations

import pytest

from matchlens.domain.models import EventType
from matchlens.ingest.adapters import SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession, event_payload

from .conftest import replay_order


@pytest.fixture(scope="module")
def played(demo_match):
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session


def _kickoffs(session):
    return session.engine.events(types={EventType.KICKOFF})


def test_timeline_keeps_one_kickoff_per_half(played):
    all_kickoffs = _kickoffs(played)
    assert len(all_kickoffs) > 2  # goals produce restarts
    shown = [r for r in played.timeline() if r["type"] == "kickoff"]
    assert sorted(r["period"] for r in shown) == [1, 2]
    assert all(r["restart"] is False for r in shown)


def test_post_goal_kickoff_payload_is_flagged_restart(played):
    shown = {r["event_id"] for r in played.timeline()}
    restarts = [e for e in _kickoffs(played) if e.event_id not in shown]
    assert restarts
    assert all(event_payload(played.engine, e)["restart"] is True for e in restarts)
    goal = next(e for e in played.engine.events(types={EventType.GOAL}))
    assert "restart" not in event_payload(played.engine, goal)


def test_restart_set_survives_a_rebuild(played):
    engine = played.engine
    before = [e.event_id for e in _kickoffs(played) if engine.is_restart(e.event_id)]
    engine._rebuild(reason=None)
    after = [e.event_id for e in _kickoffs(played) if engine.is_restart(e.event_id)]
    assert before == after and len(engine.c.period_kickoffs) == 2
