"""Holds the single active match session (FR-003).

Every replay is a *run*, recorded in the store. On startup the backend
resumes the active run if it was still live (a restart or a scale-to-zero
cold start mid-match): the engine is rebuilt from the stored events and
frames, published insights, overlays and the agent pipeline's state are
restored, and the feed continues from where the stored run ended.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from matchlens.domain.models import Event, MatchMeta, SyntheticMatch, TrackingFrame
from matchlens.ingest.adapters import FeedAdapter, ReplayControl, StoredRunAdapter, SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession, SignalHandler
from matchlens.simulator.engine import generate_match
from matchlens.simulator.storyline import Storyline, comeback_storyline
from matchlens.store.repository import Repository

log = logging.getLogger(__name__)

DEFAULT_RESTART_SPEED = 15.0
ResumeHook = Callable[[MatchSession, dict[str, Any]], Awaitable[None]]
# Derived fields stored next to each event, not part of the feed schema.
_EVENT_EXTRAS = ("minute_label", "pass_quality")


@dataclass(frozen=True)
class Recipe:
    """How the loaded match was started, so "restart" can play the same match again.

    synthetic: regenerated from ``seed`` and ``storyline`` (None is the default hero storyline).
    recorded: a recorded match file, replayed from ``match``.
    stored: a run in the database that cannot be regenerated (a season archive, a custom storyline,
    a finished run): replayed from its stored events and frames."""

    kind: Literal["synthetic", "recorded", "stored"]
    seed: int | None = None
    storyline: Storyline | None = None
    match: SyntheticMatch | None = None
    run_id: str | None = None

    @property
    def source(self) -> str:
        """What the API calls the restarted run: synthetic, recorded or rerun (of a stored run)."""
        return "rerun" if self.kind == "stored" else self.kind


def new_run_id(match_id: str) -> str:
    return f"{match_id}-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


class SessionManager:
    def __init__(self) -> None:
        self.session: MatchSession | None = None
        self.control: ReplayControl | None = None
        # What the current (or starting) match was generated from, for reset hooks.
        self.seed: int | None = None
        self.storyline: Storyline | None = None
        self.recipe: Recipe | None = None
        self.repo = Repository.detached()
        self._handlers: list[SignalHandler] = []
        self._reset_hooks: list[Callable[[], object]] = []
        self._resume_hooks: list[ResumeHook] = []

    def attach(self, repo: Repository) -> None:
        self.repo = repo

    def add_reset_hook(self, hook: Callable[[], object]) -> None:
        """Called before a new match starts, so per-match state (caps, caches) is cleared."""
        self._reset_hooks.append(hook)

    def add_resume_hook(self, hook: ResumeHook) -> None:
        """Called after a stored run is hydrated and before its feed restarts."""
        self._resume_hooks.append(hook)

    def add_signal_handler(self, handler: SignalHandler) -> None:
        self._handlers.append(handler)

    def _prepare(self, seed: int | None, storyline: Storyline | None) -> None:
        self.seed, self.storyline = seed, storyline
        for hook in self._reset_hooks:
            hook()

    def _session(self, adapter: FeedAdapter, run_id: str) -> MatchSession:
        session = MatchSession(adapter, self.repo.recorder(run_id))
        for h in self._handlers:
            session.on_signal(h)
        return session

    async def start_synthetic(
        self,
        seed: int = 7,
        speed: float = 1.0,
        start_minute: float = 0.0,
        storyline: Storyline | None = None,
    ) -> MatchSession:
        await self.stop()
        self._prepare(seed, storyline)
        self.recipe = Recipe("synthetic", seed=seed, storyline=storyline)
        match = generate_match(seed=seed, storyline=storyline)
        self.control = ReplayControl(speed=speed, start_elapsed=start_minute * 60)
        session = self._session(SyntheticReplayAdapter(match, self.control), new_run_id(match.meta.match_id))
        session.recorder.begin(match_id=match.meta.match_id, seed=seed, storyline=match.meta.storyline_title,
                               speed=speed, meta=match.meta.model_dump(mode="json"))
        self.session = session
        session.start()
        return session

    async def start_feed(self, adapter: FeedAdapter, control: ReplayControl, source: str,
                         recipe: Recipe | None = None) -> MatchSession:
        """Start a run from any feed adapter (e.g. a provider adapter used for local testing).
        Such runs are not resumed after a server restart: only synthetic runs can be regenerated.
        Pass ``recipe`` to let the API restart the match (without one it cannot)."""
        await self.stop()
        self._prepare(None, None)  # no seed: the recorded demo stays off
        self.recipe = recipe
        self.control = control
        meta = adapter.meta()
        session = self._session(adapter, new_run_id(meta.match_id))
        session.recorder.begin(match_id=meta.match_id, seed=None, storyline=None, speed=control.speed,
                               meta=meta.model_dump(mode="json"))
        session.recorder.update(source=source)
        self.session = session
        session.start()
        return session

    async def resume_or_start(self, seed: int = 7, speed: float = 1.0) -> MatchSession:
        """Resume the stored active run if it is still live and replayable, else start a new one."""
        run = await self.repo.active_run()
        if run and run.get("status") == "live" and run.get("seed") == seed:
            try:
                return await self.resume(run)
            except Exception:  # noqa: BLE001 - a bad stored run must not keep the demo down
                log.exception("could not resume run %s; starting a new one", run.get("run_id"))
        return await self.start_synthetic(seed=seed, speed=speed)

    async def resume(self, run: dict[str, Any]) -> MatchSession:
        await self.stop()
        run_id = run["run_id"]
        self._prepare(run["seed"], None)
        self.recipe = Recipe("synthetic", seed=run["seed"])
        match = generate_match(seed=run["seed"])
        if match.meta.storyline_title != run.get("storyline"):
            raise ValueError(f"run {run_id} was not played from the default storyline")
        events, frames = await self._load(run_id)
        position = frames[-1].elapsed if frames else 0.0
        self.control = ReplayControl(speed=float(run.get("speed", 1.0)), start_elapsed=position)
        if run.get("paused"):
            self.control.pause()
        session = self._session(SyntheticReplayAdapter(match, self.control), run_id)
        await self._fill(session, events, frames)
        session.recorder.begin(match_id=match.meta.match_id, seed=run["seed"], storyline=run.get("storyline"),
                               speed=self.control.speed, meta=match.meta.model_dump(mode="json"),
                               resumed_from=position, existing={k: v for k, v in run.items() if k != "run_id"})
        for hook in self._resume_hooks:
            await hook(session, run)
        log.info("resumed run %s at %s (%d events, %d frames, %d insights)", run_id,
                 session.engine.state()["minute_label"], len(events), len(frames), len(session.insights))
        self.session = session
        session.start()
        return session

    async def open_run(self, run_id: str) -> MatchSession:
        """Reopen a stored run. A synthetic run left live carries on from where it stopped;
        any other (finished, or played from a feed) opens as it was left, to look back on:
        score, timeline, stats, insights, polls and ratings."""
        run = await self.repo.get("runs", run_id, run_id)
        stored = await self.repo.get("runs", run_id, "meta")
        if not run or run.get("doc_type") != "run" or not stored:
            raise LookupError(run_id)
        run = {"run_id": run_id, **run}
        if run.get("status") == "live" and run.get("seed") is not None:
            try:
                return await self.resume(run)
            except ValueError:
                pass  # another storyline: it cannot be regenerated, so open it as it was left
        await self.stop()
        self._prepare(None, None)
        # A hero-storyline run can be regenerated, and keeps the recorded demo agents when restarted.
        default = run.get("seed") is not None and run.get("storyline") == comeback_storyline().title
        self.recipe = Recipe("synthetic", seed=run["seed"]) if default else Recipe("stored", run_id=run_id)
        events, frames = await self._load(run_id)
        self.control = ReplayControl(speed=float(run.get("speed", 1.0)))
        session = self._session(StoredRunAdapter(MatchMeta.model_validate(stored["meta"])), run_id)
        await self._fill(session, events, frames)
        session.finished = session.archived = True
        session.recorder.run = {k: v for k, v in run.items() if k != "run_id"}  # fan votes still land on it
        for hook in self._resume_hooks:
            await hook(session, run)
        log.info("opened run %s at %s (%d events)", run_id, session.engine.state()["minute_label"], len(events))
        self.session = session
        return session

    async def _load(self, run_id: str) -> tuple[list[Event], list[TrackingFrame]]:
        recorder = self.repo.recorder(run_id)
        events = [Event.model_validate({k: v for k, v in d.items() if k not in _EVENT_EXTRAS})
                  for d in await recorder.load("events", order_by="sequence")]
        frames = [TrackingFrame.model_validate(d) for d in await recorder.load("frames", order_by="elapsed")]
        return events, frames

    @staticmethod
    async def _fill(session: MatchSession, events: list[Event], frames: list[TrackingFrame]) -> None:
        """Rebuild a session from a stored run: the engine, published outputs and fan votes."""
        session.hydrate(events, frames)
        for row in await session.recorder.load("insights"):
            session.insights[row["insight_id"]] = row
        for row in await session.recorder.load("overlays"):
            session.overlays[row["overlay_id"]] = row
        session.fans.load(await session.recorder.load("fan_votes"))

    async def restart(self, speed: float | None = None, start_minute: float = 0.0) -> MatchSession:
        """Play the loaded match again from ``start_minute`` as a new run, whatever it is: the hero
        match, a custom storyline, a recorded match or a stored run. LookupError if nothing can be restarted."""
        recipe = self.recipe
        if self.session is None or recipe is None:
            raise LookupError("no match to restart")
        if speed is None:
            speed = self.control.speed if self.control and not self.session.archived else DEFAULT_RESTART_SPEED
        if recipe.kind == "synthetic":
            return await self.start_synthetic(seed=recipe.seed or 0, speed=speed, start_minute=start_minute,
                                              storyline=recipe.storyline)
        control = ReplayControl(speed=speed, start_elapsed=start_minute * 60)
        if recipe.kind == "recorded":
            assert recipe.match is not None
            return await self.start_feed(SyntheticReplayAdapter(recipe.match, control), control, "recorded", recipe)
        assert recipe.run_id is not None
        run = await self.repo.get("runs", recipe.run_id, recipe.run_id)
        stored = await self.repo.get("runs", recipe.run_id, "meta")
        if not run or not stored:
            raise LookupError(f"run {recipe.run_id} is no longer stored")
        events, frames = await self._load(recipe.run_id)
        match = SyntheticMatch(meta=MatchMeta.model_validate(stored["meta"]), events=events, tracking=frames,
                               seed=int(run.get("seed") or 0))
        session = await self.start_feed(SyntheticReplayAdapter(match, control), control, "rerun", recipe)
        session.recorder.update(rerun_of=recipe.run_id)
        return session

    def set_control(self, speed: float | None = None, paused: bool | None = None) -> None:
        ctl = self.control
        assert ctl is not None and self.session is not None
        if speed is not None:
            ctl.speed = speed
        if paused is True:
            ctl.pause()
        elif paused is False:
            ctl.resume()
        self.session.recorder.update(speed=ctl.speed, paused=ctl.paused)

    async def stop(self) -> None:
        if self.session:
            await self.session.stop()
        self.session = None

    def require(self) -> MatchSession:
        if self.session is None:
            raise LookupError("no active match")
        return self.session


manager = SessionManager()
