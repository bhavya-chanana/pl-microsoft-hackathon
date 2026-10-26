"""Offline tests for the Transfermarkt / FPL / StatsBomb adapters and identity resolution.

Fixtures (tests/fixtures/leaguedata/*) use fictional names only; nothing here
touches the network.
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest

from matchlens.leaguedata import ids
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.schema import TABLES, Club, League, SourceIds
from matchlens.leaguedata.sources import fpl, statsbomb, transfermarkt

FIX = Path(__file__).parent / "fixtures" / "leaguedata"


def _cfg(tmp_path: Path) -> Config:
    return Config(root=tmp_path)


def _club(source: str, cid: str, name: str, short: str | None = None, **other: str) -> Club:
    return Club(team_id=f"{source}:{cid}", name=name, short_name=short,
                source_ids=SourceIds(**{source: cid}, **other))  # type: ignore[arg-type]


# --------------------------------------------------------------------- slugs


def test_slug_strips_affixes_and_accents():
    assert ids.slug("Burnley FC") == "burnley"
    assert ids.slug("AFC Northvale") == "northvale"
    assert ids.slug("Club Brugge KV") == "brugge-kv"
    assert ids.slug("Real Marlowe CF") == "real-marlowe"
    assert ids.slug("SSC Santo Brava") == "santo-brava"
    assert ids.slug("Brighton & Hove Albion") == "brighton-and-hove-albion"
    assert ids.slug("Atlético de Madrid") == "atletico-de-madrid"
    assert ids.slug("Ørjan Ñandú Łukasz Æther") == "orjan-nandu-lukasz-aether"
    assert ids.slug("N'Golo-Kanté") == "ngolo-kante"
    assert ids.slug("FC") == "fc"  # never reduced to nothing


def test_slug_is_idempotent_and_person_slug_keeps_affix_words():
    for name in ("Northvale Rovers FC", "AC Eastport", "Santo Brava Football Club"):
        assert ids.slug(ids.slug(name)) == ids.slug(name)
    assert ids.person_slug("Ac Dc Alvin") == "ac-dc-alvin"


def test_match_key_format_and_canonical_ids():
    key = ids.match_key(League.UCL, "2023-24", "2024-02-14", "Eastport United", "santo-brava")
    assert key == "UCL:2023-24:2024-02-14:eastport-united-santo-brava"
    assert ids.match_key("EPL", "2023-24", "2023-08-12", "northvale-rovers", "eastport-united") == \
        "EPL:2023-24:2023-08-12:northvale-rovers-eastport-united"
    # a canonical id passes through unchanged
    assert ids.match_key("EPL", "2023-24", "d", ids.slug("Northvale Rovers FC"), "x").endswith(":northvale-rovers-x")


# ------------------------------------------------------------------- clubs


@pytest.fixture
def clubs() -> ids.ClubResolver:
    r = ids.ClubResolver(overrides={"club": {
        "understat:77": "eastport-united",
        "name:The Eastenders": "eastport-united",
    }})
    r.add_clubs("transfermarkt", [
        _club("transfermarkt", "100", "Northvale Rovers FC"),
        _club("transfermarkt", "200", "Eastport United"),
        _club("transfermarkt", "201", "Eastport City"),
        _club("transfermarkt", "300", "Santo Brava", short="Santo Brava de Futbol"),
        _club("transfermarkt", "400", "Wexford Town"),
    ])
    return r


def test_club_canonical_is_transfermarkt_name_slug(clubs):
    assert clubs.resolve("transfermarkt", "100") == "northvale-rovers"
    assert clubs.resolve("transfermarkt", "transfermarkt:100") == "northvale-rovers"
    assert clubs.resolve("transfermarkt", "200", "Eastport United") == "eastport-united"


def test_club_name_match_override_and_ids(clubs):
    assert clubs.resolve("understat", "5", "Northvale Rovers") == "northvale-rovers"
    assert clubs.resolve("understat", "6", "Santo Brava de Futbol") == "santo-brava"  # short name = alias
    assert clubs.resolve("understat", "77", "Something Else Entirely") == "eastport-united"  # id override
    assert clubs.resolve("football_data", None, "The Eastenders") == "eastport-united"  # name override
    by_key = {(link.source, link.source_id): link for link in clubs.links()}
    assert by_key[("understat", "77")].method == "override" and by_key[("understat", "77")].confidence == 1.0
    link = by_key[("understat", "5")]
    assert (link.entity_kind, link.entity_id, link.confidence) == ("club", "northvale-rovers", 0.95)


def test_club_builtin_alias_only_applies_when_target_known(monkeypatch):
    monkeypatch.setattr(ids, "BUILTIN_CLUB_ALIASES", {"Wexies": "Wexford Town", "Ghosts": "Absent Rovers"})
    r = ids.ClubResolver(overrides={})
    r.add_clubs("transfermarkt", [_club("transfermarkt", "400", "Wexford Town")])
    assert r.resolve("understat", "1", "Wexies") == "wexford-town"
    assert r.resolve("understat", "2", "Ghosts") == "ghosts"  # target unknown: its own identity


def test_club_unique_prefix_and_fuzzy_thresholds(clubs):
    assert clubs.resolve("understat", "10", "Wexford") == "wexford-town"  # unique prefix
    assert clubs.resolve("understat", "11", "Eastport") == "eastport"  # ambiguous prefix: not guessed
    assert clubs.resolve("understat", "12", "Northvale Rovrs") == "northvale-rovers"  # typo, ratio >= 0.9
    assert clubs.resolve("understat", "13", "Northvale Wanderers") == "northvale-wanderers"  # below threshold
    conf = {(link.source_id): (link.confidence, link.method) for link in clubs.links()}
    assert conf["10"] == (0.7, "fuzzy") and conf["12"] == (0.7, "fuzzy")
    assert conf["11"][0] == 0.5 and conf["13"][0] == 0.5


def test_club_links_from_multi_id_rows_and_overrides_file(tmp_path):
    (tmp_path / "overrides.json").write_text(json.dumps({"club": {"fpl:9": "wexford-town"}}))
    r = ids.ClubResolver(_cfg(tmp_path))
    r.add_clubs("transfermarkt", [_club("transfermarkt", "400", "Wexford Town")])
    r.add_clubs("wikidata", [Club(team_id="wikidata:Q1", name="Wexford Athletic Society",
                                  source_ids=SourceIds(wikidata="Q1", transfermarkt="400"))])
    assert r.resolve("wikidata", "Q1") == "wexford-town"  # shared Transfermarkt id
    assert r.resolve("fpl", "9", "Whatever") == "wexford-town"  # overrides.json
    with pytest.raises(ValueError):
        r.resolve("fpl")


# ------------------------------------------------------------------ persons


def test_player_name_club_season_then_birth_then_fuzzy():
    clubs = ids.ClubResolver(overrides={})
    clubs.add_clubs("transfermarkt", [_club("transfermarkt", "100", "Northvale Rovers FC")])
    players = ids.PlayerResolver(overrides={}, clubs=clubs)
    tm = players.resolve("transfermarkt", "11", "Alvin Marlowe", "transfermarkt:100", "2023-24", "1996-03-04")
    assert tm == "alvin-marlowe"
    # same name + club + season, no birth date -> 0.95
    assert players.resolve("understat", "u1", "Alvin Marlowe", "northvale-rovers", "2023-24") == tm
    # shorter spelling, same club and season (token subset)
    assert players.resolve("fpl", "f1", "Marlowe Alvin", "fpl:xx", "2023-24") != tm  # unknown club "fpl:xx"
    # different club, same name + birth date -> 0.9
    assert players.resolve("statsbomb", "s1", "Alvin Marlowe", "eastport-united", "2015-16", "1996-03-04") == tm
    # typo + same birth date -> fuzzy 0.7
    assert players.resolve("wikidata", "Q9", "Alvin Marlow", None, None, "1996-03-04") == tm
    methods = {(lk.source, lk.source_id): (lk.method, lk.confidence) for lk in players.links()}
    assert methods[("understat", "u1")] == ("name_club_season", 0.95)
    assert methods[("statsbomb", "s1")] == ("name_birth", 0.9)
    assert methods[("wikidata", "Q9")] == ("fuzzy", 0.7)
    assert methods[("transfermarkt", "11")][1] == 0.5  # first sight: its own identity


def test_player_fuzzy_below_threshold_and_conflicting_birth_are_not_merged():
    players = ids.PlayerResolver(overrides={})
    a = players.resolve("transfermarkt", "1", "Bram Keddy", "northvale-rovers", "2023-24", "1999-11-30")
    assert players.resolve("understat", "2", "Bram Kelly", "northvale-rovers", "2023-24") != a  # ratio < 0.9
    assert players.resolve("understat", "3", "Bram Keddy", None, None, "1980-01-01") != a  # other birth date
    assert players.resolve("understat", "4", "Bram Keddy", "northvale-rovers", "2023-24", "1980-01-01") != a


def test_player_same_source_never_merges_and_ids_are_unique():
    players = ids.PlayerResolver(overrides={})
    a = players.resolve("transfermarkt", "1", "Danil Orsk", "northvale-rovers", "2023-24", "1999-01-01")
    b = players.resolve("transfermarkt", "2", "Danil Orsk", "northvale-rovers", "2023-24", "2001-05-05")
    c = players.resolve("transfermarkt", "3", "Danil Orsk")
    assert len({a, b, c}) == 3 and a == "danil-orsk" and b == "danil-orsk-2001"
    assert players.resolve("transfermarkt", "1", "Danil Orsk") == a  # memoised


def test_player_overrides_exact_ids_and_ambiguity():
    players = ids.PlayerResolver(overrides={"player": {"fpl:900": "alvin-marlowe", "name:Bram K": "bram-keddy"}})
    tm = players.resolve("transfermarkt", "11", "Alvin Marlowe", "northvale-rovers", "2023-24")
    assert players.resolve("fpl", "900", "Completely Different Name") == tm  # override first
    assert players.resolve("understat", "7", "Bram K") == "bram-keddy"
    # a source that also carries the Transfermarkt id links by exact id
    assert players.resolve("wikidata", "Q5", "A. Marlowe", extra_ids={"transfermarkt": "11"}) == tm
    # two candidates with the same name in the same club-season, no birth date: not guessed
    players.resolve("transfermarkt", "21", "Pim Doyle", "eastport-united", "2023-24")
    players.resolve("transfermarkt", "22", "Pim Doyle", "eastport-united", "2023-24")
    assert players.resolve("understat", "9", "Pim Doyle", "eastport-united", "2023-24") not in {"pim-doyle", "pim-doyle-2"}
    link = next(lk for lk in players.links() if (lk.source, lk.source_id) == ("fpl", "900"))
    assert (link.entity_kind, link.method, link.confidence) == ("player", "override", 1.0)


def test_manager_resolver_uses_aliases_and_team_season():
    managers = ids.ManagerResolver(overrides={})
    tm = managers.resolve("transfermarkt", "transfermarkt:dario-quenby", "Dario Quenby", "northvale-rovers", "2015-16")
    assert tm == "dario-quenby"
    sb = managers.resolve("statsbomb", "701", "Dario Quenby Ferreira", "northvale-rovers", "2015-16")
    assert sb == tm  # token subset within the same club and season
    other = managers.resolve("statsbomb", "702", "Mara Teller", "eastport-united", "2015-16", aliases=["Mara T"])
    assert other == "mara-teller"
    assert {lk.entity_kind for lk in managers.links()} == {"manager"}


# ------------------------------------------------------------- Transfermarkt


def _tm_cfg(tmp_path: Path) -> Config:
    cfg = _cfg(tmp_path)
    raw = cfg.raw_dir("transfermarkt")
    raw.mkdir(parents=True)
    for table in transfermarkt.TABLES:
        (raw / f"{table}.csv.gz").write_bytes(gzip.compress((FIX / "transfermarkt" / f"{table}.csv").read_bytes()))
    return cfg


def test_tm_formation_and_stage_mapping():
    assert transfermarkt.normalize_formation("4-3-3 Attacking") == "4-3-3"
    assert transfermarkt.normalize_formation("3-5-2 flat") == "3-5-2"
    assert transfermarkt.normalize_formation("4-2-3-1") == "4-2-3-1"
    assert transfermarkt.normalize_formation("") is None and transfermarkt.normalize_formation("Unknown") is None
    stages = {"Group Stage": "group", "Group H": "group", "intermediate stage 1st leg": "playoff",
              "last 16 1st leg": "round_of_16", "Last 16 2nd Leg": "round_of_16", "Quarter-Finals 2nd leg": "quarter_final",
              "Semi-Finals 1st Leg": "semi_final", "Final": "final", "": None}
    for raw, stage in stages.items():
        assert transfermarkt.cl_stage(raw) == stage, raw
    assert transfermarkt.season_label(2023) == "2023-24" and transfermarkt.season_label("2099") == "2099-00"


def test_tm_matches_formations_managers_stages(tmp_path):
    rows = transfermarkt.normalize(_tm_cfg(tmp_path))
    assert set(rows) <= set(TABLES)
    by_key = {m.match_key: m for m in rows["matches"]}
    assert len(by_key) == 4  # 2012 season and the unplayed fixture are excluded
    m = by_key["EPL:2023-24:2023-08-12:northvale-rovers-eastport-united"]
    assert (m.home_goals, m.away_goals) == (2, 1)
    assert (m.home_formation, m.away_formation) == ("4-3-3", "3-5-2")
    assert (m.home_id, m.away_id) == ("transfermarkt:100", "transfermarkt:200")
    assert (m.home_manager_id, m.away_manager_id) == ("transfermarkt:dario-quenby", "transfermarkt:mara-teller")
    assert (m.referee, m.venue, m.attendance) == ("Ines Holloway", "Quarry Park", 30000)
    assert m.stage is None and m.source_ids.transfermarkt == "1"
    ucl = {k.split(":")[2]: v for k, v in by_key.items() if k.startswith("UCL")}
    assert ucl["2024-02-14"].stage == "round_of_16" and ucl["2024-02-14"].attendance is None
    assert ucl["2024-02-14"].home_formation is None and ucl["2024-02-14"].away_manager_id is None
    assert ucl["2023-09-20"].stage == "group" and ucl["2023-09-20"].away_formation is None
    assert ucl["2024-06-01"].stage == "final"
    assert {mg.name for mg in rows["managers"]} == {"Dario Quenby", "Mara Teller", "Luz Ferreno"}
    assert transfermarkt.coverage(rows["matches"])[("UCL", "2023-24")] == (3, 1 / 3)


def test_tm_clubs_players_valuations(tmp_path):
    rows = transfermarkt.normalize(_tm_cfg(tmp_path))
    clubs = {c.team_id: c for c in rows["clubs"]}
    home = clubs["transfermarkt:100"]
    assert (home.name, home.short_name, home.stadium, home.stadium_capacity, home.country) == \
        ("Northvale Rovers FC", "Northvale Rovers Football Club", "Quarry Park", 42000, "Elbonia")
    assert clubs["transfermarkt:300"].stadium_capacity is None
    players = {p.player_id: p for p in rows["players"]}
    p = players["transfermarkt:11"]
    assert (p.name, p.birth_date, p.nationality, p.height_cm, p.foot) == ("Alvin Marlowe", "1996-03-04", "Elbonia", 181, "right")
    assert (p.main_position, p.sub_position) == ("Attack", "Centre-Forward")
    assert players["transfermarkt:13"].height_cm is None and players["transfermarkt:13"].foot is None
    assert players["transfermarkt:22"].name == "Evo Lanter"  # empty name column falls back to the lineup name
    assert players["transfermarkt:14"].birth_date is None  # in a lineup but missing from players.csv
    values = [(v.player_id, v.date, v.market_value_eur) for v in rows["player_valuations"]]
    assert values == [("transfermarkt:11", "2023-06-01", 12_000_000), ("transfermarkt:11", "2024-01-01", 15_000_000)]


def test_tm_lineups_minutes_and_captain(tmp_path):
    rows = transfermarkt.normalize(_tm_cfg(tmp_path))
    key = "EPL:2023-24:2023-08-12:northvale-rovers-eastport-united"
    lineup = {e.player_id: e for e in rows["lineups"] if e.match_key == key}
    assert len(lineup) == 6  # the duplicated row is dropped
    striker = lineup["transfermarkt:11"]
    assert (striker.starter, striker.captain, striker.shirt, striker.position) == (True, True, 9, "Centre-Forward")
    assert (striker.minute_on, striker.minute_off) == (0, 85)  # sent off
    assert (lineup["transfermarkt:12"].minute_on, lineup["transfermarkt:12"].minute_off) == (0, 60)  # substituted
    sub = lineup["transfermarkt:13"]
    assert (sub.starter, sub.minute_on, sub.minute_off) == (False, 60, None)
    unused = lineup["transfermarkt:14"]
    assert (unused.starter, unused.minute_on, unused.minute_off, unused.shirt) == (False, 0, 0, None)
    assert lineup["transfermarkt:22"].minute_off == 80  # second yellow
    assert lineup["transfermarkt:21"].team_id == "transfermarkt:200"


def test_tm_events_kinds_and_teams(tmp_path):
    rows = transfermarkt.normalize(_tm_cfg(tmp_path))
    ev = {e.event_id.split(":")[1]: e for e in rows["match_events"]}
    assert "e7" not in ev  # shootout rows are not timeline events
    assert (ev["e1"].kind, ev["e1"].player_id, ev["e1"].related_player_id) == ("goal", "transfermarkt:11", "transfermarkt:12")
    assert ev["e2"].kind == "penalty_goal" and ev["e2"].related_player_id is None
    assert (ev["e3"].kind, ev["e3"].team_id, ev["e3"].related_player_id) == ("own_goal", "transfermarkt:200", None)
    assert ev["e4"].kind == "yellow" and ev["e5"].kind == "second_yellow" and ev["e8"].kind == "red"
    assert (ev["e6"].kind, ev["e6"].player_id, ev["e6"].related_player_id, ev["e6"].minute) == \
        ("sub", "transfermarkt:12", "transfermarkt:13", 60)  # player_id goes off, related comes on


def test_tm_normalize_filters_leagues_and_seasons(tmp_path):
    cfg = _tm_cfg(tmp_path)
    only_cl = transfermarkt.normalize(cfg, leagues=[League.UCL], seasons=["2023-24"])
    assert {m.league for m in only_cl["matches"]} == {League.UCL}
    old = transfermarkt.normalize(cfg, seasons=["2012-13"])
    assert [m.date for m in old["matches"]] == ["2012-08-12"]


def test_tm_fetch_skips_leagues_it_does_not_cover(tmp_path):
    assert transfermarkt.fetch(_cfg(tmp_path), [], ["2023-24"]) == 0


# ----------------------------------------------------------------------- FPL


def _fpl_cfg(tmp_path: Path) -> Config:
    cfg = _cfg(tmp_path)
    shutil.copytree(FIX / "fpl", cfg.raw_dir("fpl"))
    return cfg


def test_fpl_normalize_players_clubs_and_live_overrides_history(tmp_path):
    rows = fpl.normalize(_fpl_cfg(tmp_path))
    players = {p.player_id: p for p in rows["players"]}
    assert set(players) == {"fpl:900001", "fpl:900002", "fpl:900003", "fpl:900004"}
    keeper = players["fpl:900001"]
    assert (keeper.name, keeper.birth_date, keeper.main_position, keeper.source_ids.fpl) == \
        ("Orla Vennick", "1998-02-03", "Goalkeeper", "900001")  # birth date from the live payload
    assert players["fpl:900002"].name == "Pim Harrowgate Doyle" and players["fpl:900002"].main_position == "Midfield"
    assert players["fpl:900004"].main_position == "Defender" and players["fpl:900004"].birth_date is None
    clubs = {c.team_id: c for c in rows["clubs"]}
    assert (clubs["fpl:100"].name, clubs["fpl:100"].short_name, clubs["fpl:100"].country) == ("Northvale Rovers", "NOR", "England")
    assert set(clubs) == {"fpl:100", "fpl:200", "fpl:300"}


def test_fpl_contexts_and_live_season(tmp_path):
    cfg = _fpl_cfg(tmp_path)
    assert fpl.live_season({"events": [{"deadline_time": "2026-08-14T17:30:00Z"}]}) == "2026-27"
    ctx = {(c.player_id, c.season): c for c in fpl.player_contexts(cfg)}
    assert ctx[("fpl:900002", "2023-24")].team_id == "fpl:200" and ctx[("fpl:900002", "2023-24")].web_name == "Harrowgate"
    assert ctx[("fpl:900004", "2026-27")].team_name == "Santo Brava"
    only = fpl.normalize(cfg, seasons=["2023-24"])
    assert {p.player_id for p in only["players"]} == {"fpl:900001", "fpl:900002", "fpl:900003"}


def test_fpl_fetch_is_epl_only(tmp_path):
    assert fpl.fetch(_cfg(tmp_path), [League.LA_LIGA, League.UCL], ["2023-24"]) == 0


# ----------------------------------------------------------------- StatsBomb


def _sb_cfg(tmp_path: Path) -> Config:
    cfg = _cfg(tmp_path)
    shutil.copytree(FIX / "statsbomb", cfg.raw_dir("statsbomb"))
    return cfg


def test_statsbomb_coordinate_conversion_and_labels():
    assert statsbomb.to_metres(120, 80) == (105.0, 68.0)
    assert statsbomb.to_metres(60, 40) == (52.5, 34.0)
    assert statsbomb.to_metres(0, 0) == (0.0, 0.0)
    assert statsbomb.formation_text(4231) == "4-2-3-1" and statsbomb.formation_text(433) == "4-3-3"
    assert statsbomb.formation_text(None) is None
    assert statsbomb.season_label("2015/2016") == "2015-16" and statsbomb.season_label("2022") == "2022"


def test_statsbomb_shots(tmp_path):
    rows = statsbomb.normalize(_sb_cfg(tmp_path))
    shots = {s.shot_id.split(":")[1]: s for s in rows["shots"]}
    assert set(shots) == {"s-1", "s-2", "s-3", "s-5"}  # the shoot-out attempt is excluded
    goal = shots["s-1"]
    assert (goal.x, goal.y) == (94.5, 30.6)  # 108 * 105/120, 36 * 68/80
    assert (goal.result, goal.situation, goal.body_part, goal.xg) == ("goal", "open_play", "right_foot", 0.3)
    assert (goal.last_action, goal.assist_player_id, goal.team_id) == ("Throughball", "statsbomb:502", "statsbomb:10")
    assert (shots["s-2"].situation, shots["s-2"].result, shots["s-2"].body_part) == ("penalty", "saved", "left_foot")
    assert (shots["s-3"].situation, shots["s-3"].result, shots["s-3"].body_part) == ("corner", "blocked", "head")
    assert (shots["s-5"].situation, shots["s-5"].result, shots["s-5"].body_part, shots["s-5"].last_action) == \
        ("fast_break", "missed", "other", "TakeOn")
    # score before each shot: goal, own goal conceded by the away side
    assert [(s.home_score_before, s.away_score_before) for s in (goal, shots["s-2"], shots["s-3"], shots["s-5"])] == \
        [(0, 0), (1, 0), (2, 0), (2, 0)]
    assert all(0 <= s.x <= 105 and 0 <= s.y <= 68 for s in rows["shots"])


def test_statsbomb_matches_lineups_players(tmp_path):
    rows = statsbomb.normalize(_sb_cfg(tmp_path))
    by_key = {m.match_key: m for m in rows["matches"]}
    epl = by_key["EPL:2015-16:2015-09-19:northvale-rovers-eastport-united"]
    assert (epl.home_formation, epl.away_formation) == ("4-3-3", "3-4-2-1")
    assert (epl.home_goals, epl.away_goals, epl.referee, epl.venue) == (2, 0, "Ines Holloway", "Quarry Park")
    assert epl.home_manager_id == "statsbomb:701" and epl.stage is None and epl.source_ids.statsbomb == "1001"
    ucl = by_key["UCL:2017-18:2018-03-07:northvale-rovers-eastport-united"]
    assert ucl.stage == "round_of_16" and ucl.home_formation is None  # no events downloaded for this match
    managers = {m.manager_id: m for m in rows["managers"]}
    assert managers["statsbomb:701"].name == "Dario Quenby Ferreira" and managers["statsbomb:701"].birth_date == "1963-01-26"
    lineup = {e.player_id: e for e in rows["lineups"]}
    assert (lineup["statsbomb:501"].starter, lineup["statsbomb:501"].minute_on, lineup["statsbomb:501"].minute_off) == (True, 0, None)
    assert (lineup["statsbomb:502"].minute_off, lineup["statsbomb:503"].starter, lineup["statsbomb:503"].minute_on) == (59, False, 59)
    reserve = lineup["statsbomb:504"]
    assert (reserve.starter, reserve.minute_on, reserve.minute_off, reserve.position) == (False, 0, 0, None)
    assert {p.name for p in rows["players"]} >= {"Alvin Marlowe", "Dov Pellin"}
    assert {c.name for c in rows["clubs"]} == {"Northvale Rovers FC", "Eastport United"}
    assert next(c for c in rows["clubs"] if c.team_id == "statsbomb:10").stadium == "Quarry Park"


def test_statsbomb_raw_iterators(tmp_path):
    cfg = _sb_cfg(tmp_path)
    events = list(statsbomb.iter_events(cfg, 2, 27))
    assert len(events) == 9 and {e["match_id"] for e in events} == {1001}
    assert events[3]["shot"]["statsbomb_xg"] == 0.3  # raw dict untouched apart from match_id
    assert list(statsbomb.iter_events(cfg, 11, 90)) == []  # nothing downloaded
    frames = list(statsbomb.iter_three_sixty(cfg, 1001))
    assert frames[0]["event_uuid"] == "s-1" and "freeze_frame" in frames[0]
    assert list(statsbomb.iter_three_sixty(cfg, 1002)) == []


def test_statsbomb_fetch_skips_uncovered_leagues(tmp_path):
    assert statsbomb.fetch(_cfg(tmp_path), [], ["2015-16"]) == 0
