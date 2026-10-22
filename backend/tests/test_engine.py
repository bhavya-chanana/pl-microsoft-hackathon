from matchlens.domain.models import EventType
from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.pass_quality import rate_pass
from matchlens.interpret.signals import SignalType

from .conftest import replay_order


def run(match, events=None):
    eng = MatchEngine(match.meta)
    signals = []
    for kind, item in replay_order(match) if events is None else [(1, e) for e in events]:
        signals += eng.ingest_event(item) if kind else eng.ingest_frame(item)
    return eng, signals


def test_final_state_matches_feed(demo_match):
    eng, _ = run(demo_match)
    state = eng.state()
    assert state["status"] == "full_time"
    assert state["score"] == {"NBR": 3, "HBC": 2}
    assert state["players_on_pitch"] == {"NBR": 11, "HBC": 10}


def test_team_totals_match_events(demo_match):
    eng, _ = run(demo_match)
    for team in ("NBR", "HBC"):
        passes = [e for e in demo_match.events if e.type == EventType.PASS and e.team_id == team]
        shots = [e for e in demo_match.events if e.type == EventType.SHOT and e.team_id == team]
        stats = eng.team_stats(team)
        assert stats["passes"] == len(passes)
        assert stats["shots"] == len(shots)
        assert len(stats["shot_map"]) == len(shots)
    total = eng.team_stats("NBR")["possession_pct"] + eng.team_stats("HBC")["possession_pct"]
    assert abs(total - 100) < 0.2


def test_duplicates_are_ignored(demo_match):
    events = demo_match.events[:400]
    eng, _ = run(demo_match, events + events[100:200])
    ref, _ = run(demo_match, events)
    assert eng.team_stats("NBR") == ref.team_stats("NBR")


def test_out_of_order_events_give_same_result(demo_match):
    events = list(demo_match.events[:600])
    shuffled = events[:300] + events[310:320] + events[300:310] + events[320:]
    eng, _ = run(demo_match, shuffled)
    ref, _ = run(demo_match, events)
    assert eng.team_stats("HBC") == ref.team_stats("HBC")
    assert eng.player_stats("HBC-09") == ref.player_stats("HBC-09")


def test_correction_updates_everywhere(demo_match):
    eng, signals = run(demo_match)
    goal_64 = next(s for s in eng.signals() if s.type == SignalType.GOAL and s.minute_label == "64'")
    assert goal_64.facts["scorer"] == "NBR-09"
    assert goal_64.revision == 1
    assert eng.player_stats("NBR-09")["goals"] == 1  # the corrected 64' goal
    assert eng.player_stats("NBR-10")["goals"] == 1  # only the 79' goal
    corrections = [s for s in signals if s.type == SignalType.CORRECTION]
    assert corrections and corrections[0].facts["value"] == "NBR-09"
    # The re-emitted goal signal reaches consumers with the new scorer.
    assert any(s.type == SignalType.GOAL and s.revision == 1 and s.facts["scorer"] == "NBR-09" for s in signals)


def test_milestone_follows_correction(demo_match):
    eng, _ = run(demo_match)
    milestones = [s for s in eng.signals() if s.type == SignalType.MILESTONE]
    # The wrongly credited 64' goal is no longer his 100th; the 79' goal is.
    assert [(m.player_ids, m.minute_label) for m in milestones] == [(["NBR-10"], "79'")]


def test_unavailable_tracking_stats_are_none(demo_match):
    eng, _ = run(demo_match, demo_match.events)
    stats = eng.player_stats("NBR-10")
    assert stats["distance_km"] is None and stats["top_speed_kmh"] is None


def test_key_signals_present(demo_match):
    eng, _ = run(demo_match)
    types = {s.type for s in eng.signals()}
    for t in (SignalType.GOAL, SignalType.CARD, SignalType.MOMENTUM_SWING, SignalType.SPEED_BADGE,
              SignalType.SUBSTITUTION, SignalType.PERIOD):
        assert t in types


def test_pass_difficulty_components():
    easy = rate_pass((40, 34), (45, 34), True, False, False, [])
    hard = rate_pass((40, 34), (85, 60), True, True, True, [(60, 45), (70, 52), (62, 47)])
    assert easy.band == "easy" and easy.difficulty < 10
    assert hard.band == "elite" and hard.lane_opponents >= 2
    assert sum(hard.components.values()) == hard.difficulty
