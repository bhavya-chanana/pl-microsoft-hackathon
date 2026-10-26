"""Match digest and reasoner on a FICTIONAL four-club league (no real names, no network)."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import date, timedelta

import pytest
from agent_framework import Agent

from matchlens.agents import prompts, templates
from matchlens.leaguedata import digest as digest_mod
from matchlens.leaguedata import reasoner as rs
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.contracts import (
    MatchReasoning,
    MatchReasoningDraft,
    PatternTag,
    TacticalPatternDraft,
    TurningPointDraft,
)
from matchlens.leaguedata.digest import Tables, build_digest, estimate_tokens
from matchlens.leaguedata.schema import (
    Club,
    League,
    LineupEntry,
    Manager,
    Match,
    MatchEvent,
    Player,
    Shot,
    TeamMatchStats,
)

from .test_agents import ScriptedClient

SEASON = "2023-24"
N, E, W, S = "northfield-united", "eastbridge-rovers", "westmoor-athletic", "southvale-town"
NAMES = {N: "Northfield United", E: "Eastbridge Rovers", W: "Westmoor Athletic", S: "Southvale Town"}
TARGET_DATE = date(2023, 10, 28)
TARGET = f"EPL:{SEASON}:{TARGET_DATE.isoformat()}:{N}-{E}"
SCORER, REDCARD = "ada-okoye", "bram-lindqvist"


def run(coro):
    return asyncio.run(coro)


def mkey(d: date, h: str, a: str, league: str = "EPL") -> str:
    return f"{league}:{SEASON}:{d.isoformat()}:{h}-{a}"


def mk_shot(key: str, team: str, player: str, minute: int, xg: float, result: str, before: tuple[int, int], *,
            sit: str = "open_play", action: str | None = "Pass", body: str = "right_foot", n: list[int] = [0]) -> Shot:
    n[0] += 1
    return Shot(shot_id=f"sh{n[0]}", match_key=key, team_id=team, player_id=player, assist_player_id=None,
                minute=minute, x=94.0, y=34.0, xg=xg, result=result, situation=sit, body_part=body,
                last_action=action, home_score_before=before[0], away_score_before=before[1])


def filler_rows(m: Match, rng: random.Random) -> tuple[list[Shot], list[TeamMatchStats]]:
    plan = [(rng.randint(3, 88), m.home_id, rng.uniform(0.15, 0.6), "goal") for _ in range(m.home_goals)]
    plan += [(rng.randint(3, 88), m.away_id, rng.uniform(0.15, 0.6), "goal") for _ in range(m.away_goals)]
    for _ in range(rng.randint(8, 14)):
        plan.append((rng.randint(1, 90), rng.choice([m.home_id, m.away_id]), rng.uniform(0.02, 0.2),
                     rng.choice(["saved", "missed", "blocked"])))
    plan.sort(key=lambda p: (p[0], p[3] != "goal"))
    shots, score = [], [0, 0]
    for minute, team, xg, result in plan:
        shots.append(mk_shot(m.match_key, team, f"{team}-forward", minute, round(xg, 2), result, (score[0], score[1]),
                             sit=rng.choice(["open_play", "open_play", "corner", "set_piece"]),
                             action=rng.choice(["Pass", "Cross", "TakeOn", None]),
                             body=rng.choice(["right_foot", "left_foot", "head"])))
        if result == "goal":
            score[0 if team == m.home_id else 1] += 1
    stats = []
    for team, is_home in ((m.home_id, True), (m.away_id, False)):
        mine = [s for s in shots if s.team_id == team]
        stats.append(TeamMatchStats(
            match_key=m.match_key, team_id=team, is_home=is_home, goals=m.home_goals if is_home else m.away_goals,
            xg=round(sum(s.xg for s in mine), 2), npxg=round(sum(s.xg for s in mine), 2), shots=len(mine),
            shots_on_target=sum(s.result in ("goal", "saved") for s in mine), corners=rng.randint(2, 8),
            fouls=rng.randint(8, 14), yellows=rng.randint(0, 3), reds=0, ppda=round(rng.uniform(8, 12), 1),
            ppda_allowed=round(rng.uniform(8, 12), 1), deep=rng.randint(6, 12), deep_allowed=rng.randint(6, 12),
            xg_chain=round(rng.uniform(3, 5), 2), xg_buildup=round(rng.uniform(1, 2), 2)))
    return shots, stats


def build_league() -> Tables:
    rng = random.Random(11)
    rounds = [[(N, E), (W, S)], [(N, W), (E, S)], [(N, S), (E, W)]]
    matches: list[Match] = []
    for r in range(10):
        pairs = rounds[r % 3]
        for h, a in pairs:
            h, a = (h, a) if (r // 3) % 2 == 0 else (a, h)
            d = date(2023, 8, 12) + timedelta(days=7 * r)
            matches.append(Match(match_key=mkey(d, h, a), league=League.EPL, season=SEASON, date=d.isoformat(),
                                 home_id=h, away_id=a, home_goals=rng.randint(0, 3), away_goals=rng.randint(0, 2),
                                 home_formation="4-3-3", away_formation="4-4-2"))
    shots: list[Shot] = []
    stats: list[TeamMatchStats] = []
    for m in matches:
        s, st = filler_rows(m, rng)
        shots += s
        stats += st
    # the match under study: N 2-1 E, E down to ten men, E out-shot N but N's chances were better
    other = Match(match_key=mkey(TARGET_DATE, W, S), league=League.EPL, season=SEASON, date=TARGET_DATE.isoformat(),
                  home_id=W, away_id=S, home_goals=0, away_goals=0)
    target = Match(
        match_key=TARGET, league=League.EPL, season=SEASON, date=TARGET_DATE.isoformat(), stage=None,
        home_id=N, away_id=E, home_goals=2, away_goals=1, home_goals_ht=1, away_goals_ht=0,
        home_xg=1.59, away_xg=0.86, home_formation="4-2-3-1", away_formation="5-4-1",
        home_manager_id="ines-calloway", away_manager_id="tobias-vance", referee="Rhett Marlowe",
        p_home=0.5, p_draw=0.27, p_away=0.23, home_elo=1780.4, away_elo=1702.9)
    matches += [other, target]
    ts = [
        mk_shot(TARGET, N, "carla-mendes", 8, 0.06, "missed", (0, 0)),
        mk_shot(TARGET, N, SCORER, 23, 0.45, "goal", (0, 0), action="Cross", body="head"),
        mk_shot(TARGET, N, "carla-mendes", 41, 0.10, "saved", (1, 0), sit="corner", action=None),
        mk_shot(TARGET, N, SCORER, 52, 0.22, "saved", (1, 0), action="Throughball"),
        mk_shot(TARGET, N, "dmitri-voss", 78, 0.76, "goal", (1, 1), sit="penalty", action=None),
        mk_shot(TARGET, E, "eli-farouk", 12, 0.31, "saved", (0, 0)),
        mk_shot(TARGET, E, "eli-farouk", 35, 0.08, "blocked", (1, 0)),
        mk_shot(TARGET, E, "eli-farouk", 55, 0.30, "goal", (1, 0), sit="fast_break", action="Throughball"),
        mk_shot(TARGET, E, REDCARD, 64, 0.12, "missed", (1, 1)),
        mk_shot(TARGET, E, REDCARD, 70, 0.05, "blocked", (1, 1)),
    ]
    shots += ts
    stats += [
        TeamMatchStats(match_key=TARGET, team_id=N, is_home=True, goals=2, xg=1.59, npxg=0.83, shots=5,
                       shots_on_target=4, corners=6, fouls=9, yellows=1, reds=0, ppda=5.2, ppda_allowed=14.1,
                       deep=15, deep_allowed=5, xg_chain=4.0, xg_buildup=0.6),
        TeamMatchStats(match_key=TARGET, team_id=E, is_home=False, goals=1, xg=0.86, npxg=0.86, shots=5,
                       shots_on_target=2, corners=2, fouls=14, yellows=2, reds=1, ppda=14.1, ppda_allowed=5.2,
                       deep=5, deep_allowed=15, xg_chain=2.0, xg_buildup=1.4),
    ]
    events = [
        MatchEvent(event_id="ev1", match_key=TARGET, team_id=E, minute=30, kind="yellow", player_id=REDCARD),
        MatchEvent(event_id="ev2", match_key=TARGET, team_id=E, minute=46, kind="sub", player_id="eli-farouk",
                   related_player_id="finn-oyelaran"),
        MatchEvent(event_id="ev3", match_key=TARGET, team_id=E, minute=60, kind="red", player_id=REDCARD),
        MatchEvent(event_id="ev4", match_key=TARGET, team_id=N, minute=65, kind="sub", player_id="carla-mendes",
                   related_player_id="gus-tanaka"),
    ]
    lineups = []
    for team, ids in ((N, [SCORER, "carla-mendes", "dmitri-voss"]), (E, ["eli-farouk", REDCARD, "finn-oyelaran"])):
        for i, pid in enumerate(ids):
            lineups.append(LineupEntry(match_key=TARGET, team_id=team, player_id=pid, starter=i < 2, position="FW",
                                       minute_on=0 if i < 2 else 46, minute_off=None))
    players = [Player(player_id=p, name=p.replace("-", " ").title()) for p in
               {SCORER, REDCARD, "carla-mendes", "dmitri-voss", "eli-farouk", "finn-oyelaran", "gus-tanaka"}]
    return Tables(
        matches=matches, lineups=lineups, shots=shots, events=events, stats=stats, players=players,
        clubs=[Club(team_id=t, name=n) for t, n in NAMES.items()],
        managers=[Manager(manager_id="ines-calloway", name="Ines Calloway"),
                  Manager(manager_id="tobias-vance", name="Tobias Vance")])


@pytest.fixture(scope="module")
def tables() -> Tables:
    return build_league()


@pytest.fixture(scope="module")
def digest(tables):
    return build_digest(TARGET, tables)


def agent_for(client) -> Agent:
    return Agent(client, prompts.LEAGUE_REASONER, name="league_reasoner")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(rs, "BACKOFF_S", 0.0)


# ------------------------------------------------------------------ digest


def test_digest_is_deterministic_and_within_budget(tables, digest):
    again = build_digest(TARGET, tables)
    assert again.digest_hash == digest.digest_hash and again.payload() == digest.payload()
    assert len(digest.digest_hash) == 64
    assert 300 < estimate_tokens(digest) <= digest_mod.MAX_TOKENS
    unblind = build_digest(TARGET, tables, blind=False)
    assert unblind.digest_hash != digest.digest_hash
    # a different match differs
    assert build_digest(mkey(TARGET_DATE, W, S), tables).digest_hash != digest.digest_hash


def test_digest_content(digest):
    p = digest.payload()
    assert p["header"]["home"] == "{H}" and p["header"]["away"] == "{A}"
    assert p["header"]["formation"] == {"H": "4-2-3-1", "A": "5-4-1"}
    assert p["header"]["elo"] == {"H": 1780, "A": 1703}
    assert p["header"]["odds_p"]["D"] == 0.27
    assert p["summary"]["score"] == "2-1" and p["summary"]["xg"] == {"H": 1.59, "A": 0.86}
    assert p["summary"]["xg_diff"] == 0.73 and p["summary"]["shots"] == {"H": 5, "A": 5}
    assert p["summary"]["by_situation"]["H"]["penalty"] == [1, 0.76]
    shots = digest.shot_rows()
    assert list(shots) == [f"S{i:02d}" for i in range(1, 11)]
    assert shots["S03"]["min"] == 23 and shots["S03"]["side"] == "H" and shots["S03"]["res"] == "goal"
    events = digest.event_rows()
    assert [e["kind"] for e in events.values()] == ["yellow", "sub", "red", "sub"]  # goals live in the shot table
    tl = p["xg_timeline"]
    assert tl["bins"] == ["0-15", "15-30", "30-45", "45-60", "60-75", "75-90"]
    assert tl["H"][1] == 0.45 and tl["cumH"][-1] == 1.59 and len(tl["diff"]) == 6
    segs = digest.segment_rows()
    assert [s["score"] for s in segs.values()] == ["0-0", "1-0", "1-1", "2-1"]
    assert segs["G2"]["from"] == 23 and segs["G2"]["to"] == 55
    assert p["team_stats"]["H"]["ppda"] == 5.2 and "possession" not in p["team_stats"]["H"]
    assert "No possession data." in p["limitations"]
    assert p["lineups"]["H"]["cols"] == ["player", "pos", "on", "off"]
    # this match against each team's pre-match form (z-like deltas keyed M:<metric>)
    assert digest.metric_keys("H") and all(k.startswith("M:") for k in digest.metric_keys("H"))
    assert p["style_delta"]["H"]["M:ppda"][2] < -0.8
    assert "mentality" in p and "H" in p["mentality"]


def test_blind_digest_hides_every_name_and_restores_them(tables, digest):
    blob = json.dumps(digest.payload())
    for needle in [*NAMES.values(), *NAMES, "Rhett", "Marlowe", "calloway", "Calloway", "Ada", "okoye", "bram",
                   "lindqvist", "2023-10-28", "Eastbridge"]:
        assert needle not in blob, needle
    assert "names" not in digest.payload()
    assert "{P:" in blob and "{M:" in blob and "{H}" in blob and "month" in digest.payload()["header"]
    assert digest.names["{H}"] == "Northfield United" and digest.names["{A}"] == "Eastbridge Rovers"
    assert "Ada Okoye" in digest.names.values() and "Ines Calloway" in digest.names.values()
    assert set(digest.ids.values()) >= {SCORER, "ines-calloway"}
    draft = rs.offline_draft(digest.payload())
    restored = rs.restore_names(draft, digest.names)
    text = restored.model_dump_json()
    assert "Northfield United" in text and "{H}" not in text and "{P:" not in text
    open_digest = build_digest(TARGET, tables, blind=False)
    assert open_digest.names == {} and "Northfield United" in json.dumps(open_digest.payload())
    assert open_digest.payload()["header"]["date"] == "2023-10-28"


def test_digest_without_shots_or_xg_states_its_limits():
    d = date(2023, 11, 8)
    key = mkey(d, N, E, "UCL")
    m = Match(match_key=key, league=League.UCL, season=SEASON, date=d.isoformat(), stage="group", home_id=N,
              away_id=E, home_goals=1, away_goals=0, home_formation="4-3-3")
    t = Tables(matches=[m], events=[MatchEvent(event_id="g1", match_key=key, team_id=N, minute=61, kind="goal",
                                               player_id=SCORER)],
               clubs=[Club(team_id=N, name=NAMES[N]), Club(team_id=E, name=NAMES[E])],
               players=[Player(player_id=SCORER, name="Ada Okoye")])
    dg = build_digest(key, t)
    assert any("No xG" in x for x in dg.limitations) and "xg" not in dg.summary and dg.xg_timeline is None
    assert dg.header["stage"] == "group"
    assert list(dg.event_rows())[0] == "E01" and dg.event_rows()["E01"]["kind"] == "goal"
    assert list(dg.segment_rows()) == ["G1", "G2"]
    draft = rs.offline_draft(dg.payload())
    assert draft.verdict == "deserved" and draft.confidence < 0.4
    assert rs.verify(draft, dg) == []
    assert all(p.tag not in rs.NEEDS_XG for p in draft.patterns)


# ------------------------------------------------------------------ offline draft and verify


def test_offline_reasoning_validates_and_passes_the_fact_check(digest):
    draft = rs.offline_draft(digest.payload())
    assert MatchReasoningDraft.model_validate(draft.model_dump()) == draft
    assert rs.verify(draft, digest) == []
    assert draft.verdict == "deserved"  # won with +0.73 xG; the rules give dominant from +0.8
    assert draft.headline == "{H} beat {A} 2-1"
    tags = {(p.tag, p.side) for p in draft.patterns}
    assert (PatternTag.RED_CARD_IMPACT, "away") in tags
    assert all(p.evidence for p in draft.patterns) and draft.turning_points
    assert [tp.minute for tp in draft.turning_points] == sorted(tp.minute for tp in draft.turning_points)
    # the registered offline template task returns the same thing
    via_task = templates.TASKS["league_match_reasoning"]({"task": "league_match_reasoning", "digest": digest.payload()})
    assert via_task == draft


def test_offline_reasoning_end_to_end_with_the_offline_client(digest):
    out = run(rs.reason_match(agent_for(ScriptedClient()), digest, model="scripted"))
    assert isinstance(out, MatchReasoning) and out.provenance.fact_check == "passed"
    assert out.provenance.digest_hash == digest.digest_hash and out.provenance.prompt_version == rs.PROMPT_VERSION
    assert out.match_key == TARGET and out.score == "2-1" and out.league is League.EPL
    assert "Northfield United beat Eastbridge Rovers 2-1" == out.draft.headline


def mutate(draft: MatchReasoningDraft, **update) -> MatchReasoningDraft:
    return draft.model_copy(update=update)


def test_verify_blocks_an_invented_number(digest):
    good = rs.offline_draft(digest.payload())
    bad = mutate(good, why_won="{H} won with 7 shots worth 3.45 xG after 83 minutes of pressure.")
    problems = rs.verify(bad, digest)
    assert any("why_won" in p and "3.45" in p for p in problems)
    assert any("why_won" in p and "7" in p.split("not found")[0] for p in problems)
    # minutes marked as minutes, and digest values rounded as written, are fine
    ok = mutate(good, why_won="{H} scored at 23' and again at 78' from 1.64 xG, about 62% of the chances' value: 0.8 xG up.")
    assert [p for p in rs.verify(ok, digest) if p.startswith("why_won")][0].count("1.64") == 1  # 1.64 is invented
    clean = mutate(good, why_won="{H} scored at 23' and 78' from 1.59 xG against 0.86 (xG margin 0.73).")
    assert rs.verify(clean, digest) == []
    assert any("headline" in p for p in rs.verify(mutate(good, headline="{H} won 9-0"), digest))


def test_verify_blocks_unknown_evidence_and_placeholders(digest):
    good = rs.offline_draft(digest.payload())
    pattern = good.patterns[0]
    unknown_ev = pattern.model_copy(update={"evidence": ["S99"]})
    assert any("unknown evidence id(s) S99" in p for p in rs.verify(mutate(good, patterns=[unknown_ev]), digest))
    no_ev = pattern.model_copy(update={"evidence": []})
    assert any("cites no evidence" in p for p in rs.verify(mutate(good, patterns=[no_ev]), digest))
    ghost = mutate(good, home_note="{P:pdeadbe} was brilliant and {M:mabc123} was not.")
    problems = rs.verify(ghost, digest)
    assert any("{P:pdeadbe}" in p for p in problems) and any("{M:mabc123}" in p for p in problems)
    real_name = next(k for k in digest.names if k.startswith("{P:"))
    assert not [p for p in rs.verify(mutate(good, home_note=f"{real_name} scored."), digest) if "placeholder" in p]
    # a real name is not a placeholder, but braces that are not ours are refused
    assert any("{X}" in p for p in rs.verify(mutate(good, away_note="{X} struggled."), digest))


def test_verify_checks_sides_turning_points_and_verdict(digest):
    good = rs.offline_draft(digest.payload())
    away_shot = next(sid for sid, r in digest.shot_rows().items() if r["side"] == "A")
    wrong_side = TacticalPatternDraft(tag=PatternTag.COUNTER_ATTACK, side="home", description="Counters.",
                                      evidence=[away_shot], strength=0.5)
    assert any("other side" in p for p in rs.verify(mutate(good, patterns=[wrong_side]), digest))
    right_side = wrong_side.model_copy(update={"side": "away"})
    assert rs.verify(mutate(good, patterns=[right_side]), digest) == []
    wrong_metric = TacticalPatternDraft(tag=PatternTag.HIGH_PRESS, side="away", description="Pressed.",
                                        evidence=["M:ppda"], strength=0.5)
    assert any("other side" in p for p in rs.verify(mutate(good, patterns=[wrong_metric]), digest)) \
        == ("M:ppda" in digest.metric_keys("H") and "M:ppda" not in digest.metric_keys("A"))
    # a turning point must sit near the minute of the evidence it cites and use a real xG swing
    goal_id = next(sid for sid, r in digest.shot_rows().items() if r["res"] == "goal" and r["min"] == 23)
    bad_minute = TurningPointDraft(minute=70, description="A goal.", evidence=[goal_id], xg_swing=None)
    assert any("does not match the minute" in p for p in rs.verify(mutate(good, turning_points=[bad_minute]), digest))
    bad_swing = TurningPointDraft(minute=23, description="A goal.", evidence=[goal_id], xg_swing=0.123)
    assert any("xg_swing" in p for p in rs.verify(mutate(good, turning_points=[bad_swing]), digest))
    # the home side won with more xG: "smash_and_grab" would contradict the data only above +0.8
    assert rs.verify(mutate(good, verdict="fortunate"), digest) == []
    assert any("verdict" in p for p in rs.verify(mutate(good, verdict="deserved"), digest)) is False


# ------------------------------------------------------------------ rewrite turn


def invent(draft: MatchReasoningDraft, payload: dict) -> MatchReasoningDraft:
    if "problems" in payload:  # the rewrite turn: fix it
        return draft
    return draft.model_copy(update={"why_won": "{H} won thanks to 9 big chances and 4.20 xG."})


def test_rewrite_turn_repairs_a_bad_draft(digest):
    client = ScriptedClient(tamper={"league_match_reasoning": invent})
    out = run(rs.reason_match(agent_for(client), digest, model="scripted"))
    assert out.provenance.fact_check == "rewritten" and client.calls == ["league_match_reasoning"] * 2
    assert out.provenance.problems and "4.20" in out.provenance.problems[0]
    assert "4.20" not in out.draft.why_won


def test_still_failing_draft_is_blocked_and_replaced_by_the_template(digest):
    def always(draft, payload):
        return draft.model_copy(update={"why_won": "{H} won with 4.20 xG."})

    client = ScriptedClient(tamper={"league_match_reasoning": always})
    out = run(rs.reason_match(agent_for(client), digest, model="scripted"))
    assert out.provenance.fact_check == "blocked" and len(client.calls) == 2
    assert out.provenance.problems and "4.20" in out.provenance.problems[0]
    assert "4.20" not in out.draft.model_dump_json()
    assert out.draft.headline == "Northfield United beat Eastbridge Rovers 2-1"


def test_model_failure_retries_then_falls_back_to_the_template(digest):
    client = ScriptedClient(fail={"league_match_reasoning"})
    out = run(rs.reason_match(agent_for(client), digest, model="scripted", retries=2))
    assert out.provenance.fact_check == "template" and len(client.calls) == 3
    assert out.draft.verdict == "deserved"


# ------------------------------------------------------------------ batch


def test_batch_writes_files_and_resumes_by_hash(tables, tmp_path):
    cfg = Config(root=tmp_path)
    keys = [TARGET, mkey(TARGET_DATE, W, S)]
    client = ScriptedClient()
    report = run(rs.run_batch(cfg, keys, model_label="scripted-1", tables=tables, agent=agent_for(client),
                              concurrency=2))
    assert report.counts["passed"] == 2 and report.failed == 0
    path = rs.reasoning_path(cfg, "EPL", SEASON, TARGET)
    assert path == tmp_path / "reasoning" / "EPL" / SEASON / f"{TARGET.replace(':', '_')}.json"
    stored = MatchReasoning.model_validate_json(path.read_text())
    assert stored.provenance.model == "scripted-1" and stored.draft.headline.startswith("Northfield United")
    assert len(client.calls) == 2

    again = run(rs.run_batch(cfg, keys, model_label="scripted-1", tables=tables, agent=agent_for(client)))
    assert again.counts["skipped"] == 2 and len(client.calls) == 2  # nothing recomputed
    other_model = run(rs.run_batch(cfg, keys, model_label="scripted-2", tables=tables, agent=agent_for(client)))
    assert other_model.counts["passed"] == 2 and len(client.calls) == 4
    forced = run(rs.run_batch(cfg, keys[:1], model_label="scripted-2", tables=tables, agent=agent_for(client),
                              force=True))
    assert forced.counts["passed"] == 1 and len(client.calls) == 5
    limited = run(rs.run_batch(cfg, keys, model_label="scripted-3", tables=tables, agent=agent_for(client), limit=1))
    assert sum(limited.counts.values()) == 1


def test_resume_notices_changed_evidence(tables, tmp_path):
    cfg = Config(root=tmp_path)
    client = ScriptedClient()
    run(rs.run_batch(cfg, [TARGET], model_label="m", tables=tables, agent=agent_for(client)))
    path = rs.reasoning_path(cfg, "EPL", SEASON, TARGET)
    d = build_digest(TARGET, tables)
    assert rs.is_current(path, d.digest_hash, "m")
    assert not rs.is_current(path, "0" * 64, "m") and not rs.is_current(path, d.digest_hash, "other")
    assert not rs.is_current(tmp_path / "missing.json", d.digest_hash, "m")


def test_batch_failures_go_to_the_manifest_and_are_retried(tables, tmp_path):
    cfg = Config(root=tmp_path)
    failing = ScriptedClient(fail={"league_match_reasoning"})
    report = run(rs.run_batch(cfg, [TARGET], model_label="m", tables=tables, agent=agent_for(failing), retries=1))
    assert report.counts["template"] == 1 and report.failed == 1
    assert not rs.reasoning_path(cfg, "EPL", SEASON, TARGET).exists()
    lines = [json.loads(x) for x in rs.manifest_path(cfg).read_text().splitlines()]
    assert lines[0]["match_key"] == TARGET and lines[0]["status"] == "template" and lines[0]["model"] == "m"
    # next run picks it up again
    ok = run(rs.run_batch(cfg, [TARGET], model_label="m", tables=tables, agent=agent_for(ScriptedClient())))
    assert ok.counts["passed"] == 1 and rs.reasoning_path(cfg, "EPL", SEASON, TARGET).exists()
    # unknown match keys are recorded, not raised
    bad = run(rs.run_batch(cfg, ["EPL:2023-24:2000-01-01:a-b"], model_label="m", tables=tables,
                           agent=agent_for(ScriptedClient())))
    assert bad.counts["error"] == 1


def test_batch_jsonl_emission_format(tables, digest, tmp_path):
    cfg = Config(root=tmp_path)
    out = tmp_path / "azure" / "batch.jsonl"
    report = run(rs.run_batch(cfg, [TARGET, mkey(TARGET_DATE, W, S)], model_label="azure:gpt-5(high)",
                              deployment="gpt-5", tables=tables, emit_batch=out))
    assert report.counts["emitted"] == 2 and report.tokens > 0
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert len(lines) == 2
    first = lines[0]
    assert first["custom_id"] == TARGET and first["method"] == "POST" and first["url"] == "/chat/completions"
    body = first["body"]
    assert body["model"] == "gpt-5" and body["reasoning_effort"] == "high"
    assert body["messages"][0] == {"role": "system", "content": prompts.LEAGUE_REASONER}
    user = json.loads(body["messages"][1]["content"])
    assert user["task"] == "league_match_reasoning" and user["digest"] == digest.payload()
    assert "names" not in user["digest"] and "Northfield" not in body["messages"][1]["content"]
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(MatchReasoningDraft.model_fields)
    flat = json.dumps(schema)
    assert '"minimum"' not in flat and '"maximum"' not in flat
    for sub in schema["$defs"].values():
        if sub.get("type") == "object":
            assert sub["additionalProperties"] is False and sub["required"] == list(sub["properties"])
    # nothing is stored when only emitting, and a finished match is left out of the next file
    assert not cfg.reasoning_dir.exists() or not any(cfg.reasoning_dir.rglob("*.json"))
    run(rs.run_batch(cfg, [TARGET], model_label="azure:gpt-5(high)", tables=tables, agent=agent_for(ScriptedClient())))
    again = run(rs.run_batch(cfg, [TARGET], model_label="azure:gpt-5(high)", deployment="gpt-5", tables=tables,
                             emit_batch=out))
    assert again.counts["skipped"] == 1 and out.read_text() == ""


def test_main_selects_and_runs_offline(tables, tmp_path, monkeypatch, capsys):
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(Tables, "load", classmethod(lambda cls, c: tables))
    args = type("Args", (), {"leagues": [League.EPL], "seasons": [SEASON], "limit": 3})()
    assert rs.main(cfg, args) == 0
    assert len(list(cfg.reasoning_dir.rglob("*.json"))) == 3
    assert "passed" in capsys.readouterr().out
    assert len(list(rs.iter_reasonings(cfg, ["EPL"], [SEASON]))) == 3
