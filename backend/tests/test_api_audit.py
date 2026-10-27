"""Fixes from the API audit: bad input answers 4xx, stale fan ids are ignored, restart replays the loaded
match, and Scenario Lab drafts offline."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from matchlens.agents import scenario
from matchlens.agents.contracts import Language, Mode
from matchlens.agents.services import personalise_feed
from matchlens.api import app as api
from matchlens.ingest.adapters import ReplayControl, SyntheticReplayAdapter
from matchlens.runtime.manager import Recipe, SessionManager, manager
from matchlens.runtime.session import MatchSession
from matchlens.simulator.roster import default_match_meta
from matchlens.simulator.scenarios import PRESETS, check_storyline, ten_man_heist
from matchlens.store.base import MemoryStore

from .test_store import SPEED, Backend, run


@pytest.fixture
def loaded(demo_match, monkeypatch):
    s = MatchSession(SyntheticReplayAdapter(demo_match, ReplayControl(speed=1000)))
    s.hydrate([e for e in demo_match.events if e.elapsed <= 600], [f for f in demo_match.tracking if f.elapsed <= 600])
    monkeypatch.setattr(manager, "session", s)
    return s


# ------------------------------------------------------------------ input validation


def test_timeline_unknown_event_type_is_422_not_500(loaded):
    with pytest.raises(HTTPException) as caught:
        run(api.timeline(type=["goal", "bogus"]))
    assert caught.value.status_code == 422 and "bogus" in caught.value.detail
    assert isinstance(run(api.timeline(type=["pass"])), list)


def test_replay_seed_is_bounded():
    client = TestClient(api.app)  # no lifespan: validation answers before any handler runs
    for seed in (-1, 1_000_001):
        assert client.post("/api/replay", json={"seed": seed}).status_code == 422


def test_every_route_is_registered_once():
    seen = [(m, r.path) for r in api.app.router.routes for m in getattr(r, "methods", None) or ()
            if m not in ("HEAD", "OPTIONS")]
    assert len(seen) == len(set(seen)), [x for x in seen if seen.count(x) > 1]
    assert ("POST", "/api/replay/restart") in seen


# ------------------------------------------------------------------ stale fan ids


def _insight(i: int, priority: int, players: list[str]) -> dict:
    text = {"headline": f"h{i}", "body": f"b{i}"}
    return {"insight_id": f"i{i}", "signal_type": "x", "minute_label": f"{i}'", "elapsed": float(i), "team_id": None,
            "player_ids": players, "priority": priority, "stage": "published", "period": 1,
            "variants": {"casual": {"en": text}}, "club_angles": {}, "key_numbers": [],
            "fact_check": {"status": "verified"}, "evidence_event_ids": [], "headline": f"h{i}"}


def test_ids_from_another_match_are_ignored(loaded):
    meta = loaded.meta
    pid = next(iter(meta.players))
    kept = api._profile(Mode.CASUAL, Language.EN, meta.home.team_id, pid, None)
    assert (kept.club_id, kept.player_id) == (meta.home.team_id, pid)
    stale = api._profile(Mode.PLAYER_FOCUS, Language.EN, "DWO", "DWO-01", None)
    assert stale.club_id is None and stale.player_id is None
    loaded.insights.update({f"i{i}": _insight(i, 3, []) for i in range(3)})
    focus = personalise_feed(loaded, stale, 50)
    plain = personalise_feed(loaded, api._profile(Mode.PLAYER_FOCUS, Language.EN, None, None, None), 50)
    assert len(focus) == len(plain) == 3  # a stale player used to hide every insight but the top priority
    loaded.insights.clear()


# ------------------------------------------------------------------ restart


def test_restart_with_nothing_loaded_is_a_404(monkeypatch):
    monkeypatch.setattr(manager, "session", None)
    monkeypatch.setattr(manager, "recipe", None)
    r = TestClient(api.app).post("/api/replay/restart")
    assert r.status_code == 404 and "no match" in r.json()["detail"]
    with pytest.raises(LookupError):
        run(SessionManager().restart())


def test_restart_keeps_a_custom_storyline_and_the_hero_default():
    async def main():
        b = Backend(MemoryStore())
        b.writer.start()
        first = await b.manager.start_synthetic(seed=11, speed=SPEED, storyline=ten_man_heist())
        again = await b.manager.restart(speed=SPEED)
        assert b.manager.recipe == Recipe("synthetic", seed=11, storyline=ten_man_heist())
        hero = await b.manager.start_synthetic(seed=7, speed=SPEED)
        hero_again = await b.manager.restart()
        out = (first, again, hero, hero_again, b.manager.control.speed, b.manager.storyline)
        await b.manager.stop()
        await b.writer.close()
        return out

    first, again, hero, hero_again, speed, storyline = run(main())
    assert again.meta.storyline_title == "The Ten-Man Heist" and again.run_id != first.run_id
    assert hero_again.meta.match_id == hero.meta.match_id and hero_again.run_id != hero.run_id
    assert speed == SPEED and storyline is None  # the default storyline stays the default (recorded demo)


def test_a_finished_hero_run_reopens_as_regenerable():
    store = MemoryStore()

    async def main():
        await store.open()
        b = Backend(store)
        b.writer.start()
        played = await b.manager.start_synthetic(seed=7, speed=SPEED)
        await b.play_until(None)
        await b.manager.stop()
        await b.writer.flush()
        later = Backend(store)
        later.writer.start()
        past = await later.manager.open_run(played.run_id)
        assert past.archived
        recipe = later.manager.recipe
        again = await later.manager.restart(speed=SPEED)
        out = (recipe, later.manager.recipe, later.manager.seed, again.archived, again.run_id)
        await later.manager.stop()
        await later.writer.close()
        return played, out

    played, (opened, after, seed, archived, run_id) = run(main())
    assert opened == after == Recipe("synthetic", seed=7)
    assert seed == 7 and not archived and run_id != played.run_id  # recorded demo agents stay on


def test_restart_replays_a_past_run_that_cannot_be_regenerated():
    store = MemoryStore()

    async def main():
        await store.open()
        b = Backend(store)
        b.writer.start()
        played = await b.manager.start_synthetic(seed=11, speed=SPEED, storyline=ten_man_heist())
        await b.play_until(None)
        await b.manager.stop()
        await b.writer.flush()

        later = Backend(store)
        later.writer.start()
        past = await later.manager.open_run(played.run_id)
        assert past.archived and later.manager.recipe == Recipe("stored", run_id=played.run_id)
        rerun = await later.manager.restart(speed=SPEED)
        await later.play_until(None)
        first = (rerun.meta.match_id, rerun.run_id, rerun.archived, rerun.engine.score(), len(rerun.insights),
                 {i["stage"] for i in rerun.insights.values()}, later.manager.seed)
        recorded = (await later.repo.get("runs", rerun.run_id, rerun.run_id)) or {}
        again = await later.manager.restart(speed=SPEED)  # restarting a rerun replays the original, no chain
        recipe = later.manager.recipe
        await later.manager.stop()
        await later.writer.close()
        return played, past, first, recorded, again, recipe

    played, past, first, recorded, again, recipe = run(main())
    match_id, run_id, archived, score, n_insights, stages, seed = first
    assert match_id == played.meta.match_id and run_id not in (played.run_id, again.run_id) and not archived
    assert score == past.engine.score() and seed is None  # the cassette is off: data-only insights
    assert n_insights > 0 and "data" in stages  # published from the data (no recorded answers for this match)
    assert recorded["rerun_of"] == played.run_id and recorded["source"] == "rerun"
    assert recipe == Recipe("stored", run_id=played.run_id)


def test_restart_start_minute_applies_to_a_rerun():
    store = MemoryStore()

    async def main():
        await store.open()
        b = Backend(store)
        b.writer.start()
        played = await b.manager.start_synthetic(seed=11, speed=SPEED, storyline=ten_man_heist())
        await b.play_until(None)
        await b.manager.stop()
        await b.writer.flush()
        later = Backend(store)
        later.writer.start()
        await later.manager.open_run(played.run_id)
        rerun = await later.manager.restart(speed=1, start_minute=50)
        control = later.manager.control
        deadline = time.monotonic() + 30
        while rerun.engine.c.elapsed < 50 * 60 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        out = (control.start_elapsed, rerun.engine.c.elapsed, rerun.finished)
        await later.manager.stop()
        await later.writer.close()
        return out

    start, elapsed, finished = run(main())
    assert start == 3000 and elapsed >= 3000 and not finished  # skipped ahead, then real time (1x)


def test_restart_a_recorded_feed_and_a_feed_without_recipe(demo_match):
    async def main():
        b = Backend(MemoryStore())
        b.writer.start()
        control = ReplayControl(speed=SPEED)
        await b.manager.start_feed(SyntheticReplayAdapter(demo_match, control), control, "recorded",
                                   Recipe("recorded", match=demo_match))
        again = await b.manager.restart(speed=SPEED)
        control = ReplayControl(speed=SPEED)
        await b.manager.start_feed(SyntheticReplayAdapter(demo_match, control), control, "other")
        with pytest.raises(LookupError):
            await b.manager.restart()
        await b.manager.stop()
        await b.writer.close()
        return again

    assert run(main()).meta.match_id == demo_match.meta.match_id


# ------------------------------------------------------------------ Scenario Lab offline


def offline(monkeypatch):
    settings = SimpleNamespace(online=False)
    monkeypatch.setattr(api, "runtime", SimpleNamespace(team=None, clients=SimpleNamespace(settings=settings)))


def draft(prompt: str) -> dict:
    return run(api.draft_scenario(api.ScenarioDraftRequest(prompt=prompt)))


def test_offline_draft_picks_the_closest_preset(monkeypatch):
    offline(monkeypatch)
    hit = draft("Harbour City go down to ten men but steal it on the counter")
    assert hit["source"] == "template" and hit["preset_id"] == "ten-man-heist"
    assert hit["storyline"]["title"] == "The Ten-Man Heist" and "live model" in hit["note"]
    assert not check_storyline(default_match_meta(), ten_man_heist())
    assert draft("a wild seven goal thriller")["preset_id"] == "goal-fest"
    assert draft("two goals in injury time to win it")["preset_id"] == "late-late-show"


def test_offline_draft_without_a_match_falls_back_to_the_default(monkeypatch):
    offline(monkeypatch)
    miss = draft("zzzz qqqq xxxx")
    assert miss["preset_id"] == next(iter(PRESETS)) and miss["source"] == "template"
    assert miss["note"].startswith("Nothing in the description matched")


def test_online_draft_is_marked_ai(monkeypatch):
    online = SimpleNamespace(online=True)
    monkeypatch.setattr(api, "runtime", SimpleNamespace(team=None, clients=SimpleNamespace(settings=online)))
    story = ten_man_heist()

    async def written(*_):
        return scenario.Written(story, [])

    monkeypatch.setattr(api, "write_storyline", written)
    out = draft("anything at all")
    assert out["source"] == "ai" and out["preset_id"] is None and out["note"] is None
    assert out["storyline"]["title"] == story.title
