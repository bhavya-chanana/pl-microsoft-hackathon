"""P3-b: possession and field tilt per half."""

from __future__ import annotations

import pytest

from matchlens.domain.models import EventType
from matchlens.ingest.adapters import SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession

from .conftest import replay_order


@pytest.fixture(scope="module")
def played(demo_match):
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session


def _both(session):
    h, a = session.meta.home.team_id, session.meta.away.team_id
    return session.engine.team_stats(h), session.engine.team_stats(a)


def test_each_half_adds_up_to_100(played):
    h, a = _both(played)
    assert set(h["possession_pct_by_half"]) == {"1", "2"} == set(a["possession_pct_by_half"])
    for half in ("1", "2"):
        assert round(h["possession_pct_by_half"][half] + a["possession_pct_by_half"][half], 1) == 100.0
        assert round(h["field_tilt_pct_by_half"][half] + a["field_tilt_pct_by_half"][half], 1) == 100.0
    assert round(h["field_tilt_pct"] + a["field_tilt_pct"], 1) == 100.0


def test_whole_match_possession_is_the_time_weighted_halves(played):
    engine = played.engine
    h, a = _both(played)
    th = engine.c.teams[played.meta.home.team_id]
    secs = {p: th.possession_s_by_period[p] + engine.c.teams[played.meta.away.team_id].possession_s_by_period[p]
            for p in (1, 2)}
    mix = sum(h["possession_pct_by_half"][str(p)] * secs[p] for p in (1, 2)) / sum(secs.values())
    assert abs(mix - h["possession_pct"]) <= 0.2
    assert sum(th.possession_s_by_period.values()) == pytest.approx(th.possession_s)


def test_second_half_is_absent_before_it_starts(demo_match):
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
        if kind and item.type == EventType.HALF_TIME:
            break
    h, _ = _both(session)
    assert set(h["possession_pct_by_half"]) == {"1"} and set(h["field_tilt_pct_by_half"]) == {"1"}


def test_split_is_rebuild_safe(played):
    before = _both(played)
    played.engine._rebuild(reason=None)
    after = _both(played)
    for b, a in zip(before, after, strict=True):
        for k in ("possession_pct_by_half", "field_tilt_pct", "field_tilt_pct_by_half"):
            assert b[k] == a[k]
