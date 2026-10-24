"""Recorded demo match: record once, replay with no model (NFR §10.5)."""

import asyncio
from collections import Counter

import pytest

from matchlens.agents.cassette import Cassette, CassetteChatClient, CassetteMiss, request_key
from matchlens.agents.contracts import FanProfile, Language, Mode
from matchlens.agents.llm import ModelClients, OfflineChatClient
from matchlens.agents.pipeline import AgentTeam, InsightPipeline
from matchlens.agents.record import Recorder
from matchlens.agents.services import FanServices
from matchlens.ingest.adapters import ReplayControl, SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession

from .test_agents import OFFLINE, fresh_session, played, run, team_for  # noqa: F401 (played is a fixture)


@pytest.fixture(scope="module")
def recorded(demo_match, tmp_path_factory):
    cassette = Cassette(seed=7, storyline=demo_match.meta.storyline_title or "")
    client = CassetteChatClient(OfflineChatClient(), cassette, live_misses=True, record=True)
    recorder = Recorder(team_for(client), concurrency=4, modes=[Mode.CASUAL, Mode.ANALYST], languages=[Language.EN])
    run(recorder.run(demo_match))
    path = tmp_path_factory.mktemp("demo") / "hero.json"
    cassette.save(path)
    return Cassette.load(path), recorder


def test_recording_covers_signals_catchups_and_recaps(recorded):
    cassette, recorder = recorded
    tasks = Counter(key.split("|")[0] for key in cassette.entries)
    assert tasks["narrative"] >= 30 and tasks["tactical"] >= 15 and tasks["personalise"] >= 30
    assert tasks["catchup"] > 100
    assert {k.split("|")[1] for k in cassette.entries if k.startswith("recap|")} == {"half_time", "full_time"}
    assert recorder.jobs["recap"] == 4  # two phases x two profiles


def test_live_replay_is_served_entirely_from_the_recording(demo_match, recorded):
    cassette, _ = recorded

    async def main():
        client = CassetteChatClient(OfflineChatClient(), cassette, live_misses=False)
        session = MatchSession(SyntheticReplayAdapter(demo_match, ReplayControl(speed=2000)))
        pipe = InsightPipeline(team_for(client))
        session.on_signal(pipe.handle)
        session.start()
        while not session.finished:
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        fans = FanServices(AgentTeam.build(ModelClients(fast=client, strong=client, settings=OFFLINE,
                                                        cassette=cassette)))
        catchup = await fans.catchup(session, 15, FanProfile(mode=Mode.ANALYST))
        recap = await fans.recap(session, FanProfile())
        return client, session, catchup, recap

    client, session, catchup, recap = run(main())
    assert client.misses == 0 and client.hits > 100
    stages = Counter(i["stage"] for i in session.insights.values())
    assert stages["agent"] >= 20 and stages["retracted"] == 1
    assert catchup.source == "agent" and recap.source == "agent" and recap.phase == "full_time"


def test_offline_miss_falls_back_to_labelled_data(played):
    """Unrecorded and offline: the caller publishes its data-only output, labelled as data."""
    client = CassetteChatClient(OfflineChatClient(), Cassette(seed=7, storyline="x"), live_misses=False)
    with pytest.raises(CassetteMiss):
        run(client._respond([_msg({"task": "narrative", "context": {"signal": {"signal_id": "goal:x"}}})], {}, {}))
    catchup = run(FanServices(team_for(client)).catchup(fresh_session(played), 5, FanProfile()))
    assert catchup.source == "data" and catchup.fact_check.status == "data_only"


def test_focus_falls_back_to_unfocused_recording():
    payload = {"task": "catchup", "language": "en", "context": {"window": {"minutes": 15}, "moments": []},
               "profile": {"mode": "casual", "favourite_club": {"team_id": "NBR"}}}
    plain = {**payload, "profile": {"mode": "casual"}}
    assert request_key(payload) != request_key(plain) == request_key(payload, focus=False)
    cassette = Cassette(seed=7, storyline="x", entries={request_key(plain): {"text": "{}", "latency_ms": 0}})
    client = CassetteChatClient(OfflineChatClient(), cassette, live_misses=False)
    response = run(client._respond([_msg(payload)], {}, {}))
    assert response.text == "{}" and client.hits == 1


def test_disabled_for_other_matches():
    cassette = Cassette(seed=7, storyline="The Kestrel Park Comeback")
    assert cassette.matches(7, None) and not cassette.matches(8, None)
    assert not cassette.matches(7, "Another story")


def _msg(payload):
    import json

    from agent_framework import Message

    return Message("user", [json.dumps(payload)])
