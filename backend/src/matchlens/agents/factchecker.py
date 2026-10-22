"""Fact-Checker (FR-042 to FR-044).

Step 1 is deterministic: every structured claim is verified against the
stats engine, and the text is scanned for numbers that neither a supported
claim nor the context accounts for. Step 2 (only if step 1 finds issues):
one LLM rewrite with the concrete issues, then step 1 again. If issues
remain, the agent text is blocked and the data-only text is published.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from matchlens.agents.contracts import AgentClaim, FactCheckReport, FactCheckStatus, RewriteDraft
from matchlens.factcheck.verify import (
    Claim,
    ClaimResult,
    ScoreClaim,
    StatClaim,
    Verdict,
    numbers_in,
    unaccounted_numbers,
    verify_claims,
)
from matchlens.interpret.engine import MatchEngine

_MINUTE = re.compile(r"\d+(?:\+\d+)?'")
_PLAYER_ID = re.compile(r"\b[A-Z]{3}-\d{2}\b")


def allowed_numbers(context: dict[str, Any]) -> set[float]:
    """Numbers a text may mention: everything in the grounded context, plus
    harmless small counts ("two goals", "11 players") and the halves."""
    allowed = numbers_in(context) | {float(n) for n in range(0, 12)} | {45.0, 90.0, 100.0}
    return allowed


@dataclass
class CheckOutcome:
    report: FactCheckReport
    problems: list[str]


def _as_of_context(context: dict[str, Any], claims: list[Claim]) -> tuple[list[ClaimResult], list[Claim]]:
    """Verify score and stat claims against the context snapshot the agent saw.

    The live engine keeps moving while agents work (and much faster in
    replays), so "it is 0-0" is judged at the moment it describes. Event
    claims always go to the live engine so corrections are enforced.
    """
    done: list[ClaimResult] = []
    rest: list[Claim] = []
    match = context.get("match") or {}
    score = match.get("score")
    players = {p["player_id"]: p.get("stats", {}) for p in context.get("players", [])}
    teams = context.get("teams") or {}
    for c in claims:
        if isinstance(c, ScoreClaim) and score:
            h, a = match["home"]["team_id"], match["away"]["team_id"]
            ok = (score[h], score[a]) == (c.home, c.away)
            done.append(ClaimResult(claim=c, verdict=Verdict.SUPPORTED if ok else Verdict.CONTRADICTED,
                                    actual=f"{score[h]}-{score[a]}"))
            continue
        if isinstance(c, StatClaim):
            source = players.get(c.subject_id) if c.subject_type == "player" else teams.get(c.subject_id)
            actual = (source or {}).get(c.stat)
            if isinstance(actual, (int, float)) and not isinstance(actual, bool):
                tol = c.tolerance if c.tolerance is not None else max(0.5, abs(actual) * 0.03)
                ok = abs(actual - c.value) <= tol
                done.append(ClaimResult(claim=c, verdict=Verdict.SUPPORTED if ok else Verdict.CONTRADICTED,
                                        actual=actual))
                continue
        rest.append(c)
    return done, rest


def check_text(engine: MatchEngine, context: dict[str, Any], texts: list[str],
               claims: list[AgentClaim]) -> CheckOutcome:
    parsed = [c for c in (cl.to_claim() for cl in claims) if c is not None]
    snapshot_results, live_claims = _as_of_context(context, parsed)
    results: list[ClaimResult] = snapshot_results + verify_claims(engine, live_claims)
    problems: list[str] = []
    for r in results:
        if r.verdict == Verdict.CONTRADICTED:
            problems.append(f"Contradicted: {r.claim.model_dump(exclude_none=True)} (actual: {r.actual}; {r.note or ''})")
    allowed = allowed_numbers(context) | {float(c.value) for c in parsed if getattr(c, "value", None) is not None
                                           and any(r.claim is c and r.verdict == Verdict.SUPPORTED for r in results)}
    allowed |= {abs(a) for a in allowed}  # "-60" is written as "60" ("momentum at 60 towards the away side")
    joined = " ".join(t for t in texts if t)
    # Minute labels ("64'", "90+2'") are checked as whole tokens, other numbers individually.
    minute_problems = [m for m in _MINUTE.findall(joined) if not numbers_in(m) <= allowed]
    loose = unaccounted_numbers(_MINUTE.sub(" ", joined), allowed)
    if loose:
        problems.append(f"Numbers not found in the data: {', '.join(loose)}")
    if minute_problems:
        problems.append(f"Minutes not found in the data: {', '.join(minute_problems)}")
    if _PLAYER_ID.search(joined):
        problems.append("Raw player ids appear in the text; use player names")
    status = FactCheckStatus.VERIFIED if not problems else FactCheckStatus.BLOCKED
    return CheckOutcome(
        report=FactCheckReport(status=status, results=results, unaccounted_numbers=loose + minute_problems),
        problems=problems,
    )


def rewrite_payload(context: dict[str, Any], headline: str, body: str, problems: list[str]) -> str:
    return json.dumps({
        "task": "rewrite",
        "context": context,
        "draft": {"headline": headline, "body": body},
        "issues": problems,
    }, default=str)


def merge_reports(first: FactCheckReport, second: FactCheckReport, rewritten: bool) -> FactCheckReport:
    status = second.status
    if rewritten and status == FactCheckStatus.VERIFIED:
        status = FactCheckStatus.REWRITTEN
    actions = [*first.actions, *(["rewritten after failed check"] if rewritten else [])]
    if status == FactCheckStatus.BLOCKED:
        actions.append("agent text blocked; data-only text published")
    return FactCheckReport(status=status, results=second.results,
                           unaccounted_numbers=second.unaccounted_numbers, actions=actions)


__all__ = ["CheckOutcome", "RewriteDraft", "allowed_numbers", "check_text", "merge_reports", "rewrite_payload"]
