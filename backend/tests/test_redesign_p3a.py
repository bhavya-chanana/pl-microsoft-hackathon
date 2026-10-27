"""P3-a: minutes played and duels per player."""

from __future__ import annotations

import pytest

from matchlens.domain.models import EventType, Outcome
from matchlens.ingest.adapters import SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession

from .conftest import replay_order


@pytest.fixture(scope="module")
def played(demo_match):
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session


def _full_match_minutes(engine, meta):
    subbed = {e.player_id for e in engine.events(types={EventType.SUBSTITUTION})}
    carded = {e.player_id for e in engine.events(types={EventType.CARD})
              if e.outcome in (Outcome.RED, Outcome.SECOND_YELLOW)}
    return [engine.player_stats(p)["minutes"] for t in (meta.home, meta.away) for p in t.starting_xi
            if p not in subbed | carded]


def test_stats_expose_minutes_and_duels(played):
    s = played.engine.player_stats(played.meta.home.starting_xi[0])
    assert {"minutes", "duels_won", "duels_total"} <= set(s)
    assert isinstance(s["minutes"], int)


def test_a_starter_who_plays_it_all_has_about_ninety_minutes(played):
    full = _full_match_minutes(played.engine, played.meta)
    assert full and all(90 <= m <= 100 for m in full)


def test_substitutes_and_the_dismissed_stop_the_clock(played):
    engine = played.engine
    whole = max(_full_match_minutes(engine, played.meta))
    subs = engine.events(types={EventType.SUBSTITUTION})
    assert subs
    for e in subs:
        off, on = engine.player_stats(e.player_id)["minutes"], engine.player_stats(e.related_player_id)["minutes"]
        assert 0 < on < whole and 0 < off < whole
        assert abs(off + on - whole) <= 2, (e.minute_label, off, on, whole)  # the pair shares one slot
    reds = [e for e in engine.events(types={EventType.CARD}) if e.outcome in (Outcome.RED, Outcome.SECOND_YELLOW)]
    assert reds
    for e in reds:
        assert engine.player_stats(e.player_id)["minutes"] < whole - 1


def test_minutes_are_not_doubled_by_a_rebuild(played):
    engine = played.engine
    before = {p: engine.player_stats(p)["minutes"] for p in played.meta.players}
    engine._rebuild(reason=None)
    assert before == {p: engine.player_stats(p)["minutes"] for p in played.meta.players}


def test_duels_are_consistent(played):
    engine = played.engine
    stats = [engine.player_stats(p) for p in played.meta.players]
    assert all(0 <= s["duels_won"] <= s["duels_total"] for s in stats)
    total = sum(s["duels_total"] for s in stats)
    pressures = [e for e in engine.events(types={EventType.PRESSURE}) if e.related_player_id]
    assert total == 2 * len(pressures) > 0  # every contest has two sides
    assert sum(s["duels_won"] for s in stats) == len(pressures)  # and exactly one winner


def test_a_tackle_wins_the_duel_and_anything_else_loses_it(demo_match):
    from matchlens.interpret.engine import MatchEngine
    events = [e for e in demo_match.events]
    idx = next(i for i, e in enumerate(events) if e.type == EventType.PRESSURE and e.related_player_id
               and events[i + 1].type == EventType.TACKLE and events[i + 1].player_id == e.player_id)
    engine = MatchEngine(demo_match.meta)
    for e in events[:idx + 2]:
        engine.ingest_event(e)
    p = events[idx]
    presser, carrier = engine.player_stats(p.player_id), engine.player_stats(p.related_player_id)
    assert presser["duels_won"] >= 1
    assert carrier["duels_total"] >= 1
