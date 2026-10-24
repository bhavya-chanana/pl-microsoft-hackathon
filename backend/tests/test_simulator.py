from matchlens.domain.models import EventType, Outcome
from matchlens.simulator.calibration import report
from matchlens.simulator.engine import generate_match


def test_simulator_is_deterministic():
    a = generate_match(seed=3)
    b = generate_match(seed=3)
    assert [e.model_dump() for e in a.events] == [e.model_dump() for e in b.events]


def test_events_are_ordered_and_unique(demo_match):
    seqs = [e.sequence for e in demo_match.events]
    assert seqs == sorted(seqs)
    assert len({e.event_id for e in demo_match.events}) == len(demo_match.events)
    elapsed = [e.elapsed for e in demo_match.events]
    assert elapsed == sorted(elapsed)


def test_storyline_plays_out(demo_match):
    goals = [e for e in demo_match.events if e.type == EventType.GOAL]
    assert [g.minute_label for g in goals] == ["23'", "39'", "64'", "79'", "90+2'"]
    final = goals[-1].attrs
    assert (final["home_score"], final["away_score"]) == (3, 2)
    reds = [e for e in demo_match.events if e.type == EventType.CARD and e.outcome == Outcome.SECOND_YELLOW]
    assert [r.player_id for r in reds] == ["HBC-04"]


def test_storyline_correction_is_emitted(demo_match):
    corrections = [e for e in demo_match.events if e.type == EventType.CORRECTION]
    assert len(corrections) == 2  # the shot and the goal are both re-credited
    assert all(c.attrs["previous"] == "NBR-10" and c.attrs["value"] == "NBR-09" for c in corrections)


def test_sent_off_player_leaves_tracking(demo_match):
    red = next(e for e in demo_match.events if e.type == EventType.CARD and e.outcome == Outcome.SECOND_YELLOW)
    after = [f for f in demo_match.tracking if f.elapsed > red.elapsed + 5]
    assert after and all("HBC-04" not in {en.entity_id for en in f.entities} for f in after)


def test_calibration_matches_real_matches():
    """Unscripted play within one real standard deviation of real top-flight matches on every metric."""
    failures = [row for row in report(matches=12) if not row[4]]
    assert not failures, failures
