"""P1-a card text and P1-b momentum swing fixes from the redesign review."""

from __future__ import annotations

import pytest

from matchlens.agents import templates
from matchlens.agents.context import signal_pack
from matchlens.agents.factchecker import check_text
from matchlens.ingest.adapters import SyntheticReplayAdapter
from matchlens.interpret.engine import _swing_towards
from matchlens.interpret.signals import SignalType
from matchlens.runtime.session import MatchSession

from .conftest import replay_order


@pytest.fixture(scope="module")
def played(demo_match):
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session


def _cards(engine):
    return [s for s in engine.signals() if s.type == SignalType.CARD]


# ------------------------------------------------------------------ P1-a


def test_yellow_card_signal_has_no_player_count(played):
    yellows = [s for s in _cards(played.engine) if s.facts["card"] == "yellow"]
    assert yellows
    for s in yellows:
        assert "players_on_pitch" not in s.facts
        assert s.facts["player_yellow_cards"] >= 1


def test_red_card_signal_keeps_player_count(played):
    reds = [s for s in _cards(played.engine) if s.facts["card"] in ("red", "second_yellow")]
    for s in reds:
        assert s.facts["players_on_pitch"] == 10


def _card_pack(played, card, **facts):
    sig = next(s for s in _cards(played.engine) if s.facts["card"] == "yellow")
    pack = signal_pack(played.engine, sig)
    pack["signal"] = {**pack["signal"], "facts": {"card": card, **facts}}
    return pack


@pytest.mark.parametrize("lang", ["en", "hi", "es"])
def test_yellow_text_never_mentions_players_on_pitch(played, lang):
    pack = _card_pack(played, "yellow", player_yellow_cards=1)
    head, body = templates.data_text(pack, lang)
    assert "pitch" not in body and "players" not in body and "{" not in body and "-" != body
    assert all(k.label != "On pitch" for k in templates.key_numbers(pack))


@pytest.mark.parametrize("card", ["red", "second_yellow"])
@pytest.mark.parametrize("lang", ["en", "hi", "es"])
def test_red_text_says_down_to_ten(played, card, lang):
    pack = _card_pack(played, card, players_on_pitch=10)
    _, body = templates.data_text(pack, lang)
    assert "10" in body
    if lang == "en":
        assert "down to 10 players" in body
    assert [k.value for k in templates.key_numbers(pack) if k.label == "On pitch"] == ["10"]


def test_old_recorded_card_phrase_still_renders(played):
    pack = _card_pack(played, "yellow", players_on_pitch=11)
    assert templates.PHRASES["card"]["en"] == templates.PHRASES["card_yellow"]["en"]
    templates.data_text(pack, "en")


# ------------------------------------------------------------------ P1-b


@pytest.mark.parametrize("then,now,h,a,expected", [
    (-70, -8.5, 3.0, 20.0, None),   # decayed into the Even band
    (68, 0.0, 0.0, 0.0, None),      # empty window: decay, not a swing
    (-40, 30.0, 30.0, 5.0, "home"),
    (60, -20.0, 5.0, 30.0, "away"),
    (68, 12.0, 20.0, 10.0, None),   # still the same side on top, only weaker
    (-40, 30.0, 0.0, 5.0, None),    # the side it swings to did nothing in the window
])
def test_swing_towards(then, now, h, a, expected):
    assert _swing_towards(then, now, h, a, 10.0, 55.0) == expected


def test_swings_in_demo_match_agree_with_the_bar(played):
    meta = played.engine.meta
    swings = [s for s in played.engine.signals() if s.type == SignalType.MOMENTUM_SWING]
    for s in swings:
        to = s.facts["to_index"]
        assert abs(to) >= 10
        assert s.team_id == (meta.home.team_id if to > 0 else meta.away.team_id)
        assert s.facts["strength"] == round(abs(to))


def test_swing_text_passes_fact_checker(played):
    swings = [s for s in played.engine.signals() if s.type == SignalType.MOMENTUM_SWING]
    if not swings:
        pytest.skip("no swing in this replay")
    for s in swings:
        pack = signal_pack(played.engine, s)
        for lang in ("en", "hi", "es"):
            head, body = templates.data_text(pack, lang)
            out = check_text(played.engine, pack, [head, body], [])
            assert not out.problems, (lang, body, out.problems)
        assert f"{s.facts['strength']}" in templates.data_text(pack, "en")[1]
