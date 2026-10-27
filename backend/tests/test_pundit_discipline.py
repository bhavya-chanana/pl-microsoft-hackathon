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


