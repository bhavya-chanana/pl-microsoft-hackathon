"""Tactics Board offline: a free-text brief steers which built-in recipes are shown."""

from __future__ import annotations

from matchlens.tactics import playbook
from matchlens.tactics.board import situation_from_session
from matchlens.tactics.brief import BRIEF_WORDS, match_brief, steer

from .test_tactics_api import AT, Source, body, client, session, wait_done  # noqa: F401 - fixtures


def test_the_brief_names_mechanisms():
    assert match_brief("press high") == ["press_trap"]
    assert match_brief("Protect the lead with a back five") == ["low_block"]
    assert match_brief("press press, then a corner") == ["press_trap", "set_piece"]
    assert match_brief("") == [] and match_brief(None) == [] and match_brief("make us happy") == []
    assert match_brief("compress the space") == []  # whole words only
    assert set(BRIEF_WORDS) <= {m.value for m in playbook.Mechanism}


def test_matched_recipes_come_first_and_no_brief_keeps_the_order(session):
    sit, _ = situation_from_session(session, Source(kind="elapsed", elapsed=AT), session.meta.home.team_id)
    recipes = playbook.recipes(sit, session.meta.home.team_id, session.meta)
    mechs = [r.mechanism.value for r in recipes]
    assert len(set(mechs)) >= 3
    last = mechs[-1]
    steered = steer(recipes, [last])
    assert steered[0].mechanism.value == last
    assert [r.title for r in steer(recipes, [])] == [r.title for r in recipes]


def _board(client, session, **over):
    created = client.post("/api/tactics/boards", json=body(session, **over))
    assert created.status_code == 202
    board, _ = wait_done(client, created.json()["board_id"])
    assert board["status"] == "done", board["errors"]
    return board


def test_offline_brief_is_honoured_and_said_so(client, session):
    sit, _ = situation_from_session(session, Source(kind="elapsed", elapsed=AT), session.meta.home.team_id)
    mechs = [r.mechanism.value for r in playbook.recipes(sit, session.meta.home.team_id, session.meta)]
    wanted = mechs[-1]
    prompt = f"please try {BRIEF_WORDS[wanted][0]}"
    board = _board(client, session, prompt=prompt, n=3)
    shown = [t["intent"]["mechanism"] for t in board["tactics"] if t["status"] == "ready"]
    assert wanted in shown and board["prompt_matched"] == [wanted]
    assert "built-in recipes" in board["prompt_note"] and "live model" in board["prompt_note"]
    assert len(shown) == 3


def test_offline_brief_that_matches_nothing_says_so(client, session):
    board = _board(client, session, prompt="make us happy", n=3)
    assert board["prompt_matched"] == [] and "No built-in recipe fits" in board["prompt_note"]


def test_no_brief_adds_nothing(client, session):
    board = _board(client, session, n=3)
    assert board["prompt_matched"] == [] and board["prompt_note"] is None


def test_offline_brief_for_an_idea_that_does_not_fit_the_moment(client, session):
    sit, _ = situation_from_session(session, Source(kind="elapsed", elapsed=AT), session.meta.home.team_id)
    available = {r.mechanism.value for r in playbook.recipes(sit, session.meta.home.team_id, session.meta)}
    missing = next(m for m in BRIEF_WORDS if m not in available)
    board = _board(client, session, prompt=BRIEF_WORDS[missing][0], n=3)
    assert board["prompt_matched"] == [] and "does not fit this moment" in board["prompt_note"]
