"""P2-c: a scorer correction says which goal it is about and who was credited first."""

from __future__ import annotations

import pytest

from matchlens.agents import templates
from matchlens.agents.context import signal_pack
from matchlens.agents.pipeline import InsightPipeline
from matchlens.ingest.adapters import SyntheticReplayAdapter
from matchlens.interpret.signals import SignalType
from matchlens.runtime.session import MatchSession

from .conftest import replay_order
from .test_agents import ScriptedClient, fresh_session, run, team_for


@pytest.fixture(scope="module")
def played(demo_match):
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session


def _corrections(session):
    return [s for s in session.engine.signals() if s.type == SignalType.CORRECTION]


def test_correction_signal_carries_the_minute_of_the_corrected_event(played):
    goal_corr = next(s for s in _corrections(played) if s.facts["corrected_event_type"] == "goal")
    target = played.engine.event(goal_corr.evidence_event_ids[1])
    assert goal_corr.facts["corrected_minute_label"] == target.minute_label


@pytest.mark.parametrize("lang", ["en", "es", "hi"])
def test_goal_correction_card_names_minute_and_both_players(played, lang):
    sig = next(s for s in _corrections(played) if s.facts["corrected_event_type"] == "goal")
    pack = signal_pack(played.engine, sig)
    head, body = templates.data_text(pack, lang)
    names = {p["player_id"]: p["name"] for p in pack["players"]}
    assert sig.facts["corrected_minute_label"] in head
    assert names[sig.facts["value"]] in head and names[sig.facts["previous"]] in body
    if lang == "en":
        assert head.startswith("Goal at ") and "re-credited to" in head
        assert body.startswith("The data feed first credited")


def test_demo_produces_exactly_one_correction_insight(demo_match):
    # Live, the feed emits a correction signal for the shot and another for the goal.
    live = MatchSession(SyntheticReplayAdapter(demo_match))
    corrections = []
    for kind, item in replay_order(demo_match):
        out = live.engine.ingest_event(item) if kind else live.engine.ingest_frame(item)
        corrections += [s for s in out if s.type == SignalType.CORRECTION]
    assert sorted(s.facts["corrected_event_type"] for s in corrections) == ["goal", "shot"]
    played = live
    session = fresh_session(played)
    pipe = InsightPipeline(team_for(ScriptedClient()))
    for sig in corrections:
        run(pipe.handle(sig, session))
    ids = [sid for sid in session.insights if sid.startswith("correction:")]
    assert len(ids) == 1
    insight = session.insights[ids[0]]
    goal_corr = next(s for s in corrections if s.facts["corrected_event_type"] == "goal")
    assert goal_corr.facts["corrected_minute_label"] in insight["headline"]
