"""Multi-agent insight workflow (BRD §8.1), built with Microsoft Agent Framework.

    signal ─▶ Coordinator ─┬─▶ Tactical ──┐
                           └─▶ Narrator ──┴─▶ Fact-Checker ─┬─▶ Producer ─────┐
                                                            └─▶ Personaliser ─┴─▶ Publish

The Coordinator policy runs first (outside the graph) and decides the plan.
A data-only insight and overlay are published immediately for every shown
signal, then the workflow upgrades them in place (same ids, stage "agent").
Every model call has a timeout and a template fallback, so a failed or slow
agent degrades output quality but never blocks the live feed (FR-045).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from agent_framework import Agent, Executor, WorkflowBuilder, WorkflowContext, handler
from pydantic import BaseModel

from matchlens.agents import prompts, templates
from matchlens.agents.context import signal_pack
from matchlens.agents.contracts import (
    FactCheckReport,
    FactCheckStatus,
    Insight,
    InsightText,
    KeyNumber,
    Language,
    Mode,
    NarrativeDraft,
    Overlay,
    OverlayPlan,
    PersonalisedDraft,
    RewriteDraft,
    TacticalDraft,
)
from matchlens.agents.coordinator import Coordinator, Plan
from matchlens.agents.factchecker import check_text, merge_reports, rewrite_payload
from matchlens.agents.llm import CallBudget, ModelClients
from matchlens.agents.producer import OverlayScheduler
from matchlens.interpret.signals import Signal, SignalType
from matchlens.runtime.session import MatchSession

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

AGENT_TIMEOUT_S = 8.0
WORKFLOW_TIMEOUT_S = 15.0
LANGUAGES = [lang.value for lang in Language]


# ------------------------------------------------------------------ agents


@dataclass
class AgentTeam:
    """The specialist agents. Fast model for live work, strong model for long-form."""

    tactical: Agent
    narrator: Agent
    factchecker: Agent
    producer: Agent
    personaliser: Agent
    catchup: Agent
    recap: Agent
    label: str
    budget: CallBudget

    @classmethod
    def build(cls, clients: ModelClients) -> AgentTeam:
        fast, strong = clients.fast, clients.strong
        fo, so = clients.options(), clients.options(strong=True)

        def agent(client, instructions: str, name: str, description: str, options: dict) -> Agent:
            return Agent(client, instructions, name=name, description=description, default_options=options or None)

        return cls(
            tactical=agent(fast, prompts.TACTICAL, "tactical", "Explains why a moment happened", fo),
            narrator=agent(fast, prompts.NARRATOR, "narrator", "Tells the story beat", fo),
            factchecker=agent(fast, prompts.REWRITE, "fact_checker", "Fixes unsupported claims", fo),
            producer=agent(fast, prompts.PRODUCER, "producer", "Designs the broadcast graphic", fo),
            personaliser=agent(fast, prompts.PERSONALISER, "personaliser",
                               "Adapts insights to fan modes, clubs and languages", fo),
            catchup=agent(fast, prompts.CATCHUP, "catchup", "What did I miss?", fo),
            recap=agent(strong, prompts.RECAP, "recap", "Half-time and full-time recaps", so),
            label=clients.label,
            budget=clients.budget,
        )


async def call_agent(agent: Agent, payload: dict[str, Any], fmt: type[T],
                     timeout: float | None = None, budget: CallBudget | None = None) -> tuple[T | None, str | None]:
    """Run one agent with structured output. Returns (value, error)."""
    timeout = AGENT_TIMEOUT_S if timeout is None else timeout
    if budget is not None and not budget.acquire():
        return None, f"{agent.name}: call budget exhausted"
    try:
        response = await asyncio.wait_for(
            agent.run(json.dumps(payload, default=str), options={"response_format": fmt}), timeout)
        if budget is not None:
            budget.record(response.usage_details)
        value = response.value
        if value is None:
            value = fmt.model_validate_json(response.text)
        return value, None
    except TimeoutError:
        return None, f"{agent.name}: timeout after {timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 - every failure degrades to templates
        log.warning("agent %s failed: %s", agent.name, exc)
        return None, f"{agent.name}: {type(exc).__name__}"


# ------------------------------------------------------------------ messages


@dataclass
class SignalJob:
    """Plain, serialisable workflow input (the workflow copies messages between steps)."""

    signal: Signal
    plan: Plan
    pack: dict[str, Any]
    started: float = field(default_factory=time.perf_counter)


class _Stage(Executor):
    """Executor with access to the live session and agent team (not part of messages)."""

    def __init__(self, id: str, team: AgentTeam, session: MatchSession) -> None:
        super().__init__(id)
        self.team = team
        self.session = session


@dataclass
class DraftResult:
    job: SignalJob
    role: str
    draft: TacticalDraft | NarrativeDraft | None
    error: str | None = None


@dataclass
class CheckedInsight:
    job: SignalJob
    headline: str
    body: str
    explanation: str | None
    why_it_matters: str | None
    narrative: str | None
    storyline: str | None
    key_numbers: list[KeyNumber]
    report: FactCheckReport
    agents: list[str]
    errors: list[str]


@dataclass
class RenderResult:
    checked: CheckedInsight
    role: str
    value: OverlayPlan | PersonalisedDraft | None
    error: str | None = None


# ------------------------------------------------------------------ executors


class CoordinatorExecutor(Executor):
    @handler
    async def route(self, job: SignalJob, ctx: WorkflowContext[SignalJob]) -> None:
        await ctx.send_message(job)


class TacticalExecutor(_Stage):
    @handler
    async def run(self, job: SignalJob, ctx: WorkflowContext[DraftResult]) -> None:
        if job.plan != Plan.FULL:
            await ctx.send_message(DraftResult(job, "tactical", None))
            return
        draft, err = await call_agent(self.team.tactical, {"task": "tactical", "context": job.pack}, TacticalDraft, budget=self.team.budget)
        await ctx.send_message(DraftResult(job, "tactical", draft, err))


class NarratorExecutor(_Stage):
    @handler
    async def run(self, job: SignalJob, ctx: WorkflowContext[DraftResult]) -> None:
        draft, err = await call_agent(self.team.narrator, {"task": "narrative", "context": job.pack}, NarrativeDraft, budget=self.team.budget)
        await ctx.send_message(DraftResult(job, "narrator", draft, err))


class FactCheckExecutor(_Stage):
    @handler
    async def check(self, results: list[DraftResult], ctx: WorkflowContext[CheckedInsight]) -> None:
        job = results[0].job
        engine = self.session.engine
        by_role = {r.role: r for r in results}
        tac = by_role["tactical"].draft if "tactical" in by_role else None
        nar = by_role["narrator"].draft if "narrator" in by_role else None
        errors = [r.error for r in results if r.error]
        agents = [r.role for r in results if r.draft is not None]
        assert tac is None or isinstance(tac, TacticalDraft)
        assert nar is None or isinstance(nar, NarrativeDraft)

        if tac is None and nar is None:
            head, body = templates.data_text(job.pack)
            report = FactCheckReport(status=FactCheckStatus.DATA_ONLY, actions=["no agent output; data-only text"])
            await ctx.send_message(CheckedInsight(job, head, body, None, None, None, None,
                                                  templates.key_numbers(job.pack), report, agents, errors))
            return

        headline = (tac.headline if tac else nar.headline)  # type: ignore[union-attr]
        texts = [headline]
        claims = []
        key_numbers: list[KeyNumber] = []
        if tac:
            texts += [tac.explanation, tac.why_it_matters] + [k.value for k in tac.key_numbers]
            claims += tac.claims
            key_numbers = tac.key_numbers
        if nar:
            texts += [nar.headline, nar.narrative]
            claims += nar.claims
        outcome = check_text(engine, job.pack, texts, claims)
        agents.append("fact_checker")
        body = tac.explanation if tac else nar.narrative  # type: ignore[union-attr]

        if not outcome.problems:
            await ctx.send_message(CheckedInsight(
                job, headline, body, tac.explanation if tac else None, tac.why_it_matters if tac else None,
                nar.narrative if nar else None, nar.storyline if nar else None,
                key_numbers or templates.key_numbers(job.pack), outcome.report, agents, errors))
            return

        # One rewrite attempt with the concrete issues.
        fixed, err = await call_agent(self.team.factchecker, json.loads(rewrite_payload(
            job.pack, headline, body, outcome.problems)), RewriteDraft)
        if err:
            errors.append(err)
        if fixed is not None:
            second = check_text(engine, job.pack, [fixed.headline, fixed.body], fixed.claims)
            report = merge_reports(outcome.report, second.report, rewritten=True)
            report.actions.insert(0, "; ".join(outcome.problems))
            if not second.problems:
                await ctx.send_message(CheckedInsight(job, fixed.headline, fixed.body, fixed.body, None, None,
                                                      None, templates.key_numbers(job.pack), report, agents, errors))
                return
        else:
            report = outcome.report.model_copy(update={"actions": [
                "; ".join(outcome.problems), "agent text blocked; data-only text published"]})
        head, body = templates.data_text(job.pack)
        report.status = FactCheckStatus.BLOCKED
        await ctx.send_message(CheckedInsight(job, head, body, None, None, None, None,
                                              templates.key_numbers(job.pack), report, agents, errors))


class ProducerExecutor(_Stage):
    @handler
    async def run(self, item: CheckedInsight, ctx: WorkflowContext[RenderResult]) -> None:
        job = item.job
        payload = {"task": "overlay", "context": job.pack, "headline": item.headline, "body": item.body}
        plan, err = await call_agent(self.team.producer, payload, OverlayPlan, budget=self.team.budget)
        if plan is not None:
            texts = [plan.title, plan.subtitle or ""] + [s.value for s in plan.stats]
            if check_text(self.session.engine, job.pack, texts, []).problems:
                plan, err = None, "producer: unsupported numbers"
        await ctx.send_message(RenderResult(item, "producer", plan, err))


class PersonaliserExecutor(_Stage):
    @handler
    async def run(self, item: CheckedInsight, ctx: WorkflowContext[RenderResult]) -> None:
        job = item.job
        payload = {"task": "personalise", "context": job.pack, "headline": item.headline, "body": item.body,
                   "languages": LANGUAGES}
        draft, err = await call_agent(self.team.personaliser, payload, PersonalisedDraft, budget=self.team.budget)
        await ctx.send_message(RenderResult(item, "personaliser", draft, err))


class PublishExecutor(_Stage):
    @handler
    async def publish(self, results: list[RenderResult],
                      ctx: WorkflowContext[None, tuple[Insight, Overlay | None]]) -> None:
        item = results[0].checked
        job = item.job
        by_role = {r.role: r for r in results}
        errors = item.errors + [r.error for r in results if r.error]
        agents = item.agents + [r.role for r in results if r.value is not None]
        engine = self.session.engine

        personal = by_role["personaliser"].value
        variants = data_variants(job.pack)
        club_angles: dict[str, dict[str, str]] = {}
        if isinstance(personal, PersonalisedDraft) and item.report.status != FactCheckStatus.BLOCKED:
            for v in personal.variants:
                # Each variant is checked on its own; failures keep the data-only text.
                if not check_text(engine, job.pack, [v.headline, v.body], []).problems:
                    variants.setdefault(v.mode.value, {})[v.language.value] = InsightText(headline=v.headline,
                                                                                          body=v.body)
            for a in personal.club_angles:
                if not check_text(engine, job.pack, [a.text], []).problems:
                    club_angles.setdefault(a.team_id, {})[a.language.value] = a.text
        variants.setdefault(Mode.ANALYST.value, {})["en"] = InsightText(headline=item.headline, body=item.body)

        insight = build_insight(job, stage="agent" if item.report.status != FactCheckStatus.DATA_ONLY else "data",
                                headline=item.headline, explanation=item.explanation,
                                why_it_matters=item.why_it_matters, narrative=item.narrative,
                                storyline=item.storyline, key_numbers=item.key_numbers, variants=variants,
                                club_angles=club_angles, report=item.report, agents=agents)
        insight.latency_ms = round((time.perf_counter() - job.started) * 1000, 1)
        if errors:
            insight.fact_check.actions.extend(f"fallback: {e}" for e in errors)

        plan = by_role["producer"].value
        if not isinstance(plan, OverlayPlan):
            plan = templates.overlay_plan(job.pack, item.headline, item.body)
        overlay = build_overlay(plan, insight, self.session)
        await ctx.yield_output((insight, overlay))


def build_workflow(team: AgentTeam, session: MatchSession):
    coordinator = CoordinatorExecutor("coordinator")
    tactical, narrator = TacticalExecutor("tactical", team, session), NarratorExecutor("narrator", team, session)
    checker = FactCheckExecutor("fact_checker", team, session)
    producer, personaliser = ProducerExecutor("producer", team, session), PersonaliserExecutor("personaliser", team, session)
    publish = PublishExecutor("publish", team, session)
    return (
        WorkflowBuilder(name="matchlens-insight", description="Signal to verified, personalised broadcast insight",
                        start_executor=coordinator)
        .add_fan_out_edges(coordinator, [tactical, narrator])
        .add_fan_in_edges([tactical, narrator], checker)
        .add_fan_out_edges(checker, [producer, personaliser])
        .add_fan_in_edges([producer, personaliser], publish)
        .build()
    )


# ------------------------------------------------------------------ builders


def data_variants(pack: dict[str, Any]) -> dict[str, dict[str, InsightText]]:
    out: dict[str, dict[str, InsightText]] = {}
    for v in templates.personalised(pack, LANGUAGES).variants:
        out.setdefault(v.mode.value, {})[v.language.value] = InsightText(headline=v.headline, body=v.body)
    return out


def build_insight(job_or_signal: SignalJob | Signal, *, stage: str, headline: str, report: FactCheckReport,
                  pack: dict[str, Any] | None = None, explanation: str | None = None,
                  why_it_matters: str | None = None, narrative: str | None = None, storyline: str | None = None,
                  key_numbers: list[KeyNumber] | None = None, variants: dict | None = None,
                  club_angles: dict | None = None, agents: list[str] | None = None) -> Insight:
    sig = job_or_signal.signal if isinstance(job_or_signal, SignalJob) else job_or_signal
    return Insight(
        insight_id=sig.signal_id, signal_id=sig.signal_id, signal_type=sig.type.value, revision=sig.revision,
        stage=stage, elapsed=sig.elapsed, minute_label=sig.minute_label, period=sig.period, team_id=sig.team_id,
        player_ids=sig.player_ids, priority=sig.priority, headline=headline, explanation=explanation,
        why_it_matters=why_it_matters, narrative=narrative, storyline=storyline, key_numbers=key_numbers or [],
        evidence_event_ids=[e for e in sig.evidence_event_ids if e], variants=variants or {},
        club_angles=club_angles or {}, fact_check=report, agents=agents or [],
    )


def build_overlay(plan: OverlayPlan, insight: Insight, session: MatchSession | None = None) -> Overlay:
    colour = None
    if session and insight.team_id:
        colour = session.meta.team(insight.team_id).colour
    localized = {lang: t for lang, t in insight.variants.get(Mode.CASUAL.value, {}).items()}
    return Overlay(
        overlay_id=insight.insight_id, insight_id=insight.insight_id, revision=insight.revision, kind=plan.kind,
        title=plan.title[:32], subtitle=(plan.subtitle or "")[:60] or None, stats=plan.stats[:3],
        team_id=insight.team_id, focus_player_id=plan.focus_player_id, accent_colour=colour,
        start_elapsed=insight.elapsed, duration_s=float(plan.duration_s), priority=insight.priority,
        slot="lower", localized=localized, stage=insight.stage,
    )


# ------------------------------------------------------------------ pipeline


class InsightPipeline:
    """Signal handler wired into the live session."""

    def __init__(self, team: AgentTeam, coordinator: Coordinator | None = None, max_concurrent: int = 3) -> None:
        self.team = team
        self.coordinator = coordinator or Coordinator()
        self.scheduler = OverlayScheduler()
        self._sem = asyncio.Semaphore(max_concurrent)
        self.decisions: list[dict[str, Any]] = []
        self._pending: set[asyncio.Task[None]] = set()

    def reset(self) -> None:
        self.coordinator = Coordinator(self.coordinator.config)
        self.scheduler = OverlayScheduler()
        self.decisions.clear()

    async def handle(self, signal: Signal, session: MatchSession) -> None:
        if signal.type == SignalType.CORRECTION:
            for sid in filter(None, str(signal.facts.get("removed_signals") or "").split(",")):
                if not sid.startswith("correction:"):
                    self._retract(sid, session)
        decision = self.coordinator.decide(signal)
        self.decisions.append({"signal_id": signal.signal_id, "revision": signal.revision,
                               "plan": decision.plan.value, "reason": decision.reason})
        if decision.plan == Plan.SKIP:
            return
        pack = signal_pack(session.engine, signal)
        self._publish_data(signal, pack, session)
        if decision.plan in (Plan.FULL, Plan.LIGHT):
            await self._run_agents(SignalJob(signal, decision.plan, pack), session)

    def _retract(self, signal_id: str, session: MatchSession) -> None:
        self.coordinator.retracted.add(signal_id)
        old = session.insights.get(signal_id)
        if old is None:
            return
        insight = Insight.model_validate({**old, "stage": "retracted",
                                          "fact_check": {"status": "blocked",
                                                         "actions": ["withdrawn: superseded by a correction"]}})
        session.publish_insight(insight.model_dump(mode="json"))
        if signal_id in session.overlays:
            session.publish_overlay({**session.overlays[signal_id], "stage": "retracted"})

    def _publish_data(self, signal: Signal, pack: dict[str, Any], session: MatchSession) -> None:
        head, body = templates.data_text(pack)
        previous = session.insights.get(signal.signal_id)
        insight = build_insight(signal, stage="data", headline=head, explanation=body,
                                key_numbers=templates.key_numbers(pack), variants=data_variants(pack),
                                report=FactCheckReport(status=FactCheckStatus.DATA_ONLY))
        if previous:
            insight.supersedes_revision = previous["revision"]
        session.publish_insight(insight.model_dump(mode="json"))
        if signal.priority <= 4:  # minor badges stay in the companion feed, not on screen
            self._publish_overlay(build_overlay(templates.overlay_plan(pack), insight, session), session)

    def _publish_overlay(self, overlay: Overlay, session: MatchSession) -> None:
        placed = self.scheduler.place(overlay, now=session.engine.c.elapsed)
        for overlay_id, duration in self.scheduler.take_trimmed().items():
            if overlay_id in session.overlays and overlay_id != overlay.overlay_id:
                session.publish_overlay({**session.overlays[overlay_id], "duration_s": duration})
        if placed is not None:
            session.publish_overlay(placed.model_dump(mode="json"))

    async def _run_agents(self, job: SignalJob, session: MatchSession) -> None:
        async with self._sem:
            try:
                result = await asyncio.wait_for(build_workflow(self.team, session).run(job), WORKFLOW_TIMEOUT_S)
                outputs = result.get_outputs()
            except Exception as exc:  # noqa: BLE001
                log.warning("workflow failed for %s: %s", job.signal.signal_id, exc)
                return  # the data-only insight stays published
        if not outputs:
            return
        insight, overlay = outputs[-1]
        current = session.engine.signals()
        latest = next((s for s in current if s.signal_id == insight.signal_id), None)
        if insight.signal_id in self.coordinator.retracted or (latest and latest.revision > insight.revision):
            return  # superseded while the agents were working
        session.publish_insight(insight.model_dump(mode="json"))
        if overlay is not None:
            self._publish_overlay(overlay, session)
