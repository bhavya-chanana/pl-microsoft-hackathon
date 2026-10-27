"""Regression coverage for shutdown, replay controls and unsupported claims."""
import asyncio
from types import SimpleNamespace

from matchlens.agents.contracts import AgentClaim, FactCheckStatus
from matchlens.agents.factchecker import check_text
from matchlens.ingest.adapters import ReplayControl, StoredRunAdapter, paced
from matchlens.interpret.engine import MatchEngine
from matchlens.runtime.session import MatchSession
from matchlens.store.base import MemoryStore
from matchlens.store.writer import WriteBehind


def claim(**overrides):
    values = dict(kind="score", text="The score is 3-2", subject_id=None, stat=None,
                  value=None, event_id=None, player_id=None, home=None, away=None)
    values.update(overrides)
    return AgentClaim(**values)


def test_unsupported_claims_and_unclaimed_counts_are_blocked(demo_match):
    engine = MatchEngine(demo_match.meta)
    cases = [
        (["The score is 3-2"], [claim()]),
        (["Three trophies"], [claim(kind="team_stat", text="Three trophies", subject_id="NBR", stat="trophies", value=3)]),
        (["Kenji has scored 3 goals."], []),
        (["Kenji has scored three goals."], []),
        (["The score is 3-2"], []),
    ]
    for texts, claims in cases:
        assert check_text(engine, {}, texts, claims).report.status == FactCheckStatus.BLOCKED
    assert check_text(engine, {}, ["The score is 0-0 in a 4-3-3"], []).report.status == FactCheckStatus.VERIFIED


def test_close_drains_an_inflight_batch():
    async def run():
        class SlowStore(MemoryStore):
            def __init__(self):
                super().__init__()
                self.entered = asyncio.Event()
                self.release = asyncio.Event()

            async def upsert_many(self, container, run_id, docs):
                self.entered.set()
                await self.release.wait()
                await super().upsert_many(container, run_id, docs)

        store = SlowStore()
        await store.open()
        writer = WriteBehind(store, interval=0.001)
        writer.put("events", "run", {"id": "1", "run_id": "run"})
        writer.start()
        await asyncio.wait_for(store.entered.wait(), 1)
        closing = asyncio.create_task(writer.close())
        await asyncio.sleep(0)
        assert not closing.done()
        writer.put("events", "run", {"id": "2", "run_id": "run"})
        store.release.set()
        await asyncio.wait_for(closing, 1)
        assert writer.stats.written == 2 and writer.stats.dropped == 0 and writer.pending == 0
        assert len(store.items["events"]) == 2
    asyncio.run(run())


def test_stop_cancels_and_joins_signal_handlers(demo_match):
    async def run():
        session = MatchSession(StoredRunAdapter(demo_match.meta))
        entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handler(signal, session):
            entered.set()
            try:
                await release.wait()
                session.publish_insight({"insight_id": "late"})
            finally:
                cancelled.set()

        session._spawn_handler(handler, None)
        await entered.wait()
        await session.stop()
        release.set()
        await asyncio.sleep(0)
        assert cancelled.is_set() and not session._handler_tasks and not session.insights
    asyncio.run(run())


def test_pause_freezes_pending_item_and_preserves_spacing():
    async def run():
        control = ReplayControl(speed=1)
        arrivals = []
        async def consume():
            async for item in paced([SimpleNamespace(elapsed=x) for x in (0, .12, .24, .36)], control):
                arrivals.append((item.elapsed, asyncio.get_running_loop().time()))
        task = asyncio.create_task(consume())
        await asyncio.sleep(.04)
        control.pause()
        await asyncio.sleep(.2)
        assert len(arrivals) == 1
        resumed = asyncio.get_running_loop().time()
        control.resume()
        await asyncio.wait_for(task, 1)
        assert arrivals[1][1] - resumed >= .05
        assert arrivals[2][1] - arrivals[1][1] >= .08
        assert arrivals[3][1] - arrivals[2][1] >= .08
    asyncio.run(run())


def test_speed_change_preserves_progress():
    async def run():
        control = ReplayControl(speed=1)
        arrivals = []
        async def consume():
            async for item in paced([SimpleNamespace(elapsed=x) for x in (0, .3, .6)], control):
                arrivals.append(asyncio.get_running_loop().time())
        task = asyncio.create_task(consume())
        await asyncio.sleep(.1)
        changed = asyncio.get_running_loop().time()
        control.speed = 2
        await asyncio.wait_for(task, 1)
        assert .07 <= arrivals[1] - changed < .25
        assert .10 <= arrivals[2] - arrivals[1] < .25
    asyncio.run(run())


def test_claim_cannot_credit_a_different_player(demo_match):
    engine = MatchEngine(demo_match.meta)
    first, second = list(demo_match.meta.players.values())[:2]
    text = f"{first.name} has 0 goals."
    wrong = claim(kind="player_stat", text="0 goals", subject_id=second.player_id,
                  stat="goals", value=0)
    right = claim(kind="player_stat", text="0 goals", subject_id=first.player_id,
                  stat="goals", value=0)
    assert check_text(engine, {}, [text], [wrong]).report.status == FactCheckStatus.BLOCKED
    assert check_text(engine, {}, [text], [right]).report.status == FactCheckStatus.VERIFIED


def test_grounded_ranges_are_not_scores(demo_match):
    engine = MatchEngine(demo_match.meta)
    out = check_text(engine, {"duration": [5, 10]}, ["The phase lasted 5–10 minutes."], [])
    assert out.report.status == FactCheckStatus.VERIFIED


def test_restore_archived_session_never_requeues(demo_match):
    from matchlens.agents.pipeline import InsightPipeline

    async def run():
        session = MatchSession(StoredRunAdapter(demo_match.meta))
        from tests.conftest import replay_order
        for kind, item in replay_order(demo_match):
            session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
        signal = session.engine.signals()[-1]
        session.insights[signal.signal_id] = {"stage": "data", "revision": signal.revision}
        decisions = [{"signal_id": signal.signal_id, "revision": signal.revision,
                      "plan": "full", "reason": "test"}]
        session.archived = session.finished = True
        pipeline = InsightPipeline(team=None)
        count = await pipeline.restore(session, None, decisions)
        assert count == 0 and not pipeline._pending and not session._handler_tasks
    asyncio.run(run())


def test_named_snapshot_subject_counts(demo_match):
    from matchlens.agents.context import match_header, player_brief

    engine = MatchEngine(demo_match.meta)
    player = next(iter(demo_match.meta.players.values()))
    context = {"match": match_header(engine), "players": [player_brief(engine, player.player_id)],
               "teams": {engine.meta.home.team_id: engine.team_stats(engine.meta.home.team_id)}}
    for text in (f"{player.name} has 0 goals.", f"{engine.meta.home.name} have 0 shots."):
        assert check_text(engine, context, [text], []).report.status == FactCheckStatus.VERIFIED
    other = next(p for p in demo_match.meta.players.values() if p.player_id != player.player_id)
    for text in (f"{player.name} has 3 goals.", f"{other.name} has 0 goals."):
        assert check_text(engine, context, [text], []).report.status == FactCheckStatus.BLOCKED
