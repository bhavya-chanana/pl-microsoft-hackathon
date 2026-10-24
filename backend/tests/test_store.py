import asyncio
import os
import re
import uuid
from pathlib import Path

import pytest

from matchlens.agents.contracts import FanProfile
from matchlens.agents.llm import OfflineChatClient
from matchlens.agents.pipeline import InsightPipeline
from matchlens.agents.services import FanServices
from matchlens.interpret.engine import MatchEngine
from matchlens.runtime.manager import SessionManager
from matchlens.store.base import CONTAINERS, EMULATOR_KEY, MemoryStore, StoreSettings
from matchlens.store.repository import Repository
from matchlens.store.writer import WriteBehind, _chunks

from .conftest import replay_order
from .test_agents import ScriptedClient, team_for

ROOT = Path(__file__).resolve().parents[2]
SPEED = 2000


def run(coro):
    return asyncio.run(coro)


class Backend:
    """What the API lifespan wires together, on a given store: one 'process' of the backend."""

    def __init__(self, store) -> None:
        self.writer = WriteBehind(store, interval=0.05)
        self.repo = Repository(store, self.writer)
        self.pipeline = InsightPipeline(team_for(OfflineChatClient()))
        self.manager = SessionManager()
        self.manager.attach(self.repo)
        self.manager.add_signal_handler(self.pipeline.handle)
        self.manager.add_reset_hook(self.pipeline.reset)
        self.manager.add_resume_hook(self.on_resume)
        self.requeued = 0

    async def on_resume(self, session, run_doc) -> None:
        state = await session.recorder.get("runs", "pipeline")
        decisions = await session.recorder.load("decisions", order_by="seq")
        self.requeued = await self.pipeline.restore(session, state, decisions)

    async def play_until(self, minute: float | None) -> None:
        session = self.manager.require()
        while not session.finished and (minute is None or session.engine.c.elapsed < minute * 60):
            await asyncio.sleep(0.01)
        if minute is None:
            await asyncio.sleep(0.5)  # let the last agent jobs publish

    async def crash(self) -> None:
        """Stop like a killed container: the feed and in-flight agent jobs die, queued writes flush."""
        await self.manager.stop()
        for task in asyncio.all_tasks() - {asyncio.current_task()}:
            if task.get_name() != "store-writer":
                task.cancel()
        await asyncio.sleep(0)
        await self.writer.close()


def test_full_run_is_stored(demo_match):
    store = MemoryStore()

    async def main():
        await store.open()
        b = Backend(store)
        b.writer.start()
        session = await b.manager.start_synthetic(seed=7, speed=SPEED)
        await b.play_until(None)
        await b.writer.close()
        return session

    session = run(main())
    rid = session.run_id
    count = lambda c: sum(1 for (r, _) in store.items[c] if r == rid)  # noqa: E731
    assert count("events") == len(demo_match.events)
    assert count("frames") == len(demo_match.tracking)
    assert count("insights") == len(session.insights) and count("overlays") == len(session.overlays)
    assert count("decisions") > 0 and count("agent_runs") > 0 and count("signals") >= len(session.engine.signals())
    run_doc = store.items["runs"][(rid, rid)]
    assert run_doc["status"] == "finished" and run_doc["score"] == {"NBR": 3, "HBC": 2}
    assert {"meta", "pipeline", "stats:half_time", "stats:full_time"} <= {i for (r, i) in store.items["runs"] if r == rid}
    assert store.items["runs"][("_active", "active")]["active_run_id"] == rid
    goal = store.items["events"][(rid, next(e.event_id for e in demo_match.events if e.type == "goal"))]
    assert goal["minute_label"] and goal["type"] == "goal"


def test_restart_mid_match_resumes_the_run(demo_match):
    """Kill the backend at 50', start a fresh one on the same store: same run, same final result,
    nothing double counted, and insights published before the restart are still there."""
    store = MemoryStore()

    async def main():
        await store.open()
        first = Backend(store)
        first.writer.start()
        session = await first.manager.start_synthetic(seed=7, speed=SPEED)
        await first.play_until(50)
        before = {k: v["stage"] for k, v in session.insights.items()}
        await first.crash()

        second = Backend(store)  # cold cache, like a new container
        second.writer.start()
        resumed = await second.manager.resume_or_start(seed=7, speed=SPEED)
        hydrated_minute = resumed.engine.c.elapsed / 60
        await second.play_until(None)
        await second.writer.close()
        return session, resumed, before, hydrated_minute, second

    first_session, resumed, before, hydrated_minute, second = run(main())
    assert resumed.run_id == first_session.run_id
    assert 48 <= hydrated_minute <= 52
    assert resumed.engine.state()["score"] == {"NBR": 3, "HBC": 2}
    assert set(before) <= set(resumed.insights)

    # Identical to an uninterrupted engine: no event or tracking frame ingested twice.
    straight = MatchEngine(demo_match.meta)
    for kind, item in replay_order(demo_match):
        straight.ingest_event(item) if kind else straight.ingest_frame(item)
    for pid in ("NBR-09", "HBC-11", "NBR-01"):
        assert resumed.engine.player_stats(pid) == straight.player_stats(pid)
    assert resumed.engine.team_stats("NBR") == straight.team_stats("NBR")

    rid = resumed.run_id
    assert sum(1 for (r, _) in store.items["events"] if r == rid) == len(demo_match.events)
    run_doc = store.items["runs"][(rid, rid)]
    assert run_doc["status"] == "finished" and run_doc["resumes"] == 1
    # Coordinator decisions continue the stored sequence rather than starting again.
    seqs = sorted(d["seq"] for (r, _), d in store.items["decisions"].items() if r == rid)
    assert seqs == list(range(1, len(seqs) + 1)) and len(second.pipeline.decisions) == len(seqs)


def test_finished_run_starts_a_new_one():
    store = MemoryStore()

    async def main():
        await store.open()
        b = Backend(store)
        b.writer.start()
        first = await b.manager.start_synthetic(seed=7, speed=SPEED)
        await b.play_until(None)
        await b.writer.close()
        again = Backend(store)
        again.writer.start()
        second = await again.manager.resume_or_start(seed=7, speed=SPEED)
        await again.manager.stop()
        await again.writer.close()
        return first.run_id, second.run_id, await again.repo.list_runs()

    first, second, runs = run(main())
    assert first != second
    assert [r["run_id"] for r in runs][:2] == [second, first]


def test_fan_outputs_survive_a_restart(played):
    store = MemoryStore()
    client = ScriptedClient()

    async def main():
        await store.open()
        repo = Repository(store, WriteBehind(store))
        session = _session_on(played, repo.recorder("r1"))
        fans = FanServices(team_for(client))
        first = await fans.catchup(session, 15, FanProfile())
        await repo.flush()
        calls = len(client.calls)
        cold = Repository(store, WriteBehind(store))  # new process: empty cache
        again = await fans.catchup(_session_on(played, cold.recorder("r1")), 15, FanProfile())
        return first, again, calls, cold

    first, again, calls, cold = run(main())
    assert again == first and len(client.calls) == calls  # served from the store, no model call
    assert cold.misses == 1 and cold.stats()["cache_entries"] == 1


def _session_on(played, recorder):
    from matchlens.runtime.session import MatchSession

    s = MatchSession(played.adapter, recorder)
    s.engine = played.engine
    return s


@pytest.fixture(scope="module")
def played(demo_match):
    from matchlens.ingest.adapters import SyntheticReplayAdapter
    from matchlens.runtime.session import MatchSession

    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session


def test_read_through_cache():
    store = MemoryStore()

    async def main():
        await store.open()
        await store.upsert_many("insights", "r1", [{"id": "a", "run_id": "r1", "headline": "x"}])
        repo = Repository(store, WriteBehind(store))
        first = await repo.get("insights", "r1", "a")
        second = await repo.get("insights", "r1", "a")
        missing = await repo.get("insights", "r1", "nope")
        return repo, first, second, missing

    repo, first, second, missing = run(main())
    assert first == second == {"headline": "x"} and missing is None
    assert (repo.hits, repo.misses) == (1, 2)


def test_writer_batches_retries_and_never_blocks():
    class Flaky(MemoryStore):
        fails = 1

        async def upsert_many(self, container, run_id, docs):
            if self.fails:
                self.fails -= 1
                raise RuntimeError("throttled")
            assert len(docs) <= 100
            await super().upsert_many(container, run_id, docs)

    store = Flaky()

    async def main():
        await store.open()
        writer = WriteBehind(store)
        for i in range(250):
            writer.put("events", "r1", {"id": f"e{i}", "run_id": "r1", "n": i})
        writer.put("events", "r1", {"id": "e0", "run_id": "r1", "n": "latest"})
        assert writer.pending == 250  # the second write of e0 replaced the first
        await writer.flush()
        return writer

    writer = run(main())
    assert writer.stats.written == 250 and writer.stats.dropped == 0 and writer.stats.last_error
    assert store.items["events"][("r1", "e0")]["n"] == "latest"
    assert [len(c) for c in _chunks([{"id": str(i), "x": "y" * 20_000} for i in range(150)])] == [74, 74, 2]


def test_bicep_declares_every_container():
    bicep = (ROOT / "infra" / "cosmos.bicep").read_text()
    declared = {name: tuple(re.findall(r"'(\w+)'", fields)) for name, fields in
                re.findall(r"\{ name: '(\w+)', unindexed: \[(.*?)\] \}", bicep)}
    assert declared == {s.name: s.unindexed for s in CONTAINERS.values()}


@pytest.mark.skipif(not os.getenv("MATCHLENS_COSMOS_TEST_ENDPOINT"), reason="needs the Cosmos DB emulator")
def test_cosmos_round_trip():
    """Against the vNext emulator: docker run -p 8081:8081 mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-latest"""
    from matchlens.store.cosmos import CosmosStore

    settings = StoreSettings(kind="cosmos", endpoint=os.environ["MATCHLENS_COSMOS_TEST_ENDPOINT"],
                             database=f"test-{uuid.uuid4().hex[:8]}", key=EMULATOR_KEY)
    rid = uuid.uuid4().hex

    async def main():
        store = CosmosStore(settings)
        await store.open()
        try:
            writer = WriteBehind(store)
            for i in range(130):
                writer.put("events", rid, {"id": f"e{i:03d}", "run_id": rid, "sequence": i, "type": "pass"})
            writer.put("runs", rid, {"id": rid, "run_id": rid, "doc_type": "run", "created_at": "2026-10-24"})
            await writer.flush()
            one = await store.read("events", rid, "e007")
            ordered = await store.query("events", rid, order_by="sequence", descending=True, limit=3)
            runs = await store.query("runs", None, where={"doc_type": "run"})
            return writer, one, ordered, runs
        finally:
            await store._client.delete_database(settings.database)
            await store.close()

    writer, one, ordered, runs = run(main())
    assert writer.stats.written == 131 and writer.stats.dropped == 0
    assert one["sequence"] == 7
    assert [d["sequence"] for d in ordered] == [129, 128, 127]
    assert rid in {r["run_id"] for r in runs}
