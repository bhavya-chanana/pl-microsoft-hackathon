"""Record the hero demo match into a cassette (NFR §10.5).

Plays the match in lockstep with the agents: the feed only moves on while
fewer than ``--concurrency`` agent jobs are running, so every signal gets the
full workflow and its context pack matches what a live replay would build.
Along the way it records catch-ups whenever the moments in a window change,
and half-time and full-time recaps, for every mode and language.

Run (needs AZURE_OPENAI_ENDPOINT and az login):
    uv run python -m matchlens.agents.record --out demo/hero-match.json

Re-running with the same --out resumes: recorded answers are reused and only
missing ones are asked for.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

# Recording has no live deadline: give slow answers room rather than lose them.
os.environ.setdefault("MATCHLENS_AGENT_TIMEOUT_S", "90")

from matchlens.agents import services  # noqa: E402
from matchlens.agents.cassette import Cassette, CassetteChatClient, moments_digest  # noqa: E402
from matchlens.agents.context import window_pack  # noqa: E402
from matchlens.agents.contracts import FanProfile, Language, Mode  # noqa: E402
from matchlens.agents.llm import LlmSettings, ModelClients, build_clients  # noqa: E402
from matchlens.agents.pipeline import AgentTeam, InsightPipeline  # noqa: E402
from matchlens.agents.services import CATCHUP_WINDOWS, FanServices  # noqa: E402
from matchlens.ingest.adapters import FeedItem, feed_order  # noqa: E402
from matchlens.domain.models import MatchMeta, SyntheticMatch  # noqa: E402
from matchlens.runtime.session import MatchSession  # noqa: E402
from matchlens.simulator.engine import generate_match  # noqa: E402

log = logging.getLogger("matchlens.record")

services.RECAP_TIMEOUT_S = 120.0


class LockstepAdapter:
    """Feeds a match as fast as the agents keep up, calling ``between`` before each item."""

    def __init__(self, match: SyntheticMatch, between: Callable[[], Awaitable[None]]) -> None:
        self._match = match
        self._items = feed_order(match.events, match.tracking)
        self._between = between

    def meta(self) -> MatchMeta:
        return self._match.meta

    async def stream(self) -> AsyncIterator[FeedItem]:
        for item in self._items:
            await self._between()
            yield item
        await self._between()


class Recorder:
    def __init__(self, team: AgentTeam, concurrency: int, modes: list[Mode], languages: list[Language]) -> None:
        self.pipeline = InsightPipeline(team, max_concurrent=concurrency)
        self.fans = FanServices(team)
        self.concurrency = concurrency
        self.profiles = [FanProfile(mode=m, language=lang) for m in modes for lang in languages]
        self.inflight: set[asyncio.Task[object]] = set()
        self.session: MatchSession | None = None
        self._digests: set[tuple[int, str]] = set()
        self._recapped: set[str] = set()
        self._position: tuple[int, int] = (-1, -1)
        self.jobs: Counter[str] = Counter()

    def _spawn(self, kind: str, coro: Awaitable[object]) -> None:
        task = asyncio.ensure_future(coro)
        self.inflight.add(task)
        task.add_done_callback(self.inflight.discard)
        self.jobs[kind] += 1

    async def on_signal(self, signal, session: MatchSession) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.inflight.add(task)
        self.jobs["signal"] += 1
        try:
            await self.pipeline.handle(signal, session)
        finally:
            self.inflight.discard(task)

    async def between(self) -> None:
        # Let jobs started by the last item build their context before the match moves on.
        await asyncio.sleep(0)
        session = self.session
        assert session is not None
        engine = session.engine
        # Window contents change when a moment arrives or (checked each minute) ages out.
        position = (int(engine.c.elapsed // 60), len(engine.signals()))
        if position != self._position:
            self._position = position
            insights = list(session.insights.values())
            for window in CATCHUP_WINDOWS:
                digest = moments_digest(window_pack(engine, window, insights))
                if (window, digest) not in self._digests:
                    self._digests.add((window, digest))
                    for p in self.profiles:
                        self._spawn("catchup", self.fans.catchup(session, window, p))
        status = str(engine.state()["status"])
        if status in ("half_time", "full_time") and status not in self._recapped:
            self._recapped.add(status)
            for p in self.profiles:
                self._spawn("recap", self.fans.recap(session, p))
        await asyncio.sleep(0)
        while len(self.inflight) >= self.concurrency:
            await asyncio.wait(set(self.inflight), return_when=asyncio.FIRST_COMPLETED)

    async def run(self, match: SyntheticMatch) -> MatchSession:
        session = MatchSession(LockstepAdapter(match, self.between))
        self.session = session
        session.on_signal(self.on_signal)
        session.start()
        last = time.monotonic()
        assert session._task is not None
        while not session._task.done() or self.inflight:
            await asyncio.sleep(1)
            if time.monotonic() - last > 30:
                last = time.monotonic()
                log.info("%s, %d jobs running, jobs so far %s",
                         engine_minute(session), len(self.inflight), dict(self.jobs))
        return session


def engine_minute(session: MatchSession) -> str:
    return str(session.engine.state()["minute_label"])


async def record(out: Path, seed: int, concurrency: int, modes: list[Mode], languages: list[Language]) -> None:
    settings = LlmSettings.from_env()
    if not settings.online:
        raise SystemExit("Recording needs a live model: set AZURE_OPENAI_ENDPOINT (and az login).")
    os.environ["MATCHLENS_CASSETTE"] = "off"  # build the live clients only; this module wraps them
    live = build_clients(settings)
    match = generate_match(seed=seed)
    title = match.meta.storyline_title or ""
    cassette = Cassette.load(out) if out.exists() else Cassette(seed=seed, storyline=title)
    if not cassette.matches(seed, title):
        raise SystemExit(f"{out} was recorded for seed {cassette.seed}; pick another --out")
    cassette.path = out
    before = len(cassette.entries)

    def wrap(client):
        return CassetteChatClient(client, cassette, live_misses=True, record=True)

    fast = wrap(live.fast)
    strong = fast if live.strong is live.fast else wrap(live.strong)
    clients = ModelClients(fast=fast, strong=strong, settings=settings, cassette=cassette)
    recorder = Recorder(AgentTeam.build(clients), concurrency, modes, languages)
    started = time.monotonic()
    try:
        session = await recorder.run(match)
    finally:
        cassette.save()
    stages = Counter(i["stage"] for i in session.insights.values())
    checks = Counter(i["fact_check"]["status"] for i in session.insights.values())
    misses = sum(c.misses for c in {id(fast): fast, id(strong): strong}.values())
    print(f"recorded {len(cassette.entries) - before} new answers ({len(cassette.entries)} total, "
          f"{misses} model calls) in {time.monotonic() - started:.0f}s -> {out}")
    print(f"jobs: {dict(recorder.jobs)}")
    print(f"insights by stage: {dict(stages)}; fact-check: {dict(checks)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("demo/hero-match.json"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--modes", nargs="+", default=[m.value for m in Mode], choices=[m.value for m in Mode])
    parser.add_argument("--languages", nargs="+", default=[lang.value for lang in Language],
                        choices=[lang.value for lang in Language])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("azure", "httpx", "httpx2", "agent_framework"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(record(args.out, args.seed, args.concurrency,
                       [Mode(m) for m in args.modes], [Language(lang) for lang in args.languages]))


if __name__ == "__main__":
    main()
