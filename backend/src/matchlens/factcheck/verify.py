"""Deterministic claim verification used by the Fact-Checker (FR-042 to FR-044).

Agents must return structured claims alongside their text. Each claim is
checked against the stats engine; the text is also scanned for numbers that
no verified claim or signal fact accounts for.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from matchlens.interpret.engine import MatchEngine


class Verdict(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class StatClaim(BaseModel):
    kind: Literal["stat"] = "stat"
    subject_type: Literal["player", "team"]
    subject_id: str
    stat: str = Field(description="A key from get_player_stats / get_team_stats, e.g. 'pass_accuracy'")
    value: float
    tolerance: float | None = Field(default=None, description="Allowed absolute difference")


class EventClaim(BaseModel):
    kind: Literal["event"] = "event"
    event_id: str
    type: str | None = None
    player_id: str | None = None
    team_id: str | None = None
    minute_label: str | None = None


class ScoreClaim(BaseModel):
    kind: Literal["score"] = "score"
    home: int
    away: int


class MomentumClaim(BaseModel):
    kind: Literal["momentum"] = "momentum"
    team_on_top: str = Field(description="team_id currently ahead on the momentum index")


Claim = Annotated[StatClaim | EventClaim | ScoreClaim | MomentumClaim, Field(discriminator="kind")]


class ClaimResult(BaseModel):
    claim: Claim
    verdict: Verdict
    actual: str | float | int | None = None
    note: str | None = None


def _check_stat(engine: MatchEngine, c: StatClaim) -> ClaimResult:
    try:
        stats = engine.player_stats(c.subject_id) if c.subject_type == "player" else engine.team_stats(c.subject_id)
    except KeyError:
        return ClaimResult(claim=c, verdict=Verdict.UNVERIFIABLE, note="unknown subject")
    actual = stats.get(c.stat)
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return ClaimResult(claim=c, verdict=Verdict.UNVERIFIABLE, actual=None, note="statistic unavailable")
    tol = c.tolerance if c.tolerance is not None else max(0.5, abs(actual) * 0.03)
    ok = abs(actual - c.value) <= tol
    return ClaimResult(claim=c, verdict=Verdict.SUPPORTED if ok else Verdict.CONTRADICTED, actual=actual)


def _check_event(engine: MatchEngine, c: EventClaim) -> ClaimResult:
    e = engine.event(c.event_id)
    if e is None:
        return ClaimResult(claim=c, verdict=Verdict.CONTRADICTED, note="no such event")
    mismatches = []
    if c.type and e.type.value != c.type:
        mismatches.append(f"type is {e.type.value}")
    if c.player_id and e.player_id != c.player_id:
        mismatches.append(f"player is {e.player_id}")
    if c.team_id and e.team_id != c.team_id:
        mismatches.append(f"team is {e.team_id}")
    if c.minute_label and e.minute_label != c.minute_label:
        mismatches.append(f"minute is {e.minute_label}")
    if mismatches:
        return ClaimResult(claim=c, verdict=Verdict.CONTRADICTED, note="; ".join(mismatches))
    return ClaimResult(claim=c, verdict=Verdict.SUPPORTED)


def _check_score(engine: MatchEngine, c: ScoreClaim) -> ClaimResult:
    score = engine.score()
    home, away = score[engine.meta.home.team_id], score[engine.meta.away.team_id]
    ok = (home, away) == (c.home, c.away)
    return ClaimResult(claim=c, verdict=Verdict.SUPPORTED if ok else Verdict.CONTRADICTED, actual=f"{home}-{away}")


def _check_momentum(engine: MatchEngine, c: MomentumClaim) -> ClaimResult:
    idx = engine.momentum_now()
    if abs(idx) < 10:
        return ClaimResult(claim=c, verdict=Verdict.CONTRADICTED, actual=idx, note="momentum is balanced")
    on_top = engine.meta.home.team_id if idx > 0 else engine.meta.away.team_id
    ok = on_top == c.team_on_top
    return ClaimResult(claim=c, verdict=Verdict.SUPPORTED if ok else Verdict.CONTRADICTED, actual=idx)


def verify_claims(engine: MatchEngine, claims: list[Claim]) -> list[ClaimResult]:
    out = []
    for c in claims:
        match c:
            case StatClaim():
                out.append(_check_stat(engine, c))
            case EventClaim():
                out.append(_check_event(engine, c))
            case ScoreClaim():
                out.append(_check_score(engine, c))
            case MomentumClaim():
                out.append(_check_momentum(engine, c))
    return out


_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


def unaccounted_numbers(text: str, allowed: set[float]) -> list[str]:
    """Numbers in text that do not match any allowed value (within rounding).

    Minute labels ("64'", "90+2'") and small ordinal words are common in
    football writing, so the allowed set should include the relevant minutes.
    """
    found = []
    for raw in _NUMBER.findall(text):
        value = float(raw.replace(",", "."))
        if any(abs(value - a) <= max(0.51, abs(a) * 0.02) for a in allowed):
            continue
        found.append(raw)
    return found


def numbers_in(value: object) -> set[float]:
    """Collect every number in a nested structure (facts, stats, labels)."""
    out: set[float] = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
        return out
    if isinstance(value, str):
        for raw in _NUMBER.findall(value):
            out.add(float(raw.replace(",", ".")))
        return out
    if isinstance(value, dict):
        for v in value.values():
            out |= numbers_in(v)
        return out
    if isinstance(value, (list, tuple, set)):
        for v in value:
            out |= numbers_in(v)
    return out
