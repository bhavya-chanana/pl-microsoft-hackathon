"""Coordinator policy (BRD §8.1, FR-020, NFR cost).

Decides, for each signal, which agents run. The decision is deterministic and
cheap so it can run on every signal; the expensive part (LLM calls) only
happens when the policy says so.

Plans:
* ``full``      Tactical + Narrator in parallel, then Fact-Checker, Producer, Personaliser.
* ``light``     Narrator only, then Fact-Checker and Personaliser (no tactical analysis).
* ``data_only`` no LLM calls; a data card is published from templates.
* ``retract``   the signal was removed by a correction; withdraw its insight.
* ``skip``      not shown at all (trivial or duplicate).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from matchlens.interpret.signals import Signal, SignalType


class Plan(StrEnum):
    FULL = "full"
    LIGHT = "light"
    DATA_ONLY = "data_only"
    RETRACT = "retract"
    SKIP = "skip"


FULL_TYPES = {SignalType.GOAL, SignalType.MOMENTUM_SWING, SignalType.MILESTONE, SignalType.BIG_CHANCE,
              SignalType.BIG_SAVE, SignalType.CORRECTION}
LIGHT_TYPES = {SignalType.CARD, SignalType.PRESSURE_SPELL, SignalType.CHAOS_SPELL, SignalType.CONTROL_SPELL,
               SignalType.HARD_PASS, SignalType.SHOT_SPEED_BADGE}
ALWAYS_TYPES = {SignalType.GOAL, SignalType.MILESTONE, SignalType.CORRECTION, SignalType.CARD, SignalType.PERIOD}
SPELL_TYPES = {SignalType.CHAOS_SPELL, SignalType.CONTROL_SPELL, SignalType.PRESSURE_SPELL}


@dataclass
class CoordinatorConfig:
    max_llm_runs_per_window: int = 6      # hard cap on agent pipelines per window
    window_s: float = 300.0               # match seconds
    min_spell_elapsed_s: float = 360.0    # ignore spells in the first 6 minutes (trivial samples)
    min_gap_same_type_s: float = 240.0    # match seconds between two LLM runs of the same low-priority type


@dataclass
class Decision:
    plan: Plan
    reason: str


@dataclass
class Coordinator:
    config: CoordinatorConfig = field(default_factory=CoordinatorConfig)
    _runs: deque[float] = field(default_factory=deque)
    _seen: dict[str, int] = field(default_factory=dict)        # signal_id -> latest revision handled
    _last_type: dict[str, float] = field(default_factory=dict)  # type -> match elapsed of last LLM run
    retracted: set[str] = field(default_factory=set)

    def decide(self, signal: Signal, removed: bool = False, now: float | None = None) -> Decision:
        # Caps run on match time so replays at any speed get the same editorial choices.
        now = signal.elapsed if now is None else now
        if removed:
            self.retracted.add(signal.signal_id)
            return Decision(Plan.RETRACT, "removed by correction")
        prev = self._seen.get(signal.signal_id)
        if prev is not None and signal.revision <= prev:
            return Decision(Plan.SKIP, "duplicate revision")
        self._seen[signal.signal_id] = signal.revision
        self.retracted.discard(signal.signal_id)

        if signal.type == SignalType.CORRECTION and signal.facts.get("corrected_event_type") not in (None, "goal"):
            return Decision(Plan.SKIP, "covered by the goal correction")
        if signal.type in SPELL_TYPES and signal.elapsed < self.config.min_spell_elapsed_s:
            return Decision(Plan.SKIP, "spell too early to be meaningful")
        if signal.type == SignalType.PERIOD or signal.priority >= 4 and signal.type not in LIGHT_TYPES:
            return Decision(Plan.DATA_ONLY, "low priority")

        wanted = Plan.FULL if signal.type in FULL_TYPES else Plan.LIGHT if signal.type in LIGHT_TYPES else Plan.DATA_ONLY
        if wanted == Plan.DATA_ONLY:
            return Decision(wanted, "no agent value")

        # Revisions of already explained moments always re-run so text matches corrected data.
        is_revision = prev is not None
        if signal.type not in ALWAYS_TYPES and not is_revision:
            last = self._last_type.get(signal.type.value)
            if last is not None and signal.elapsed - last < self.config.min_gap_same_type_s:
                return Decision(Plan.DATA_ONLY, "same type explained recently")

        while self._runs and now - self._runs[0] > self.config.window_s:
            self._runs.popleft()
        if len(self._runs) >= self.config.max_llm_runs_per_window and signal.type not in ALWAYS_TYPES:
            return Decision(Plan.DATA_ONLY, "rate cap reached")

        self._runs.append(now)
        self._last_type[signal.type.value] = signal.elapsed
        return Decision(wanted, "revision" if is_revision else "selected")
