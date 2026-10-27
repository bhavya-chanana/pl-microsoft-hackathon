"""Ask the Pundit about cards and fouls: routed, grounded in the card events, answered from the data."""

from __future__ import annotations

import asyncio

import pytest

from matchlens.agents.contracts import AgentClaim, FactCheckStatus, FanProfile, Language
from matchlens.agents.pundit import _count, ask, pundit_pack, route, template
from matchlens.domain.models import EventType, Outcome

from .test_agents import ScriptedClient, fresh_session, played, team_for  # noqa: F401 - fixture


def run(coro):
    return asyncio.run(coro)


def counts(engine):
    cards = engine.events(types={EventType.CARD})
    yellow = sum(e.outcome in (Outcome.YELLOW, Outcome.SECOND_YELLOW) for e in cards)
    red = [e for e in cards if e.outcome in (Outcome.RED, Outcome.SECOND_YELLOW)]
    return yellow, red


@pytest.mark.parametrize("question", [
    "how many yellow cards in the match?", "Any red cards?", "who got booked", "was anyone sent off",
    "how many fouls so far", "¿cuántas tarjetas amarillas hay?", "कितने पीले कार्ड हुए?"])
def test_card_questions_are_routed_to_discipline(played, question):
    assert route(question, played.engine)[0] == "discipline"


def test_other_routes_are_unchanged(played):
    engine = played.engine
    assert route("How has Brandvold played?", engine)[:2] == ("player", "NBR-10")
    assert route("Why are Northbridge on top?", engine)[0] == "momentum"
    assert route("What's the score?", engine)[0] == "score"


def test_the_pack_carries_card_counts_and_real_event_ids(played):
    engine = played.engine
    pack = pundit_pack(engine, {}, "discipline", None, None, "how many yellow cards?")
    for tid, stats in pack["teams"].items():
        assert stats["yellow_cards"] == engine.team_stats(tid)["yellow_cards"]
        assert stats["red_cards"] == engine.team_stats(tid)["red_cards"]
    assert pack["cards"] and all(engine.has_event(c["event_id"]) for c in pack["cards"])
    assert all(c["player"] and c["minute"].endswith("'") for c in pack["cards"])
    assert pack["asked_about"] == ["cards"]
    assert "cards" not in pundit_pack(engine, {}, "summary", None, None, "")  # other questions stay small


def test_offline_answer_counts_the_cards_from_the_events(played):
    engine = played.engine
    yellow, red = counts(engine)
    assert yellow and red
    answer = run(ask(team_for(ScriptedClient()), fresh_session(played), "how many yellow cards in the match?",
                     FanProfile()))
    assert answer.intent == "discipline" and answer.fact_check.status == FactCheckStatus.VERIFIED
    assert f"{yellow} yellow cards" in answer.answer and f"{len(red)} red card" in answer.answer
    name = engine.meta.players[red[0].player_id].name
    assert f"Sent off: {name}" in answer.answer and red[0].minute_label in answer.answer
    for team in (engine.meta.home, engine.meta.away):
        stats = engine.team_stats(team.team_id)
        assert f"{team.name}: {stats['yellow_cards']} yellow, {stats['red_cards']} red." in answer.answer
    assert "balanced game" not in answer.answer and "It's " not in answer.answer  # not the match summary
    assert [k.value for k in answer.key_numbers] == [str(yellow), str(len(red))]


def test_a_question_about_one_team_answers_for_that_team(played):
    engine = played.engine
    team = engine.meta.away
    stats = engine.team_stats(team.team_id)
    answer = run(ask(team_for(ScriptedClient()), fresh_session(played),
                     f"How many yellow cards did {team.name} get?", FanProfile()))
    assert answer.intent == "discipline" and answer.fact_check.status == FactCheckStatus.VERIFIED
    assert answer.answer.startswith(f"{team.name} have {stats['yellow_cards']} yellow card")
    assert engine.meta.home.name not in answer.answer.split("Sent off")[0]


def test_a_followed_club_does_not_narrow_a_match_wide_card_question(played):
    engine = played.engine
    profile = FanProfile(club_id=engine.meta.home.team_id)
    answer = run(ask(team_for(ScriptedClient()), fresh_session(played), "how many cards in the match?", profile))
    assert engine.meta.away.name in answer.answer and engine.meta.home.name in answer.answer


def test_fouls_are_counted_from_team_stats(played):
    engine = played.engine
    home, away = engine.meta.home, engine.meta.away
    answer = run(ask(team_for(ScriptedClient()), fresh_session(played), "how many fouls?", FanProfile()))
    assert f"Fouls: {home.name} {engine.team_stats(home.team_id)['fouls']}, {away.name} " \
           f"{engine.team_stats(away.team_id)['fouls']}." in answer.answer
    assert "yellow" not in answer.answer


@pytest.mark.parametrize("language", [Language.ES, Language.HI])
def test_the_data_answer_is_translated(played, language):
    answer = run(ask(team_for(ScriptedClient()), fresh_session(played), "how many yellow cards?",
                     FanProfile(language=language)))
    assert answer.intent == "discipline" and "yellow" not in answer.answer and "Sent off" not in answer.answer


def test_no_cards_says_so(demo_match):
    from matchlens.interpret.engine import MatchEngine

    engine = MatchEngine(demo_match.meta)
    pack = pundit_pack(engine, {}, "discipline", None, None, "any cards?")
    assert template(pack, "en").answer == "No cards have been shown yet."


# ------------------------------------------------------------- the fact-checker still blocks a wrong count


def tampered(played, answer, claims=()):
    def say(draft, payload):
        return draft.model_copy(update={"answer": answer, "claims": list(claims)})

    return run(ask(team_for(ScriptedClient(tamper={"pundit": say})), fresh_session(played),
                   "how many yellow cards in the match?", FanProfile()))


def test_a_wrong_match_total_is_blocked_and_the_right_one_passes(played):
    yellow, red = counts(played.engine)
    wrong = tampered(played, f"There have been {yellow + 2} yellow cards.")
    assert wrong.fact_check.status == FactCheckStatus.BLOCKED and wrong.source == "data"
    assert any("Unclaimed statistic" in a for a in wrong.fact_check.actions)
    assert f"{yellow} yellow cards" in wrong.answer  # the data answer is shown instead
    right = tampered(played, f"There have been {yellow} yellow cards and {len(red)} red card.")
    assert right.fact_check.status == FactCheckStatus.VERIFIED and right.agents[-1] == "fact_checker"


def test_a_wrong_team_count_is_blocked(played):
    engine = played.engine
    team = engine.meta.home
    actual = engine.team_stats(team.team_id)["yellow_cards"]

    def claim(value):
        return AgentClaim(kind="team_stat", text=f"{value} yellow cards", subject_id=team.team_id,
                          stat="yellow_cards", value=float(value), event_id=None, player_id=None, home=None, away=None)

    ok = tampered(played, f"{team.name} have {actual} yellow cards.", [claim(actual)])
    assert ok.fact_check.status == FactCheckStatus.VERIFIED
    bad = tampered(played, f"{team.name} have {actual + 1} yellow cards.", [claim(actual + 1)])
    assert bad.fact_check.status == FactCheckStatus.BLOCKED
    unsupported = tampered(played, f"{team.name} have {actual + 1} yellow cards.")
    assert unsupported.fact_check.status == FactCheckStatus.BLOCKED


def test_a_claim_without_a_subject_is_still_malformed(played):
    claim = AgentClaim(kind="team_stat", text="0 yellow cards", subject_id=None, stat="yellow_cards", value=0.0,
                       event_id=None, player_id=None, home=None, away=None)
    blocked = tampered(played, "There were no yellow cards.", [claim])
    assert blocked.fact_check.status == FactCheckStatus.BLOCKED
    assert any("Malformed claim" in a for a in blocked.fact_check.actions)


def test_an_invented_card_event_is_blocked(played):
    claim = AgentClaim(kind="event", text="a card", subject_id=None, stat=None, value=None,
                       event_id="card:no-such-event", player_id=None, home=None, away=None)
    blocked = tampered(played, "A card was shown.", [claim])
    assert blocked.fact_check.status == FactCheckStatus.BLOCKED


# ------------------------------------------------------------------ summary prose


def test_turnovers_agree_with_the_number():
    assert _count(1, "turnover", "en") == "1 turnover"
    assert _count(1.0, "turnover", "en") == "1 turnover"
    assert _count(1.4, "turnover", "en") == "1.4 turnovers"
    assert _count(0, "red", "en") == "0 red cards" and _count(1, "red", "en") == "1 red card"
    assert _count(1, "turnover", "es") == "1 pérdida"


@pytest.mark.parametrize("tpm", [1, 1.4])
def test_the_tempo_line_does_not_contradict_the_momentum_line(played, tpm):
    engine = played.engine
    pack = pundit_pack(engine, {}, "summary", None, None, "what happened")
    pack["momentum"]["now"] = 60.0  # one side clearly on top
    pack["momentum"]["rhythm"] = {"state": "balanced", "turnovers_per_min": tpm, "controlling_team": None}
    answer = template(pack, "en")
    text = " ".join([answer.answer, *answer.points])
    assert "are on top" in text and "balanced game" not in text and "no side in full control" not in text
    assert f"{_count(tpm, 'turnover', 'en')} a minute" in text


# ------------------------------------------------------------- cached answers: versioned, and no cached failures


def _cached_session(played, run_id="pundit-cache"):
    from matchlens.runtime.session import MatchSession
    from matchlens.store.base import MemoryStore
    from matchlens.store.repository import Repository
    from matchlens.store.writer import WriteBehind

    store = MemoryStore()
    run(store.open())
    repo = Repository(store, WriteBehind(store))
    session = MatchSession(played.adapter, repo.recorder(run_id))
    session.engine = played.engine
    return session, repo


def _live(client):
    team = team_for(client)
    team.label = "live-model"  # anything but "offline-templates"
    return team


def _stored(session, repo, key):
    run(repo.flush())
    return run(session.recorder.fan_cached(key))


def test_an_answer_cached_under_an_older_version_is_not_served(played):
    import hashlib
    import json

    from matchlens.agents import pundit

    session, repo = _cached_session(played)
    engine, question, profile = played.engine, "how many yellow cards in the match?", FanProfile()
    old = [int(engine.c.elapsed // 60), len(engine.signals()), pundit._norm(question), profile.model_dump_json()]
    old_key = hashlib.sha256(json.dumps(old).encode()).hexdigest()[:32]
    stale = run(ask(team_for(ScriptedClient()), session, "who is winning?", profile)).model_dump(mode="json")
    stale["answer"] = "STALE ANSWER FROM BEFORE THE FIX"
    session.recorder.fan_output(old_key, "pundit", stale)
    run(repo.flush())
    assert run(session.recorder.fan_cached(old_key)) is not None  # the old entry really is in the store
    assert pundit._key(engine, question, profile) != old_key
    fresh = run(ask(team_for(ScriptedClient()), session, question, profile))
    assert "STALE" not in fresh.answer and fresh.intent == "discipline"


def test_a_blocked_live_answer_is_not_stored_but_a_verified_one_is(played):
    from matchlens.agents.pundit import _key

    yellow, _ = counts(played.engine)
    question, profile = "how many yellow cards in the match?", FanProfile()
    key = _key(played.engine, question, profile)

    session, repo = _cached_session(played, "blocked")
    wrong = lambda draft, payload: draft.model_copy(  # noqa: E731
        update={"answer": f"There have been {yellow + 2} yellow cards.", "claims": []})
    blocked = run(ask(_live(ScriptedClient(tamper={"pundit": wrong})), session, question, profile))
    assert blocked.source == "data" and blocked.fact_check.status == FactCheckStatus.BLOCKED
    assert _stored(session, repo, key) is None  # a retry may do better

    failed_session, failed_repo = _cached_session(played, "failed")
    failed = run(ask(_live(ScriptedClient(fail={"pundit"})), failed_session, question, profile))
    assert failed.source == "data" and _stored(failed_session, failed_repo, key) is None

    ok_session, ok_repo = _cached_session(played, "ok")
    client = ScriptedClient()
    good = run(ask(_live(client), ok_session, question, profile))
    assert good.source == "agent" and good.fact_check.status == FactCheckStatus.VERIFIED
    assert _stored(ok_session, ok_repo, key) is not None
    calls = len(client.calls)
    assert run(ask(_live(client), ok_session, question, profile)) == good and len(client.calls) == calls


def test_offline_template_answers_stay_cached(played):
    from matchlens.agents.pundit import _key

    session, repo = _cached_session(played, "offline")
    question, profile = "how many yellow cards in the match?", FanProfile()
    answer = run(ask(team_for(ScriptedClient()), session, question, profile))
    assert answer.source == "data"
    assert _stored(session, repo, _key(played.engine, question, profile)) is not None


def test_fan_service_keys_carry_the_output_version(played):
    from matchlens.agents import services

    fans = services.FanServices(team_for(ScriptedClient()))
    session, _ = _cached_session(played, "svc")
    key = fans._key(session, "recap", "p")
    services.OUTPUT_VERSION += 1
    try:
        assert fans._key(session, "recap", "p") != key
    finally:
        services.OUTPUT_VERSION -= 1
