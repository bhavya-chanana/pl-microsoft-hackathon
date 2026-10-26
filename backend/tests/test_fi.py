"""Football Intelligence API: accounts, fusion, spoiler-safe reads and idempotent ingest."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from matchlens.fi.api import router, state
from matchlens.fi.simulator import ensure_seeded

GOAL_E05_MS = (19 * 60 + 29) * 1000  # Shaw, away
GOAL_E05_CONFIRMED_MS = (20 * 60 + 27) * 1000


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    path = tmp_path_factory.mktemp("fi") / "fi.sqlite"
    state.start(str(path), seed=False)
    ensure_seeded(state.db)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    assert c.post("/api/fi/auth/login", json={"username": "tester", "password": "secret1"}).status_code == 200
    return c


def test_sign_in_creates_then_checks_password(client: TestClient) -> None:
    fresh = TestClient(client.app)
    first = fresh.post("/api/fi/auth/login", json={"username": "newfan", "password": "pass1234"})
    assert first.json()["created"] is True
    assert fresh.post("/api/fi/auth/login", json={"username": "NEWFAN", "password": "pass1234"}).json()["created"] is False
    assert fresh.post("/api/fi/auth/login", json={"username": "newfan", "password": "wrong!!"}).status_code == 401
    assert TestClient(client.app).get("/api/fi/auth/me").status_code == 401


def test_preferences_round_trip(client: TestClient) -> None:
    prefs = {"persona": "tactical", "favoriteTeam": "NOR", "followedPlayers": ["h9"], "spoilersVisible": False,
             "language": "en", "onboarded": True}
    assert client.post("/api/fi/me/preferences", json=prefs).status_code == 200
    assert client.get("/api/fi/auth/me").json()["preferences"] == prefs


def test_goal_is_provisional_from_ball_sensor_then_confirmed_on_video(client: TestClient) -> None:
    def goal(t: int):
        view = client.get("/api/fi/matches/nor-har/view", params={"playhead_ms": t}).json()
        return next((e for e in view["events"] if e["id"] == "e05"), None), view["score"]

    assert goal(GOAL_E05_MS + 1000)[0] is None  # not known yet
    event, score = goal(GOAL_E05_MS + 3000)
    assert event["status"] == "provisional" and score == {"home": 0, "away": 1, "provisional": True}
    event, score = goal(GOAL_E05_CONFIRMED_MS)
    assert event["status"] == "confirmed" and not score["provisional"]


@pytest.mark.parametrize("playhead", [0, 600_000, 1_800_000, 2_700_000, 4_000_000])
def test_view_never_contains_anything_after_the_playhead(client: TestClient, playhead: int) -> None:
    view = client.get("/api/fi/matches/nor-har/view", params={"playhead_ms": playhead}).json()
    for e in view["events"]:
        assert e["atMs"] <= playhead
        assert all(r["availableMs"] <= playhead for r in e["revisions"])
    assert all(p["minute"] * 60_000 <= playhead for p in view["pulse"])
    stats = client.get("/api/fi/matches/nor-har/stats", params={"playhead_ms": playhead}).json()
    assert stats["throughMs"] <= playhead and stats["physicalThroughMs"] <= playhead
    assert all(s["availableMs"] <= playhead for s in stats["shots"])
    # Event ids from later in the match must not appear anywhere in the payloads.
    later = {e["id"] for e in client.get("/api/fi/matches/nor-har-r1/view", params={"mode": "full_review", "playhead_ms": 0}).json()["events"]
             if e["revisions"][0]["availableMs"] > playhead}
    blob = json.dumps(view) + json.dumps(stats)
    assert not any(f'"{eid}"' in blob for eid in later)


def test_roster_hides_substitution_times(client: TestClient) -> None:
    players = client.get("/api/fi/matches/nor-har").json()["players"]
    assert players and all("onFromMs" not in p and "offAtMs" not in p for p in players)


def test_full_review_only_for_finished_matches(client: TestClient) -> None:
    assert client.get("/api/fi/matches/nor-har/view", params={"playhead_ms": 0, "mode": "full_review"}).status_code == 400
    final = client.get("/api/fi/matches/nor-har-r1/view", params={"playhead_ms": 0, "mode": "full_review"}).json()
    assert final["score"] == {"home": 2, "away": 3, "provisional": False}


def test_physical_stats_come_from_vest_frames(client: TestClient) -> None:
    p = client.get("/api/fi/matches/nor-har/players/h11", params={"playhead_ms": 63 * 60_000}).json()
    assert p["stats"]["topSpeed"]["value"] == 34.6 and p["stats"]["topSpeed"]["source"] == "vest"
    assert 6 < p["stats"]["distance"]["value"] < 9
    assert p["heatmap"] and len(p["heatmap"]) == 4


def test_ingest_is_idempotent_and_overturn_changes_the_score(client: TestClient) -> None:
    obs = {"observations": [{"id": "var-e20", "kind": "var_overturn", "media_from_ms": 3_513_000, "media_to_ms": 3_600_000,
                             "available_ms": 3_600_000, "team": "home", "player_ids": ["h9"], "confidence": 0.97,
                             "description": "VAR: offside in build-up",
                             "event": {"id": "e20", "type": "goal", "team": "home", "player_ids": ["h9", "h11"], "critical": True}}]}
    headers = {"Idempotency-Key": "test:var-e20"}
    assert client.post("/api/fi/ingest/nor-har-r1/vision", json=obs, headers=headers).json()["accepted"] == 1
    assert client.post("/api/fi/ingest/nor-har-r1/vision", json=obs, headers=headers).json()["duplicate"] is True
    before = client.get("/api/fi/matches/nor-har-r1/view", params={"playhead_ms": 3_599_000}).json()
    after = client.get("/api/fi/matches/nor-har-r1/view", params={"playhead_ms": 3_600_000}).json()
    assert before["score"]["home"] == 2 and after["score"]["home"] == 1
    assert next(e for e in after["events"] if e["id"] == "e20")["status"] == "overturned"
