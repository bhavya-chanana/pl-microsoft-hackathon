"""Tactics Board creative agents: personas, repair, diversity, selection, verdict and cassette keys.

No network: the model is the offline template client or a scripted one.
"""

from __future__ import annotations

import asyncio
import hashlib
import math

import pytest

from matchlens.agents import tactician, templates
from matchlens.agents.cassette import Cassette, CassetteChatClient, request_key
from matchlens.agents.llm import LlmSettings, ModelClients, OfflineChatClient, build_clients, is_reasoning_model, reasoning_family
from matchlens.agents.tactician import (
    PERSONAS,
    TacticDraftSet,
    VerdictDraft,
    build_payload,
    check_verdict,
    numbers_of,
    text_number_allowance,
    text_number_problems,
    unknown_names,
    context_keys,
    ideate,
    offline_verdict,
    select_diverse,
    verdict,
    verdict_payload,
)
from matchlens.domain.models import EntityPosition, TrackingFrame
from matchlens.simulator.engine import generate_match
from matchlens.tactics import playbook
from matchlens.tactics.intent import Mechanism, TacticIntent
from matchlens.tactics.models import Interval, OutcomeStats, PlayerSpot, Situation, TacticResult
from matchlens.tactics.validate import check_intent

from .test_agents import OFFLINE, ScriptedClient

PLAY_KEYS = set(playbook.PLAYBOOK_KEYS)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ helpers


def make_situation(match, elapsed: float = 600.0, tags: dict[str, list[str]] | None = None) -> Situation:
    """The demo match frozen at ``elapsed``; the team with the ball is the one being coached."""
    meta = match.meta
    frame = min(match.tracking, key=lambda f: abs(f.elapsed - elapsed))
    pos = {e.entity_id: (e.x, e.y) for e in frame.entities}
    team_of = {p: t.team_id for t in (meta.home, meta.away) for p in t.starting_xi + t.bench}
    bx, by = pos["ball"]
    carrier = min((p for p in pos if p in team_of), key=lambda p: math.hypot(pos[p][0] - bx, pos[p][1] - by))
    team = team_of[carrier]
    tags = tags or {}
    spots = [PlayerSpot(player_id=p, name=meta.players[p].name, team_id=team_of[p], role=meta.players[p].role,
                        x=x, y=y, tags=tags.get(p, [])) for p, (x, y) in pos.items() if p in team_of]
    frame = TrackingFrame(match_id=meta.match_id, clock=frame.clock, elapsed=frame.elapsed, period=frame.period,
                          entities=[EntityPosition(entity_id=k, x=x, y=y, speed=0.0) for k, (x, y) in pos.items()])
    rx = bx if team == meta.home.team_id else 105 - bx
    return Situation(
        match_id=meta.match_id, elapsed=frame.elapsed, minute_label="10'", period=1,
        score={meta.home.team_id: 0, meta.away.team_id: 0}, possession_team_id=team, carrier_id=carrier,
        ball=(bx, by), ball_zone="centre:mid", phase="final_third" if rx > 70 else "progression",
        coaching_team_id=team, players=spots, frame=frame)


@pytest.fixture(scope="module")
def meta(demo_match):
    return demo_match.meta


@pytest.fixture(scope="module")
def situation(demo_match):
    return make_situation(demo_match)


@pytest.fixture(autouse=True)
def fast_stagger(monkeypatch):
    monkeypatch.setattr(tactician, "STAGGER_S", 0.02)


def offline_clients(client=None) -> ModelClients:
    client = client or OfflineChatClient()
    return ModelClients(fast=client, strong=client, settings=OFFLINE)


def valid(meta, situation, intent) -> bool:
    return not check_intent(meta, situation, intent, 10.0, PLAY_KEYS) and intent.team_id == situation.coaching_team_id


def outcome(p_shot=0.3, xg=0.12, concede=0.2, **kw) -> OutcomeStats:
    def iv(v):
        return Interval(value=v, low=max(0.0, v - 0.1), high=min(1.0, v + 0.1))
    base = dict(rollouts=24, horizon_s=10.0, p_shot=iv(p_shot), mean_xg=xg, p_goal=iv(0.05), p_box_entry=iv(0.4),
                p_turnover=iv(0.3), p_concede_shot=iv(concede), xt_delta=0.02, time_to_shot_s=5.2,
                terminals={"horizon": 20, "shot": 4})
    base.update(kw)
    return OutcomeStats(**base)


def result(intent: TacticIntent, persona: str, utility: float, **kw) -> TacticResult:
    return TacticResult(tactic_id=f"t-{abs(hash((intent.title, persona))) % 10**8:08d}", persona=persona,
                        intent=intent, outcome=outcome(), utility=utility, status="ready", **kw)


def blank(team: str, **kw) -> TacticIntent:
    base = dict(team_id=team, title="t", idea="i", mechanism=Mechanism.OVERLOAD, directives=[], ball_plan=[],
                press=None, style_changes=[], rest_defence=2, cites=[])
    base.update(kw)
    return TacticIntent(**base)


# --------------------------------------------------------------------- ideate


def test_offline_ideate_returns_distinct_valid_mechanisms(meta, situation):
    streamed: list[tuple[str, TacticIntent]] = []

    async def on_tactic(persona, intent):
        streamed.append((persona, intent))

    results, errors = run(ideate(offline_clients(), situation, {}, meta=meta, prompt=None, n=10, language="en",
                                 on_tactic=on_tactic))
    assert not errors
    assert len(results) >= 3 and streamed == results
    assert len({i.mechanism for _, i in results}) >= 3
    assert all(valid(meta, situation, i) for _, i in results)
    assert {p for p, _ in results} <= set(PERSONAS)
    assert len({i.model_dump_json() for _, i in results}) == len(results)


def test_n_caps_what_is_returned_and_streamed(meta, situation):
    seen = []

    async def on_tactic(persona, intent):
        seen.append(intent)

    results, _ = run(ideate(offline_clients(), situation, {}, meta=meta, n=3, on_tactic=on_tactic))
    assert len(results) == 3 == len(seen)


def test_claimed_mechanisms_reach_later_calls(meta, situation):
    claims: dict[str, list[str]] = {}

    def spy(draft, payload):
        claims[payload["persona"]["name"]] = list(payload["claimed_mechanisms"])
        return draft

    results, _ = run(ideate(offline_clients(ScriptedClient(tamper={"tactics": spy})), situation, {}, meta=meta))
    order = list(PERSONAS)
    assert claims[order[0]] == []
    assert claims[order[-1]], "the last persona must see what the earlier ones claimed"
    landed = {i.mechanism.value for _, i in results}
    assert set(claims[order[-1]]) <= landed
    # Each persona is told about the claims of those that landed before it.
    sizes = [len(claims[p]) for p in order]
    assert sizes == sorted(sizes)


def test_invalid_intent_gets_one_repair_turn(meta, situation):
    asked: list[dict] = []

    def break_first_time(draft, payload):
        asked.append(payload)
        if payload["persona"]["name"] == "positional" and "problems" not in payload:
            bad = draft.tactics[0].model_copy(update={"cites": ["made:up:key"]})
            return TacticDraftSet(tactics=[bad, draft.tactics[1]])
        return draft

    client = ScriptedClient(tamper={"tactics": break_first_time})
    results, errors = run(ideate(offline_clients(client), situation, {}, meta=meta))
    positional = [p for p in asked if p["persona"]["name"] == "positional"]
    assert len(positional) == 2, "exactly one repair turn"
    assert any("made:up:key" in p for p in positional[1]["problems"])
    assert positional[1]["previous"][0]["cites"] == ["made:up:key"]
    assert not errors
    mine = [i for p, i in results if p == "positional"]
    assert len(mine) == 2 and all(valid(meta, situation, i) for i in mine)
    assert all("made:up:key" not in i.cites for _, i in results)
    assert len([p for p in asked if p["persona"]["name"] != "positional"]) == len(PERSONAS) - 1


def test_still_invalid_intents_are_dropped(meta, situation):
    def always_bad(draft, payload):
        if payload["persona"]["name"] != "wide":
            return draft
        return TacticDraftSet(tactics=[t.model_copy(update={"team_id": "NOT-A-TEAM"}) for t in draft.tactics])

    results, errors = run(ideate(offline_clients(ScriptedClient(tamper={"tactics": always_bad})), situation, {},
                                 meta=meta))
    assert all(valid(meta, situation, i) for _, i in results)
    assert not [i for p, i in results if p == "wide"]
    assert any("wide: no valid tactics" in e for e in errors)  # and the board is told


def test_failed_calls_fall_back_to_recipes(meta, situation):
    results, errors = run(ideate(offline_clients(ScriptedClient(fail={"tactics"})), situation, {}, meta=meta))
    assert results and all(p == "recipe" for p, _ in results)
    assert all(valid(meta, situation, i) for _, i in results)
    assert len(errors) >= len(PERSONAS)
    assert len({i.mechanism for _, i in results}) >= 3


def test_slow_model_hits_the_deadline_and_falls_back(meta, situation, monkeypatch):
    monkeypatch.setattr(tactician, "MIN_CALL_S", 0.0)
    results, errors = run(ideate(offline_clients(ScriptedClient(delay=2.0)), situation, {}, meta=meta,
                                 deadline_s=0.3))
    assert any("timeout" in e for e in errors)
    assert results and all(p == "recipe" for p, _ in results)


# --------------------------------------------------------------------- payload


def test_payload_shape(meta, situation):
    pid = next(p.player_id for p in situation.players if p.team_id == situation.coaching_team_id)
    tagged = make_situation_with_tags(situation, {pid: ["aerial threat"]})
    team = situation.coaching_team_id
    opp = meta.opponent(team).team_id
    ctx = {team: {"style": {"press_intensity": 1.2}, "playbook": {"patterns": ["high_press"]}, "mentality": {"comeback": 0.4}},
           opp: {"weaknesses": ["slow on the turn"]}}
    base = outcome()
    payload = build_payload(tagged, ctx, base, "  go direct down the left ", "maverick", 2, ["overload", "overload"],
                            "es", meta=meta, snapshot_digest="abc123")
    assert payload["task"] == "tactics" and payload["language"] == "es" and payload["n"] == 2
    assert payload["prompt"] == "go direct down the left" and payload["snapshot_digest"] == "abc123"
    assert payload["persona"]["name"] == "maverick" and payload["persona"]["brief"]
    assert payload["claimed_mechanisms"] == ["overload"]
    assert len(payload["players"]) == 22
    mine = [p for p in payload["players"] if p["team_id"] == team]
    assert payload["players"][0]["team_id"] == team and len(mine) == 11
    gk = next(p for p in mine if p["role"] == "GK")
    assert gk["x"] < 20, "positions are in the coached team's own frame (own goal at x=0)"
    row = next(p for p in payload["players"] if p["id"] == pid)
    assert row["tags"] == ["aerial threat"] and row["top_speed"] == meta.players[pid].top_speed
    assert row["dist_to_ball"] >= 0 and set(row) >= {"id", "name", "role", "x", "y", "dist_to_ball", "top_speed", "tags"}
    assert [t["role"] for t in payload["teams"]] == ["coached", "opponent"]
    assert payload["teams"][0]["style"] == {"press_intensity": 1.2}
    assert payload["teams"][0]["context"]["playbook"] == {"patterns": ["high_press"]}
    allowed = payload["allowed"]
    assert "right:att" in allowed["zones"] and "run_to" in allowed["actions"] and "overload" in allowed["mechanisms"]
    assert set(allowed["style_knobs"]["press_intensity"]) == {0.3, 2.0, 1.0}
    keys = set(allowed["context_keys"])
    assert f"tag:{pid}:aerial threat" in keys and f"{team}.mentality.comeback" in keys
    assert f"{team}.playbook.patterns:high_press" in keys and f"{opp}.weaknesses:slow on the turn" in keys
    assert "baseline.p_shot" in keys
    assert payload["baseline"]["p_shot"]["value"] == 0.3
    assert "recipes" not in payload
    assert payload["moment"]["coached_team_in_possession"] is True


def make_situation_with_tags(situation: Situation, tags: dict[str, list[str]]) -> Situation:
    return situation.model_copy(update={"players": [
        p.model_copy(update={"tags": tags.get(p.player_id, p.tags)}) for p in situation.players]})


def test_context_keys_without_context(situation):
    assert context_keys(situation, {}, None) == []


def test_online_calls_leave_recipes_out_of_the_payload(meta, situation, monkeypatch):
    seen: list[dict] = []

    def from_recipes(payload):  # online payloads carry no recipes: this stands in for the model
        seen.append(payload)
        return TacticDraftSet(tactics=playbook.recipes(situation, situation.coaching_team_id, meta, 10.0)[:2])

    monkeypatch.setitem(templates.TASKS, "tactics", from_recipes)
    online = LlmSettings(endpoint="https://x", fast_model="a", strong_model="b", mode="online")
    client = ScriptedClient()
    run(ideate(ModelClients(fast=client, strong=client, settings=online), situation, {}, meta=meta))
    assert seen and all("recipes" not in p and p["already_on_board"] for p in seen)


# ------------------------------------------------------------- diverse selection


def test_select_diverse_keeps_a_wildcard_and_spreads_mechanisms():
    t = "NBR"
    strong = [result(blank(t, title=f"overload {i}", mechanism=Mechanism.OVERLOAD), "wide", 0.30 - i * 0.001)
              for i in range(5)]
    other = result(blank(t, title="switch", mechanism=Mechanism.SWITCH), "positional", 0.20)
    wild = result(blank(t, title="false nine", mechanism=Mechanism.ISOLATION), "maverick", 0.01)
    picked = select_diverse(strong + [other, wild], 3)
    assert len(picked) == 3
    assert picked[0].intent.title == "overload 0", "the best idea is always kept"
    assert wild in picked, "a valid maverick idea always makes the board"
    assert other in picked, "MMR prefers a different mechanism to a fourth overload"
    assert [r.utility for r in picked] == sorted((r.utility for r in picked), reverse=True)


def test_select_diverse_ignores_rejected_and_unscored():
    t = "NBR"
    good = result(blank(t, title="a"), "direct", 0.2)
    rejected = result(blank(t, title="b"), "maverick", 0.9).model_copy(update={"status": "rejected"})
    unscored = TacticResult(tactic_id="t-x", persona="maverick", intent=blank(t, title="c"))
    assert select_diverse([good, rejected, unscored], 3) == [good]
    assert select_diverse([], 3) == [] and select_diverse([good], 0) == []


def test_select_diverse_returns_everything_when_few():
    t = "NBR"
    rs = [result(blank(t, title=str(i)), "direct", i / 10) for i in range(3)]
    assert [r.utility for r in select_diverse(rs, 5)] == [0.2, 0.1, 0.0]


# ---------------------------------------------------------------------- verdict


def ranked_pair(team: str):
    a = result(blank(team, title="Overload the left", mechanism=Mechanism.OVERLOAD), "wide", 0.2,
               lift={"p_shot": 0.12}, rank=1, confidence="medium")
    a = a.model_copy(update={"outcome": outcome(p_shot=0.42, xg=0.17, concede=0.15)})
    b = result(blank(team, title="Third-man run", mechanism=Mechanism.THIRD_MAN), "positional", 0.1, rank=2,
               lift={"p_shot": 0.02}, confidence="low")
    # The baseline's shot-chance range (0.25-0.31) sits below A's (0.32-0.52): A is a clear winner.
    base = outcome(p_shot=0.30, xg=0.11, concede=0.2)
    return [a, b], base.model_copy(update={"p_shot": Interval(value=0.30, low=0.25, high=0.31)})


def test_verdict_accepts_a_grounded_answer(meta, situation):
    ranked, base = ranked_pair(situation.coaching_team_id)

    def grounded(draft, payload):
        top = payload["ranked"][0]
        return VerdictDraft(
            headline="Overload the left", recommended_tactic_id=top["tactic_id"],
            cited_metrics=["p_shot", "mean_xg"],
            text="Overload the left lifts shot chance to 42% from 30% and expected goals to 0.17, "
                 "while conceding a shot stays at 15%. Based on 24 simulations.")

    client = ScriptedClient(tamper={"tactics_verdict": grounded})
    problems: list[str] = []
    out = run(verdict(offline_clients(client), situation, ranked, base, "en", meta=meta, errors=problems))
    assert out.headline == "Overload the left" and "42%" in out.text and not problems
    assert out.recommended_tactic_id == ranked[0].tactic_id


def test_verdict_rejects_an_invented_number(meta, situation):
    ranked, base = ranked_pair(situation.coaching_team_id)

    def invented(draft, payload):
        return VerdictDraft(headline="Overload", recommended_tactic_id=payload["ranked"][0]["tactic_id"],
                            cited_metrics=["p_shot"],
                            text="Overload the left lifts shot chance to 42% and creates 37 more chances.")

    problems: list[str] = []
    out = run(verdict(offline_clients(ScriptedClient(tamper={"tactics_verdict": invented})), situation, ranked,
                      base, "en", meta=meta, errors=problems))
    assert "37" not in out.text and out.headline.startswith("Try: Overload the left")
    assert out.recommended_tactic_id == ranked[0].tactic_id
    assert problems and "37" in problems[0]


@pytest.mark.parametrize("bad", [
    VerdictDraft(headline="h", text="Fine.", recommended_tactic_id="t-unknown", cited_metrics=[]),
    VerdictDraft(headline="h", text="Fine.", recommended_tactic_id=None, cited_metrics=["goals_scored"]),
])
def test_check_verdict_flags_unknown_ids_and_metrics(meta, situation, bad):
    ranked, base = ranked_pair(situation.coaching_team_id)
    payload = verdict_payload(situation, ranked, base, "en", meta=meta)
    assert check_verdict(bad, payload)


@pytest.mark.parametrize("lang", ["en", "es", "hi"])
def test_template_verdict_passes_its_own_fact_check(meta, situation, lang):
    ranked, base = ranked_pair(situation.coaching_team_id)
    payload = verdict_payload(situation, ranked, base, lang, meta=meta)
    draft = offline_verdict(payload)
    assert draft.recommended_tactic_id == ranked[0].tactic_id
    assert check_verdict(draft, payload) == []
    assert "42" in draft.text and "30" in draft.text


def test_verdict_without_results_recommends_nothing(meta, situation):
    out = run(verdict(offline_clients(), situation, [], None, "en", meta=meta))
    assert out.recommended_tactic_id is None and out.cited_metrics == []


def test_verdict_falls_back_when_the_model_fails(meta, situation):
    ranked, base = ranked_pair(situation.coaching_team_id)
    problems: list[str] = []
    out = run(verdict(offline_clients(ScriptedClient(fail={"tactics_verdict"})), situation, ranked, base, "es",
                      meta=meta, errors=problems))
    assert out.headline.startswith("Probar") and problems


# ---------------------------------------------------------------------- cassette


def test_cassette_key_format(meta, situation):
    prompt = "attack down the left"
    digest = hashlib.sha1(prompt.encode()).hexdigest()[:10]
    ctx = {}
    payload = build_payload(situation, ctx, None, prompt, "gegenpress", language="hi", meta=meta,
                            snapshot_digest="snap42")
    team = situation.coaching_team_id
    assert request_key(payload) == f"tactics|snap42|{team}|gegenpress|n2|h10|{digest}|hi"
    v = verdict_payload(situation, [], None, "es", prompt=prompt, snapshot_digest="snap42")
    assert request_key(v) == f"tactics_verdict|snap42|{team}|||{digest}|es"
    v2 = verdict_payload(situation, [], outcome(), "es", prompt=prompt, snapshot_digest="snap42", risk="bold")
    assert request_key(v2) == f"tactics_verdict|snap42|{team}|h10|bold|{digest}|es"
    # Both sides of one moment share a snapshot digest, so the coached team must be in the key.
    other = meta.opponent(team).team_id
    flipped = dict(payload, coaching={"team_id": other, "name": "x"})
    assert request_key(flipped) != request_key(payload) and other in request_key(flipped)
    assert request_key(dict(payload, n=3)) != request_key(payload)
    assert request_key(dict(payload, horizon_s=6.0)) != request_key(payload)
    assert request_key({k: v for k, v in payload.items() if k != "coaching"}) is None
    no_prompt = build_payload(situation, ctx, None, None, "direct", meta=meta, snapshot_digest="s")
    assert request_key(no_prompt) == f"tactics|s|{team}|direct|n2|h10|{hashlib.sha1(b'').hexdigest()[:10]}|en"


def test_record_then_replay_a_board_without_a_model(meta, situation):
    cassette = Cassette(seed=7, storyline="x")
    recorder = CassetteChatClient(OfflineChatClient(), cassette, live_misses=True, record=True)
    first, _ = run(ideate(offline_clients(recorder), situation, {}, meta=meta, prompt="p", snapshot_digest="d1"))
    ranked, base = ranked_pair(situation.coaching_team_id)
    v1 = run(verdict(offline_clients(recorder), situation, ranked, base, "en", meta=meta, prompt="p",
                     snapshot_digest="d1"))
    keys = set(cassette.entries)
    team = situation.coaching_team_id
    assert {f"tactics|d1|{team}|{p}|n2|h10|{hashlib.sha1(b'p').hexdigest()[:10]}|en" for p in PERSONAS} <= keys
    assert any(k.startswith(f"tactics_verdict|d1|{team}|") for k in keys)

    replay = CassetteChatClient(OfflineChatClient(), cassette, live_misses=False)
    again, errors = run(ideate(offline_clients(replay), situation, {}, meta=meta, prompt="p", snapshot_digest="d1"))
    assert [i.model_dump() for _, i in again] == [i.model_dump() for _, i in first] and not errors
    assert replay.hits >= len(PERSONAS) and replay.misses == 0
    # An unrecorded moment is a miss; offline it degrades to recipes instead of failing.
    other, errors = run(ideate(offline_clients(replay), situation, {}, meta=meta, prompt="p", snapshot_digest="d2"))
    assert replay.misses >= len(PERSONAS) and other and all(p == "recipe" for p, _ in other)
    assert v1.recommended_tactic_id == ranked[0].tactic_id


# ------------------------------------------------------------------ llm settings


def settings(creative: str, **kw) -> LlmSettings:
    return LlmSettings(endpoint="https://x", fast_model="gpt-5-mini", strong_model="gpt-5", mode="online",
                       creative_model=creative, **kw)


@pytest.mark.parametrize("deployment,reasoning", [
    ("gpt-5", True), ("gpt-5-mini", True), ("GPT-5.1-chat", True), ("o3", True), ("o4-mini", True),
    ("gpt-4.1", False), ("gpt-4o", False), ("my-deployment", False), ("omni-chat", False),
])
def test_reasoning_family_detection(deployment, reasoning):
    assert is_reasoning_model(deployment) is reasoning


def test_override_decides_when_the_deployment_name_says_nothing():
    dummy = OfflineChatClient()

    def opts(name, flag):
        return ModelClients(fast=dummy, strong=dummy, settings=settings(name, creative_is_reasoning=flag)
                            ).creative_options()

    # A gpt-5 deployment called "creative" must never get a temperature (the API would answer 400).
    assert opts("creative", None) == {"reasoning": {"effort": "medium"}}
    assert opts("creative", True) == {"reasoning": {"effort": "medium"}}
    assert opts("creative", False) == {"temperature": 0.9}
    assert opts("gpt-5-mini", False) == {"temperature": 0.9}, "an explicit override wins over the name"
    assert opts("gpt-4.1", True) == {"reasoning": {"effort": "medium"}}
    assert reasoning_family("creative") is None and reasoning_family("gpt-4o") is False


def test_override_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("MATCHLENS_LLM", "offline")
    monkeypatch.setenv("MATCHLENS_MODEL_CREATIVE_REASONING", "0")
    assert LlmSettings.from_env().creative_is_reasoning is False
    monkeypatch.setenv("MATCHLENS_MODEL_CREATIVE_REASONING", "1")
    assert LlmSettings.from_env().creative_is_reasoning is True
    monkeypatch.delenv("MATCHLENS_MODEL_CREATIVE_REASONING")
    assert LlmSettings.from_env().creative_is_reasoning is None


def test_temperature_is_never_sent_to_reasoning_deployments():
    dummy = OfflineChatClient()
    for name in ("gpt-5-mini", "gpt-5", "o3"):
        opts = ModelClients(fast=dummy, strong=dummy, settings=settings(name, creative_temperature=1.1)
                            ).creative_options()
        assert "temperature" not in opts and opts == {"reasoning": {"effort": "medium"}}
    chat = ModelClients(fast=dummy, strong=dummy, settings=settings("gpt-4.1", creative_temperature=1.1))
    assert chat.creative_options() == {"temperature": 1.1}
    offline = ModelClients(fast=dummy, strong=dummy, settings=OFFLINE)
    assert offline.creative_options() == {}


def test_creative_model_defaults_to_the_strong_model(monkeypatch):
    monkeypatch.delenv("MATCHLENS_MODEL_CREATIVE", raising=False)
    monkeypatch.delenv("MATCHLENS_CREATIVE_TEMPERATURE", raising=False)
    monkeypatch.setenv("MATCHLENS_LLM", "offline")
    s = LlmSettings.from_env()
    assert s.creative_deployment == s.strong_model and s.creative_temperature == 0.9
    monkeypatch.setenv("MATCHLENS_MODEL_CREATIVE", "gpt-5-chat")
    monkeypatch.setenv("MATCHLENS_CREATIVE_TEMPERATURE", "0.5")
    s = LlmSettings.from_env()
    assert s.creative_deployment == "gpt-5-chat" and s.creative_temperature == 0.5


def test_creative_client_falls_back_to_strong_and_follows_the_cassette_switch():
    dummy = OfflineChatClient()
    clients = ModelClients(fast=dummy, strong=dummy, settings=OFFLINE)
    assert clients.creative_client is dummy
    cassette = Cassette(seed=7, storyline="x")
    creative = CassetteChatClient(dummy, cassette, live_misses=False)
    clients = ModelClients(fast=dummy, strong=dummy, settings=OFFLINE, cassette=cassette, creative=creative)
    clients.use_cassette(3, None)
    assert creative.enabled is False
    clients.use_cassette(7, None)
    assert creative.enabled is True


def test_offline_build_clients_still_work(monkeypatch):
    monkeypatch.setenv("MATCHLENS_LLM", "offline")
    monkeypatch.delenv("MATCHLENS_CASSETTE", raising=False)
    clients = build_clients(settings=OFFLINE)
    assert clients.creative is None and clients.creative_client is clients.strong


def test_personas_cover_the_five_styles():
    assert list(PERSONAS) == ["positional", "gegenpress", "direct", "wide", "maverick"]
    assert all(p.brief and p.mechanisms for p in PERSONAS.values())
    assert any(w in PERSONAS["maverick"].brief for w in ("false nine", "goalkeeper"))


def test_record_tactics_dry_run_fills_a_cassette(tmp_path):
    from matchlens.agents import record_tactics

    out = tmp_path / "hero-tactics.json"
    run(record_tactics.record(out, seed=7, count=1, k=4, languages=["en"], prompts=[None], offline=True))
    cassette = Cassette.load(out)
    kinds = {k.split("|")[0] for k in cassette.entries}
    assert kinds == {"tactics", "tactics_verdict"}
    assert sum(k.startswith("tactics|") for k in cassette.entries) == len(PERSONAS)
    moments = record_tactics.hero_moments(generate_match(seed=7))
    assert len(moments) == 8 and [m.elapsed for m in moments] == sorted(m.elapsed for m in moments)


def test_context_keys_name_rows_of_kb_lists(situation):
    keys = context_keys(situation, {"NBR": {"patterns_for": [{"tag": "low_block", "share_pct": 20}],
                                            "key_players": [{"player_id": "NBR-9", "name": "A B"}]}}, None)
    assert "NBR.patterns_for:low_block" in keys and "NBR.key_players:NBR-9" in keys


# ----------------------------------------------------- stricter fact-checking


def _verdict_text(meta, situation, text, cited=("p_shot", "mean_xg"), headline="Overload the left"):
    ranked, base = ranked_pair(situation.coaching_team_id)
    payload = verdict_payload(situation, ranked, base, "en", meta=meta)
    draft = VerdictDraft(headline=headline, text=text, recommended_tactic_id=ranked[0].tactic_id,
                         cited_metrics=list(cited))
    return check_verdict(draft, payload)


GOOD = ("Overload the left lifts shot chance to 42% from 30% and expected goals to 0.17 from 0.11. "
        "Conceding a shot stays at 15%.")


def test_grounded_text_passes_the_strict_check(meta, situation):
    assert _verdict_text(meta, situation, GOOD) == []


@pytest.mark.parametrize("text", [
    "Overload the left lifts shot chance to 57% from 30%.",           # not a statistic we supplied
    "Overload the left lifts shot chance to 43% from 30%.",           # near miss: no more +-1 tolerance
    "Overload the left gives 0.171 expected goals.",                  # 3 decimals is not a rendered form
    "Overload the left lifts shot chance to 42% in 14 simulations.",  # 14 is neither 24 nor a rank
    "Overload the left makes 1,200 shots from 30%.",                  # thousands separator is 1200, not 1.2
])
def test_loose_numbers_are_rejected(meta, situation, text):
    problems = _verdict_text(meta, situation, text)
    assert problems and "numbers" in problems[0], problems


def test_interval_bounds_only_when_the_metric_is_cited(meta, situation):
    text = "Overload the left lifts shot chance to 42%, somewhere between 32% and 52%."
    assert any("numbers" in p for p in _verdict_text(meta, situation, text, cited=("mean_xg",)))
    assert _verdict_text(meta, situation, text, cited=("p_shot",)) == []


def test_percent_forms_and_fractions_are_both_fine(meta, situation):
    assert _verdict_text(meta, situation, "Shot chance 0.42 against 0.3, or 42.0% against 30.0%.") == []


def test_spelled_out_counts_above_two_are_rejected(meta, situation):
    assert any("spelled-out" in p for p in _verdict_text(meta, situation, GOOD + " Thirty more shots follow."))
    assert any("spelled-out" in p for p in _verdict_text(meta, situation, GOOD + " Expect three chances."))
    assert _verdict_text(meta, situation, GOOD + " One run and two passes.") == []


def test_invented_names_are_rejected(meta, situation):
    problems = _verdict_text(meta, situation, GOOD + " The run of Zinedine Zidane stretches them.")
    assert any("Zidane" in p for p in problems)
    pid = next(p for p in situation.players if p.team_id == situation.coaching_team_id)
    assert _verdict_text(meta, situation, GOOD + f" The run of {pid.name} stretches them.") == []
    # Tactic titles and team names are fine; a sentence-initial common word is not a name.
    assert _verdict_text(meta, situation, GOOD + f" Third-man run is the safer option for {meta.home.name}.") == []
    assert _verdict_text(meta, situation, GOOD + " Mbappe's pace is the reason.")


def test_unknown_names_helper():
    vocab = {"salah", "liverpool"}
    assert unknown_names("Salah drifts inside. Overload follows. Liverpool press.", vocab) == []  # sentence starts
    assert unknown_names("The cross finds Haaland.", vocab) == ["Haaland"]
    assert unknown_names("Erling Haaland arrives.", vocab) == ["Erling", "Haaland"]
    assert unknown_names("Then Haaland arrives.", vocab) == ["Haaland"]
    assert unknown_names("Haaland's run.", vocab) == ["Haaland's"]
    assert numbers_of("1,200 and 0,17 and 42%") == [1200.0, 0.17, 42.0]


def test_ideate_rejects_invented_numbers_in_title_and_idea(meta, situation):
    asked: list[dict] = []

    def stat_stuffing(draft, payload):
        asked.append(payload)
        if payload["persona"]["name"] == "positional" and "problems" not in payload:
            bad = draft.tactics[0].model_copy(update={"idea": "Overload that scores 83% of the time."})
            return TacticDraftSet(tactics=[bad, draft.tactics[1]])
        return draft

    results, errors = run(ideate(offline_clients(ScriptedClient(tamper={"tactics": stat_stuffing})), situation, {},
                                 meta=meta))
    repair = next(p for p in asked if "problems" in p)
    assert any("83" in prob for prob in repair["problems"])
    assert all("83%" not in i.idea for _, i in results)


def test_title_numbers_allow_shirts_minutes_and_baseline_only(meta, situation):
    team = situation.coaching_team_id
    pid = next(p.player_id for p in situation.players if p.team_id == team)
    shirt = meta.players[pid].shirt
    allowed = text_number_allowance(meta, situation, 10.0, outcome())
    ok = blank(team, title=f"No. {shirt} drops", idea=f"After the {situation.minute_label[:-1]}th minute, 30% baseline.")
    assert text_number_problems(ok, allowed) == []
    assert text_number_problems(blank(team, idea="He has scored 37 this season."), allowed)
