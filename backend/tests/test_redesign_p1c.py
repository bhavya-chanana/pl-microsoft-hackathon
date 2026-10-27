"""P1-c: no live-only coach advice after the final whistle."""

from __future__ import annotations

import asyncio

import pytest

from matchlens.agents.coach import find_issues
from matchlens.agents.services import FanServices
from matchlens.domain.models import EventType, Outcome
from matchlens.ingest.adapters import SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession

from .conftest import replay_order
from .test_agents import ScriptedClient, team_for

LIVE_KINDS = {"extra_man", "man_down", "chase_game", "see_it_out", "card_risk", "fresh_legs"}


def run(coro):
    return asyncio.run(coro)


def _session(demo_match, stop):
    """Replay the demo match until stop(kind, item, engine) is true for an ingested item (inclusive)."""
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
        if stop(kind, item, session.engine):
            break
    return session


# ------------------------------------------------------------------ P1-c


def test_full_time_brief_has_no_live_only_items(demo_match):
    session = _session(demo_match, lambda *_: False)
    assert session.engine.state()["status"] == "full_time"
    for team in ("HBC", "NBR"):
        assert not {i.kind for i in find_issues(session.engine, team)} & LIVE_KINDS


def test_man_down_is_found_mid_match(demo_match):
    def after_red(kind, item, engine):
        return kind and item.type == EventType.CARD and item.outcome in (Outcome.RED, Outcome.SECOND_YELLOW)

    session = _session(demo_match, after_red)
    assert session.engine.state()["status"] != "full_time"
    red_team = next(t for t in ("HBC", "NBR") if session.engine.state()["players_on_pitch"][t] == 10)
    other = "NBR" if red_team == "HBC" else "HBC"
    assert "man_down" in {i.kind for i in find_issues(session.engine, red_team)}
    assert "extra_man" in {i.kind for i in find_issues(session.engine, other)}


def _ingest(session, kind, item):
    session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)


def test_brief_is_regenerated_when_the_match_ends(demo_match):
    items = replay_order(demo_match)
    probe = MatchSession(SyntheticReplayAdapter(demo_match))
    flip = None
    for n, (kind, item) in enumerate(items):
        _ingest(probe, kind, item)
        if probe.engine.state()["status"] == "full_time":
            flip = n
            break
    assert flip is not None
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in items[:flip]:
        _ingest(session, kind, item)
    assert session.engine.state()["status"] != "full_time"
    client = ScriptedClient()
    fans = FanServices(team_for(client))
    live = run(fans.coach_brief(session, "HBC", "en"))
    calls = len(client.calls)
    assert run(fans.coach_brief(session, "HBC", "en")) == live and len(client.calls) == calls
    _ingest(session, *items[flip])
    assert session.engine.state()["status"] == "full_time"
    final = run(fans.coach_brief(session, "HBC", "en"))
    assert len(client.calls) > calls  # not served from the live brief's cache entry
    assert not {i.issue_id for i in final.items} & LIVE_KINDS
