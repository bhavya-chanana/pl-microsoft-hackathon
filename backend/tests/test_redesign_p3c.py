"""P3-c: the tactics board says how many ideas were asked for and why fewer are shown."""

from __future__ import annotations


import pytest
from matchlens.agents import tactician
from matchlens.tactics.board import _shortfall

from .test_tactics_api import body, client, session, wait_done  # noqa: F401


def test_shortfall_names_a_timeout_in_plain_words():
    errors = ["positional: timeout after 20s", "wide: no time left to ask"]
    text = _shortfall(errors, 2, 4)
    assert text == "2 of 4 ideas ready: the AI ran out of time, so fewer ideas were tested."
    assert not {"ideate", "persona", "recipe"} & set(text.lower().replace(":", " ").split())


@pytest.mark.parametrize("errors,fragment", [
    (["deadline reached after 30s; showing what was ready"], "ran out of time"),
    (["Overload: rejected (no space)"], "did not pass the checks"),
    (["ideate: RuntimeError: boom"], "could not be reached"),
    ([], "not enough distinct ideas"),
])
def test_shortfall_reasons(errors, fragment):
    assert fragment in _shortfall(errors, 1, 3)


def test_no_shortfall_when_the_board_is_full():
    assert _shortfall(["gaffer: x"], 3, 3) is None
    assert _shortfall([], 5, 3) is None


def test_board_reports_requested_and_shortfall(client, session):  # noqa: F811
    req = client.post("/api/tactics/boards", json=body(session, n=3))
    board, _ = wait_done(client, req.json()["board_id"])
    ready = [t for t in board["tactics"] if t["status"] == "ready"]
    assert board["requested"] == 3
    assert (board["shortfall"] is None) == (len(ready) >= 3)
    req = client.post("/api/tactics/boards", json=body(session, n=5))
    board, _ = wait_done(client, req.json()["board_id"])
    ready = [t for t in board["tactics"] if t["status"] == "ready"]
    assert board["requested"] == 5
    if len(ready) < 5:
        assert board["shortfall"].startswith(f"{len(ready)} of 5 ideas ready")
    else:
        assert board["shortfall"] is None


def test_board_names_the_timeout_when_the_ai_ideas_are_missing(client, session, monkeypatch):  # noqa: F811
    async def timed_out(*args, **kwargs):
        return [], ["positional: timeout after 20s", "wide: no time left to ask"]

    monkeypatch.setattr(tactician, "ideate", timed_out)
    req = client.post("/api/tactics/boards", json=body(session, n=5))
    board, _ = wait_done(client, req.json()["board_id"])
    ready = [t for t in board["tactics"] if t["status"] == "ready"]
    assert board["requested"] == 5 and len(ready) < 5
    assert board["shortfall"] == f"{len(ready)} of 5 ideas ready: the AI ran out of time, so fewer ideas were tested."
