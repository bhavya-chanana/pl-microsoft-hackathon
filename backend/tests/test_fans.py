"""Fan polls and player ratings: polls follow the match, votes are anonymous and survive a restart."""

from __future__ import annotations

import asyncio

import pytest

from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.ratings import data_rating
from matchlens.runtime.fans import FanZone
from matchlens.store.base import MemoryStore

from .conftest import replay_order
from .test_store import SPEED, Backend

VOTER, OTHER = "a" * 20, "b" * 20


@pytest.fixture(scope="module")
def played(demo_match):
    """The demo match played to full time through a fan zone."""
    engine, stored = MatchEngine(demo_match.meta), []
    fans = FanZone(demo_match.meta, salt="run-1", sink=lambda doc_id, doc: stored.append((doc_id, doc)))
    opened = []
    for kind, item in replay_order(demo_match):
        if kind:
            engine.ingest_event(item)
            opened += [p.poll_id for p in fans.observe(item, engine) if p.poll_id not in opened]
        else:
            engine.ingest_frame(item)
    return engine, fans, opened, stored


def test_polls_follow_the_match(played, demo_match):
    engine, fans, opened, _ = played
    goals = sum(1 for e in demo_match.events if e.type == "goal")
    results = [p for p in fans.polls.values() if p.kind == "result"]
    assert len(results) == goals + 2  # kick-off, every goal, half-time
    assert {"motm-ht", "motm-ft"} <= set(fans.polls) and opened[0] == "result-0"
    assert all(p.closed and p.answer == "NBR" for p in results)  # 3-2 to Northbridge
    ht = fans.polls["motm-ht"]
    assert ht.closed and len(ht.options) == 4 and ht.answer in {o for o, _ in ht.options}
    ft = fans.polls["motm-ft"]
    assert not ft.closed and ft.answer == ft.options[0][0]
    assert ft.answer == max(fans.ratings(engine), key=lambda r: r["data_rating"])["player_id"]
    assert fans.polls["result-1"].context["en"] == "After the goal"
    assert fans.polls["result-0"].public()["options"][1]["label"]["es"] == "Empate"


def test_votes_are_anonymous_and_replaceable(played):
    engine, fans, _, stored = played
    poll = fans.polls["motm-ft"]
    first, second = poll.options[0][0], poll.options[1][0]
    fans.vote("motm-ft", VOTER, first)
    fans.vote("motm-ft", OTHER, first)
    fans.vote("motm-ft", VOTER, second)  # changing your mind replaces the vote
    counts = {o["option_id"]: o["votes"] for o in poll.public()["options"]}
    assert counts[first] == 1 and counts[second] == 1 and poll.public()["total_votes"] == 2
    assert VOTER not in str(stored) and all(len(doc["voter"]) == 16 for _, doc in stored)
    with pytest.raises(PermissionError):
        fans.vote("result-0", VOTER, "NBR")  # closed
    with pytest.raises(ValueError):
        fans.vote("motm-ft", VOTER, "nobody")
    with pytest.raises(ValueError):
        fans.vote("motm-ft", "short", first)
    with pytest.raises(LookupError):
        fans.vote("nope", VOTER, first)


def test_fan_ratings_sit_next_to_the_data(played):
    engine, fans, _, _ = played
    scorer = next(r for r in fans.ratings(engine) if any(p["key"] == "goals" for p in r["parts"]))
    fans.rate(scorer["player_id"], VOTER, 9, engine)
    row = fans.rate(scorer["player_id"], OTHER, 6, engine)
    assert row["fan_rating"] == 7.5 and row["fan_votes"] == 2
    assert row["gap"] == round(7.5 - row["data_rating"], 1)
    assert row["parts"][0]["delta"] >= abs(row["parts"][-1]["delta"]) or row["parts"][0]["key"] == "goals"
    unused = next(pid for pid in engine.meta.players if pid not in {r["player_id"] for r in fans.ratings(engine)})
    with pytest.raises(PermissionError):
        fans.rate(unused, VOTER, 7, engine)
    with pytest.raises(ValueError):
        fans.rate(scorer["player_id"], VOTER, 11, engine)


def test_data_rating_weights():
    base = {"player_id": "X-1", "position": "GK", "passes": 0}
    assert data_rating(base).value == 6.0
    assert data_rating({**base, "saves": 4}, conceded=0, finished=True).value == 7.8  # saves + clean sheet
    assert data_rating({**base, "red_card": True}, conceded=4).value == 3.5
    striker = {"player_id": "X-9", "position": "FWD", "goals": 3, "assists": 2, "xg": 2.5, "key_passes": 4}
    assert data_rating(striker).value == 10.0  # capped


def test_votes_survive_a_restart(demo_match):
    store = MemoryStore()

    async def main():
        await store.open()
        first = Backend(store)
        first.writer.start()
        session = await first.manager.start_synthetic(seed=7, speed=SPEED)
        await first.play_until(50)
        assert "motm-ht" in session.fans.polls
        session.fans.vote("motm-ht", VOTER, session.fans.polls["motm-ht"].options[0][0])
        await first.crash()

        second = Backend(store)
        second.writer.start()
        resumed = await second.manager.resume_or_start(seed=7, speed=SPEED)
        polls = {p["poll_id"]: p for p in resumed.snapshot()["polls"]}
        await second.manager.stop()
        await second.writer.close()
        return resumed, polls

    resumed, polls = asyncio.run(main())
    assert polls["motm-ht"]["total_votes"] == 1 and "result-0" in polls
    assert resumed.run_id.startswith(demo_match.meta.match_id)
