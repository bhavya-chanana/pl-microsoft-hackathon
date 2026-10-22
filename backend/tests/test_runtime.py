import asyncio

import pytest

from matchlens.factcheck.verify import (
    EventClaim,
    ScoreClaim,
    StatClaim,
    Verdict,
    numbers_in,
    unaccounted_numbers,
    verify_claims,
)
from matchlens.ingest.adapters import ReplayControl, SyntheticReplayAdapter
from matchlens.interpret.engine import MatchEngine
from matchlens.runtime.session import MatchSession

from .conftest import replay_order


@pytest.fixture(scope="module")
def engine(demo_match):
    eng = MatchEngine(demo_match.meta)
    for kind, item in replay_order(demo_match):
        eng.ingest_event(item) if kind else eng.ingest_frame(item)
    return eng


def test_verify_claims(engine, demo_match):
    goal = next(e for e in demo_match.events if e.type.value == "goal")
    passes = engine.team_stats("NBR")["passes"]
    results = verify_claims(engine, [
        ScoreClaim(home=3, away=2),
        ScoreClaim(home=2, away=2),
        StatClaim(subject_type="team", subject_id="NBR", stat="passes", value=passes),
        StatClaim(subject_type="team", subject_id="NBR", stat="passes", value=passes + 40),
        StatClaim(subject_type="player", subject_id="NBR-10", stat="not_a_stat", value=1),
        EventClaim(event_id=goal.event_id, type="goal", player_id=goal.player_id),
        EventClaim(event_id=goal.event_id, player_id="NBR-01"),
        EventClaim(event_id="does-not-exist"),
    ])
    assert [r.verdict for r in results] == [
        Verdict.SUPPORTED, Verdict.CONTRADICTED, Verdict.SUPPORTED, Verdict.CONTRADICTED,
        Verdict.UNVERIFIABLE, Verdict.SUPPORTED, Verdict.CONTRADICTED, Verdict.CONTRADICTED,
    ]


def test_corrected_scorer_is_verified(engine, demo_match):
    goal = [e for e in demo_match.events if e.type.value == "goal"][2]
    wrong, right = verify_claims(engine, [
        EventClaim(event_id=goal.event_id, player_id="NBR-10"),
        EventClaim(event_id=goal.event_id, player_id="NBR-09"),
    ])
    assert wrong.verdict == Verdict.CONTRADICTED and right.verdict == Verdict.SUPPORTED


def test_unaccounted_numbers():
    allowed = numbers_in({"xg": 0.47, "minute": "64'", "speed": 99.6})
    assert unaccounted_numbers("A 0.47 xG chance at 64', struck at 99.6 km/h", allowed) == []
    assert unaccounted_numbers("He has 12 goals this season", allowed) == ["12"]


def test_session_replays_quickly_and_publishes(demo_match):
    async def main():
        control = ReplayControl(speed=1000, start_elapsed=0)
        session = MatchSession(SyntheticReplayAdapter(demo_match, control))
        q = session.subscribe()
        session.start()
        kinds = set()
        while not (session.finished and q.empty()):
            try:
                kinds.add((await asyncio.wait_for(q.get(), 0.2))["type"])
            except TimeoutError:
                pass
        return session, kinds

    session, kinds = asyncio.run(main())
    assert {"event", "state", "frame", "signal", "correction", "feed_status"} <= kinds
    assert session.engine.state()["score"] == {"NBR": 3, "HBC": 2}
    snap = session.snapshot()
    assert snap["finished"] and snap["timeline"][-1]["type"] == "full_time"


def test_join_mid_match_catches_up_instantly(demo_match):
    async def main():
        control = ReplayControl(speed=1, start_elapsed=40 * 60)  # join at 40'
        session = MatchSession(SyntheticReplayAdapter(demo_match, control))
        session.start()
        await asyncio.sleep(0.5)
        await session.stop()
        return session.engine.state()

    state = asyncio.run(main())
    assert state["score"] == {"NBR": 0, "HBC": 2}
