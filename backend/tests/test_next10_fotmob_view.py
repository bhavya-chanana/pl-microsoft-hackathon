"""The FotMob view on an invented match (fictional clubs and players), the file loader and the endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from matchlens.next10 import fotmob_view as fv

HOME, AWAY = 1001, 2002


def _shot(i: int, team: int, x: float, y: float, kind: str = "Miss", minute: int = 10, **kw: Any) -> dict[str, Any]:
    return {"id": i, "eventType": kind, "teamId": team, "playerId": 50 + i, "playerName": f"P{i}", "fullName": f"Player {i}",
            "x": x, "y": y, "min": minute, "minAdded": None, "isBlocked": False, "isOnTarget": kind == "Goal",
            "expectedGoals": 0.1234567, "expectedGoalsOnTarget": 0.0, "shotType": "RightFoot", "situation": "RegularPlay",
            "period": "FirstHalf", "isOwnGoal": False, **kw}


def _player(pid: int, name: str, shirt: str, pos: int, rating: float, events: list[str] | None = None) -> dict[str, Any]:
    perf: dict[str, Any] = {"rating": rating}
    if events:
        perf["events"] = [{"type": t} for t in events]
    return {"id": pid, "name": name, "shirtNumber": shirt, "positionId": 100 + pid, "usualPlayingPositionId": pos, "age": 24,
            "countryCode": "XXX", "horizontalLayout": {"x": 0.5, "y": 0.5}, "performance": perf}


def _stat(title: str, key: str, a: Any, b: Any) -> dict[str, Any]:
    return {"title": title, "key": key, "stats": [a, b], "rawStats": [{"value": a}, {"value": b}], "format": "integer",
            "type": "text", "highlighted": "home"}


def fixture() -> dict[str, Any]:
    stats = {"stats": [{"title": "Top stats", "key": "top_stats", "stats": [
        {"title": "Top stats", "key": "top_stats", "stats": [None, None], "rawStats": [None, None], "type": "title"},
        _stat("Total shots", "total_shots", 5, 3),
        {"title": "Accurate passes", "key": "accurate_passes", "stats": ["217 (79%)", "258 (82%)"],
         "rawStats": [{"value": 217, "total": 273}, {"value": 258, "total": 315}], "type": "text"}]}]}
    return {
        "general": {"matchId": "9000001", "leagueName": "Cup of Nowhere", "leagueRoundName": "2",
                    "homeTeam": {"name": "Dovewick", "id": HOME}, "awayTeam": {"name": "Orbit Rovers", "id": AWAY},
                    "teamColors": {"lightMode": {"home": "#aa0000", "away": "#0000aa"}, "darkMode": {"home": "#cc2222", "away": "#2222cc"},
                                   "fontLightMode": {"home": "#fff", "away": "#fff"}}},
        "header": {"teams": [{"name": "Dovewick", "id": HOME, "score": 2}, {"name": "Orbit Rovers", "id": AWAY, "score": 1}],
                   "status": {"started": True, "finished": False, "cancelled": False, "scoreStr": "2 - 1",
                              "numberOfHomeRedCards": 0, "numberOfAwayRedCards": 1,
                              "halfs": {"firstHalfEnded": "x", "secondHalfStarted": "y"},
                              "liveTime": {"short": "‎63‎’", "long": "62:10", "addedTime": 0}}},
        "content": {
            "matchFacts": {
                "infoBox": {"Match Date": {"utcTime": "2030-01-02T19:45:00.000Z"},
                            "Tournament": {"leagueName": "Cup of Nowhere", "roundName": "2"},
                            "Stadium": {"name": "Dove Park", "city": "Dovewick", "country": "Nowhere", "capacity": 20000, "surface": "grass"},
                            "Referee": {"text": "Ref Example", "country": "Nowhere"}, "Attendance": 18000},
                "events": {"events": [  # newest first, as FotMob lists them
                    {"type": "Substitution", "time": 60, "overloadTime": None, "isHome": False, "swap": [
                        {"name": "Sub In", "id": "7"}, {"name": "Sub Out", "id": "8"}]},
                    {"type": "Card", "time": 55, "isHome": False, "card": "Red", "player": {"id": 9, "name": "Red Card"}},
                    {"type": "Half", "time": 45, "halfStrShort": "HT"},
                    {"type": "Goal", "time": 45, "overloadTime": 2, "isHome": True, "player": {"id": 11, "name": "Home Scorer"},
                     "newScore": [2, 0], "goalDescriptionKey": "penalty", "ownGoal": None, "assistStr": None,
                     "shotmapEvent": {"expectedGoals": 0.76}},
                    {"type": "Goal", "time": 20, "isHome": True, "player": {"id": 12, "name": "Own Goaler"}, "newScore": [1, 0],
                     "ownGoal": True, "assistStr": "assist by Helper One", "assistPlayerId": 13, "assistInput": "Helper One"}]},
                "teamForm": [[{"resultString": "W", "score": "2 - 0", "date": {"utcTime": "2029-12-01T12:00:00Z"},
                               "tooltipText": {"homeScore": "2", "awayScore": "0"},
                               "home": {"id": "1001", "name": "Dovewick", "isOurTeam": True}, "away": {"id": "3", "name": "Third FC", "isOurTeam": False}}],
                             [{"resultString": "L", "score": "3 - 1", "date": {"utcTime": "2029-12-02T12:00:00Z"},
                               "tooltipText": {"homeScore": "3", "awayScore": "1"},
                               "home": {"id": "4", "name": "Fourth FC", "isOurTeam": False}, "away": {"id": "2002", "name": "Orbit Rovers", "isOurTeam": True}}]]},
            "weather": {"temperature": 7, "description": "Drizzle", "windSpeed": 12, "windDirectionCardinal": "N", "iconCode": 3},
            "stats": {"Periods": {"All": stats, "FirstHalf": stats, "SecondHalf": {"stats": []}}},
            "momentum": {"main": {"data": [{"minute": 0, "value": 0}, {"minute": 1, "value": 12}, {"minute": 2, "value": None}]}},
            "shotmap": {"shots": [
                _shot(2, AWAY, 94, 34, "Goal", 30),        # away penalty-spot goal
                _shot(1, HOME, 94, 34, "Goal", 20),        # home penalty-spot goal
                _shot(3, HOME, 80, 20, "AttemptSaved", 25, isBlocked=True),
                _shot(4, 9999, 80, 20)]},                  # a team we do not know: dropped
            "lineup": {"lineupType": "standard",
                       "homeTeam": {"id": HOME, "name": "Dovewick", "formation": "4-4-2", "rating": 6.71234,
                                    "coach": {"id": 99, "name": "Coach Example", "countryCode": "XXX"},
                                    "starters": [_player(11, "Home Scorer", "9", 3, 7.2, ["goal", "goal", "yellowCard"])],
                                    "subs": [_player(14, "Bench One", "14", 2, 6.0)], "unavailable": [
                                        {"id": 15, "name": "Hurt Player", "unavailability": {"type": "injury", "expectedReturn": "Soon"}}]},
                       "awayTeam": {"id": AWAY, "name": "Orbit Rovers", "formation": "4-3-3", "starters": [], "subs": []}},
            "playerStats": {"11": {"stats": [{"key": "top_stats", "stats": {
                "FotMob rating": {"stat": {"value": 7.53}}, "Expected goals (xG)": {"stat": {"value": 0.84}}}}]}},
            "attackingZones": {"home": {"total": {"left": 40, "center": 30, "right": 30}, "firstHalf": {"left": 1, "center": 2, "right": 3}}},
            "h2h": {"summary": [2, 1, 1], "matches": [
                {"status": {"finished": False, "utcTime": "2031-01-01T00:00:00Z"}, "home": {"id": "1001", "name": "Dovewick"}, "away": {"id": "2002", "name": "Orbit Rovers"}},
                {"status": {"finished": True, "scoreStr": "1 - 2", "utcTime": "2029-01-01T00:00:00Z"}, "league": {"name": "League A"},
                 "home": {"id": "2002", "name": "Orbit Rovers"}, "away": {"id": "1001", "name": "Dovewick"}},
                {"status": {"finished": True, "scoreStr": "0 - 0", "utcTime": "2028-01-01T00:00:00Z"},
                 "home": {"id": "1001", "name": "Dovewick"}, "away": {"id": "2002", "name": "Orbit Rovers"}}]},
            "news": [{"title": "left out"}], "buzz": {"x": 1}}}


@pytest.fixture(scope="module")
def view() -> dict[str, Any]:
    return fv.build_view(fixture(), "fotmob:9000001", "2030-01-02T20:00:00+00:00")


def test_header(view: dict[str, Any]) -> None:
    h = view["header"]
    assert (h["home"]["name"], h["home"]["score"], h["away"]["score"], h["away"]["red_cards"]) == ("Dovewick", 2, 1, 1)
    assert h["home"]["colour"] == "#aa0000" and h["away"]["colour_dark"] == "#2222cc"
    assert h["status"]["state"] == "live" and h["status"]["minute"] == 63 and h["status"]["period"] == 2
    assert h["venue"]["name"] == "Dove Park" and h["referee"]["name"] == "Ref Example" and h["attendance"] == 18000
    assert h["weather"]["description"] == "Drizzle" and h["weather"]["temperature_c"] == 7
    assert h["competition"] == "Cup of Nowhere" and h["kickoff_utc"].startswith("2030-01-02")


def test_events_in_match_order(view: dict[str, Any]) -> None:
    ev = view["events"]
    assert [(e["type"], e["minute"], e["added"]) for e in ev] == [
        ("goal", 20, None), ("goal", 45, 2), ("half", 45, None), ("card", 55, None), ("sub", 60, None)]
    own, pen = ev[0], [e for e in ev if e["type"] == "goal"][1]
    assert own["own_goal"] and own["assist"] == {"id": 13, "name": "Helper One"} and own["score"] == [1, 0]
    assert pen["penalty"] and pen["added"] == 2 and pen["xg"] == 0.76 and pen["assist"] is None
    card = next(e for e in ev if e["type"] == "card")
    assert card["card"] == "red" and card["team"] == "away"
    sub = next(e for e in ev if e["type"] == "sub")
    assert sub["player_in"]["name"] == "Sub In" and sub["player_out"]["id"] == 8
    assert next(e for e in ev if e["type"] == "half")["team"] is None


def test_stats_groups_keep_fotmobs_own(view: dict[str, Any]) -> None:
    s = view["stats"]
    assert set(s) == {"all", "first_half", "second_half"} and s["second_half"] == []
    rows = s["all"][0]["rows"]
    assert s["all"][0]["key"] == "top_stats" and [r["key"] for r in rows] == ["total_shots", "accurate_passes"]  # title row dropped
    assert (rows[0]["home"], rows[0]["away"]) == (5, 3)
    assert (rows[1]["home"], rows[1]["home_total"], rows[1]["away_text"]) == (217, 273, "258 (82%)")


def test_momentum_keeps_gaps(view: dict[str, Any]) -> None:
    assert view["momentum"] == [{"minute": 0, "value": 0}, {"minute": 1, "value": 12}, {"minute": 2, "value": None}]


def test_shot_coordinates_home_attacks_positive_x(view: dict[str, Any]) -> None:
    shots = view["shotmap"]
    assert [s["id"] for s in shots] == [1, 3, 2]  # by minute, the unknown team dropped
    home_pen, home_blocked, away_pen = shots
    assert (home_pen["x"], home_pen["y"]) == (41.5, 0.0) and home_pen["team"] == "home"  # penalty spot, home goal at +x
    assert (away_pen["x"], away_pen["y"]) == (-41.5, 0.0) and away_pen["team"] == "away"  # the away side attacks -x
    assert (home_blocked["x"], home_blocked["y"]) == (27.5, -14.0)
    assert home_pen["outcome"] == "goal" and home_blocked["outcome"] == "blocked"
    assert home_pen["xg"] == 0.123 and home_pen["player"] == "Player 1"
    # all shots of a side lie in the half it attacks
    assert fv.shot_xy(100, 10, True)[0] > 0 and fv.shot_xy(100, 10, False)[0] < 0
    assert fv.shot_xy(None, 1, True) == (None, None)


def test_lineups(view: dict[str, Any]) -> None:
    home = view["lineups"]["home"]
    assert home["formation"] == "4-4-2" and home["coach"]["name"] == "Coach Example" and home["rating"] == 6.71
    p = home["starters"][0]
    assert (p["name"], p["shirt"], p["position"], p["goals"], p["yellow_cards"], p["red_cards"]) == ("Home Scorer", 9, "FWD", 2, 1, 0)
    assert p["rating"] == 7.53 and p["xg"] == 0.84  # the precise rating from playerStats
    assert home["subs"][0]["position"] == "MID" and home["subs"][0]["rating"] == 6.0
    assert home["unavailable"][0]["reason"] == "injury"
    assert view["lineups"]["away"]["coach"] is None


def test_zones_h2h_form(view: dict[str, Any]) -> None:
    assert view["attacking_zones"]["home"]["first_half"] == {"left": 1, "center": 2, "right": 3}
    h2h = view["h2h"]
    assert h2h["summary"] == {"home_wins": 2, "draws": 1, "away_wins": 1}
    assert [(m["score"], m["result_for_home"]) for m in h2h["last"]] == [("1 - 2", "W"), ("0 - 0", "D")]  # newest first, no fixtures
    home_form, away_form = view["team_form"]["home"][0], view["team_form"]["away"][0]
    assert (home_form["result"], home_form["venue"], home_form["goals_for"], home_form["opponent"]["name"]) == ("W", "H", 2, "Third FC")
    assert (away_form["venue"], away_form["goals_for"], away_form["goals_against"]) == ("A", 1, 3)


def test_left_out_sections(view: dict[str, Any]) -> None:
    assert set(view) == {"match_id", "fotmob_id", "fetched_at", "header", "events", "stats", "momentum", "shotmap", "lineups",
                         "attacking_zones", "h2h", "team_form", "shootout"}
    assert "news" not in json.dumps(view) and "left out" not in json.dumps(view)


def test_missing_sections_are_none_not_errors() -> None:
    v = fv.build_view({"general": {"homeTeam": {"id": 1, "name": "A"}, "awayTeam": {"id": 2, "name": "B"}}, "header": {"status": {}},
                       "content": {}}, "fotmob:1")
    assert v["header"]["status"]["state"] == "scheduled"
    assert v["stats"] is None and v["momentum"] is None and v["lineups"] is None and v["h2h"] is None and v["team_form"] is None
    assert v["events"] == [] and v["shotmap"] is None
    broken = fv.build_view({"general": {}, "header": {}, "content": {"lineup": {"homeTeam": 5}}}, "fotmob:2")
    assert broken["lineups"] is None and broken["events"] == []  # one malformed section does not raise


def test_match_ids() -> None:
    assert fv.parse_match_id("fotmob:6275371") == 6275371 and fv.parse_match_id("6275371") == 6275371
    assert fv.parse_match_id("clip:liv-bha-2627") is None


def test_load_view_reads_the_cache_only_and_follows_the_file(tmp_path: Path) -> None:
    assert fv.load_view("fotmob:9000001", tmp_path) is None and fv.load_view("clip:x", tmp_path) is None
    path = tmp_path / "9000001.json"
    path.write_text(json.dumps(fixture()))
    first = fv.load_view("fotmob:9000001", tmp_path)
    assert first and first["header"]["home"]["score"] == 2 and fv.load_view("fotmob:9000001", tmp_path) is first  # cached by mtime
    changed = fixture()
    changed["header"]["teams"][0]["score"] = 3
    path.write_text(json.dumps(changed))
    import os
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 5_000_000_000))
    assert fv.load_view("fotmob:9000001", tmp_path)["header"]["home"]["score"] == 3


def test_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from matchlens.api import next10_routes as routes
    from matchlens.local import fotmob

    (tmp_path / "9000001.json").write_text(json.dumps(fixture()))
    monkeypatch.setattr(fotmob, "CACHE_DIR", tmp_path)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        ok = client.get("/api/next10/matches/fotmob:9000001/fotmob")
        assert ok.status_code == 200 and ok.json()["header"]["home"]["name"] == "Dovewick"
        assert client.get("/api/next10/matches/fotmob:123/fotmob").status_code == 404
        assert client.get("/api/next10/matches/clip:liv-bha-2627/fotmob").status_code == 404


def test_a_video_match_reads_its_fotmob_id_from_the_match_doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from matchlens.api import next10_routes as routes
    from matchlens.local import fotmob
    from matchlens.next10.repo import MemoryRepo

    (tmp_path / "9000001.json").write_text(json.dumps(fixture()))
    monkeypatch.setattr(fotmob, "CACHE_DIR", tmp_path)
    repo = MemoryRepo()
    repo.put("matches", {"id": "clip:a", "match_id": "clip:a", "source": "video", "fotmob_id": 9000001},
             {"id": "clip:b", "match_id": "clip:b", "source": "video"})
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        monkeypatch.setattr(routes.state, "repo", repo)
        got = client.get("/api/next10/matches/clip:a/fotmob")
        assert got.status_code == 200 and got.json()["match_id"] == "clip:a" and got.json()["fotmob_id"] == 9000001
        assert client.get("/api/next10/matches/clip:b/fotmob").status_code == 404


class _Linear:
    """A clock map where the match clock is the video second plus 900."""

    def to_video(self, m: float, period: int | None = None) -> float:
        return m - 900.0

    def to_match(self, v: float) -> float:
        return v + 900.0


def test_fotmob_moments_on_a_clip_placed_through_the_clock_map() -> None:
    from matchlens.next10.moments import video_fotmob_moments

    d = fixture()
    d["content"]["shotmap"]["shots"] = [  # the fixture's goals at minutes 20 (own goal) and 45, plus plain shots
        _shot(1, HOME, 94, 34, "Goal", 45, expectedGoals=0.76),
        _shot(2, AWAY, 90, 30, "Miss", 21, expectedGoals=0.30),            # a chance for the away side
        _shot(3, AWAY, 80, 30, "Miss", 21, expectedGoals=0.05),            # too small
        _shot(4, HOME, 85, 30, "Miss", 60, expectedGoals=0.50),            # outside the clip window
        _shot(5, AWAY, 99, 34, "Goal", 20, isOwnGoal=True, expectedGoals=0.0)]
    match = {"home": {"name": "Dovewick"}, "away": {"name": "Orbit Rovers"}}
    docs = video_fotmob_moments("clip:x", d, match, _Linear(), window=(1100.0, 2800.0), pins={20: 305.5})
    by = {x["id"]: x for x in docs}
    kinds = sorted((x["minute"], x["kind"], x["team_side"]) for x in docs)
    # minute 20 own goal by the away side's player: the goal belongs to home, so away conceded it; no chance for it
    assert (20, "goal_conceded", "away") in kinds and not any(k[0] == 20 and k[1] == "chance" for k in kinds)
    assert (21, "chance", "away") in kinds and (45, "goal_conceded", "away") in kinds
    assert not any(k[0] == 45 and k[1] == "chance" for k in kinds)  # a goal is one moment, not also a chance
    assert not any(k[0] == 60 for k in kinds) and len([k for k in kinds if k[0] == 21]) == 1
    own = next(x for x in docs if x["minute"] == 20)
    assert own["placed"] == "observed" and own["t_s"] == 305.5 and own["t_s_error_s"] == 1.0 and own["own_goal"]
    mid = next(x for x in docs if x["minute"] == 21)
    assert mid["placed"] == "clock_map" and mid["t_s"] == 20 * 60 + 30 - 900 and mid["t_s_error_s"] == 30.0
    assert mid["match_clock_s"] == 1230.0 and mid["team"] == "Orbit Rovers" and by[mid["id"]]["source"] == "fotmob"


def test_shootout_start_from_the_cached_shot_map() -> None:
    from matchlens.next10.moments import shootout_start

    d = fixture()
    assert shootout_start(d) is None and shootout_start(None) is None
    d["content"]["shotmap"]["shots"] += [_shot(7, HOME, 94, 34, "Goal", 121, period="PenaltyShootout"),
                                         _shot(8, AWAY, 94, 34, "Miss", 122, period="PenaltyShootout")]
    assert shootout_start(d) == 120  # the score doc's minute for FotMob's 121st


def _with_shootout() -> dict[str, Any]:
    d = fixture()
    d["header"]["status"].update(finished=True, scoreStr="2 - 2", reason={"short": "Pen", "long": "Pen 4 - 3", "penalties": [4, 3]})
    kick = {"type": "Goal", "time": 121, "isPenaltyShootoutEvent": True, "isHome": True, "player": {"id": 21, "name": "Kicker One"},
            "newScore": [3, 2], "penShootoutScore": [1, 0], "goalDescriptionKey": "penalty"}
    miss = {"type": "MissedPenalty", "time": 121, "isPenaltyShootoutEvent": True, "isHome": False, "nameStr": "Kicker Two",
            "player": {"id": 22, "name": "Kicker Two"}, "penShootoutScore": [1, 0]}
    ev = d["content"]["matchFacts"]["events"]
    ev["events"] = [{"type": "PenaltyShootout", "time": 121, "penaltyScore": [4, 3]}, dict(kick), *ev["events"]]
    ev["penaltyShootoutEvents"] = [kick, miss]
    d["content"]["shotmap"]["shots"] += [_shot(70, HOME, 94, 34, "Goal", 91, period="PenaltyShootout", expectedGoals=0.79),
                                         _shot(71, AWAY, 94, 34, "AttemptSaved", 92, period="PenaltyShootout", expectedGoals=0.79)]
    return d


def test_penalty_shootout_is_kept_out_of_events_and_shots_and_returned_apart() -> None:
    v = fv.build_view(_with_shootout(), "fotmob:9000001")
    assert [s["id"] for s in v["shotmap"]] == [1, 3, 2]  # the two kicks (ids 70, 71) are not shots of the match
    assert sum(s["xg"] for s in v["shotmap"]) < 0.5 and all(s["minute"] < 91 for s in v["shotmap"])
    assert [e["type"] for e in v["events"]] == ["goal", "goal", "half", "card", "sub"]  # no kick, no shoot-out marker
    h = v["header"]
    assert h["penalties"] == {"home": 4, "away": 3} and h["status"]["label"] == "Pen" and h["status"]["text"] == "Pen 4 - 3"
    assert h["score_str"] == "2 - 2" and h["status"]["state"] == "finished"
    assert v["shootout"] == [
        {"order": 1, "team_side": "home", "scored": True, "player": {"id": 21, "name": "Kicker One"}, "score": [1, 0]},
        {"order": 2, "team_side": "away", "scored": False, "player": {"id": 22, "name": "Kicker Two"}, "score": [1, 0]}]
    plain = fv.build_view(fixture(), "fotmob:9000001")
    assert plain["shootout"] is None and plain["header"]["penalties"] is None


def test_model_peaks_next_to_a_goal_are_dropped_and_peaks_in_shot_minutes_say_which_shot() -> None:
    from matchlens.next10.moments import refine_model_moments, video_fotmob_moments

    d = fixture()
    d["content"]["shotmap"]["shots"] = [
        _shot(1, HOME, 94, 34, "Goal", 45, expectedGoals=0.76),
        _shot(2, AWAY, 90, 30, "AttemptSaved", 21, expectedGoals=0.30, fullName="Orbit Striker"),
        _shot(3, HOME, 94, 34, "Goal", 120, period="PenaltyShootout")]
    match = {"home": {"name": "Dovewick"}, "away": {"name": "Orbit Rovers"}}
    goals = video_fotmob_moments("clip:x", d, match, _Linear(), window=(1100.0, 2800.0), pins={20: 305.5})

    def peak(t: float) -> dict[str, Any]:
        return {"id": f"p{t}", "kind": "chance", "source": "model", "t_s": t, "match_clock_s": t + 900, "p_shot10": 0.48,
                "label": "Model shot-chance peak: 48% chance of a shot within 10 s (no recorded shot)"}

    got = {x["id"]: x for x in refine_model_moments([peak(300.0), peak(330.0), peak(500.0)], d, goals)}
    assert "p300.0" not in got  # 5.5 s from the own goal at 305.5: the goal covers it
    assert "a shot followed in this minute: Orbit Striker, saved" in got["p330.0"]["label"]  # clock 1230 = minute 21
    assert "no recorded shot" not in got["p330.0"]["label"]
    assert got["p500.0"]["label"].endswith("(no recorded shot)")  # clock 1400 = minute 24, no shot
