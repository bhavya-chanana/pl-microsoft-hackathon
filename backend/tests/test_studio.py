"""Telestrator moments, Match Pulse, Ask the Pundit and Scenario Lab."""

import asyncio

import pytest

from matchlens.agents.contracts import AgentClaim, FactCheckStatus, FanProfile, Language, Mode
from matchlens.agents.pundit import ask, route
from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH, EventType
from matchlens.interpret.moments import moment
from matchlens.interpret.pulse import pulse
from matchlens.interpret.signals import SignalType
from matchlens.simulator.engine import generate_match
from matchlens.simulator.roster import default_match_meta
from matchlens.simulator.scenarios import PRESETS, check_storyline, script_vs_reality
from matchlens.simulator.storyline import Beat, BeatType, Storyline

from .conftest import replay_order
from .test_agents import ScriptedClient, fresh_session, played, team_for  # noqa: F401 - fixture


def run(coro):
    return asyncio.run(coro)


def signal_of(session, type_):
    return next(s for s in session.engine.signals() if s.type == type_)


# ------------------------------------------------------------------ moments


def test_goal_moment_draws_the_build_up_from_real_events(played):
    engine = played.engine
    goal = signal_of(played, SignalType.GOAL)
    m = moment(engine, goal)
    assert m["start_elapsed"] < goal.elapsed < m["end_elapsed"]
    assert m["frames"] and all(m["start_elapsed"] - 1 <= f["elapsed"] <= m["end_elapsed"] + 1 for f in m["frames"])
    kinds = [d["kind"] for d in m["drawings"]]
    assert "shot" in kinds and "ring" in kinds and ("lane" in kinds or "carry" in kinds)
    window = m["end_elapsed"] - m["start_elapsed"]
    for d in m["drawings"]:
        assert 0 <= d["at_s"] <= window
        assert all(0 <= p["x"] <= PITCH_LENGTH and 0 <= p["y"] <= PITCH_WIDTH for p in d["points"])
        # Every stroke tied to an event points at one the engine really ingested.
        assert d["event_id"] is None or engine.has_event(d["event_id"])
    # The build-up belongs to the scoring side and ends with the shot.
    assert {e["team_id"] for e in m["events"]} == {goal.team_id}
    assert m["events"][-1]["type"] == "shot"


@pytest.mark.parametrize("kind", [SignalType.PRESSURE_SPELL, SignalType.CHAOS_SPELL, SignalType.HARD_PASS,
                                  SignalType.SPEED_BADGE, SignalType.CARD, SignalType.MOMENTUM_SWING])
def test_every_moment_kind_has_drawings(played, kind):
    sig = next((s for s in played.engine.signals() if s.type == kind), None)
    if sig is None:
        pytest.skip(f"no {kind} in the demo match")
    m = moment(played.engine, sig)
    assert m["drawings"], kind
    assert any(d["emphasis"] == "primary" for d in m["drawings"]) or kind == SignalType.CHAOS_SPELL


# -------------------------------------------------------------------- pulse


def test_pulse_rows_match_the_engine(played):
    engine = played.engine
    p = pulse(engine)
    assert len(p["rows"]) == len(engine.momentum_series())
    assert {r["state"] for r in p["rows"]} <= {"control", "balanced", "chaos"}
    assert [r["momentum"] for r in p["rows"]] == [m.index for m in engine.momentum_series()]
    pressing = sum(sum(r["pressure"].values()) for r in p["rows"])
    total = len(engine.events(types={EventType.PRESSURE}))
    assert 0 < pressing <= total
    goals = [pin for pin in p["pins"] if pin["type"] == "goal"]
    assert len(goals) == sum(engine.score().values())
    assert all(s["state"] != "balanced" and s["minutes"] >= 2 for s in p["spells"])


# ------------------------------------------------------------------- pundit


def test_router_reads_three_languages(played):
    engine = played.engine
    assert route("Who has been the best player?", engine)[0] == "best_player"
    assert route("How has Brandvold played?", engine)[:2] == ("player", "NBR-10")
    assert route("Why are Northbridge on top?", engine)[0] == "momentum"
    assert route("क्या स्कोर है?", engine)[0] == "score"
    assert route("¿Quién presiona más?", engine)[0] == "pressure"
    assert route("Who will win?", engine)[0] == "prediction"
    assert route("What's the weather in Paris?", engine)[0] == "off_topic"


@pytest.mark.parametrize("language", list(Language))
def test_pundit_answers_from_the_data(played, language):
    client = ScriptedClient()
    session = fresh_session(played)
    profile = FanProfile(mode=Mode.CASUAL, language=language)
    answer = run(ask(team_for(client), session, "Who has been the best player?", profile))
    assert answer.intent == "best_player" and answer.answer
    assert answer.fact_check.status == FactCheckStatus.VERIFIED
    assert answer.agents == ["pundit_router", "pundit", "fact_checker"]
    assert all(c.insight_id in {s.signal_id for s in session.engine.signals()} for c in answer.cited_moments)
    # Same question, same match position: answered from the store.
    calls = len(client.calls)
    assert run(ask(team_for(client), session, "Who has been the best player?", profile)) == answer
    assert len(client.calls) == calls


def test_pundit_blocks_a_wrong_number(played):
    def lie(draft, payload):
        claim = AgentClaim(kind="score", text="9-0", subject_id=None, stat=None, value=None, event_id=None,
                           player_id=None, home=9, away=0)
        return draft.model_copy(update={"answer": "It's 9-0.", "claims": [claim]})

    session = fresh_session(played)
    answer = run(ask(team_for(ScriptedClient(tamper={"pundit": lie})), session, "What's the score?", FanProfile()))
    assert answer.fact_check.status == FactCheckStatus.BLOCKED
    assert "9-0" not in answer.answer and answer.source == "data"
    assert answer.agents == ["pundit_router"]


def test_pundit_refuses_off_topic_without_a_model_call(played):
    client = ScriptedClient()
    answer = run(ask(team_for(client), fresh_session(played), "Tell me a joke about cats", FanProfile()))
    assert answer.intent == "off_topic" and "pundit" not in client.calls


# ---------------------------------------------------------------- scenarios


@pytest.mark.parametrize("preset_id", list(PRESETS))
def test_presets_are_playable_and_delivered(preset_id):
    factory, seed = PRESETS[preset_id]
    story = factory()
    assert check_storyline(default_match_meta(), story) == []
    match = generate_match(seed=seed, storyline=story)
    from matchlens.ingest.adapters import SyntheticReplayAdapter
    from matchlens.runtime.session import MatchSession

    session = MatchSession(SyntheticReplayAdapter(match))
    for kind, item in replay_order(match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    report = script_vs_reality(session.engine, story)
    assert report["finished"]
    assert report["delivered"] == report["total"], [b for b in report["beats"] if b["status"] != "delivered"]
    goals = sum(b.type == BeatType.GOAL for b in story.beats)
    assert sum(session.engine.score().values()) == goals


def test_check_storyline_finds_unplayable_scripts():
    meta = default_match_meta()
    story = Storyline(title="Broken", summary="x", beats=[
        Beat(minute=20, type=BeatType.RED_CARD, team_id="HBC", player_id="HBC-11"),
        Beat(minute=30, type=BeatType.GOAL, team_id="HBC", player_id="HBC-11"),  # sent off
        Beat(minute=40, type=BeatType.SUBSTITUTION, team_id="NBR", player_id="NBR-12",
             related_player_id="NBR-01"),  # bench player off, starter on
        Beat(minute=50, type=BeatType.GOAL, team_id="NBR", player_id="HBC-09"),  # wrong team
    ])
    problems = check_storyline(meta, story)
    assert any("30' goal" in p for p in problems)
    assert any("40' substitution" in p and "NBR-12" in p for p in problems)
    assert any("40' substitution" in p and "bench" in p for p in problems)
    assert any("50' goal" in p for p in problems)
