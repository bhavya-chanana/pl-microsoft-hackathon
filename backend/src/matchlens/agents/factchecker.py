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
    """Numbers in the grounded context, plus harmless small counts ("down to 10 men",
    "11 players") and the half and scale markers. Counts of a statistic ("3 goals")
    and scorelines are checked separately below."""
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


def _match_totals(teams: object) -> dict[str, float]:
    """Match-wide card and foul counts: both teams' figures added up. Empty unless both teams are in the snapshot."""
    if not isinstance(teams, dict) or len(teams) != 2 or not all(isinstance(t, dict) for t in teams.values()):
        return {}
    totals: dict[str, float] = {}
    for stat in ("yellow_cards", "red_cards", "fouls"):
        values = [t.get(stat) for t in teams.values()]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            totals[stat] = float(sum(values))  # type: ignore[arg-type]
    if "yellow_cards" in totals and "red_cards" in totals:
        totals["cards"] = totals["yellow_cards"] + totals["red_cards"]
    return totals


def check_text(engine: MatchEngine, context: dict[str, Any], texts: list[str],
               claims: list[AgentClaim]) -> CheckOutcome:
    parsed = []
    problems: list[str] = []
    for cl in claims:
        claim = cl.to_claim()
        if claim is None:
            problems.append(f"Malformed claim: {cl.model_dump(exclude_none=True)}")
        else:
            parsed.append(claim)
    snapshot_results, live_claims = _as_of_context(context, parsed)
    results: list[ClaimResult] = snapshot_results + verify_claims(engine, live_claims)
    for r in results:
        if r.verdict != Verdict.SUPPORTED:
            problems.append(f"{r.verdict.value.capitalize()}: {r.claim.model_dump(exclude_none=True)} (actual: {r.actual}; {r.note or ''})")
    allowed = allowed_numbers(context) | {float(c.value) for c in parsed if getattr(c, "value", None) is not None
                                           and any(r.claim is c and r.verdict == Verdict.SUPPORTED for r in results)}
    allowed |= {abs(a) for a in allowed}  # "-60" is written as "60" ("momentum at 60 towards the away side")
    joined = " ".join(t for t in texts if t)
    # A number elsewhere in the context does not establish a statistic.
    count_pattern = re.compile(
        r"\b(?:\d+(?:[.,]\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven)"
        r"\s+(?:(?:yellow|red)\s+)?(?:goals?|assists?|shots?|passes?|saves?|tackles?|fouls?|cards?|offsides?|corners?|troph(?:y|ies)|titles?)\b",
        re.IGNORECASE,
    )
    supported_stat_texts = [(cl.text.lower(), cl.value, cl.stat or "", cl.subject_id, cl.kind) for cl in claims if isinstance(cl.to_claim(), StatClaim)
                            and any(r.claim == cl.to_claim() and r.verdict == Verdict.SUPPORTED for r in results)]
    grounded_drafts = [str(issue.get("draft", {}).get("evidence", ""))
                       for issue in context.get("issues", [])]
    players = [p for p in context.get("players", []) if isinstance(p, dict)]
    if isinstance(context.get("player"), dict):
        players.append(context["player"])
    subjects = list(players)
    totals = _match_totals(context.get("teams"))
    teams = context.get("teams") or {}
    for team in (engine.meta.home, engine.meta.away):
        if isinstance(teams.get(team.team_id), dict):
            subjects.append({"name": team.name, "short_name": team.short_name, "stats": teams[team.team_id]})
    for text in texts:
        for match in count_pattern.finditer(text):
            phrase = match.group().lower()
            word, *adjective, metric = phrase.split()
            number_words = {name: n for n, name in enumerate(
                ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven"))}
            value = number_words.get(word) if word in number_words else float(word.replace(",", "."))
            key = metric if metric.endswith("s") else metric + "s"
            if adjective:  # "yellow cards" is the yellow_cards stat, not all cards
                key = f"{adjective[0]}_{key}"
            # Associate the count with the nearest named subject preceding it in
            # this sentence, using only stats in the snapshot the agent saw.
            prefix = re.split(r"[.!?;]", text[:match.start()])[-1]
            mentions = []
            for subject in subjects:
                for name in (subject.get("name"), subject.get("short_name")):
                    if name:
                        mentions.extend((m.start(), len(name), subject) for m in re.finditer(
                            r"(?<!\w)" + re.escape(name) + r"(?!\w)", prefix, re.IGNORECASE))
            nearest = max(mentions, key=lambda m: (m[0], m[1]))[2] if mentions else None
            actual = nearest.get("stats", {}).get(key) if nearest else None
            # A claim covering this phrase must name the right subject; without one,
            # the snapshot stat for the named subject grounds it.
            # A match-wide count ("9 yellow cards in the match") names no team: it is grounded when it equals the
            # two teams' figures added up, in the snapshot the agent saw.
            grounded_total = nearest is None and totals.get(key) == value
            claimed = any(phrase in claim_text and claim_value == value and key in stat
                          for claim_text, claim_value, stat, _, _ in supported_stat_texts)
            grounded_subject_stat = bool(isinstance(actual, (int, float)) and not isinstance(actual, bool)
                                         and actual == value and not claimed)
            def claim_matches_subject(subject_id: str | None, kind: str) -> bool:
                if kind == "player_stat":
                    subject = engine.meta.players.get(subject_id or "")
                else:
                    subject = next((t for t in (engine.meta.home, engine.meta.away)
                                    if t.team_id == subject_id), None)
                return subject is not None and any(
                    re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE)
                    for name in (subject.name, subject.short_name) if name)
            if not (any(phrase in claim_text and claim_value == value and key in stat
                        and claim_matches_subject(subject_id, kind)
                        for claim_text, claim_value, stat, subject_id, kind in supported_stat_texts)
                    or text in grounded_drafts or grounded_subject_stat or grounded_total):
                problems.append(f"Unclaimed statistic: {match.group()}")
    # A score must agree as a pair, not just as individual numbers. Exclude formations.
    score = (context.get("match") or {}).get("score")
    if score:
        match_context = context["match"]
        actual_score = (score[match_context["home"]["team_id"]], score[match_context["away"]["team_id"]])
    else:
        live_score = engine.score()
        actual_score = (live_score[engine.meta.home.team_id], live_score[engine.meta.away.team_id])
    valid_scores = {actual_score}
    def collect_scores(value: object) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("home_score"), int) and isinstance(value.get("away_score"), int):
                valid_scores.add((value["home_score"], value["away_score"]))
            for key, item in value.items():
                if key in ("score_before", "score_now", "score_at_start") and isinstance(item, dict):
                    home, away = engine.meta.home.team_id, engine.meta.away.team_id
                    if home in item and away in item:
                        valid_scores.add((item[home], item[away]))
                collect_scores(item)
        elif isinstance(value, list):
            for item in value:
                collect_scores(item)
    collect_scores(context)
    for m in re.finditer(r"(?<![\d-])([0-9]+)\s*[-–]\s*([0-9]+)(?!\s*[-–]\s*\d|\d)", joined):
        # No scoreline reaches 20 a side: "2023-24" is a season, "65-35" a possession
        # split and "0-100" the momentum scale.
        if max(int(m.group(1)), int(m.group(2))) > 20:
            continue
        # Ranges with units are not scorelines.
        if re.match(r"\s*(?:minutes?|mins?|seconds?|secs?|km(?:/h)?|m/s|metres?|meters?)\b",
                    joined[m.end():], re.IGNORECASE):
            continue
        if (int(m.group(1)), int(m.group(2))) not in valid_scores:
            problems.append(f"Contradicted score: {m.group()} (actual: {actual_score[0]}-{actual_score[1]})")
    # Minute labels ("64'", "90+2'") are checked as whole tokens, other numbers individually.
    minute_problems = [m for m in _MINUTE.findall(joined) if not numbers_in(m) <= allowed]
    # Formation tokens are conventional football notation, rather than counts.
    number_text = re.sub(r"(?<![\d-])\d{1,2}(?:-\d{1,2}){2,}(?![\d-])", " ", joined)
    allowed |= {float(n) for pair in valid_scores for n in pair}
    loose = unaccounted_numbers(_MINUTE.sub(" ", number_text), allowed)
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
