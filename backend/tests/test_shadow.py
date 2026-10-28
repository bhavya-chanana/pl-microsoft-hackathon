"""Shadow predictions: features see only the past, labels only the future, and scoring compares with baselines."""

import asyncio
import json

import numpy as np

from matchlens.predict import shadow
from matchlens.predict.shadow import (
    HEADS,
    Fixture,
    HazardModel,
    MatchState,
    Shot,
    expected_home,
    features,
    fit,
    fit_logistic,
    fixtures,
    fotmob_minute,
    fotmob_state,
    label,
    live_minute,
    persistence,
    prediction_docs,
    score_match,
)


def state() -> MatchState:
    return MatchState(shots=[Shot("home", 3, 0.10, False), Shot("away", 12, 0.40, True), Shot("home", 15, 0.05, False),
                             Shot("home", 20, 0.70, True)],
                      reds=[("away", 30)], strength_home=0.6)


def test_features_only_see_the_past():
    s = state()
    f = features(s, "home", 12)
    assert f["score_diff"] == -1 and f["trailing"] == 1.0
    assert f["shots10_for"] == 1 and f["shots10_against"] == 1  # minutes 3..12
    assert features(s, "home", 13)["shots10_for"] == 0  # minutes 4..13: the minute-3 shot has left the window
    assert f["shot_rate_for"] == 1 / 13
    assert abs(f["strength"] - 0.1) < 1e-12
    assert features(s, "away", 12)["strength"] == -features(s, "home", 12)["strength"]
    assert features(s, "home", 31)["reds_diff"] == -1.0 and features(s, "home", 29)["reds_diff"] == 0.0
    # Later shots never leak into earlier features.
    assert features(s, "home", 14) == features(MatchState(s.shots[:2], s.reds, 0.6), "home", 14)


def test_labels_only_see_the_future():
    s = state()
    assert label(s, "home", 14, "shot_1m") == 1  # floor minute 15
    assert label(s, "home", 15, "shot_1m") == 0  # the minute-15 shot is not in its own future
    assert label(s, "home", 15, "shot_5m") == 1 and label(s, "home", 15, "goal_5m") == 1  # goal at 20
    assert label(s, "home", 20, "goal_5m") == 0
    assert label(s, "away", 7, "goal_5m") == 1 and label(s, "away", 6, "goal_5m") == 0


def test_persistence_baseline_uses_the_rate_so_far():
    s = state()
    assert 0 < persistence(s, "home", 9, "shot_1m") < persistence(s, "home", 9, "shot_5m") < 1
    assert persistence(s, "away", 5, "shot_5m") < 0.01  # nothing yet: near zero, clipped above 0


def test_logistic_recovers_a_known_model():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(20000, 2))
    y = (rng.random(20000) < 1 / (1 + np.exp(-(-1.0 + 2.0 * x[:, 0] - 0.5 * x[:, 1])))).astype(float)
    w = fit_logistic(x, y, l2=0.1)
    assert np.allclose(w, [-1.0, 2.0, -0.5], atol=0.1)


def synthetic_matches(n: int, seed: int = 1) -> list[tuple[str, MatchState, int]]:
    """Fictional matches where the stronger side shoots more, so a model has something to find."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        strength = float(rng.uniform(0.2, 0.8))
        shots = [Shot(side, m, 0.1, bool(rng.random() < 0.1)) for m in range(93)
                 for side, rate in (("home", 0.07 + 0.2 * (strength - 0.5)), ("away", 0.07 - 0.2 * (strength - 0.5)))
                 if rng.random() < rate]
        out.append((f"fx-{i}", MatchState(shots=shots, strength_home=strength), 93))
    return out


def test_fit_beats_the_base_rate_and_round_trips(tmp_path):
    model, report = fit(synthetic_matches(300), source="synthetic")
    shot5 = report["heads"]["shot_5m"]
    assert shot5["auc"] > 0.6 and shot5["brier"] < shot5["brier_base"]
    model.save(tmp_path / "m.json")
    again = HazardModel.load(tmp_path / "m.json")
    f = features(state(), "home", 25)
    assert again.predict(f) == model.predict(f) and set(model.predict(f)) == set(HEADS)
    assert model.version.startswith(shadow.MODEL_FAMILY)


def details(finished: bool = False, live: str | None = "31'") -> dict:
    """A fictional FotMob matchDetails payload with the fields the shadow model reads."""
    return {
        "header": {"teams": [{"id": 1, "name": "Westmoor", "score": 1}, {"id": 2, "name": "Eastbrook", "score": 0}],
                   "status": {"started": True, "finished": finished, "liveTime": {"short": live} if live else None}},
        "content": {
            "shotmap": {"shots": [
                {"teamId": 1, "min": 5, "minAdded": None, "expectedGoals": 0.2, "eventType": "Miss"},
                {"teamId": 2, "min": 18, "minAdded": None, "expectedGoals": 0.1, "eventType": "AttemptSaved"},
                {"teamId": 1, "min": 45, "minAdded": 2, "expectedGoals": 0.5, "eventType": "Goal"},
                {"teamId": 2, "min": 50, "minAdded": None, "expectedGoals": 0.0, "eventType": "Goal", "isOwnGoal": True},
            ]},
            "matchFacts": {"events": {"events": [
                {"type": "Card", "card": "Yellow", "time": 10, "isHome": True},
                {"type": "Card", "card": "YellowRed", "time": 60, "overloadTime": None, "isHome": False},
            ]}},
        },
    }


def test_fotmob_minutes_and_state():
    assert fotmob_minute(1, None) == 0 and fotmob_minute(45, 2) == 46
    assert live_minute(details(live="31'")) == 30
    assert live_minute(details(live="45+3'")) == 47
    assert live_minute(details(live="HT")) is None
    assert live_minute(details(finished=True)) is None
    s = fotmob_state(details(), 0.55)
    assert [(x.team, x.minute, x.goal) for x in s.shots] == [("home", 4, False), ("away", 17, False), ("home", 46, True)]
    assert s.reds == [("away", 59)]


def test_fixtures_match_by_team_id_not_name():
    listing = {"leagues": [{"name": "Cup", "matches": [
        {"id": 11, "home": {"id": 8650, "name": "Liverpool"}, "away": {"id": 5, "name": "Northbay"},
         "status": {"utcTime": "2026-10-28T20:00:00Z"}},
        {"id": 12, "home": {"id": 777, "name": "Liverpool (W)"}, "away": {"id": 6, "name": "Southfield"},
         "status": {"utcTime": "2026-10-28T19:00:00Z"}},
        {"id": 13, "home": {"id": 8, "name": "Rivermouth"}, "away": {"id": 9773, "name": "FC Porto"},
         "status": {"utcTime": "2026-10-28T19:30:00Z"}},
    ]}]}
    assert [f.fotmob_id for f in fixtures(listing)] == [13, 11]  # by kick-off, women's team left out


def test_strength_from_elo_with_a_floor_for_unrated_clubs():
    elo = {"liverpool": 1900.0, "chelsea": 1850.0, "bournemouth": 1700.0}
    p, source = expected_home(elo, "Liverpool", "Chelsea")
    assert source == "clubelo" and 0.5 < p < 0.75
    p, source = expected_home(elo, "Académico Viseu", "Liverpool")
    assert source == "clubelo+floor" and p < 0.3  # 50 below the weakest rated club
    assert expected_home(None, "Liverpool", "Chelsea") == (None, "none")


def test_score_compares_with_both_baselines():
    model, _ = fit(synthetic_matches(120), source="synthetic")
    fx = Fixture(99, "Westmoor", "Eastbrook", 1, 2, "Cup", "2026-10-28T19:30:00Z")
    live = fotmob_state(details(), 0.55)
    preds = [d for m in range(0, 60) for d in prediction_docs(fx, MatchState(
        [s for s in live.shots if s.minute <= m], live.reds, 0.55), m, model)]
    assert all(d["id"] == f"fotmob:99:shadow:{d['team_side']}:{d['minute']}" for d in preds)
    result = score_match(details(finished=True, live=None), preds, model, fx.match_id)
    shot1 = result["heads"]["shot_1m"]
    assert shot1["n"] == 120 and shot1["positives"] == 3
    assert {"brier", "brier_baseline", "brier_persistence", "logloss", "logloss_baseline"} <= set(shot1)
    assert result["heads"]["goal_5m"]["positives"] == 5  # minutes 41-45 for the home goal at floor 46; own goal ignored


def test_sink_mirrors_locally_even_when_the_store_fails(tmp_path):
    class Broken:
        async def upsert(self, container, docs):
            raise RuntimeError("emulator down")

    sink = shadow.Sink(Broken(), tmp_path)
    asyncio.run(sink.write("predictions", [{"id": "a", "match_id": "m"}]))
    assert json.loads((tmp_path / "predictions.jsonl").read_text()) == {"id": "a", "match_id": "m"}
