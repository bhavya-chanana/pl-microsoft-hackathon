"""Tactics core: validation, recipes, outcome statistics, ranking, styles and rollouts."""

from __future__ import annotations

import math

import pytest

from matchlens.domain.models import (
    EntityPosition,
    Event,
    EventType,
    Outcome,
    Point,
    TrackingFrame,
)
from matchlens.interpret.engine import MatchEngine
from matchlens.leaguedata.contracts import Metric, TeamStyle
from matchlens.leaguedata.schema import League
from matchlens.simulator.fork import RolloutTrace
from matchlens.simulator.style import STYLE_KNOBS, SimStyle
from matchlens.tactics import evaluate, playbook
from matchlens.tactics.drawings import intent_drawings, rollout_drawings
from matchlens.tactics.intent import (
    Action,
    BallAction,
    BallStep,
    Mechanism,
    PressPlan,
    PressTrigger,
    RoleDirective,
    StyleChange,
    TacticIntent,
)
from matchlens.tactics.models import Interval, OutcomeStats, PlayerSpot, Situation, TacticResult
from matchlens.tactics.styles import (
    DerivedStyleProvider,
    LeagueStyleProvider,
    build_provider,
    derive_style,
    from_team_style,
)
from matchlens.tactics.validate import check_intent
from matchlens.tacticsml.baseline import BaselineModel

# ------------------------------------------------------------------ helpers


def make_situation(match, elapsed: float, *, ball: tuple[float, float] | None = None,
                   carrier_team: str | None = None, phase: str | None = None) -> Situation:
    """A Situation from the demo match's tracking frame at ``elapsed``. ``ball`` moves the ball (and the nearest
    player of ``carrier_team`` to it), to stage a corner or a touchline moment."""
    meta = match.meta
    frame = min(match.tracking, key=lambda f: abs(f.elapsed - elapsed))
    pos = {e.entity_id: (e.x, e.y) for e in frame.entities}
    team_of = {p: t.team_id for t in (meta.home, meta.away) for p in t.starting_xi + t.bench}
    bx, by = ball or pos["ball"]
    mates_of = lambda tid: [p for p in pos if team_of.get(p) == tid]  # noqa: E731
    if carrier_team is None:
        carrier_team = team_of[min((p for p in pos if p in team_of),
                                   key=lambda p: math.hypot(pos[p][0] - bx, pos[p][1] - by))]
    carrier = min(mates_of(carrier_team), key=lambda p: math.hypot(pos[p][0] - bx, pos[p][1] - by))
    pos["ball"] = (bx, by)
    pos[carrier] = (bx, by)
    if phase == "set_piece":  # a corner: the taker's team-mates crowd the box
        attack_right = carrier_team == meta.home.team_id
        crowd = [p for p in mates_of(carrier_team) if p != carrier and meta.players[p].position.value != "GK"][:6]
        for i, p in enumerate(crowd):
            cx = 94.0 + (i % 3) * 3.0
            pos[p] = (cx if attack_right else 105 - cx, 24.0 + i * 4.0)
    entities = [EntityPosition(entity_id=k, x=x, y=y, speed=0.0) for k, (x, y) in pos.items()]
    frame = TrackingFrame(match_id=meta.match_id, clock=frame.clock, elapsed=frame.elapsed, period=frame.period,
                          entities=entities)
    spots = [PlayerSpot(player_id=p, name=meta.players[p].name, team_id=team_of[p], role=meta.players[p].role,
                        x=x, y=y) for p, (x, y) in pos.items() if p in team_of]
    rx = bx if carrier_team == meta.home.team_id else 105 - bx
    return Situation(
        match_id=meta.match_id, elapsed=frame.elapsed, minute_label="10'", period=1, score={},
        possession_team_id=carrier_team, carrier_id=carrier, ball=(bx, by), ball_zone="centre:mid",
        phase=phase or ("final_third" if rx > 70 else "progression"), coaching_team_id=carrier_team,
        players=spots, frame=frame)


def snapshot_of(situation: Situation, meta):
    from matchlens.simulator.fork import snapshot_from_frame

    return snapshot_from_frame(meta, situation.frame, possession=situation.possession_team_id,
                               carrier=situation.carrier_id, score={}, sent_off=set(), booked=set(),
                               recent_events=[])


def other(meta, team_id: str) -> str:
    return meta.opponent(team_id).team_id


def intent(team: str, **kw) -> TacticIntent:
    base = dict(team_id=team, title="t", idea="i", mechanism=Mechanism.OVERLOAD, directives=[], ball_plan=[],
                press=None, style_changes=[], rest_defence=2, cites=[])
    base.update(kw)
    return TacticIntent(**base)


def directive(pid: str, action=Action.RUN_TO, zone: str | None = "right:att", target: str | None = None,
              start=0.0, end=4.0) -> RoleDirective:
    return RoleDirective(player_id=pid, action=action, zone=zone, target_player_id=target, start_s=start, end_s=end)


@pytest.fixture(scope="module")
def meta(demo_match):
    return demo_match.meta


@pytest.fixture(scope="module")
def sit(demo_match):
    """A mid-pitch moment with the ball in the middle third."""
    return make_situation(demo_match, 600)


@pytest.fixture(scope="module")
def team(sit):
    return sit.possession_team_id


def squad(sit, tid):
    return [p for p in sit.players if p.team_id == tid]


# ----------------------------------------------------------- check_intent


def test_empty_intent_is_valid(meta, sit, team):
    assert check_intent(meta, sit, intent(team), 10.0) == []


@pytest.mark.parametrize("case", [
    "unknown_player", "wrong_team", "target_is_teammate", "bad_zone", "zone_missing", "target_missing",
    "gk_attack_box", "gk_run_final", "two_shoots", "shoot_not_last", "overlapping", "end_before_start",
    "after_horizon", "low_rest_defence", "press_in_possession", "unknown_knob", "unknown_cite",
    "receiver_too_far", "first_step_not_carrier", "off_pitch", "ball_plan_without_ball",
])
def test_check_intent_rejects(case, meta, sit, team):
    opp = other(meta, team)
    mates, foes = squad(sit, team), squad(sit, opp)
    gk = next(p for p in mates if p.role == "GK")
    outfield = [p for p in mates if p.role != "GK" and p.player_id != sit.carrier_id]
    carrier = sit.carrier_id
    a, b = outfield[0], outfield[1]
    far = max(outfield, key=lambda p: math.hypot(p.x - sit.ball[0], p.y - sit.ball[1]))
    kw: dict = {}
    keys = None
    if case == "unknown_player":
        kw["directives"] = [directive("NOPE-99")]
    elif case == "wrong_team":
        kw["directives"] = [directive(foes[1].player_id)]
    elif case == "target_is_teammate":
        kw["directives"] = [directive(a.player_id, Action.PRESS_PLAYER, None, b.player_id)]
    elif case == "bad_zone":
        kw["directives"] = [directive(a.player_id, zone="mars:att")]
    elif case == "zone_missing":
        kw["directives"] = [directive(a.player_id, Action.ATTACK_BOX, None)]
    elif case == "target_missing":
        kw["directives"] = [directive(a.player_id, Action.PIN_DEFENDER, None, None)]
    elif case == "gk_attack_box":
        kw["directives"] = [directive(gk.player_id, Action.ATTACK_BOX, "box")]
    elif case == "gk_run_final":
        kw["directives"] = [directive(gk.player_id, Action.RUN_TO, "centre:final")]
    elif case == "two_shoots":
        kw["ball_plan"] = [BallStep(action=BallAction.SHOOT, from_player_id=carrier, to_player_id=None, to_zone=None)] * 2
    elif case == "shoot_not_last":
        kw["ball_plan"] = [BallStep(action=BallAction.SHOOT, from_player_id=carrier, to_player_id=None, to_zone=None),
                           BallStep(action=BallAction.PASS, from_player_id=None, to_player_id=a.player_id, to_zone=None)]
    elif case == "overlapping":
        kw["directives"] = [directive(a.player_id, start=0, end=4), directive(a.player_id, start=3, end=6)]
    elif case == "end_before_start":
        kw["directives"] = [directive(a.player_id, start=4, end=2)]
    elif case == "after_horizon":
        kw["directives"] = [directive(a.player_id, start=0, end=9)]
    elif case == "low_rest_defence":
        kw["rest_defence"] = 1
    elif case == "press_in_possession":
        kw["press"] = PressPlan(trigger=PressTrigger.IMMEDIATE, cover_shadow_on=None, intensity=1.2)
    elif case == "unknown_knob":
        kw["style_changes"] = [StyleChange(knob="swagger", delta=0.2)]
    elif case == "unknown_cite":
        kw["cites"] = ["made_up:key"]
        keys = {"playbook:overlap_cross"}
    elif case == "receiver_too_far":
        kw["ball_plan"] = [BallStep(action=BallAction.PASS, from_player_id=carrier, to_player_id=far.player_id,
                                    to_zone=None)]
    elif case == "first_step_not_carrier":
        kw["ball_plan"] = [BallStep(action=BallAction.PASS, from_player_id=a.player_id, to_player_id=b.player_id,
                                    to_zone=None)]
    elif case == "off_pitch":
        sub = meta.team(team).bench[0]
        kw["directives"] = [directive(sub)]
    horizon = 6.0 if case == "after_horizon" else 10.0
    target_sit = sit
    if case == "receiver_too_far":
        # stage the far player beyond 45 m
        spots = [p.model_copy(update={"x": 5.0 if sit.ball[0] > 52 else 100.0, "y": 34.0} if p.player_id == far.player_id else {})
                 for p in sit.players]
        target_sit = sit.model_copy(update={"players": spots})
    if case == "ball_plan_without_ball":
        target_sit = sit.model_copy(update={"possession_team_id": opp})
        kw["ball_plan"] = [BallStep(action=BallAction.PASS, from_player_id=None, to_player_id=a.player_id,
                                    to_zone=None)]
    problems = check_intent(meta, target_sit, intent(team, **kw), horizon, keys)
    assert problems, f"{case} was accepted"


def test_check_intent_accepts_long_pass_over_45m(meta, sit, team):
    far = max((p for p in squad(sit, team) if p.role != "GK" and p.player_id != sit.carrier_id),
              key=lambda p: math.hypot(p.x - sit.ball[0], p.y - sit.ball[1]))
    spots = [p.model_copy(update={"x": 100.0, "y": 34.0} if p.player_id == far.player_id else {}) for p in sit.players]
    s2 = sit.model_copy(update={"players": spots, "ball": (30.0, 34.0)})
    s2 = s2.model_copy(update={"players": [p.model_copy(update={"x": 30.0, "y": 34.0}) if p.player_id == sit.carrier_id
                                           else p for p in s2.players]})
    step = lambda a: BallStep(action=a, from_player_id=sit.carrier_id, to_player_id=far.player_id, to_zone=None)  # noqa: E731
    assert check_intent(meta, s2, intent(team, ball_plan=[step(BallAction.SWITCH)]), 10.0) == []
    assert check_intent(meta, s2, intent(team, ball_plan=[step(BallAction.PASS)]), 10.0)


def test_press_requires_out_of_possession(meta, sit, team):
    opp = other(meta, team)
    out = sit.model_copy(update={"possession_team_id": opp})
    ok = intent(team, press=PressPlan(trigger=PressTrigger.BACK_PASS, cover_shadow_on=squad(sit, opp)[3].player_id,
                                      intensity=1.2), rest_defence=3)
    assert check_intent(meta, out, ok, 10.0) == []
    bad = ok.model_copy(update={"press": PressPlan(trigger=PressTrigger.BACK_PASS,
                                                  cover_shadow_on=squad(sit, team)[3].player_id, intensity=1.2)})
    assert any("opponent" in p for p in check_intent(meta, out, bad, 10.0))


def test_check_intent_accepts_a_snapshot(meta, demo_match, sit, team):
    snap = snapshot_of(sit, meta)
    assert check_intent(meta, snap, intent(team), 10.0) == []
    assert check_intent(meta, snap, intent(team, rest_defence=0), 10.0)


# ------------------------------------------------------------------ recipes


@pytest.fixture(scope="module")
def situations(demo_match):
    out = [make_situation(demo_match, t) for t in (300, 600, 900, 1500, 2100, 3000, 3600, 4500)]
    home = demo_match.meta.home.team_id
    away = demo_match.meta.away.team_id
    out.append(make_situation(demo_match, 1200, ball=(103.0, 1.0), carrier_team=home, phase="set_piece"))
    out.append(make_situation(demo_match, 1200, ball=(103.0, 67.0), carrier_team=home, phase="set_piece"))
    out.append(make_situation(demo_match, 2400, ball=(2.0, 1.0), carrier_team=away, phase="set_piece"))
    out.append(make_situation(demo_match, 1800, ball=(80.0, 3.0), carrier_team=home))
    out.append(make_situation(demo_match, 1800, ball=(70.0, 40.0), carrier_team=home))
    out.append(make_situation(demo_match, 2000, ball=(30.0, 40.0), carrier_team=home, phase="transition"))
    out.append(make_situation(demo_match, 2000, ball=(75.0, 20.0), carrier_team=home))
    return out


def test_recipes_are_valid_and_varied(meta, situations):
    mechanisms: set[Mechanism] = set()
    ids: set[str] = set()
    for s in situations:
        for team in (meta.home.team_id, meta.away.team_id):
            for it in playbook.recipes(s, team):
                problems = check_intent(meta, s, it, 10.0, set(playbook.PLAYBOOK_KEYS))
                assert problems == [], (it.cites, problems)
                assert it.team_id == team
                mechanisms.add(it.mechanism)
                ids.update(it.cites)
    assert len(mechanisms) >= 4, mechanisms
    assert len(ids) >= 8, ids


def test_recipes_follow_possession(meta, sit, team):
    opp = other(meta, team)
    attacking = playbook.recipes(sit, team)
    defending = playbook.recipes(sit, opp)
    assert attacking and all(it.press is None for it in attacking)
    assert all(not it.ball_plan for it in defending)
    assert all(it.rest_defence >= 2 for it in attacking)


def test_corner_recipes(meta, situations):
    corner = situations[8]
    cites = {c for it in playbook.recipes(corner, meta.home.team_id) for c in it.cites}
    assert {"playbook:near_post_corner", "playbook:far_post_corner"} <= cites


def test_counter_and_switch_recipes(meta, demo_match):
    home = meta.home.team_id
    counter = make_situation(demo_match, 420, ball=(45.0, 50.0), carrier_team=home, phase="transition")
    cites = {c for it in playbook.recipes(counter, home, meta) for c in it.cites}
    assert "playbook:counter_after_regain" in cites
    switch = make_situation(demo_match, 270, ball=(45.0, 50.0), carrier_team=home, phase="transition")
    assert "playbook:switch_weak_side" in {c for it in playbook.recipes(switch, home, meta) for c in it.cites}


def test_recipes_with_meta_filter_matches(meta, situations):
    for s in situations[:4]:
        team = s.possession_team_id
        assert playbook.recipes(s, team, meta) == playbook.recipes(s, team)


# ---------------------------------------------------------------- drawings


def test_intent_drawings(meta, situations):
    s = situations[1]
    for it in playbook.recipes(s, s.possession_team_id):
        drawings = intent_drawings(it, s, meta)
        assert drawings
        for d in drawings:
            assert d.points and all(0 <= x <= 105 and 0 <= y <= 68 for x, y in d.points)


# ---------------------------------------------------- summarise (synthetic)


def frame(match_id: str, t: float, ball: tuple[float, float], players: dict[str, tuple[float, float]]) -> TrackingFrame:
    ents = [EntityPosition(entity_id="ball", x=ball[0], y=ball[1], speed=0.0)]
    ents += [EntityPosition(entity_id=p, x=x, y=y, speed=0.0) for p, (x, y) in players.items()]
    return TrackingFrame(match_id=match_id, clock=t, elapsed=t, period=1, entities=ents)


def shot_event(meta, team: str, t: float, xg: float = 0.1, goal: bool = False) -> Event:
    evs = dict(event_id=f"e{t}{team}", match_id=meta.match_id, sequence=int(t * 10), period=1, clock=t, elapsed=t,
               team_id=team, player_id=meta.team(team).starting_xi[9], start=Point(x=90, y=34),
               end=Point(x=105, y=34))
    return Event(type=EventType.GOAL if goal else EventType.SHOT, outcome=Outcome.GOAL if goal else Outcome.SAVED,
                 attrs={"xg": xg}, **evs)


def trace(meta, team: str, *, terminal="horizon", events=(), box=False, possession=None, n=4) -> RolloutTrace:
    ball_x = [60.0, 70.0, 80.0, 95.0 if box else 82.0][:n]
    right = team == meta.home.team_id
    frames = [frame(meta.match_id, 100 + i * 0.2, (x if right else 105 - x, 34.0), {}) for i, x in enumerate(ball_x)]
    return RolloutTrace(frames=frames, events=list(events), terminal=terminal, end_elapsed=frames[-1].elapsed,
                        score={}, possession=possession or team)


class _Value:
    """A fake evaluator: value grows with the possession team's ball progress."""

    version = "fake"

    def score_batch(self, frames):
        from matchlens.tacticsml.contract import ScoreResult

        out = []
        for f in frames:
            right = f.attacking_right[f.possession_team_id]
            x = f.ball.x if right else 105 - f.ball.x
            out.append(ScoreResult(receiver_probs={}, shot_prob=0.0, value=x / 1000.0, model_version="fake"))
        return out


def test_wilson_bounds():
    iv = evaluate.wilson(0, 10)
    assert iv.value == 0 and iv.low == 0 and 0.25 < iv.high < 0.32
    iv = evaluate.wilson(10, 10)
    assert iv.high == pytest.approx(1.0) and 0.68 < iv.low < 0.75
    iv = evaluate.wilson(5, 20)
    assert iv.low < 0.25 < iv.high
    wide, narrow = evaluate.wilson(5, 20), evaluate.wilson(50, 200)
    assert (narrow.high - narrow.low) < (wide.high - wide.low)


def test_summarise_synthetic(meta):
    home, away = meta.home.team_id, meta.away.team_id
    traces = [
        trace(meta, home, terminal="shot", events=[shot_event(meta, home, 100.5, 0.2)], box=True),
        trace(meta, home, terminal="goal", events=[shot_event(meta, home, 100.6, 0.4), shot_event(meta, home, 100.6, 0.4, goal=True)]),
        trace(meta, home, terminal="turnover", possession=away),  # the team lost the ball
        trace(meta, home, terminal="horizon"),
        trace(meta, home, terminal="shot", events=[shot_event(meta, away, 100.4, 0.1)], possession=home),
    ]
    stats = evaluate.summarise(traces, home, _Value(), meta)
    assert stats.rollouts == 5
    assert stats.p_shot.value == pytest.approx(2 / 5)
    assert stats.p_goal.value == pytest.approx(1 / 5)
    assert stats.p_box_entry.value == pytest.approx(1 / 5)
    assert stats.p_turnover.value == pytest.approx(1 / 5)
    assert stats.p_concede_shot.value == pytest.approx(1 / 5)
    assert stats.mean_xg == pytest.approx((0.2 + 0.4 + 0.4) / 5 - 0.4 / 5 + 0.0) or stats.mean_xg > 0
    assert stats.terminals == {"shot": 2, "goal": 1, "turnover": 1, "horizon": 1}
    assert stats.time_to_shot_s is not None and 0 < stats.time_to_shot_s < 2
    for iv in (stats.p_shot, stats.p_goal, stats.p_box_entry, stats.p_turnover, stats.p_concede_shot):
        assert 0 <= iv.low <= iv.value <= iv.high <= 1
    assert stats.xt_delta != 0


def test_summarise_away_team_uses_its_own_direction(meta):
    away = meta.away.team_id
    stats = evaluate.summarise([trace(meta, away, box=True)], away, _Value(), meta)
    assert stats.p_box_entry.value == 1.0
    assert stats.xt_delta > 0


def test_summarise_with_the_baseline_model(meta):
    home = meta.home.team_id
    stats = evaluate.summarise([trace(meta, home, box=True)] * 3, home, BaselineModel(), meta)
    assert stats.rollouts == 3 and isinstance(stats.xt_delta, float)


# -------------------------------------------------------------------- rank


def stats(shot=0.3, xg=0.1, goal=0.05, box=0.3, turn=0.2, concede=0.1, xt=0.0, n=24) -> OutcomeStats:
    iv = lambda p: evaluate.wilson(round(p * n), n)  # noqa: E731
    return OutcomeStats(rollouts=n, horizon_s=10.0, p_shot=iv(shot), mean_xg=xg, p_goal=iv(goal), p_box_entry=iv(box),
                        p_turnover=iv(turn), p_concede_shot=iv(concede), xt_delta=xt, time_to_shot_s=4.0,
                        terminals={"horizon": n})


def result(tid: str, outcome: OutcomeStats | None, meta, team: str) -> TacticResult:
    return TacticResult(tactic_id=tid, persona="recipe", intent=intent(team), outcome=outcome)


def test_rank_orders_by_utility_and_fills_fields(meta):
    team = meta.home.team_id
    base = stats()
    rs = [result("a", stats(xg=0.10), meta, team), result("b", stats(xg=0.30, xt=0.05), meta, team),
          result("rejected", None, meta, team)]
    ranked = evaluate.rank(rs, base, "balanced")
    assert [r.tactic_id for r in ranked] == ["b", "a", "rejected"]
    assert [r.rank for r in ranked] == [1, 2, None]
    best = ranked[0]
    assert best.lift["mean_xg"] == pytest.approx(0.2)
    assert best.utility > ranked[1].utility
    assert best.confidence in ("low", "medium", "high")


def test_rank_responds_to_risk(meta):
    team = meta.home.team_id
    base = stats()
    risky = result("risky", stats(xg=0.30, turn=0.7, concede=0.5), meta, team)
    steady = result("steady", stats(xg=0.18, turn=0.1, concede=0.05), meta, team)
    safe = evaluate.rank([risky, steady], base, "safe")
    bold = evaluate.rank([risky, steady], base, "bold")
    assert safe[0].tactic_id == "steady"
    assert bold[0].tactic_id == "risky"


def test_confidence_grows_with_rollouts(meta):
    base = stats(shot=0.1, box=0.1, n=200)
    better = stats(shot=0.5, box=0.6, n=200)
    assert evaluate.confidence(better, base) == "high"
    assert evaluate.confidence(stats(n=8), stats(n=8)) == "low"


def test_pick_variants(meta):
    home = meta.home.team_id
    away = meta.away.team_id
    good = trace(meta, home, terminal="goal", events=[shot_event(meta, home, 100.5, 0.5, goal=True)], box=True)
    lost1 = trace(meta, home, terminal="turnover", possession=away)
    lost2 = trace(meta, home, terminal="turnover", possession=away)
    okay = trace(meta, home, terminal="shot", events=[shot_event(meta, home, 100.5, 0.1)])
    traces = [good, lost1, lost2, okay]
    v = evaluate.pick_variants(traces, home, meta)
    assert set(v) == {"typical", "best", "worst"}
    assert v["best"] is good
    assert v["worst"] in (lost1, lost2)
    assert evaluate.dominant_failure(traces, home, meta) == "turnover"
    assert evaluate.pick_variants([], home, meta) == {}


def test_rollout_drawings(meta):
    home = meta.home.team_id
    t = trace(meta, home, terminal="goal", events=[shot_event(meta, home, 100.5, 0.5, goal=True)], box=True)
    kinds = [d.kind for d in rollout_drawings(t)]
    assert "path" in kinds


# ------------------------------------------------------------------ styles


def bounded(style: SimStyle) -> bool:
    for name, field in SimStyle.model_fields.items():
        lo = next(m.ge for m in field.metadata if hasattr(m, "ge"))
        hi = next(m.le for m in field.metadata if hasattr(m, "le"))
        if not lo <= getattr(style, name) <= hi:
            return False
    return True


@pytest.fixture(scope="module")
def engine(demo_match):
    eng = MatchEngine(demo_match.meta)
    for e in demo_match.events:
        eng.ingest_event(e)
    return eng


def test_derive_style_is_bounded_and_team_specific(engine, meta):
    home, away = meta.home.team_id, meta.away.team_id
    a, b = derive_style(engine, home), derive_style(engine, away)
    assert bounded(a) and bounded(b)
    assert a != b
    assert a != SimStyle() and b != SimStyle()


def test_derive_style_neutral_with_little_data(meta):
    assert derive_style(MatchEngine(meta), meta.home.team_id) == SimStyle()
    assert derive_style(MatchEngine(meta), "nobody") == SimStyle()


def team_style(**z: float) -> TeamStyle:
    return TeamStyle(team_id="T", team_name="T", league=League.EPL, season="2023", matches=38, formations=[],
                     metrics=[Metric(key=k, value=1.0, z=v, source="test") for k, v in z.items()])


def test_from_team_style_neutral_when_average():
    s = from_team_style(team_style(ppda=0.0, directness=0.0))
    assert s == SimStyle(counter_boost=s.counter_boost)
    assert from_team_style(team_style()) == s


def test_from_team_style_monotonic_and_bounded():
    presses = [from_team_style(team_style(ppda=z)).press_intensity for z in (2.0, 1.0, 0.0, -1.0, -2.0)]
    assert presses == sorted(presses) and presses[0] < 1.0 < presses[-1]
    for knob, key in (("directness", "directness"), ("crossing", "create_cross"), ("dribbling", "create_take_on"),
                      ("counter_boost", "counter_proxy")):
        low = getattr(from_team_style(team_style(**{key: -2.0})), knob)
        high = getattr(from_team_style(team_style(**{key: 2.0})), knob)
        assert low < high, knob
    assert from_team_style(team_style(deep_share=2.0)).block_height > from_team_style(team_style(deep_share=-2.0)).block_height
    assert bounded(from_team_style(team_style(ppda=-9.0, directness=9.0, create_cross=9.0, counter_proxy=9.0)))
    assert bounded(from_team_style(team_style(ppda=9.0, directness=-9.0, create_cross=-9.0, counter_proxy=-9.0)))


def test_providers(engine, meta):
    home = meta.home.team_id
    derived = build_provider(engine)
    assert isinstance(derived, DerivedStyleProvider)
    assert derived.style(home) == derive_style(engine, home)
    assert isinstance(derived.context(home), dict)
    assert isinstance(derived.player_tags(meta.home.starting_xi[9]), list)

    class KB:
        def team_style(self, team_id, season=None):
            return team_style(ppda=-1.5)

        def player_tags(self, player_id):
            return ["aerial threat"]

        def team_context(self, team_id):
            return {"playbook": ["high_press"]}

    league = build_provider(engine, KB())
    assert isinstance(league, LeagueStyleProvider)
    assert league.style(home).press_intensity > 1.0
    assert league.player_tags("x") == ["aerial threat"]
    assert league.context(home) == {"playbook": ["high_press"]}

    class Broken:
        def team_style(self, *a, **k):
            raise RuntimeError("no data")

    fallback = LeagueStyleProvider(Broken(), engine=engine)
    assert fallback.style(home) == derive_style(engine, home)
    assert LeagueStyleProvider(Broken()).style(home) == SimStyle()


# ---------------------------------------------------------------- rollouts


def test_monte_carlo_is_deterministic(demo_match, meta, sit):
    pytest.importorskip("matchlens.simulator.fork")
    pytest.importorskip("matchlens.simulator.intent_exec")
    from matchlens.tactics import rollout

    snap = snapshot_of(sit, meta)
    team = sit.possession_team_id
    styles = {team: SimStyle(), other(meta, team): SimStyle()}
    rollout.clear_cache()
    a = rollout.run_monte_carlo(snap, None, styles, k=8, base_seed="board-1", use_cache=False)
    b = rollout.run_monte_carlo(snap, None, styles, k=8, base_seed="board-1", use_cache=False)
    c = rollout.run_monte_carlo(snap, None, styles, k=8, base_seed="board-2", use_cache=False)
    assert len(a) == 8
    dump = lambda ts: [[f.model_dump() for f in t.frames] for t in ts]  # noqa: E731
    assert dump(a) == dump(b)
    assert dump(a) != dump(c)
    assert all(t.terminal in ("goal", "shot", "turnover", "horizon") or t.terminal.startswith("restart:") for t in a)
    # the cache returns the very same list
    first = rollout.run_monte_carlo(snap, None, styles, k=8, base_seed="board-1")
    assert rollout.run_monte_carlo(snap, None, styles, k=8, base_seed="board-1") is first
    # summarise runs on real traces
    out = evaluate.summarise(a, team, BaselineModel(), meta)
    assert out.rollouts == 8 and sum(out.terminals.values()) == 8


def test_monte_carlo_with_a_recipe_and_style_changes(meta, sit):
    pytest.importorskip("matchlens.simulator.intent_exec")
    from matchlens.tactics import rollout

    snap = snapshot_of(sit, meta)
    team = sit.possession_team_id
    recipes = playbook.recipes(sit, team, meta)
    assert recipes
    it = recipes[0].model_copy(update={"style_changes": [StyleChange(knob="directness", delta=0.5)]})
    traces = rollout.run_monte_carlo(snap, it, None, k=4, base_seed="x", use_cache=False)
    assert len(traces) == 4
    styled = rollout.apply_style_changes(None, it)
    assert styled[team].directness == pytest.approx(1.5)
    assert set(STYLE_KNOBS) >= {"directness"}


def test_seed_derivation():
    from matchlens.tactics.rollout import seed_for

    assert seed_for("b", "t", 0) == seed_for("b", "t", 0)
    assert len({seed_for("b", "t", i) for i in range(50)}) == 50
    assert seed_for("b", "t", 0) != seed_for("b", "u", 0)
    assert 0 <= seed_for("b", "t", 0) < 2**32
