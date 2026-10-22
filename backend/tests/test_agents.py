"""Agent layer tests with fake model clients (no Azure needed)."""

import asyncio
import json
from collections import Counter

import pytest
from agent_framework import BaseChatClient, ChatResponse, Message

from matchlens.agents import templates
from matchlens.agents.contracts import FactCheckStatus, FanProfile, Language, Mode
from matchlens.agents.coordinator import Coordinator, Plan
from matchlens.agents.llm import LlmSettings, ModelClients, OfflineChatClient
from matchlens.agents.pipeline import AgentTeam, InsightPipeline
from matchlens.agents.producer import OverlayScheduler
from matchlens.agents.services import FanServices, personalise_feed
from matchlens.ingest.adapters import ReplayControl, SyntheticReplayAdapter
from matchlens.interpret.signals import SignalType
from matchlens.runtime.session import MatchSession

from .conftest import replay_order

OFFLINE = LlmSettings(endpoint=None, fast_model="fast", strong_model="strong", mode="offline")


class ScriptedClient(BaseChatClient):
    """Behaves like the offline client, but lets a test tamper with answers per task."""

    def __init__(self, tamper=None, fail: set[str] = frozenset(), delay: float = 0.0) -> None:
        super().__init__()
        self.tamper = tamper or {}
        self.fail = set(fail)
        self.delay = delay
        self.calls: list[str] = []

    async def _respond(self, messages):
        payload = json.loads(messages[-1].text)
        task = payload["task"]
        self.calls.append(task)
        if self.delay:
            await asyncio.sleep(self.delay)
        if task in self.fail:
            raise RuntimeError(f"{task} unavailable")
        draft = templates.TASKS[task](payload)
        if task in self.tamper:
            draft = self.tamper[task](draft, payload)
        return ChatResponse(messages=Message("assistant", [draft.model_dump_json()]))

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return self._respond(list(messages))


def team_for(client) -> AgentTeam:
    return AgentTeam.build(ModelClients(fast=client, strong=client, settings=OFFLINE))


@pytest.fixture(scope="module")
def played(demo_match):
    """A session whose engine has ingested the full demo match (not started)."""
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session


def fresh_session(played):
    s = MatchSession(played.adapter)
    s.engine = played.engine
    return s


def signal_of(session, type_, minute=None):
    return next(s for s in session.engine.signals()
                if s.type == type_ and (minute is None or s.minute_label == minute))


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ coordinator


def test_coordinator_policy(played):
    c = Coordinator()
    goal = signal_of(played, SignalType.GOAL)
    assert c.decide(goal).plan == Plan.FULL
    assert c.decide(goal).plan == Plan.SKIP  # same revision again
    assert c.decide(goal.model_copy(update={"revision": 1})).plan == Plan.FULL  # corrected revision re-runs
    badge = signal_of(played, SignalType.DISTANCE_BADGE)
    assert c.decide(badge).plan == Plan.DATA_ONLY
    assert c.decide(goal, removed=True).plan == Plan.RETRACT


def test_coordinator_rate_cap_never_blocks_goals(played):
    c = Coordinator()
    swings = [s for s in played.engine.signals() if s.type == SignalType.MOMENTUM_SWING]
    for s in swings:
        c.decide(s.model_copy(update={"elapsed": 1000.0}), now=1000.0)
    for i in range(10):
        c.decide(signal_of(played, SignalType.BIG_CHANCE).model_copy(
            update={"signal_id": f"x{i}", "type": SignalType.BIG_CHANCE}), now=1000.0)
    assert c.decide(signal_of(played, SignalType.GOAL), now=1000.0).plan == Plan.FULL


# ------------------------------------------------------------------ pipeline


def test_goal_insight_verified_with_all_agents(played):
    client = ScriptedClient()
    session = fresh_session(played)
    pipe = InsightPipeline(team_for(client))
    goal = signal_of(played, SignalType.GOAL, "23'")
    run(pipe.handle(goal, session))
    ins = session.insights[goal.signal_id]
    assert ins["stage"] == "agent" and ins["fact_check"]["status"] == "verified"
    assert set(ins["agents"]) >= {"tactical", "narrator", "fact_checker", "producer", "personaliser"}
    assert set(ins["variants"]) == {m.value for m in Mode}
    assert all(set(v) == {lang.value for lang in Language} for v in ins["variants"].values())
    assert {"NBR", "HBC"} <= set(ins["club_angles"])
    assert Counter(client.calls) == Counter(["tactical", "narrative", "overlay", "personalise"])
    overlay = session.overlays[goal.signal_id]
    assert overlay["stage"] == "agent" and 4 <= overlay["duration_s"] <= 12


def _wrong_score(draft, payload):
    for c in draft.claims:
        if c.kind == "score":
            c.home, c.away = 5, 0
    return draft


def _invented_number(draft, payload):
    return draft.model_copy(update={"explanation": draft.explanation + " He has 37 goals this season."})


def test_wrong_claim_is_rewritten(played):
    client = ScriptedClient(tamper={"tactical": _wrong_score})
    session = fresh_session(played)
    goal = signal_of(played, SignalType.GOAL, "23'")
    run(InsightPipeline(team_for(client)).handle(goal, session))
    ins = session.insights[goal.signal_id]
    assert ins["fact_check"]["status"] == FactCheckStatus.REWRITTEN
    assert "rewrite" in client.calls


def test_unfixable_claim_is_blocked_and_data_text_shown(played):
    client = ScriptedClient(tamper={"tactical": _invented_number,
                                    "rewrite": lambda d, p: d.model_copy(update={"body": d.body + " 37 goals."})})
    session = fresh_session(played)
    goal = signal_of(played, SignalType.GOAL, "23'")
    run(InsightPipeline(team_for(client)).handle(goal, session))
    ins = session.insights[goal.signal_id]
    assert ins["fact_check"]["status"] == FactCheckStatus.BLOCKED
    assert "37" in ins["fact_check"]["unaccounted_numbers"] or any("37" in a for a in ins["fact_check"]["actions"])
    assert "37" not in json.dumps(ins["variants"]) and "37 goals" not in (ins["explanation"] or "")


def test_corrected_scorer_claim_is_caught(played):
    """An agent that still credits the 64' goal to the original scorer is corrected."""
    goal = signal_of(played, SignalType.GOAL, "64'")

    def stale(draft, payload):
        draft.claims.append(templates.AgentClaim(
            kind="event", text="Brandvold scores", subject_id=None, stat=None, value=None,
            event_id=goal.evidence_event_ids[0], player_id="NBR-10", home=None, away=None))
        return draft

    client = ScriptedClient(tamper={"narrative": stale})
    session = fresh_session(played)
    run(InsightPipeline(team_for(client)).handle(goal, session))
    ins = session.insights[goal.signal_id]
    assert ins["fact_check"]["status"] == FactCheckStatus.REWRITTEN
    assert "Arakawa" in ins["headline"]


def test_agent_failure_falls_back_to_data(played):
    client = ScriptedClient(fail={"tactical", "narrative", "overlay", "personalise"})
    session = fresh_session(played)
    goal = signal_of(played, SignalType.GOAL, "79'")
    run(InsightPipeline(team_for(client)).handle(goal, session))
    ins = session.insights[goal.signal_id]
    assert ins["fact_check"]["status"] == FactCheckStatus.DATA_ONLY
    assert "Brandvold" in ins["headline"] and set(ins["variants"]) == {m.value for m in Mode}
    assert goal.signal_id in session.overlays


def test_agent_timeout_falls_back(played, monkeypatch):
    import matchlens.agents.pipeline as pipeline
    monkeypatch.setattr(pipeline, "AGENT_TIMEOUT_S", 0.05)
    client = ScriptedClient(delay=0.5)
    session = fresh_session(played)
    goal = signal_of(played, SignalType.GOAL, "38'")
    run(InsightPipeline(team_for(client)).handle(goal, session))
    assert session.insights[goal.signal_id]["fact_check"]["status"] == FactCheckStatus.DATA_ONLY


def test_full_replay_with_agents(demo_match):
    """End to end: replay, every shown signal gets an insight, corrections retract and re-explain."""
    async def main():
        session = MatchSession(SyntheticReplayAdapter(demo_match, ReplayControl(speed=2000)))
        client = OfflineChatClient()
        pipe = InsightPipeline(team_for(client))
        session.on_signal(pipe.handle)
        session.start()
        while not session.finished:
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        return session, pipe

    session, pipe = run(main())
    insights = session.insights
    stages = Counter(i["stage"] for i in insights.values())
    assert stages["agent"] >= 20 and stages["retracted"] == 1
    assert not any(i["fact_check"]["status"] == "blocked" and i["stage"] == "agent" for i in insights.values())
    retracted = next(i for i in insights.values() if i["stage"] == "retracted")
    assert retracted["signal_type"] == "milestone" and retracted["minute_label"] == "64'"
    corrected = next(i for i in insights.values() if i["signal_type"] == "goal" and i["minute_label"] == "64'")
    assert corrected["revision"] == 1 and "Arakawa" in corrected["headline"]
    plans = Counter(d["plan"] for d in pipe.decisions)
    assert plans["data_only"] > 0 and plans["full"] > 0
    # Producer caps: never two graphics in one slot at the same time.
    by_slot: dict[str, list] = {}
    for o in session.overlays.values():
        if o["stage"] != "retracted":
            by_slot.setdefault(o["slot"], []).append((o["start_elapsed"], o["start_elapsed"] + o["duration_s"]))
    for spans in by_slot.values():
        spans.sort()
        assert all(a[1] <= b[0] + 1e-6 for a, b in zip(spans, spans[1:]))


def test_scheduler_preempts_lower_priority():
    from matchlens.agents.contracts import Overlay, OverlayKind
    sched = OverlayScheduler()
    base = dict(insight_id="i", revision=0, title="t", subtitle=None, stats=[], team_id=None, focus_player_id=None,
                accent_colour=None, duration_s=8, slot="lower")
    minor = Overlay(overlay_id="a", kind=OverlayKind.PLAYER_CARD, start_elapsed=100, priority=4, **base)
    goal = Overlay(overlay_id="b", kind=OverlayKind.LOWER_THIRD, start_elapsed=102, priority=1, **base)
    late = Overlay(overlay_id="c", kind=OverlayKind.LOWER_THIRD, start_elapsed=103, priority=3, **base)
    assert sched.place(minor, 100).start_elapsed == 100
    assert sched.place(goal, 102).start_elapsed == 102
    placed = sched.place(late, 103)
    assert placed is not None and placed.start_elapsed == 110  # waits for the goal graphic


# ------------------------------------------------------------------ fan services


def test_catchup_and_recap(played):
    fans = FanServices(team_for(ScriptedClient()))
    session = fresh_session(played)
    profile = FanProfile(language=Language.ES, club_id="NBR", player_id="NBR-10")
    c = run(fans.catchup(session, 15, profile))
    assert c.score_now == {"NBR": 3, "HBC": 2} and c.score_before == {"NBR": 2, "HBC": 2}
    assert c.fact_check.status == FactCheckStatus.VERIFIED and 1 <= len(c.bullets) <= 5
    assert c.bullets[0][0].isdigit()
    r = run(fans.recap(session, FanProfile()))
    assert r.phase == "full_time" and "3-2" in r.title
    with pytest.raises(ValueError):
        run(fans.catchup(session, 7, profile))


def test_catchup_blocks_invented_facts(played):
    def invent(draft, payload):
        return draft.model_copy(update={"bullets": draft.bullets + ["88' A 45 m screamer from 41 yards"]})

    fans = FanServices(team_for(ScriptedClient(tamper={"catchup": invent})))
    c = run(fans.catchup(fresh_session(played), 5, FanProfile()))
    assert c.source == "data" and "screamer" not in " ".join(c.bullets)


def test_personalised_feed(played):
    session = fresh_session(played)
    pipe = InsightPipeline(team_for(ScriptedClient()))
    for s in played.engine.signals():
        if s.type in (SignalType.GOAL, SignalType.DISTANCE_BADGE, SignalType.MILESTONE):
            run(pipe.handle(s, session))
    casual = personalise_feed(session, FanProfile(mode=Mode.CASUAL, language=Language.HI, club_id="HBC"))
    assert casual and all(x["signal_type"] != "distance_badge" for x in casual)
    assert any(x["club_angle"] for x in casual)
    focus = personalise_feed(session, FanProfile(mode=Mode.PLAYER_FOCUS, player_id="NBR-10"))
    assert all(x["favourite_player"] or x["signal_type"] == "goal" for x in focus)
    assert any(x["signal_type"] == "milestone" for x in focus)
