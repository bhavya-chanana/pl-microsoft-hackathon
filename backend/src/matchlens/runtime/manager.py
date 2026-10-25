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
from datetime import UTC, datetime
from typing import Any

from matchlens.domain.models import Event, TrackingFrame
from matchlens.ingest.adapters import FeedAdapter, ReplayControl, SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession, SignalHandler
from matchlens.simulator.engine import generate_match
from matchlens.simulator.storyline import Storyline
from matchlens.store.repository import Repository

log = logging.getLogger(__name__)

ResumeHook = Callable[[MatchSession, dict[str, Any]], Awaitable[None]]
# Derived fields stored next to each event, not part of the feed schema.
_EVENT_EXTRAS = ("minute_label", "pass_quality")


def new_run_id(match_id: str) -> str:
    return f"{match_id}-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


class SessionManager:
    def __init__(self) -> None:
        self.session: MatchSession | None = None
        self.control: ReplayControl | None = None
        # What the current (or starting) match was generated from, for reset hooks.
        self.seed: int | None = None
        self.storyline: Storyline | None = None
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
        match = generate_match(seed=seed, storyline=storyline)
        self.control = ReplayControl(speed=speed, start_elapsed=start_minute * 60)
        session = self._session(SyntheticReplayAdapter(match, self.control), new_run_id(match.meta.match_id))
        session.recorder.begin(match_id=match.meta.match_id, seed=seed, storyline=match.meta.storyline_title,
                               speed=speed, meta=match.meta.model_dump(mode="json"))
        self.session = session
        session.start()
        return session

    async def start_feed(self, adapter: FeedAdapter, control: ReplayControl, source: str) -> MatchSession:
        """Start a run from any feed adapter (e.g. a provider adapter used for local testing).
        Such runs are not resumed after a restart: only synthetic runs can be regenerated."""
        await self.stop()
        self._prepare(None, None)  # no seed: the recorded demo stays off
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
        match = generate_match(seed=run["seed"])
        if match.meta.storyline_title != run.get("storyline"):
            raise ValueError(f"run {run_id} was not played from the default storyline")
        recorder = self.repo.recorder(run_id)
        events = [Event.model_validate({k: v for k, v in d.items() if k not in _EVENT_EXTRAS})
                  for d in await recorder.load("events", order_by="sequence")]
        frames = [TrackingFrame.model_validate(d) for d in await recorder.load("frames", order_by="elapsed")]
        position = frames[-1].elapsed if frames else 0.0
        self.control = ReplayControl(speed=float(run.get("speed", 1.0)), start_elapsed=position)
        if run.get("paused"):
            self.control.pause()
        session = self._session(SyntheticReplayAdapter(match, self.control), run_id)
        session.hydrate(events, frames)
        for row in await recorder.load("insights"):
            session.insights[row["insight_id"]] = row
        for row in await recorder.load("overlays"):
            session.overlays[row["overlay_id"]] = row
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
