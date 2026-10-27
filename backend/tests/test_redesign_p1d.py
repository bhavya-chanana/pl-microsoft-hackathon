"""P1-d: a tactic is recommended only when it clearly beats the baseline."""

from __future__ import annotations

import asyncio

import pytest

from matchlens.agents.tactician import (
    VerdictDraft,
    check_verdict,
    offline_verdict,
    verdict,
    verdict_payload,
)
from matchlens.tactics.board import _fallback_verdict
from matchlens.tactics.evaluate import clear_gain, clear_winner
from matchlens.tactics.models import Interval

from .test_agents import ScriptedClient
from .test_tactician import (  # noqa: F401
    fast_stagger,
    meta,
    offline_clients,
    outcome,
    ranked_pair,
    result,
    situation,
)


def _noisy(team):
    """Top-ranked idea whose ranges overlap the baseline's (the default +-0.1 intervals)."""
    ranked, _ = ranked_pair(team)
    base = outcome(p_shot=0.30, xg=0.11, concede=0.2)
    return ranked, base


def _clear_second(team):
    """Rank 1 is within noise; rank 2 clearly lowers the chance of conceding a shot."""
    ranked, base = _noisy(team)
    noisy, other = ranked
    better = outcome(p_shot=0.31, xg=0.12, concede=0.2).model_copy(
        update={"p_concede_shot": Interval(value=0.05, low=0.02, high=0.08)})
    other = other.model_copy(update={"outcome": better})
    return [noisy, other], base


def test_clear_gain_rules():
    base = outcome(p_shot=0.30, concede=0.2)
    assert not clear_gain(outcome(p_shot=0.42, concede=0.15), base)  # both ranges overlap
    assert clear_gain(outcome(p_shot=0.60, concede=0.2), base)  # low 0.5 > base high 0.4
    low = outcome(p_shot=0.30, concede=0.2).model_copy(update={"p_concede_shot": Interval(value=0.05, low=0.02, high=0.08)})
    assert clear_gain(low, base)  # high 0.08 < base low 0.1
    assert clear_winner([], base) is None


@pytest.mark.parametrize("lang", ["en", "es", "hi"])
def test_offline_verdict_within_noise_recommends_nothing(meta, situation, lang):
    ranked, base = _noisy(situation.coaching_team_id)
    payload = verdict_payload(situation, ranked, base, lang, meta=meta)
    assert payload["clear_winner_id"] is None
    draft = offline_verdict(payload)
    assert draft.recommended_tactic_id is None
    assert draft.headline == {"en": "No clear winner", "es": "Sin ganador claro", "hi": "कोई स्पष्ट विजेता नहीं"}[lang]
    assert check_verdict(draft, payload) == []
    assert "42" in draft.text and "30" in draft.text  # the top idea's margin, from the payload numbers


def test_fallback_verdict_within_noise_recommends_nothing(situation):
    ranked, base = _noisy(situation.coaching_team_id)
    out = _fallback_verdict(ranked, base, "en")
    assert out.recommended_tactic_id is None and out.headline == "No clear winner"


def test_model_draft_recommending_rank_one_is_rejected_within_noise(meta, situation):
    ranked, base = _noisy(situation.coaching_team_id)

    def pick_first(draft, payload):
        return VerdictDraft(headline="Overload", recommended_tactic_id=payload["ranked"][0]["tactic_id"],
                            cited_metrics=["p_shot"], text="Overload the left lifts shot chance to 42% from 30%.")

    problems: list[str] = []
    out = run(verdict(offline_clients(ScriptedClient(tamper={"tactics_verdict": pick_first})), situation, ranked,
                      base, "en", meta=meta, errors=problems))
    assert out.recommended_tactic_id is None and out.headline == "No clear winner"
    assert problems and "clear_winner_id" in problems[0]


def test_clear_winner_ranked_second_is_the_one_recommended(meta, situation):
    ranked, base = _clear_second(situation.coaching_team_id)
    payload = verdict_payload(situation, ranked, base, "en", meta=meta)
    assert payload["clear_winner_id"] == ranked[1].tactic_id
    assert offline_verdict(payload).recommended_tactic_id == ranked[1].tactic_id
    assert _fallback_verdict(ranked, base, "en").recommended_tactic_id == ranked[1].tactic_id
    assert check_verdict(VerdictDraft(headline="h", text="Fine.", recommended_tactic_id=ranked[0].tactic_id,
                                      cited_metrics=[]), payload)


def run(coro):
    return asyncio.run(coro)
