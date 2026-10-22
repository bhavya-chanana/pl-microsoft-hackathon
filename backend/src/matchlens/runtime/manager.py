"""Holds the single active match session (FR-003)."""

from __future__ import annotations

from matchlens.ingest.adapters import ReplayControl, SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession, SignalHandler
from matchlens.simulator.engine import generate_match
from matchlens.simulator.storyline import Storyline


class SessionManager:
    def __init__(self) -> None:
        self.session: MatchSession | None = None
        self.control: ReplayControl | None = None
        self._handlers: list[SignalHandler] = []

    def add_signal_handler(self, handler: SignalHandler) -> None:
        self._handlers.append(handler)

    async def start_synthetic(
        self,
        seed: int = 7,
        speed: float = 1.0,
        start_minute: float = 0.0,
        storyline: Storyline | None = None,
    ) -> MatchSession:
        await self.stop()
        match = generate_match(seed=seed, storyline=storyline)
        self.control = ReplayControl(speed=speed, start_elapsed=start_minute * 60)
        session = MatchSession(SyntheticReplayAdapter(match, self.control))
        for h in self._handlers:
            session.on_signal(h)
        self.session = session
        session.start()
        return session

    async def stop(self) -> None:
        if self.session:
            await self.session.stop()
        self.session = None

    def require(self) -> MatchSession:
        if self.session is None:
            raise LookupError("no active match")
        return self.session


manager = SessionManager()
