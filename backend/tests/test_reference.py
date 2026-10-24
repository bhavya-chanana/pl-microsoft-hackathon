from matchlens.simulator.calibration import NEUTRAL
from matchlens.simulator.engine import generate_match
from matchlens.simulator.reference import (
    METRICS,
    load_profile,
    measure_simulated,
    measure_statsbomb,
    summarise,
)


def _sb(kind, **kw):
    return {"type": {"name": kind}, **{k.rstrip("_"): v for k, v in kw.items()}}  # pass_ -> pass


def test_measure_statsbomb_event_shapes():
    """Real event data in StatsBomb's shape (120 x 80 yards), no download."""
    events = [
        _sb("Pass", location=[60, 40], pass_={"end_location": [70, 40]}),  # 8.75 m, complete
        _sb("Pass", location=[30, 40], pass_={"end_location": [80, 40], "outcome": {"name": "Incomplete"}},
            under_pressure=True),  # 43.75 m, failed
        _sb("Pass", location=[0, 0], pass_={"type": {"name": "Throw-in"}, "end_location": [5, 5]}),
        _sb("Pass", location=[120, 0], pass_={"type": {"name": "Corner"}, "end_location": [110, 40]}),
        _sb("Shot", location=[108, 40], shot={"outcome": {"name": "Goal"}, "statsbomb_xg": 0.3,
                                              "body_part": {"name": "Head"}}),
        _sb("Shot", location=[90, 40], shot={"outcome": {"name": "Blocked"}, "statsbomb_xg": 0.05,
                                             "body_part": {"name": "Right Foot"}}),
        _sb("Foul Committed", foul_committed={"card": {"name": "Yellow Card"}}),
        _sb("Duel", duel={"type": {"name": "Tackle"}, "outcome": {"name": "Won"}}),
        _sb("Interception", interception={"outcome": {"name": "Lost"}}),
    ]
    m = measure_statsbomb(events)
    assert (m["passes"], m["throw_ins"], m["corners"]) == (3, 1, 1)
    assert m["pass_completion_pct"] == 66.67 and m["pressured_pass_pct"] == 33.33
    assert (m["shots"], m["goals"], m["on_target_pct"], m["blocked_pct"], m["headers_pct"]) == (2, 1, 50, 50, 50)
    assert m["shots_in_box_pct"] == 50 and round(m["shot_distance_m"], 1) == 18.4
    assert (m["fouls"], m["yellow_cards"], m["tackles"], m["interceptions"]) == (1, 1, 1, 0)


def test_profile_is_aggregate_only():
    profile = load_profile()
    assert profile["matches"] >= 50 and "StatsBomb" in profile["source"]
    assert set(profile["metrics"]) == set(METRICS)
    assert all(set(v) == {"mean", "sd", "p10", "p90"} for v in profile["metrics"].values())


def test_simulated_matches_measure_every_metric():
    sim = summarise([measure_simulated(generate_match(seed=1, storyline=NEUTRAL))])
    assert set(sim) == set(METRICS)
