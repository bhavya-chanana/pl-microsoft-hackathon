"""FotMob adapter. Every fixture here is fictional and only shaped like a
FotMob matchDetails response; no real match data is used or stored."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from fastapi import HTTPException

from matchlens.domain.models import EventType, Outcome
from matchlens.local import fotmob
from matchlens.ingest.adapters import ReplayControl
from matchlens.interpret.engine import MatchEngine

HOME, AWAY = 101, 202


def _player(pid: int, name: str, shirt: int, pos: int, gk: bool = False) -> dict:
    first, last = name.split(" ", 1)
    return {"id": pid, "name": name, "firstName": first, "lastName": last, "shirtNumber": str(shirt),
            "usualPlayingPositionId": pos, "countryName": "Nowhere", **({"positionId": 11} if gk else {})}


def _team(base: int, prefix: str) -> dict:
    starters = [_player(base + 1, f"{prefix} Keeper", 1, 0, gk=True)]
    starters += [_player(base + n, f"{prefix} Player{n}", n, 1 if n < 6 else 2 if n < 9 else 3) for n in range(2, 12)]
    subs = [_player(base + n, f"{prefix} Sub{n}", n, 2) for n in (12, 13)]
    return {"formation": "4-3-3", "starters": starters, "subs": subs}


def _shot(sid, team, player, minute, kind, *, x=95.0, y=34.0, xg=0.2, period="FirstHalf", added=None,
          blocked=False, keeper=None, header=False):
    return {"id": sid, "eventType": kind, "teamId": team, "playerId": player, "x": x, "y": y, "min": minute,
            "minAdded": added, "isBlocked": blocked, "isOnTarget": kind in ("Goal", "AttemptSaved"),
            "blockedX": 90.0 if blocked else None, "blockedY": 33.0 if blocked else None, "goalCrossedY": 35.0,
            "expectedGoals": xg, "expectedGoalsOnTarget": 0.5 if kind == "Goal" else 0,
            "shotType": "Header" if header else "RightFoot", "situation": "RegularPlay", "period": period,
            "isOwnGoal": False, "keeperId": keeper}


def details(*, finished: bool = True, scorer: int = HOME + 9) -> dict:
    """A fictional finished match: Kestrel Rovers 2-1 Marlow Vale, with stoppage time, an own goal,
    cards and a substitution."""
    shots = [
        _shot(1, HOME, HOME + 9, 7, "Goal", xg=0.45, keeper=AWAY + 1),
        _shot(2, AWAY, AWAY + 10, 20, "AttemptSaved", x=88.0, y=20.0, xg=0.31, keeper=HOME + 1),
        _shot(3, AWAY, AWAY + 11, 45, "Miss", added=2, xg=0.05, header=True),
        _shot(4, HOME, HOME + 10, 60, "AttemptSaved", blocked=True, xg=0.08),
    ]
    events = [
        {"type": "Goal", "time": 7, "eventId": 1, "playerId": scorer, "homeScore": 0, "awayScore": 0,
         "newScore": [1, 0], "isHome": True, "assistPlayerId": HOME + 8, "shotmapEvent": {"id": 1}},
        {"type": "Card", "time": 45, "overloadTime": 2, "eventId": 2, "playerId": AWAY + 4, "isHome": False,
         "card": "Yellow"},
        {"type": "AddedTime", "time": 45, "minutesAddedInput": 3},
        {"type": "Half", "time": 45, "halfStrShort": "HT"},
        {"type": "Substitution", "time": 55, "isHome": True,
         "swap": [{"id": str(HOME + 12)}, {"id": str(HOME + 11)}]},
        # Own goal by a Kestrel defender: Marlow Vale's goal.
        {"type": "Goal", "time": 70, "eventId": 3, "playerId": HOME + 3, "homeScore": 1, "awayScore": 0,
         "newScore": [1, 1], "isHome": False, "ownGoal": True},
        {"type": "Goal", "time": 88, "eventId": 4, "playerId": HOME + 10, "homeScore": 1, "awayScore": 1,
         "newScore": [2, 1], "isHome": True},
        {"type": "Half", "time": 90, "halfStrShort": "FT"},
    ]
    if not finished:
        shots = shots[:2]
        events = events[:1]
    return {
        "general": {"matchId": "999", "leagueName": "Test League", "matchTimeUTCDate": "2026-01-01T15:00:00Z",
                    "homeTeam": {"id": HOME, "name": "Kestrel Rovers"},
                    "awayTeam": {"id": AWAY, "name": "Marlow Vale"},
                    "teamColors": {"lightMode": {"home": "#112233", "away": "#445566"}}},
        "header": {"status": {"started": True, "finished": finished, "halfs": {
            "firstHalfStarted": "01.01.2026 15:00:00", "firstHalfEnded": "01.01.2026 15:48:00",
            "secondHalfStarted": "01.01.2026 16:03:00" if finished else "",
            "secondHalfEnded": "01.01.2026 16:51:00" if finished else ""}}},
        "content": {
            "lineup": {"homeTeam": _team(HOME, "Home"), "awayTeam": _team(AWAY, "Away")},
            "matchFacts": {"events": {"events": events}, "infoBox": {"Stadium": {"name": "Test Ground"}}},
            "shotmap": {"shots": shots},
            "stats": {"Periods": {"All": {"stats": [{"stats": [
                {"key": "BallPossesion", "type": "graph", "rawStats": [{"value": 55}, {"value": 45}]},
                {"key": "passes", "type": "title", "rawStats": [None, None]},
            ]}]}}},
            "playerStats": {str(HOME + 9): {"id": HOME + 9, "stats": [
                {"title": "Top stats", "stats": {"FotMob rating": {"stat": {"value": 8.1}}}}]}},
            "momentum": {"main": {"data": [{"minute": 1, "value": 10}]}},
        },
    }


def _engine(parsed: fotmob.Parsed) -> MatchEngine:
    eng = MatchEngine(parsed.meta)
    for e in parsed.events:
        eng.ingest_event(e)
    return eng


def test_meta_from_lineup():
    meta = fotmob.parse(details()).meta
    assert (meta.match_id, meta.home.team_id, meta.away.team_id) == ("fm-999", "KES", "MAR")
    assert meta.venue == "Test Ground" and meta.home.colour == "#112233"
    assert len(meta.home.starting_xi) == 11 and len(meta.home.bench) == 2
    keeper = meta.players[f"KES-{HOME + 1}"]
    assert keeper.position.value == "GK" and keeper.shirt == 1 and keeper.short_name == "Keeper"
    assert meta.players[f"MAR-{AWAY + 10}"].position.value == "FWD"


def test_team_codes_never_clash():
    d = details()
    d["general"]["awayTeam"]["name"] = "Kestrel Town"
    meta = fotmob.parse(d).meta
    assert meta.home.team_id != meta.away.team_id


def test_events_and_engine_totals():
    parsed = fotmob.parse(details())
    by_type = {}
    for e in parsed.events:
        by_type.setdefault(e.type, []).append(e)
    assert len(by_type[EventType.SHOT]) == 4 and len(by_type[EventType.GOAL]) == 3
    assert len(by_type[EventType.KICKOFF]) == 2 and len(by_type[EventType.SAVE]) == 1
    # Feed order is time order, and sequence follows it.
    assert [e.sequence for e in parsed.events] == sorted(e.sequence for e in parsed.events)

    eng = _engine(parsed)
    home, away = eng.team_stats("KES"), eng.team_stats("MAR")
    assert (home["goals"], away["goals"]) == (2, 1)
    assert (home["shots"], away["shots"]) == (2, 2)
    assert home["xg"] == pytest.approx(0.53) and away["xg"] == pytest.approx(0.36)
    assert eng.state()["status"] == "full_time"
    # The own goal counts for Marlow Vale, not as the defender's goal.
    assert eng.player_stats(f"KES-{HOME + 3}")["goals"] == 0
    assert eng.player_stats(f"KES-{HOME + 9}")["goals"] == 1
    assert eng.player_stats(f"KES-{HOME + 8}")["assists"] == 1


def test_shots_are_mirrored_for_the_away_side():
    parsed = fotmob.parse(details())
    home_shot = next(e for e in parsed.events if e.event_id == "fm-shot-1")
    away_shot = next(e for e in parsed.events if e.event_id == "fm-shot-2")
    assert home_shot.start.x == 95.0 and home_shot.end.x == 105.0  # home attacks x = 105
    assert away_shot.start.x == pytest.approx(17.0) and away_shot.start.y == pytest.approx(48.0)
    assert away_shot.end.x == 0.0
    assert away_shot.outcome == Outcome.SAVED and away_shot.attrs["big_chance"] is True
    blocked = next(e for e in parsed.events if e.event_id == "fm-shot-4")
    assert blocked.outcome == Outcome.BLOCKED and blocked.end.x == 90.0
    header = next(e for e in parsed.events if e.event_id == "fm-shot-3")
    assert header.attrs["body_part"] == "head" and header.outcome == Outcome.OFF_TARGET
    goal = next(e for e in parsed.events if e.event_id == "fm-goal-1")
    assert goal.attrs["shot_event_id"] == "fm-shot-1" and goal.elapsed > home_shot.elapsed


def test_stoppage_time_and_halves():
    parsed = fotmob.parse(details())
    ev = {e.event_id: e for e in parsed.events}
    # The first half ran 48 minutes by the real timestamps, so 3 minutes of stoppage.
    assert ev["fm-card-2"].minute_label == "45+2'"
    assert ev["fm-half-time"].elapsed == 2879 and ev["fm-half-time"].minute_label == "45+3'"
    assert ev["fm-kickoff-2"].elapsed == 2880 and ev["fm-kickoff-2"].clock == 2700
    order = [e.event_id for e in parsed.events]
    assert order.index("fm-half-time") < order.index("fm-kickoff-2")
    # Second-half events sit after the first-half stoppage in elapsed time.
    assert ev[f"fm-sub-55-KES-{HOME + 11}"].elapsed == 54 * 60 + 30 + 180
    assert ev["fm-full-time"].minute_label == "90+3'"


def test_substitution_direction():
    parsed = fotmob.parse(details())
    sub = next(e for e in parsed.events if e.type == EventType.SUBSTITUTION)
    assert sub.player_id == f"KES-{HOME + 11}" and sub.related_player_id == f"KES-{HOME + 12}"
    eng = _engine(parsed)
    assert eng.player_stats(f"KES-{HOME + 12}")["on_pitch"] is True


def test_provider_stats_kept_apart():
    stats = fotmob.parse(details()).stats
    assert stats["teams"]["All"]["BallPossesion"] == {"KES": {"value": 55}, "MAR": {"value": 45}}
    assert "passes" not in stats["teams"]["All"]  # title rows are skipped
    assert stats["ratings"] == {f"KES-{HOME + 9}": 8.1}


def test_diff_emits_new_events_once_and_corrects_changes():
    emitted: dict = {}
    first, n = fotmob.diff(emitted, fotmob.parse(details()).events, 0)
    assert len(first) == len(emitted) and n == 0
    again, n = fotmob.diff(emitted, fotmob.parse(details()).events, n)
    assert again == []
    # FotMob credits the first goal to a different player on a later poll.
    changed, n = fotmob.diff(emitted, fotmob.parse(details(scorer=HOME + 10)).events, n)
    assert len(changed) == 1 and n == 1
    corr = changed[0]
    assert corr.type == EventType.CORRECTION and corr.corrects_event_id == "fm-goal-1"
    assert corr.attrs == {"field": "player_id", "previous": f"KES-{HOME + 9}", "value": f"KES-{HOME + 10}"}

    parsed = fotmob.parse(details())
    eng = _engine(parsed)
    eng.ingest_event(corr)
    assert eng.player_stats(f"KES-{HOME + 9}")["goals"] == 0
    assert eng.player_stats(f"KES-{HOME + 10}")["goals"] == 2


def test_live_adapter_polls_and_stops_at_full_time():
    polls = [details(finished=False), details()]

    async def fetch():
        return polls.pop(0) if polls else details()

    async def run():
        adapter = fotmob.FotMobAdapter(details(finished=False), control=ReplayControl(), fetch=fetch, poll_s=1)
        assert adapter.live and adapter.poll_s == fotmob.MIN_POLL_S  # never faster than FotMob's own app
        adapter.poll_s = 0  # tests only: the real floor is 10 s
        return [item async for item in adapter.stream()]

    items = asyncio.run(run())
    ids = [e.event_id for e in items]
    assert len(ids) == len(set(ids))  # nothing sent twice
    assert items[-1].type == EventType.FULL_TIME
    assert {e.event_id for e in items} == {e.event_id for e in fotmob.parse(details()).events}


def test_finished_match_replays_through_a_session():
    from matchlens.runtime.session import MatchSession

    async def run():
        adapter = fotmob.FotMobAdapter(details(), control=ReplayControl(speed=60, start_elapsed=10_000))
        assert not adapter.live
        session = MatchSession(adapter)
        session.start()
        await asyncio.wait_for(session._task, timeout=5)
        return session

    session = asyncio.run(run())
    assert session.finished and session.engine.state()["status"] == "full_time"
    assert session.engine.team_stats("KES")["goals"] == 2


def test_api_is_off_unless_enabled(monkeypatch, tmp_path):
    from matchlens.local import routes as api
    from matchlens.runtime.manager import manager

    monkeypatch.delenv("MATCHLENS_FOTMOB", raising=False)
    with pytest.raises(HTTPException) as off:
        asyncio.run(api.start_fotmob(api.FotMobRequest(match_id=1)))
    assert off.value.status_code == 404

    monkeypatch.setenv("MATCHLENS_FOTMOB", "1")
    monkeypatch.setattr(fotmob, "CACHE_DIR", tmp_path / "fotmob")
    saved = tmp_path / "fotmob" / "999.json"
    saved.parent.mkdir()
    saved.write_text(json.dumps(details()))
    with pytest.raises(HTTPException) as outside:
        asyncio.run(api.start_fotmob(api.FotMobRequest(path="/etc/hosts")))
    assert outside.value.status_code == 422

    async def run():
        started = await api.start_fotmob(api.FotMobRequest(path=str(saved), speed=60, start_minute=100))
        stats = await api.fotmob_stats()
        await manager.stop()
        return started, stats

    started, stats = asyncio.run(run())
    assert started["match_id"] == "fm-999" and started["live"] is False and started["home"] == "Kestrel Rovers"
    assert stats["ratings"] == {f"KES-{HOME + 9}": 8.1}


def test_lineup_required(tmp_path):
    d = copy.deepcopy(details())
    d["content"]["lineup"] = {}
    path = tmp_path / "no-lineup.json"
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="lineup"):
        asyncio.run(fotmob.open_adapter(path=path, control=ReplayControl()))
    with pytest.raises(FileNotFoundError):
        asyncio.run(fotmob.open_adapter(path=tmp_path / "missing.json", control=ReplayControl()))
