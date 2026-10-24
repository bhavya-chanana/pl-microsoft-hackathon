import asyncio

import pytest

from matchlens.agents.coach import TEXT, find_issues
from matchlens.agents.contracts import FactCheckStatus
from matchlens.agents.services import FanServices
from matchlens.ingest.adapters import SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession

from .conftest import replay_order
from .test_agents import ScriptedClient, fresh_session, team_for


@pytest.fixture(scope="module")
def played(demo_match):
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session


def run(coro):
    return asyncio.run(coro)


def test_every_issue_has_every_language():
    assert all(set(t) == {"en", "hi", "es"} for t in TEXT.values())
    assert all(len(v) == 4 for t in TEXT.values() for v in t.values())


def test_brief_is_ranked_grounded_and_fact_checked(played):
    client = ScriptedClient()
    fans = FanServices(team_for(client))
    session = fresh_session(played)
    brief = run(fans.coach_brief(session, "HBC", "en"))
    assert [i.rank for i in brief.items] == list(range(1, len(brief.items) + 1)) and 1 <= len(brief.items) <= 3
    assert brief.fact_check.status == FactCheckStatus.VERIFIED
    assert brief.agents == ["coach_coordinator", "coach", "fact_checker"]
    # Harbour City finished with ten men: the coordinator must say so, with high confidence.
    man_down = next(i for i in brief.items if i.issue_id == "man_down")
    assert man_down.confidence == "high" and "10 players against 11" in man_down.evidence
    assert all(i.trade_off and i.recommendation and i.key_numbers for i in brief.items)
    # Same position, same question: answered from the store, no second model call.
    calls = len(client.calls)
    assert run(fans.coach_brief(session, "HBC", "en")) == brief and len(client.calls) == calls
    hindi = run(fans.coach_brief(session, "HBC", "hi"))
    assert [i.issue_id for i in hindi.items] == [i.issue_id for i in brief.items]
    assert hindi.items[0].title != brief.items[0].title


def test_agent_cannot_rerank_or_invent(played):
    def tamper(draft, payload):
        items = list(reversed(draft.items))
        items[0] = items[0].model_copy(update={"evidence": "They have had 17 shots in 9 minutes."})
        return draft.model_copy(update={"items": items})

    fans = FanServices(team_for(ScriptedClient(tamper={"coach": tamper})))
    session = fresh_session(played)
    brief = run(fans.coach_brief(session, "NBR", "es"))
    expected = [i.kind for i in find_issues(session.engine, "NBR")]
    assert [i.issue_id for i in brief.items] == expected  # order comes from the coordinator
    assert brief.source == "data" and brief.fact_check.status == FactCheckStatus.BLOCKED
    assert "17 shots" not in " ".join(i.evidence for i in brief.items)


def test_agent_failure_falls_back_to_data(played):
    fans = FanServices(team_for(ScriptedClient(fail={"coach"})))
    brief = run(fans.coach_brief(fresh_session(played), "NBR", "en"))
    assert brief.source == "data" and brief.fact_check.status == FactCheckStatus.DATA_ONLY
    assert brief.agents == ["coach_coordinator"]
