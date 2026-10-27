"""Playbook aggregation, the knowledge bundle (export -> load), its queries and its API, on fictional data."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from matchlens.agents import templates
from matchlens.agents.llm import build_clients
from matchlens.api import kb_routes
from matchlens.leaguedata import kb as kb_mod
from matchlens.leaguedata import playbook as pb
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.contracts import (
    ClubProfile,
    FormationShare,
    ManagerProfile,
    MatchReasoning,
    MatchReasoningDraft,
    Mentality,
    Metric,
    PatternTag,
    PlayerProfile,
    Provenance,
    TacticalPatternDraft,
    TeamStyle,
)
from matchlens.leaguedata.schema import League, Match, SourceIds

SEASON, PREV = "2023-24", "2022-23"
T = PatternTag


def mkey(date, home, away, season=SEASON):
    return f"EPL:{season}:{date}:{home}-{away}"


def mk_match(date, home, away, hg, ag, hm="mgr-old", am="mgr-x", season=SEASON) -> Match:
    return Match(match_key=mkey(date, home, away, season), league=League.EPL, season=season, date=date,
                 home_id=home, away_id=away, home_goals=hg, away_goals=ag, home_manager_id=hm, away_manager_id=am)


def mk_reasoning(m: Match, patterns, headline="A tidy game", verdict="deserved") -> MatchReasoning:
    draft = MatchReasoningDraft(
        verdict=verdict, headline=headline, why_won="They created the better chances.", how=["pressing"],
        patterns=[TacticalPatternDraft(tag=t, side=s, description=f"{t.value} shown", evidence=["S01"], strength=x)
                  for t, s, x in patterns],
        turning_points=[], home_note="h", away_note="a", confidence=0.7, limitations=[])
    return MatchReasoning(
        match_key=m.match_key, league=m.league, season=m.season, home_id=m.home_id, away_id=m.away_id,
        score=f"{m.home_goals}-{m.away_goals}", draft=draft,
        provenance=Provenance(model="test", prompt_version="x", digest_hash="h", fact_check="passed",
                              created_at="2024-01-01T00:00:00+00:00"))


def mk_style(team_id, name, season=SEASON, league=League.EPL, ppda_pct=80.0, manager="mgr-new") -> TeamStyle:
    return TeamStyle(
        team_id=team_id, team_name=name, league=league, season=season, matches=38,
        formations=[FormationShare(formation="4-3-3", share=0.75), FormationShare(formation="4-2-3-1", share=0.25)],
        manager_id=manager,
        metrics=[Metric(key="ppda", value=8.2, pct=ppda_pct, z=1.0, source="understat"),
                 Metric(key="deep_share", value=0.31, pct=40.0, z=-0.2, is_proxy=True, source="understat"),
                 Metric(key="directness", value=0.12, pct=10.0, z=-1.4, is_proxy=True, source="understat")])


def mk_player(pid, name, team, minutes, tags, season=SEASON) -> PlayerProfile:
    return PlayerProfile(
        player_id=pid, name=name, ids=SourceIds(), team_id=team, season=season, league=League.EPL,
        positions=["FW"], minutes=minutes,
        per90=[Metric(key="npxg", value=0.5, source="understat"), Metric(key="xa", value=0.2, source="understat")],
        shot_profile=[], role_tags=tags)


# The fictional season of Riverton Rovers (riverton): four matches, the manager changes after two.
M1 = mk_match("2023-08-12", "riverton", "ashford", 2, 0)
M2 = mk_match("2023-08-26", "belmont", "riverton", 1, 1, hm="mgr-b", am="mgr-old")
M3 = mk_match("2023-10-07", "riverton", "castle-town", 0, 1, hm="mgr-new")
M4 = mk_match("2023-10-21", "ashford", "riverton", 0, 3, hm="mgr-a", am="mgr-new")
MATCHES = [M1, M2, M3, M4]
REASONINGS = [
    mk_reasoning(M1, [(T.HIGH_PRESS, "home", 0.9), (T.COUNTER_ATTACK, "home", 0.4), (T.LOW_BLOCK, "away", 0.5)],
                 "Riverton press Ashford into errors"),
    mk_reasoning(M2, [(T.HIGH_PRESS, "home", 0.7), (T.SET_PIECE_THREAT, "away", 0.6)], "Belmont hold on for a draw",
                 verdict="even"),
    mk_reasoning(M3, [(T.LOW_BLOCK, "away", 0.8), (T.HIGH_PRESS, "home", 0.3)], "Castle Town frustrate Riverton",
                 verdict="smash_and_grab"),
    mk_reasoning(M4, [(T.HIGH_PRESS, "away", 0.95), (T.COUNTER_ATTACK, "away", 0.8), (T.DEFENSIVE_ERRORS, "home", 0.6)],
                 "Riverton run riot at Ashford", verdict="dominant"),
]
MENT = Mentality(team_id="riverton", season=SEASON, matches=4, comeback_rate=0.25, points_from_losing=1.0,
                 lead_protection=1.0, late_goal_share=0.4, late_concede_share=0.0, response_after_conceding=1.2,
                 big_game_ppg=1.5, knockout_record=None, red_cards_per_match=0.0, finishing_streak=1.5,
                 notes=["1 comeback from a losing position in 2 matches in which they trailed",
                        "2 of 5 goals scored from the 75th minute on"])


def freq(agg, side, tag):
    return next(f for f in getattr(agg, f"patterns_{side}") if f.tag == tag)


# ------------------------------------------------------------------ aggregation


def test_aggregate_shares_win_rates_and_examples():
    agg = pb.aggregate("riverton", League.EPL, SEASON, REASONINGS, MATCHES)
    assert (agg.matches, agg.wins, agg.draws, agg.losses) == (4, 2, 1, 1)
    press = freq(agg, "for", T.HIGH_PRESS)  # M1 (W), M3 (L), M4 (W); M2 shows it for the opponent
    assert press.share == pytest.approx(0.75)
    assert press.win_rate == pytest.approx(2 / 3, abs=1e-3)
    assert press.example_keys == [M4.match_key, M1.match_key, M3.match_key]  # strongest first
    counter = freq(agg, "for", T.COUNTER_ATTACK)  # M1 and M4, both won
    assert counter.share == pytest.approx(0.5) and counter.win_rate == 1.0
    assert freq(agg, "for", T.SET_PIECE_THREAT).share == pytest.approx(0.25)  # M2, a draw
    assert freq(agg, "for", T.SET_PIECE_THREAT).win_rate == 0.0
    # against: Ashford's low block (M1), Belmont's press (M2), Castle Town's low block (M3), Ashford's errors (M4)
    low = freq(agg, "against", T.LOW_BLOCK)
    assert low.share == pytest.approx(0.5) and low.win_rate == pytest.approx(0.5)
    assert freq(agg, "against", T.HIGH_PRESS).share == pytest.approx(0.25)
    assert [f.share for f in agg.patterns_for] == sorted((f.share for f in agg.patterns_for), reverse=True)
    assert agg.evidence_matches and set(agg.evidence_matches) <= {m.match_key for m in MATCHES}
    assert len(agg.headlines) == 4 and agg.headlines[0].match_key == M1.match_key and agg.headlines[0].result == "W"


def test_aggregate_ignores_other_teams_and_seasons_and_works_without_match_rows():
    other = mk_match("2023-09-02", "ashford", "belmont", 1, 0)
    old = mk_match("2022-09-02", "riverton", "belmont", 3, 0, season=PREV)
    rs = [*REASONINGS, mk_reasoning(other, [(T.HIGH_PRESS, "home", 0.9)]),
          mk_reasoning(old, [(T.HIGH_PRESS, "home", 0.9)])]
    agg = pb.aggregate("riverton", League.EPL, SEASON, rs, [])  # no Match rows: the score is parsed
    assert agg.matches == 4 and (agg.wins, agg.draws, agg.losses) == (2, 1, 1)
    assert agg.manager_id is None and agg.manager_splits == []
    assert pb.aggregate("nobody", League.EPL, SEASON, rs, []).matches == 0


def test_manager_splits_only_when_the_manager_changed():
    agg = pb.aggregate("riverton", League.EPL, SEASON, REASONINGS, MATCHES)
    assert agg.manager_id == "mgr-new"
    assert [(s.manager_id, s.matches) for s in agg.manager_splits] == [("mgr-old", 2), ("mgr-new", 2)]
    old, new = agg.manager_splits
    assert (old.first_date, old.last_date) == ("2023-08-12", "2023-08-26")
    assert next(f for f in old.patterns_for if f.tag == T.HIGH_PRESS).share == pytest.approx(0.5)
    assert next(f for f in new.patterns_for if f.tag == T.HIGH_PRESS).share == pytest.approx(1.0)
    same = [mk_match(m.date, m.home_id, m.away_id, m.home_goals, m.away_goals, hm="m1", am="m1") for m in MATCHES]
    same = [m.model_copy(update={"home_manager_id": "m1" if m.home_id == "riverton" else "z",
                                 "away_manager_id": "m1" if m.away_id == "riverton" else "z"}) for m in same]
    assert pb.aggregate("riverton", League.EPL, SEASON, REASONINGS, same).manager_splits == []


# ------------------------------------------------------------------ playbook text


def prepared(players=()):
    style = mk_style("riverton", "Riverton Rovers")
    names = {"ashford": "Ashford Athletic", "belmont": "Belmont", "castle-town": "Castle Town"}
    return pb.prepare_playbook("riverton", League.EPL, SEASON, reasonings=REASONINGS, matches=MATCHES, style=style,
                               mentality=MENT, players=players, names=names)


PLAYERS = [mk_player("p-ada", "Ada Quill", "riverton", 3000, ["creator"]),
           mk_player("p-bo", "Bo Marsh", "riverton", 2500, ["aerial threat"]),
           mk_player("p-cy", "Cy Dunn", "riverton", 200, [])]


def test_template_validates_and_uses_only_given_numbers():
    prep = prepared(PLAYERS)
    draft = pb.template_draft(prep.payload)
    assert pb.check_draft(draft, prep.payload) == []
    assert "high press in 75% of matches" in " ".join(draft.strengths)
    assert draft.key_players[0] in {"p-ada", "p-bo", "p-cy"}
    assert templates.TASKS["league_playbook"](prep.payload) == draft  # the offline model answers with it


def test_check_draft_blocks_invented_numbers_and_players():
    prep = prepared(PLAYERS)
    good = pb.template_draft(prep.payload)
    bad = good.model_copy(update={"strengths": ["Won 87% of matches", "Scored 33 goals"], "key_players": ["p-ghost"]})
    problems = pb.check_draft(bad, prep.payload)
    assert any("numbers not in the data" in p and "87" in p for p in problems)
    assert any("p-ghost" in p for p in problems)
    assert pb.check_draft(good.model_copy(update={"summary": ""}), prep.payload)


def test_candidates_rank_by_minutes_weighted_output():
    cands = pb.candidate_players(PLAYERS)
    assert [c["player_id"] for c in cands] == ["p-ada", "p-bo", "p-cy"]


def test_payload_is_compact_and_has_no_defaults_to_guess():
    payload = prepared(PLAYERS).payload
    assert payload["task"] == "league_playbook" and payload["record"]["matches"] == 4
    assert payload["headlines"][0]["opponent"] == "Ashford Athletic"
    assert payload["mentality"]["notes"] and "team_id" not in payload["mentality"]
    assert len(json.dumps(payload)) < 12_000


def test_build_playbook_template_and_offline_model_paths():
    prep = prepared(PLAYERS)
    built = asyncio.run(pb.build_playbook(prep, None))
    assert built.fact_check == "template" and built.playbook.summary and built.playbook.team_id == "riverton"
    assert built.playbook.evidence_matches == prep.agg.evidence_matches
    offline = asyncio.run(pb.build_playbook(prep, build_clients()))  # OfflineChatClient answers from the template
    assert offline.fact_check == "passed"
    assert offline.playbook.patterns_for == built.playbook.patterns_for
    assert asyncio.run(pb.build_playbook(pb.prepare_playbook(
        "nobody", League.EPL, SEASON, reasonings=[], matches=[], style=mk_style("nobody", "Nobody")),
        build_clients())).fact_check == "template"


def test_build_playbook_repairs_once_then_falls_back(monkeypatch):
    prep = prepared(PLAYERS)
    good = pb.template_draft(prep.payload)
    bad = good.model_copy(update={"summary": "They scored 4871 goals"})

    def run(answers):
        queue = list(answers)

        async def fake(agent, payload, timeout):
            return queue.pop(0), None

        monkeypatch.setattr(pb, "_call", fake)
        return asyncio.run(pb.build_playbook(prep, build_clients()))

    repaired = run([bad, good])
    assert repaired.fact_check == "repaired" and repaired.problems
    fallen = run([bad, bad])
    assert fallen.fact_check == "template" and "4871" in " ".join(fallen.problems)


def test_playbooks_resume_by_inputs_hash(tmp_path):
    cfg = Config(root=tmp_path)
    prep = prepared(PLAYERS)
    built = asyncio.run(pb.build_playbook(prep, None))
    digest = prep.inputs_hash("template")
    path = pb.save_playbook(cfg, built, digest)
    assert path == pb.playbook_path(cfg, League.EPL, SEASON, "riverton")
    assert pb.stored_hash(path) == digest
    assert prepared(PLAYERS).inputs_hash("template") == digest
    assert prepared(PLAYERS[:2]).inputs_hash("template") != digest
    assert [p.team_id for p in pb.load_playbooks(cfg)] == ["riverton"]


# ------------------------------------------------------------------ the bundle


def make_bundle(path):
    styles = [mk_style("riverton", "Riverton Rovers"),
              mk_style("riverton", "Riverton Rovers", season=PREV, ppda_pct=55.0),
              mk_style("ashford", "Ashford Athletic", manager=None),
              mk_style("belmont", "FC Bélmont", manager=None),
              mk_style("man-city-x", "Manchester Citadel", manager=None),
              mk_style("man-utd-x", "Manchester Unified", manager=None),
              mk_style("riverton", "Riverton Rovers", league=League.UCL)]
    players = [*PLAYERS, mk_player("p-ada", "Ada Quill", "riverton", 2800, ["creator", "set-piece taker"], season=PREV)]
    club = ClubProfile(team_id="riverton", name="Riverton Rovers", country="Fictionland", founded=1890,
                       stadium="Mill Park", honours=["Fiction Cup 2019-20"], rivals=[], manager_timeline=[],
                       history="A fictional club.", attribution=["Wikidata, CC0"])
    mgr = ManagerProfile(manager_id="mgr-new", name="Nia Fenn", nationality="Fictionland", tenures=[],
                         trophies=[], formations=[FormationShare(formation="4-3-3", share=1.0)], philosophy=None,
                         attribution=[])
    prep = prepared(PLAYERS)
    book = asyncio.run(pb.build_playbook(prep, None)).playbook
    kb_mod.write_bundle(path, styles=styles, mentality=[MENT], players=players, clubs={"riverton": club},
                        managers={"mgr-new": mgr}, reasonings=REASONINGS, playbooks=[book], curated_version="v1")
    return path


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp("kb") / "kb")


@pytest.fixture(scope="module")
def kb(bundle):
    return kb_mod.KnowledgeBase.load(bundle)


def test_bundle_layout_and_index(bundle):
    names = {p.name for p in bundle.iterdir()}
    assert names == {"teams.json", "mentality.json", "players.jsonl", "clubs.json", "managers.json",
                     "reasonings.jsonl", "playbooks.json", "index.json"}
    index = json.loads((bundle / "index.json").read_text())
    assert index["format"] == 1 and index["curated_version"] == "v1" and index["built_at"]
    assert index["counts"]["reasonings"] == 4 and index["counts"]["players"] == 4
    assert index["counts"]["playbooks"] == 1 and index["counts"]["teams"] == 5
    assert (bundle / "reasonings.jsonl").read_text().count("\n") == 4
    assert not (bundle.parent / "kb.partial").exists()
    make_bundle(bundle)  # re-export replaces the old bundle cleanly
    assert not (bundle.parent / "kb.old").exists()


def test_round_trip_and_team_queries(kb):
    assert kb.team_style("riverton").season == SEASON  # latest
    assert kb.team_style("riverton").league == League.EPL  # the domestic style wins over the UCL one
    assert kb.team_style("riverton", PREV).metric("ppda").pct == 55.0
    assert kb.team_style("riverton", "1999-00") is None and kb.team_style("nobody") is None
    assert kb.mentality("riverton").notes == MENT.notes and kb.mentality("riverton", PREV) is None
    assert kb.playbook("riverton").summary and kb.playbook("riverton", PREV) is None
    assert kb.club("riverton").stadium == "Mill Park" and kb.club("ashford") is None
    assert kb.manager("mgr-new").name == "Nia Fenn"
    assert kb.seasons("riverton") == [PREV, SEASON]
    listing = {t["team_id"]: t for t in kb.list_teams()}
    assert listing["riverton"]["leagues"] == ["EPL"] or "EPL" in listing["riverton"]["leagues"]
    assert listing["riverton"]["latest_season"] == SEASON


def test_player_queries(kb):
    assert kb.player("p-ada").season == SEASON and kb.player("p-ada", PREV).minutes == 2800
    assert kb.player("p-ada", "1999-00") is None and kb.player("p-ghost") is None
    assert kb.player_tags("p-ada") == ["creator"] and kb.player_tags("p-ghost") == []
    assert [p.player_id for p in kb.team_players("riverton")] == ["p-ada", "p-bo", "p-cy"]


def test_match_queries(kb):
    keys = lambda rows: [m.match_key for m in rows]  # noqa: E731
    assert keys(kb.matches(team="riverton")) == [M4.match_key, M3.match_key, M2.match_key, M1.match_key]  # newest first
    assert keys(kb.matches(team="Ashford Athletic")) == [M4.match_key, M1.match_key]  # names resolve
    assert keys(kb.matches(team="riverton", opponent="ashford")) == [M4.match_key, M1.match_key]
    assert keys(kb.matches(team="riverton", result="win")) == [M4.match_key, M1.match_key]
    assert keys(kb.matches(team="riverton", result="loss")) == [M3.match_key]
    assert keys(kb.matches(team="riverton", result="draw")) == [M2.match_key]
    assert kb.matches(result="win") == []  # a result needs a team
    assert keys(kb.matches(team="riverton", tags=["high_press"])) == [M4.match_key, M3.match_key, M1.match_key]
    assert keys(kb.matches(team="riverton", tags=["high_press", "counter_attack"])) == [M4.match_key, M1.match_key]
    assert keys(kb.matches(tags=[T.LOW_BLOCK])) == [M3.match_key, M1.match_key]  # either side without a team
    assert keys(kb.matches(season=PREV)) == [] and keys(kb.matches(league="EPL", limit=2)) == [M4.match_key, M3.match_key]
    assert kb.matches(league=League.LA_LIGA) == [] and kb.matches(team="nobody") == []
    first = kb.matches(team="riverton", limit=1)[0]
    assert first.home_name == "Ashford Athletic" and first.result_for("riverton") == "win"
    assert first.result_for("zzz") is None


def test_search_scores_headlines_tags_and_teams(kb):
    hits = kb.search("press")
    assert hits and hits[0].kind == "match" and hits[0].id in {M1.match_key, M4.match_key}
    assert {h.id for h in hits} >= {M1.match_key, M2.match_key, M3.match_key, M4.match_key}
    top = kb.search("riverton run riot")
    assert top[0].id == M4.match_key
    assert kb.search("castle")[0].id == M3.match_key
    assert any(h.kind == "team" and h.id == "ashford" for h in kb.search("ashford"))
    assert kb.search("zzzzqq") == [] and kb.search("   ") == [] and len(kb.search("press", limit=2)) == 2


def test_resolve_team_fuzzy(kb):
    assert kb.resolve_team("riverton") == "riverton"
    assert kb.resolve_team("Riverton Rovers") == "riverton"
    assert kb.resolve_team("Riverton Rovers FC") == "riverton"
    assert kb.resolve_team("riverton-rovers") == "riverton"
    assert kb.resolve_team("RIVERTON") == "riverton"
    assert kb.resolve_team("Belmont") == "belmont" and kb.resolve_team("Bélmont FC") == "belmont"
    assert kb.resolve_team("Ashford") == "ashford"
    assert kb.resolve_team("Rivertown Rovers") == "riverton"  # one letter off
    assert kb.resolve_team("Manchester Citadel") == "man-city-x"
    assert kb.resolve_team("Man Utd Unified") is None or kb.resolve_team("Man Utd Unified") == "man-utd-x"
    assert kb.resolve_team("Manchester") is None  # ambiguous
    assert kb.resolve_team("Totally Unknown Town") is None and kb.resolve_team("") is None


def test_team_context_shape_and_size(kb):
    ctx = kb.team_context("riverton")
    assert set(ctx) >= {"team", "manager", "formations", "style", "patterns_for", "patterns_against", "strengths",
                        "weaknesses", "game_state", "mentality", "key_players", "recent"}
    assert ctx["team"]["name"] == "Riverton Rovers" and ctx["manager"] == {"manager_id": "mgr-new", "name": "Nia Fenn"}
    assert ctx["formations"][0] == "4-3-3 75%"
    assert ctx["style"][0]["key"] == "directness" or ctx["style"][0]["key"] == "ppda"  # most extreme percentile first
    assert {"key", "value", "pct", "proxy"} <= set(ctx["style"][0])
    assert ctx["patterns_for"][0] == {"tag": "high_press", "share_pct": 75, "win_pct": 67}
    assert ctx["key_players"][0]["player_id"] and "tags" in ctx["key_players"][0]
    assert [r["match_key"] for r in ctx["recent"]] == [M4.match_key, M3.match_key, M2.match_key]
    assert len(json.dumps(ctx)) < 4000
    assert kb.team_context("nobody") == {}
    bare = kb.team_context("ashford")  # no playbook, mentality or players: still well formed
    assert bare["team"]["team_id"] == "ashford" and "patterns_for" not in bare and len(json.dumps(bare)) < 4000


def test_team_context_stays_under_budget_with_long_text(kb):
    long = "x" * 400
    book = kb.playbook("riverton").model_copy(update={"strengths": [long] * 6, "weaknesses": [long] * 6,
                                                      "game_state": [long] * 6})
    kb._playbooks[("riverton", SEASON)] = book
    try:
        assert len(json.dumps(kb.team_context("riverton"))) < 4000
    finally:
        kb._playbooks[("riverton", SEASON)] = kb_mod.KnowledgeBase.load(kb.directory).playbook("riverton")


def test_load_rejects_unknown_format(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"format": 99}))
    with pytest.raises(ValueError):
        kb_mod.KnowledgeBase.load(tmp_path)
    with pytest.raises(FileNotFoundError):
        kb_mod.KnowledgeBase.load(tmp_path / "missing")


def test_get_kb_reads_the_environment(monkeypatch, bundle, tmp_path):
    kb_mod.reset_cache()
    monkeypatch.delenv("MATCHLENS_KB_DIR", raising=False)
    assert kb_mod.get_kb() is None
    monkeypatch.setenv("MATCHLENS_KB_DIR", str(tmp_path / "nothing-here"))
    assert kb_mod.get_kb() is None
    monkeypatch.setenv("MATCHLENS_KB_DIR", str(bundle))
    first = kb_mod.get_kb()
    assert first is not None and first is kb_mod.get_kb()  # cached
    kb_mod.reset_cache()


# ------------------------------------------------------------------ API


@pytest.fixture
def client(monkeypatch):
    kb_mod.reset_cache()
    app = FastAPI()
    app.include_router(kb_routes.router)
    yield TestClient(app)
    kb_mod.reset_cache()


@pytest.mark.parametrize("url", ["/api/kb/teams", "/api/kb/teams/riverton", "/api/kb/players/p-ada",
                                 "/api/kb/matches", "/api/kb/search?q=press"])
def test_routes_404_when_unconfigured(client, monkeypatch, url):
    monkeypatch.delenv("MATCHLENS_KB_DIR", raising=False)
    r = client.get(url)
    assert r.status_code == 404 and r.json() == {"detail": "knowledge base not configured"}


def test_routes_serve_the_bundle(client, monkeypatch, bundle):
    monkeypatch.setenv("MATCHLENS_KB_DIR", str(bundle))
    teams = client.get("/api/kb/teams").json()
    assert {t["team_id"] for t in teams} >= {"riverton", "ashford"}
    body = client.get("/api/kb/teams/riverton").json()
    assert body["style"]["season"] == SEASON and body["playbook"]["team_id"] == "riverton"
    assert body["mentality"]["comeback_rate"] == 0.25 and body["club"]["stadium"] == "Mill Park"
    assert body["manager"]["name"] == "Nia Fenn"
    assert client.get("/api/kb/teams/Riverton Rovers").json()["team_id"] == "riverton"
    assert client.get(f"/api/kb/teams/riverton?season={PREV}").json()["style"]["season"] == PREV
    assert client.get("/api/kb/teams/nobody").status_code == 404
    player = client.get("/api/kb/players/p-ada").json()
    assert player["player"]["name"] == "Ada Quill" and player["tags"] == ["creator"]
    assert client.get("/api/kb/players/p-ghost").status_code == 404

    rows = client.get("/api/kb/matches", params={"team": "riverton", "result": "win", "tags": "high_press"}).json()
    assert [r["match_key"] for r in rows] == [M4.match_key, M1.match_key]
    assert len(client.get("/api/kb/matches", params={"limit": 2}).json()) == 2
    assert client.get("/api/kb/matches", params={"tags": "not_a_tag"}).status_code == 422
    assert client.get("/api/kb/matches", params={"league": "MARS"}).status_code == 422
    assert client.get("/api/kb/matches", params={"result": "win"}).status_code == 422
    assert client.get("/api/kb/matches", params={"limit": 0}).status_code == 422
    hits = client.get("/api/kb/search", params={"q": "castle"}).json()
    assert hits[0]["id"] == M3.match_key
    assert client.get("/api/kb/search").status_code == 422


# ------------------------------------------------------------------ CLI end to end (needs DuckDB)


def test_load_reasonings_skips_blocked(tmp_path):
    cfg = Config(root=tmp_path)
    blocked = REASONINGS[0].model_copy(update={"provenance": REASONINGS[0].provenance.model_copy(
        update={"fact_check": "blocked"})})
    for r in (blocked, REASONINGS[1]):
        path = cfg.reasoning_dir / "EPL" / SEASON / (r.match_key.replace(":", "_") + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(r.model_dump_json())
    assert [r.match_key for r in pb.load_reasonings(cfg)] == [REASONINGS[1].match_key]


def test_playbooks_and_export_commands_on_curated_tables(tmp_path):
    pytest.importorskip("duckdb")
    from types import SimpleNamespace

    from matchlens.leaguedata import build as build_mod
    from matchlens.leaguedata import curate as cur
    from matchlens.leaguedata.schema import Club, Player, PlayerMatch, Shot, TeamMatchStats, TextDoc

    cfg = Config(root=tmp_path)
    shot = Shot(shot_id="s1", match_key=M1.match_key, team_id="riverton", player_id="p-ada", assist_player_id=None,
                minute=20, x=94.0, y=34.0, xg=0.3, result="goal", situation="open_play", body_part="right_foot",
                last_action=None, home_score_before=0, away_score_before=0)
    tables = {
        "matches": MATCHES,
        "shots": [shot],
        "team_match_stats": [TeamMatchStats(match_key=M1.match_key, team_id=t, is_home=t == "riverton", goals=g,
                                            xg=1.0, ppda=9.0, ppda_allowed=11.0, deep=8, deep_allowed=6)
                             for t, g in (("riverton", 2), ("ashford", 0))],
        "player_matches": [PlayerMatch(match_key=M1.match_key, player_id="p-ada", team_id="riverton", minutes=90,
                                       goals=1, shots=1, xg=0.3)],
        "players": [Player(player_id="p-ada", name="Ada Quill")],
        "text_docs": [TextDoc(entity_kind="club", entity_id="riverton", title="Riverton Rovers", text="A club.",
                              url="https://example.test/wiki/Riverton", licence="CC BY-SA 4.0",
                              retrieved="2026-01-01")],
        "clubs": [Club(team_id=t, name=n) for t, n in (("riverton", "Riverton Rovers"), ("ashford", "Ashford Athletic"),
                                                      ("belmont", "Belmont"), ("castle-town", "Castle Town"))],
    }
    build_mod._write_tables(cfg, tables)  # noqa: SLF001
    cur.curate(cfg)
    for r in REASONINGS:
        path = cfg.reasoning_dir / "EPL" / SEASON / (r.match_key.replace(":", "_") + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(r.model_dump_json())
    args = SimpleNamespace(leagues=[League.EPL], seasons=[SEASON], limit=None, refresh=False)

    assert pb.main(cfg, args) == 0
    files = sorted((cfg.root / "playbooks" / "EPL" / SEASON).glob("*.json"))
    assert [f.stem for f in files] == ["ashford", "belmont", "castle-town", "riverton"]
    stamp = files[-1].stat().st_mtime_ns
    assert pb.main(cfg, args) == 0  # resume: nothing rebuilt
    assert files[-1].stat().st_mtime_ns == stamp
    book = next(p for p in pb.load_playbooks(cfg) if p.team_id == "riverton")
    assert book.style.team_name == "Riverton Rovers" and book.patterns_for and book.mentality is not None
    assert "p-ada" in book.key_players

    assert kb_mod.main(cfg, args) == 0
    loaded = kb_mod.KnowledgeBase.load(cfg.kb_dir)
    assert loaded.team_style("riverton").matches == 4 and loaded.playbook("riverton") is not None
    assert loaded.player("p-ada").name == "Ada Quill" and len(loaded.matches(team="riverton")) == 4
    assert loaded.club("riverton") is not None and loaded.mentality("riverton").matches == 4
    assert loaded.resolve_team("Riverton Rovers FC") == "riverton"
    assert 0 < len(json.dumps(loaded.team_context("riverton"))) < 4000
