"""The next10 API: absent unless MATCHLENS_NEXT10=1; endpoints over an in-memory repo, and over the emulator when
MATCHLENS_NEXT10_TEST_ENDPOINT is set. Fictional clubs only."""

import asyncio
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from matchlens.api import next10_routes
from matchlens.next10.hub import hub
from matchlens.next10.repo import CosmosRepo, MemoryRepo
from matchlens.store import base
from matchlens.store.next10 import Next10Settings, Next10Store

MID = "fotmob:900001"


def seed_docs(repo: MemoryRepo | None = None) -> dict[str, list[dict]]:
    side = {"home": {"name": "Westmoor", "fotmob_id": 1}, "away": {"name": "Eastbrook", "fotmob_id": 2}}
    docs: dict[str, list[dict]] = {
        "matches": [
            {"id": MID, "match_id": MID, "source": "fotmob", **side, "competition": "Lab League",
             "kickoff_utc": "2026-10-27T19:00:00Z", "status": "finished", "score": {"home": 2, "away": 1}},
            {"id": "fotmob:900002", "match_id": "fotmob:900002", "source": "fotmob", **side, "competition": "Lab League",
             "kickoff_utc": "2026-11-03T19:00:00Z", "status": "scheduled", "score": {"home": 0, "away": 0}},
        ],
        "predictions": [
            {"id": f"{MID}:{team}:{minute}", "match_id": MID, "kind": "shadow_event", "team_side": team,
             "team": "Westmoor" if team == "home" else "Eastbrook", "minute": minute,
             "t_issued": f"2026-10-27T19:{minute:02d}:00Z", "probs": {"shot_1m": 0.1, "shot_5m": 0.3, "goal_5m": 0.04},
             "model_version": "t", "features": {}}
            for minute in (10, 5) for team in ("home", "away")
        ] + [{"id": f"score:{MID}", "match_id": MID, "kind": "score", "scored_at": "2026-10-27T21:00:00Z",
              "heads": {"shot_1m": {"n": 180, "base_rate": 0.1, "brier": 0.09, "logloss": 0.3,
                                    "brier_baseline": 0.1, "brier_persistence": 0.12, "logloss_baseline": 0.33}}}],
        "frames": [{"id": f"{MID}:{t * 200}", "match_id": MID, "t_s": t / 5, "match_clock_s": None,
                    "entities": [{"id": "ball", "team": "ball", "x": 0.0, "y": 0.0, "observed": True, "conf": 0.9}],
                    "calibrated": True} for t in range(100)],
        "moments": [{"id": "m2", "match_id": MID, "kind": "chance", "t_s": 40.0, "embedding": [0.1] * 64},
                    {"id": "m1", "match_id": MID, "kind": "goal_conceded", "t_s": 10.0, "embedding": [0.1] * 64}],
        "video_jobs": [{"id": "j1", "job_id": "j1", "match_id": MID, "status": "done"}],
    }
    if repo is not None:
        for name, rows in docs.items():
            repo.put(name, *rows)
    return docs


@pytest.fixture
def client(monkeypatch):
    repo = MemoryRepo()
    seed_docs(repo)
    monkeypatch.setattr(next10_routes.state, "repo", repo)
    app = FastAPI()
    app.include_router(next10_routes.router)
    return TestClient(app)


def test_router_is_absent_unless_flagged(monkeypatch):
    monkeypatch.delenv("MATCHLENS_NEXT10", raising=False)
    off = FastAPI()
    assert next10_routes.mount(off) is False
    assert not [p for p in off.openapi()["paths"] if p.startswith("/api/next10")]
    monkeypatch.setenv("MATCHLENS_NEXT10", "1")
    on = FastAPI()
    assert next10_routes.mount(on) is True
    assert "/api/next10/matches" in on.openapi()["paths"]


@pytest.mark.skipif(os.getenv("MATCHLENS_NEXT10") == "1", reason="the flag is set in this environment")
def test_deployed_app_has_no_next10_routes():
    from matchlens.api.app import app

    assert not [p for p in app.openapi()["paths"] if p.startswith("/api/next10")]


def test_matches_newest_first(client):
    rows = client.get("/api/next10/matches").json()
    assert [m["id"] for m in rows] == ["fotmob:900002", MID]


def test_match_with_score_and_unknown(client):
    body = client.get(f"/api/next10/matches/{MID}").json()
    assert body["match"]["home"]["name"] == "Westmoor"
    assert body["score"]["heads"]["shot_1m"]["n"] == 180
    assert client.get("/api/next10/matches/fotmob%3A900002").json()["score"] is None
    assert client.get("/api/next10/matches/nope").status_code == 404


def test_predictions_ordered_and_filtered(client):
    rows = client.get(f"/api/next10/matches/{MID}/predictions").json()
    assert [r["minute"] for r in rows] == [5, 5, 10, 10]
    assert all(r["kind"] == "shadow_event" for r in rows)  # the score document is not a prediction
    home = client.get(f"/api/next10/matches/{MID}/predictions", params={"team": "home", "kind": "shadow_event"}).json()
    assert [(r["team_side"], r["minute"]) for r in home] == [("home", 5), ("home", 10)]
    assert len(client.get(f"/api/next10/matches/{MID}/predictions", params={"team": "Eastbrook"}).json()) == 2


def test_last_and_clock_window(client):
    repo = next10_routes.state.repo
    repo.put("predictions", *[{"id": f"n{t}", "match_id": MID, "kind": "next10", "team_side": "home", "t_s": float(t),
                               "t_issued": f"2026-10-27T20:00:{t:02d}Z", "probs": {}} for t in range(30)])
    base = f"/api/next10/matches/{MID}/predictions"
    window = client.get(base, params={"kind": "next10", "from_s": 10, "to_s": 12}).json()
    assert [p["t_s"] for p in window] == [10.0, 11.0, 12.0]
    assert [p["id"] for p in client.get(base, params={"kind": "next10", "last": 1}).json()] == ["n29"]
    # shadow documents have a minute and no t_s: the window is on minute * 60
    shadow = client.get(base, params={"kind": "shadow_event", "from_s": 400, "to_s": 700}).json()
    assert {p["minute"] for p in shadow} == {10}
    assert [f["t_s"] for f in client.get(f"/api/next10/matches/{MID}/frames", params={"last": 1}).json()] == [19.8]


def test_frames_window_and_moments_and_jobs(client):
    frames = client.get(f"/api/next10/matches/{MID}/frames", params={"from_s": 2, "to_s": 3}).json()
    assert [f["t_s"] for f in frames] == [2.0, 2.2, 2.4, 2.6, 2.8, 3.0]
    assert client.get(f"/api/next10/matches/{MID}/frames", params={"from_s": 3, "to_s": 2}).status_code == 422
    moments = client.get(f"/api/next10/matches/{MID}/moments").json()
    assert [m["id"] for m in moments] == ["m1", "m2"] and "embedding" not in moments[0]
    assert [m["id"] for m in client.get(f"/api/next10/matches/{MID}/moments", params={"to_s": 20}).json()] == ["m1"]
    assert client.get(f"/api/next10/matches/{MID}/jobs").json()[0]["status"] == "done"


def test_verify_lists_scores(client):
    rows = client.get("/api/next10/verify").json()
    assert [r["id"] for r in rows] == [f"score:{MID}"]


def test_store_down_answers_503(monkeypatch):
    monkeypatch.setattr(next10_routes.state, "repo", None)
    app = FastAPI()
    app.include_router(next10_routes.router)
    assert TestClient(app).get("/api/next10/matches").status_code == 503


def test_live_socket_hub_and_poll(client, monkeypatch):
    monkeypatch.setattr(next10_routes, "POLL_S", 0.2)
    repo = next10_routes.state.repo
    with client.websocket_connect(f"/api/next10/live/{MID}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        first = {ws.receive_json()["doc"]["team_side"] for _ in range(2)}  # the latest prediction of each side
        assert first == {"home", "away"}
        hub.publish(MID, "frame", {"id": f"{MID}:1", "match_id": MID, "t_s": 500.0})
        msg = ws.receive_json()
        assert msg["type"] == "frame" and msg["doc"]["t_s"] == 500.0
        # a writer in another process reaches the socket through the store
        repo.put("predictions", {"id": "late", "match_id": MID, "kind": "shadow_event", "team_side": "home",
                                 "team": "Westmoor", "minute": 11, "t_issued": "2026-10-27T19:11:00Z", "probs": {}})
        msg = ws.receive_json()
        assert msg["type"] == "prediction" and msg["doc"]["id"] == "late"
        # the same document published on the hub is not sent twice
        hub.publish(MID, "prediction", {"id": "late", "match_id": MID})
        hub.publish(MID, "stats", {"id": "s", "live_ready": True})
        assert ws.receive_json()["type"] == "stats"


@pytest.mark.skipif(not os.getenv("MATCHLENS_NEXT10_TEST_ENDPOINT"), reason="needs the next10 Cosmos emulator")
def test_endpoints_against_the_emulator(monkeypatch):
    settings = Next10Settings(endpoint=os.environ["MATCHLENS_NEXT10_TEST_ENDPOINT"],
                              database=f"next10_test_{uuid.uuid4().hex[:8]}", key=base.EMULATOR_KEY)
    docs = seed_docs()

    async def run() -> None:
        async with Next10Store(settings) as store:
            try:
                for name, rows in docs.items():
                    await store.upsert(name, rows)
                monkeypatch.setattr(next10_routes.state, "repo", CosmosRepo(store))
                app = FastAPI()
                app.include_router(next10_routes.router)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                    get = c.get
                    assert [m["id"] for m in (await get("/api/next10/matches")).json()] == ["fotmob:900002", MID]
                    body = (await get(f"/api/next10/matches/{MID}")).json()
                    assert body["score"]["heads"]["shot_1m"]["n"] == 180 and "_etag" not in body["match"]
                    preds = (await get(f"/api/next10/matches/{MID}/predictions", params={"team": "home"})).json()
                    assert [p["minute"] for p in preds] == [5, 10]
                    frames = (await get(f"/api/next10/matches/{MID}/frames", params={"from_s": 2, "to_s": 3})).json()
                    assert [f["t_s"] for f in frames] == [2.0, 2.2, 2.4, 2.6, 2.8, 3.0]
                    moments = (await get(f"/api/next10/matches/{MID}/moments")).json()
                    assert [m["id"] for m in moments] == ["m1", "m2"] and "embedding" not in moments[0]
                    assert (await get(f"/api/next10/matches/{MID}/frames", params={"last": 1})).json()[0]["t_s"] == 19.8
                    shadow = (await get(f"/api/next10/matches/{MID}/predictions",
                                        params={"from_s": 400, "to_s": 700})).json()
                    assert {p["minute"] for p in shadow} == {10}
                    await store.upsert("predictions", [{"id": f"n{t}", "match_id": MID, "kind": "next10", "t_s": float(t),
                                                         "team_side": "home", "probs": {}} for t in range(5)])
                    last = (await get(f"/api/next10/matches/{MID}/predictions", params={"kind": "next10", "last": 1})).json()
                    assert [p["id"] for p in last] == ["n4"]
                    assert (await get(f"/api/next10/matches/{MID}/jobs")).json()[0]["id"] == "j1"
                    assert [r["id"] for r in (await get("/api/next10/verify")).json()] == [f"score:{MID}"]
                    assert (await get("/api/next10/matches/unknown")).status_code == 404
                    assert await CosmosRepo(store).latest_frame_s(MID) == pytest.approx(19.8)
            finally:
                await store._client.delete_database(settings.database)

    asyncio.run(run())
