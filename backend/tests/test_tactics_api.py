"""Tactics Board API: freeze a moment of a match, stream tactics, rank them, replay the branches (offline)."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from matchlens.agents.llm import build_clients
from matchlens.api import tactics_routes
from matchlens.ingest.adapters import ReplayControl, SyntheticReplayAdapter
from matchlens.runtime.manager import manager
from matchlens.runtime.session import MatchSession
from matchlens.tactics.board import BoardService, Source, situation_from_session

AT = 3780.0  # the 64th minute of the second half


@pytest.fixture(scope="module")
def session(demo_match):
    s = MatchSession(SyntheticReplayAdapter(demo_match, ReplayControl(speed=1000)))
    events = [e for e in demo_match.events if e.elapsed <= AT]
    frames = [f for f in demo_match.tracking if f.elapsed <= AT]
    s.hydrate(events, frames)
    return s


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setenv("MATCHLENS_TACTICS_K", "8")
    monkeypatch.setenv("MATCHLENS_TACTICS_PER_MINUTE", "100")
    monkeypatch.setattr(manager, "session", session)
    monkeypatch.setattr(tactics_routes, "service", BoardService(build_clients()))
    tactics_routes.reset_meter()
    app = FastAPI()
    app.include_router(tactics_routes.router)
    with TestClient(app) as c:
        yield c


def body(session, **over):
    team = session.meta.home.team_id
    req = {"source": {"kind": "elapsed", "elapsed": AT}, "team_id": team, "n": 3, "horizon_s": 10,
           "risk": "balanced", "language": "en"}
    return req | over


def wait_done(client, board_id, limit=40.0):
    start = time.monotonic()
    seen = []
    while time.monotonic() - start < limit:
        board = client.get(f"/api/tactics/boards/{board_id}").json()
        seen.append(board)
        if board["status"] in ("done", "failed"):
            return board, seen
        time.sleep(0.05)
    raise AssertionError("board did not finish")


def test_situation_from_session(session):
    sit, snap = situation_from_session(session, Source(kind="elapsed", elapsed=AT), session.meta.home.team_id)
    assert sit.minute_label.startswith("6") and sit.period == 2
    assert sit.attacking_right == {session.meta.home.team_id: True, session.meta.away.team_id: False}
    assert sit.phase in ("build_up", "progression", "final_third", "transition", "set_piece", "defending")
    assert sit.carrier_id in {p.player_id for p in sit.players}
    assert sum(sit.score.values()) == sum(session.engine.score().values())
    assert len(sit.players) == 22 and snap.elapsed == sit.elapsed


def test_board_lifecycle(client, session):
    r = client.post("/api/tactics/boards", json=body(session, prompt="we need a goal"))
    assert r.status_code == 202
    created = r.json()
    assert set(created) == {"board_id", "situation"}
    first = client.get(f"/api/tactics/boards/{created['board_id']}").json()
    assert first["status"] in ("thinking", "evaluating")
    board, seen = wait_done(client, created["board_id"])
    assert board["status"] == "done", board["errors"]
    assert any(b["tactics"] for b in seen[:-1]) or len(board["tactics"]) >= 3  # cards stream in
    assert board["baseline"] and board["baseline"]["rollouts"] == 8
    ready = [t for t in board["tactics"] if t["status"] == "ready"]
    assert len(ready) == 3
    assert sorted(t["rank"] for t in ready) == [1, 2, 3]
    assert len({t["intent"]["mechanism"] for t in ready}) >= 3
    assert board["verdict"] and board["verdict"]["headline"]
    assert board["verdict"]["recommended_tactic_id"] in {t["tactic_id"] for t in ready}
    assert set(ready[0]["lift"]) >= {"p_shot", "mean_xg", "p_box_entry", "p_turnover", "p_concede_shot", "xt_delta"}
    assert board["evaluator"] and board["model_label"] and board["recorded"] is False
    assert isinstance(board["situation"]["ball"], list)
    allowed = {"goal", "shot", "box_entry", "retained", "turnover", "blocked"}
    assert set(board["baseline"]["terminals"]) <= allowed
    assert sum(board["baseline"]["terminals"].values()) == 8


def test_rollouts(client, session):
    board_id = client.post("/api/tactics/boards", json=body(session)).json()["board_id"]
    board, _ = wait_done(client, board_id)
    ids = ["baseline", board["tactics"][0]["tactic_id"]]
    players = {p["player_id"] for p in board["situation"]["players"]}
    for tid in ids:
        for variant in ("typical", "best", "worst"):
            r = client.get(f"/api/tactics/boards/{board_id}/rollouts/{tid}", params={"variant": variant})
            assert r.status_code == 200, r.text
            view = r.json()
            assert view["tactic_id"] == tid and view["variant"] == variant
            assert len(view["frames"]) >= 2 and view["terminal"] in {
                "goal", "shot", "box_entry", "retained", "turnover", "blocked"}
            dt = view["frames"][1]["elapsed"] - view["frames"][0]["elapsed"]
            assert dt == pytest.approx(0.2, abs=0.02)  # 5 Hz
            assert {e["entity_id"] for e in view["frames"][0]["entities"]} <= players | {"ball"}
            assert all(board["situation"]["elapsed"] <= e["elapsed"] <= view["frames"][-1]["elapsed"] + 0.5
                       for e in view["events"])
    assert client.get(f"/api/tactics/boards/{board_id}/rollouts/baseline", params={"variant": "x"}).status_code == 422
    assert client.get(f"/api/tactics/boards/{board_id}/rollouts/t-nope").status_code == 404


def test_deterministic(client, session):
    ranks = []
    for _ in range(2):
        bid = client.post("/api/tactics/boards", json=body(session)).json()["board_id"]
        board, _ = wait_done(client, bid)
        ranks.append([(t["tactic_id"], t["rank"], t["utility"]) for t in board["tactics"] if t["status"] == "ready"])
    assert ranks[0] == ranks[1]


def test_simulate(client, session):
    bid = client.post("/api/tactics/boards", json=body(session)).json()["board_id"]
    board, _ = wait_done(client, bid)
    intent = board["tactics"][0]["intent"]
    r = client.post("/api/tactics/simulate", json={"intent": intent, "source": {"kind": "elapsed", "elapsed": AT},
                                                   "horizon_s": 8, "k": 6})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["status"] == "ready" and result["outcome"]["rollouts"] == 6
    src = {"kind": "elapsed", "elapsed": AT}
    directive = {"player_id": "NOBODY", "action": "run_to", "zone": "right:att", "target_player_id": None,
                 "start_s": 0, "end_s": 4}
    r = client.post("/api/tactics/simulate", json={"intent": dict(intent, directives=[directive]), "source": src})
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["problems"]
    r = client.post("/api/tactics/simulate", json={"intent": dict(intent, team_id="NOPE"), "source": src})
    assert r.status_code == 422


def test_errors(client, session, monkeypatch):
    assert client.get("/api/tactics/boards/nope").status_code == 404
    assert client.post("/api/tactics/boards", json=body(session, team_id="ZZZ")).status_code == 422
    assert client.post("/api/tactics/boards", json=body(session, n=9)).status_code == 422
    assert client.post("/api/tactics/boards", json=body(session, horizon_s=30)).status_code == 422
    assert client.post("/api/tactics/boards", json=body(session, prompt="x" * 401)).status_code == 422
    assert client.post("/api/tactics/boards", json=body(session, risk="wild")).status_code == 422
    assert client.post("/api/tactics/boards", json=body(session, source={"kind": "moment"})).status_code == 422
    assert client.post("/api/tactics/boards",
                       json=body(session, source={"kind": "moment", "insight_id": "nope"})).status_code == 404
    assert client.post("/api/tactics/boards",
                       json=body(session, source={"kind": "elapsed", "elapsed": 9999})).status_code == 404
    monkeypatch.setattr(manager, "session", None)
    assert client.post("/api/tactics/boards", json=body(session)).status_code == 404


def test_rate_cap(client, session, monkeypatch):
    monkeypatch.setenv("MATCHLENS_TACTICS_PER_MINUTE", "1")
    tactics_routes.reset_meter()
    assert client.post("/api/tactics/boards", json=body(session)).status_code == 202
    assert client.post("/api/tactics/boards", json=body(session)).status_code == 429


def test_lru(session, monkeypatch):
    monkeypatch.setenv("MATCHLENS_TACTICS_K", "4")
    import asyncio

    async def main():
        svc = BoardService(build_clients(), max_boards=2)
        from matchlens.tactics.board import BoardRequest

        ids = [(await svc.create(session, BoardRequest(**body(session)))).board_id for _ in range(3)]
        assert svc.get(ids[0]) is None and svc.get(ids[1]) and svc.get(ids[2])
        await svc.close()

    asyncio.run(main())
