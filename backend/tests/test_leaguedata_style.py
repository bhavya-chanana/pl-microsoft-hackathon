"""Team style, player profiles and the table build, on small fictional fixtures."""

from __future__ import annotations

import sys
import types

import pytest

from matchlens.leaguedata import build as build_mod
from matchlens.leaguedata import players as players_mod
from matchlens.leaguedata import style as style_mod
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.contracts import TeamStyle
from matchlens.leaguedata.schema import (
    TABLES,
    Club,
    League,
    LineupEntry,
    Match,
    Player,
    PlayerMatch,
    PlayerValuation,
    Shot,
    TeamMatchStats,
)

EPL, SEASON = League.EPL, "2023-24"


def mkey(date: str, home: str, away: str) -> str:
    return f"EPL:{SEASON}:{date}:{home}-{away}"


def mk_match(date, home, away, hg=1, ag=0, **kw) -> Match:
    return Match(match_key=mkey(date, home, away), league=EPL, season=SEASON, date=date,
                 home_id=home, away_id=away, home_goals=hg, away_goals=ag, **kw)


def mk_stats(match: Match, team: str, **kw) -> TeamMatchStats:
    return TeamMatchStats(match_key=match.match_key, team_id=team, is_home=team == match.home_id,
                          goals=0, **kw)


_shot_n = 0


def mk_shot(match: Match, team: str, minute: int, xg: float, *, x=94.0, y=34.0, situation="open_play",
            body="right_foot", action=None, before=(0, 0), result="saved") -> Shot:
    global _shot_n
    _shot_n += 1
    return Shot(shot_id=f"s{_shot_n}", match_key=match.match_key, team_id=team, player_id=f"p-{team}",
                assist_player_id=None, minute=minute, x=x, y=y, xg=xg, result=result, situation=situation,
                body_part=body, last_action=action, home_score_before=before[0], away_score_before=before[1])


def metric(style: TeamStyle, key: str):
    m = style.metric(key)
    assert m is not None, key
    return m


# ------------------------------------------------------------------ team style


def test_ppda_deep_share_directness_on_known_numbers():
    m1 = mk_match("2023-08-12", "a", "b")
    m2 = mk_match("2023-08-19", "c", "a")
    stats = [
        mk_stats(m1, "a", ppda=8.0, ppda_allowed=12.0, deep=10, deep_allowed=10, xg=1.5, npxg=1.5, xg_chain=4.0, xg_buildup=1.0),
        mk_stats(m1, "b", ppda=12.0, ppda_allowed=8.0, deep=10, deep_allowed=10, xg=0.5),
        mk_stats(m2, "a", ppda=10.0, ppda_allowed=14.0, deep=20, deep_allowed=20, xg=2.5, npxg=2.0, xg_chain=6.0, xg_buildup=3.0),
        mk_stats(m2, "c", ppda=14.0, ppda_allowed=10.0, deep=20, deep_allowed=20, xg=1.0),
    ]
    st = style_mod.team_style("a", EPL, SEASON, [m1, m2], stats)
    assert st.matches == 2
    assert metric(st, "ppda").value == pytest.approx(9.0)
    assert metric(st, "ppda_allowed").value == pytest.approx(13.0)
    assert metric(st, "deep_share").value == pytest.approx(0.5)
    assert metric(st, "deep_share").is_proxy and metric(st, "directness").is_proxy
    # directness = 1 - sum(buildup)/sum(chain) = 1 - 4/10
    assert metric(st, "directness").value == pytest.approx(0.6)
    assert metric(st, "xg_for90").value == pytest.approx(2.0)
    assert metric(st, "xg_against90").value == pytest.approx(0.75)
    assert metric(st, "npxg_for90").value == pytest.approx(1.75)
    assert st.metric("possession") is None  # omitted when the data has none
    assert tuple(m.key for m in st.metrics if m.key != "possession") == tuple(
        k for k in style_mod.STYLE_KEYS if k != "possession")
    assert len(st.vector(style_mod.STYLE_KEYS)) == len(style_mod.STYLE_KEYS)


def test_situation_shares_sum_to_one_and_shot_profile():
    m = mk_match("2023-08-12", "a", "b")
    shots = [
        mk_shot(m, "a", 5, 0.2, situation="open_play", x=95, action="Cross", body="head"),
        mk_shot(m, "a", 10, 0.1, situation="corner", x=80, y=20),
        mk_shot(m, "a", 20, 0.3, situation="set_piece", action="Throughball"),
        mk_shot(m, "a", 30, 0.1, situation="direct_free_kick", x=78),
        mk_shot(m, "a", 40, 0.76, situation="penalty", x=94),
        mk_shot(m, "a", 50, 0.4, situation="open_play", action="TakeOn"),
    ]
    st = style_mod.team_style("a", EPL, SEASON, [m], [], shots)
    assert sum(metric(st, f"shot_share_{s}").value for s in style_mod.SITUATIONS) == pytest.approx(1.0)
    assert sum(metric(st, f"xg_share_{s}").value for s in style_mod.SITUATIONS) == pytest.approx(1.0)
    assert metric(st, "shot_share_open_play").value == pytest.approx(2 / 6)
    assert metric(st, "xg_share_penalty").value == pytest.approx(0.76 / 1.86)
    # non-penalty profile: 5 shots, 2 outside the box (x=80,y=20 / x=78,y=34)
    assert metric(st, "box_shot_share").value == pytest.approx(3 / 5)
    assert metric(st, "header_share").value == pytest.approx(1 / 5)
    assert metric(st, "xg_per_shot").value == pytest.approx(1.1 / 5)
    assert metric(st, "create_cross").value == pytest.approx(1 / 5)
    assert metric(st, "create_through_ball").value == pytest.approx(1 / 5)
    cp = metric(st, "counter_proxy")
    assert cp.is_proxy and cp.value == pytest.approx(2 / 5)  # no fast_break in the data
    # xG by 15-minute bin: 0-15: 0.3, 15-30: 0.3, 30-45: 0.1+0.76, 45-60: 0.4
    assert metric(st, "xg_0_15").value == pytest.approx(0.3)
    assert metric(st, "xg_45_60").value == pytest.approx(0.4)


def test_counter_proxy_uses_fast_break_when_present():
    m = mk_match("2023-08-12", "a", "b")
    shots = [mk_shot(m, "a", 5, 0.2, situation="fast_break"), mk_shot(m, "a", 9, 0.2, action="Throughball")]
    cp = metric(style_mod.team_style("a", EPL, SEASON, [m], [], shots), "counter_proxy")
    assert cp.value == pytest.approx(0.5)


def test_game_state_split_and_sit_back_index():
    m = mk_match("2023-08-12", "a", "b", hg=1, ag=0)
    shots = [
        mk_shot(m, "a", 10, 0.2, before=(0, 0)),
        mk_shot(m, "a", 45, 0.3, before=(0, 0), result="goal"),  # a take the lead here
        mk_shot(m, "a", 50, 0.1, before=(1, 0)),
        mk_shot(m, "b", 60, 0.4, before=(1, 0)),
    ]
    st = style_mod.team_style("a", EPL, SEASON, [m], [], shots)
    assert metric(st, "xg90_level").value == pytest.approx(0.5 / 45 * 90)
    assert metric(st, "shots90_level").value == pytest.approx(2 / 45 * 90)
    assert metric(st, "xg90_leading").value == pytest.approx(0.1 / 45 * 90)
    assert metric(st, "shots90_leading").value == pytest.approx(2.0)
    assert metric(st, "xg90_trailing").value is None  # never trailed
    # level share 0.5/0.5 = 1.0, leading share 0.1/(0.1+0.4) = 0.2
    assert metric(st, "sit_back_index").value == pytest.approx(0.8)

    # the opponent trailed for the second half
    sb = style_mod.team_style("b", EPL, SEASON, [m], [], shots)
    assert metric(sb, "xg90_trailing").value == pytest.approx(0.4 / 45 * 90)
    assert metric(sb, "sit_back_index").value is None  # never led


def _season_of(n: int):
    """Team a plays n matches (alternating home/away) against b, c, with rising numbers."""
    matches, stats, shots = [], [], []
    for i in range(n):
        date = f"2023-09-{i + 1:02d}"
        opp = "b" if i % 2 == 0 else "c"
        m = mk_match(date, "a", opp) if i % 2 == 0 else mk_match(date, opp, "a")
        matches.append(m)
        stats.append(mk_stats(m, "a", ppda=10.0 + i, xg=float(i)))
        stats.append(mk_stats(m, opp, ppda=20.0, xg=0.5))
        shots.append(mk_shot(m, "a", 20, 0.1 * (i + 1)))
    return matches, stats, shots


def test_as_of_match_uses_only_earlier_matches():
    matches, stats, shots = _season_of(9)
    ref = matches[7]
    full = style_mod.team_style("a", EPL, SEASON, matches, stats, shots, as_of_match=ref.match_key)
    assert full.as_of_match == ref.match_key
    assert full.matches == 6  # rolling window of the last 6 of the 7 earlier matches

    # same answer when the reference and later matches are not even in the data -> no leakage
    earlier = [m for m in matches if m.date < ref.date]
    keys = {m.match_key for m in earlier}
    only_before = style_mod.team_style(
        "a", EPL, SEASON, earlier, [s for s in stats if s.match_key in keys],
        [s for s in shots if s.match_key in keys], as_of_match=ref.match_key)
    assert [(m.key, m.value, m.pct, m.z) for m in full.metrics] == [
        (m.key, m.value, m.pct, m.z) for m in only_before.metrics]

    # recency weights: ppda of matches 1..6 (10+i) with weights 0.8**age
    ws = [0.8 ** (6 - j) for j in range(1, 7)]
    expected = sum(w * (10.0 + i) for w, i in zip(ws, range(1, 7), strict=True)) / sum(ws)
    assert metric(full, "ppda").value == pytest.approx(expected)

    # the first match has nothing before it
    first = style_mod.team_style("a", EPL, SEASON, matches, stats, shots, as_of_match=matches[0].match_key)
    assert first.matches == 0 and metric(first, "ppda").value is None
    # season-long style does include everything
    assert style_mod.team_style("a", EPL, SEASON, matches, stats, shots).matches == 9


def test_percentiles_and_z_within_league_season():
    ms, stats = [], []
    for i, (home, away) in enumerate((("a", "b"), ("b", "c"), ("c", "a"))):
        m = mk_match(f"2023-08-{12 + i * 7}", home, away)
        ms.append(m)
    ppda = {"a": 6.0, "b": 8.0, "c": 10.0}
    for m in ms:
        for t in (m.home_id, m.away_id):
            stats.append(mk_stats(m, t, ppda=ppda[t]))
    # a different league-season must not influence the first
    other = Match(match_key="LA_LIGA:2023-24:2023-08-12:x-y", league=League.LA_LIGA, season=SEASON,
                  date="2023-08-12", home_id="x", away_id="y", home_goals=0, away_goals=0)
    stats += [TeamMatchStats(match_key=other.match_key, team_id=t, is_home=t == "x", goals=0, ppda=50.0)
              for t in ("x", "y")]
    table = style_mod.league_styles(EPL, SEASON, [*ms, other], stats)
    assert set(table) == {"a", "b", "c"}
    pa, pb, pc = (metric(table[t], "ppda") for t in "abc")
    assert (pa.pct, pb.pct, pc.pct) == pytest.approx((100 / 6, 50.0, 500 / 6))
    assert pb.z == pytest.approx(0.0)
    assert pa.z == pytest.approx(-pc.z) and pc.z == pytest.approx(1.2247449)
    vec = table["c"].vector(("ppda", "does_not_exist"))
    assert vec == pytest.approx([1.2247449, 0.0])


def test_formations_and_manager():
    m1 = mk_match("2023-08-12", "a", "b", home_formation="4-3-3", home_manager_id="m1")
    m2 = mk_match("2023-08-19", "c", "a", away_formation="4-3-3", away_manager_id="m1")
    m3 = mk_match("2023-08-26", "a", "d", home_formation="3-5-2", home_manager_id="m2")
    st = style_mod.team_style("a", EPL, SEASON, [m1, m2, m3])
    assert {(f.formation, round(f.share, 3)) for f in st.formations} == {("4-3-3", 0.667), ("3-5-2", 0.333)}
    assert st.manager_id == "m2"


# --------------------------------------------------------------------- players


def _player_world():
    matches = [mk_match(f"2023-09-{i + 1:02d}", "a", "b") for i in range(10)]
    pms, shots, lineups, players, vals = [], [], [], [], []
    peers = [f"peer{i}" for i in range(10)]
    for i, pid in enumerate(peers):
        players.append(Player(player_id=pid, name=f"Peer {i}", birth_date="2000-01-01"))
        for m in matches:
            pms.append(PlayerMatch(match_key=m.match_key, player_id=pid, team_id="a", minutes=90,
                                   shots=1, key_passes=1, xg=0.02 * i, xa=0.01 * i,
                                   xg_chain=0.1, xg_buildup=0.05))
    # the striker: all shots in the box, many of them headers, one penalty
    players.append(Player(player_id="st", name="Fictional Striker", birth_date="1998-02-01", height_cm=188,
                          foot="right", main_position="Forward"))
    for j, m in enumerate(matches):
        pms.append(PlayerMatch(match_key=m.match_key, player_id="st", team_id="a", minutes=90, shots=2,
                               key_passes=0, xg=0.9, xa=0.0, xg_chain=0.5, xg_buildup=0.05))
        lineups.append(LineupEntry(match_key=m.match_key, team_id="a", player_id="st", starter=True,
                                   position="Left Winger" if j == 0 else "Centre-Forward"))
        for k in range(2):
            shots.append(Shot(shot_id=f"st{j}{k}", match_key=m.match_key, team_id="a", player_id="st",
                              assist_player_id=None, minute=20 + k, x=96.0, y=34.0, xg=0.45, result="saved",
                              situation="open_play", body_part="head" if k == 0 else "right_foot",
                              last_action=None, home_score_before=0, away_score_before=0))
    shots.append(Shot(shot_id="pen", match_key=matches[0].match_key, team_id="a", player_id="st",
                      assist_player_id=None, minute=70, x=94.0, y=34.0, xg=0.76, result="goal",
                      situation="penalty", body_part="right_foot", last_action=None,
                      home_score_before=0, away_score_before=0))
    # the creator
    players.append(Player(player_id="cr", name="Fictional Creator", birth_date="1995-06-15"))
    for m in matches:
        pms.append(PlayerMatch(match_key=m.match_key, player_id="cr", team_id="a", minutes=90, shots=0,
                               key_passes=4, xg=0.0, xa=0.5, xg_chain=0.3, xg_buildup=0.4))
    # a substitute under the minutes threshold
    players.append(Player(player_id="sub", name="Fictional Sub"))
    pms.append(PlayerMatch(match_key=matches[0].match_key, player_id="sub", team_id="a", minutes=30,
                           shots=1, key_passes=0, xg=0.2, xa=0.0))
    vals = [PlayerValuation(player_id="st", date="2023-01-10", market_value_eur=20_000_000),
            PlayerValuation(player_id="st", date="2024-01-10", market_value_eur=30_000_000),
            PlayerValuation(player_id="st", date="2024-09-10", market_value_eur=99_000_000)]
    return pms, shots, lineups, players, vals


def test_player_profile_per90_tags_age_value():
    pms, shots, lineups, players, vals = _player_world()
    builder = players_mod.ProfileBuilder(pms, shots, lineups, players, vals)
    st = builder.player_profile("st", "a", EPL, SEASON)
    per90 = {m.key: m for m in st.per90}
    assert st.minutes == 900
    # npxG from the shot table excludes the penalty: 20 shots x 0.45 over 10 matches
    assert per90["npxg90"].value == pytest.approx(9.0 / 900 * 90)
    assert per90["shots90"].value == pytest.approx(2.0)
    assert per90["npxg90"].pct == pytest.approx(100 * (11 + 0.5) / 12)  # best of 12 eligible players
    assert st.age == pytest.approx(25.5, abs=0.05)  # 1998-02-01 -> 2023-08-01
    assert st.market_value_eur == 30_000_000  # latest value on or before the season end
    assert st.positions == ["Centre-Forward", "Left Winger"]
    assert st.height_cm == 188 and st.foot == "right" and st.ids.understat is None
    prof = {m.key: m.value for m in st.shot_profile}
    assert prof["box_shot_share"] == pytest.approx(1.0)
    assert prof["header_share"] == pytest.approx(0.5)
    assert prof["np_shots"] == 20
    assert prof["shot_share_penalty"] == pytest.approx(1 / 21)
    assert set(st.role_tags) == {"poacher", "aerial threat"}

    cr = builder.player_profile("cr", "a", EPL, SEASON)
    assert "creator" in cr.role_tags and "poacher" not in cr.role_tags
    assert {m.key: m.value for m in cr.per90}["xa90"] == pytest.approx(0.5)

    sub = builder.player_profile("sub", "a", EPL, SEASON)
    assert sub.minutes == 30
    assert all(m.value is None and m.pct is None for m in sub.per90)
    assert sub.role_tags == []

    # module-level function gives the same answer
    again = players_mod.player_profile("st", "a", EPL, SEASON, pms, shots, lineups, players, vals)
    assert again == st


def test_long_range_and_set_piece_tags():
    m = mk_match("2023-09-01", "a", "b")
    shots = [
        Shot(shot_id=f"l{i}", match_key=m.match_key, team_id="a", player_id="lr", assist_player_id=None,
             minute=10 + i, x=80.0, y=30.0, xg=0.04, result="missed",
             situation="direct_free_kick" if i < 3 else "open_play", body_part="left_foot",
             last_action=None, home_score_before=0, away_score_before=0)
        for i in range(12)
    ]
    pm = [PlayerMatch(match_key=m.match_key, player_id="lr", team_id="a", minutes=90, shots=12)]
    prof = players_mod.player_profile("lr", "a", EPL, SEASON, pm, shots, [], [], [])
    assert prof.name == "lr" and prof.age is None
    assert set(prof.role_tags) == {"long-range shooter", "set-piece taker"}


# ----------------------------------------------------------------------- build


def _fake_source(name: str, clubs, matches, **tables):
    mod = types.ModuleType(f"fake_leaguedata_{name}")
    data = {"clubs": clubs, "matches": matches, **tables}
    mod.normalize = lambda cfg: data  # type: ignore[attr-defined]
    sys.modules[mod.__name__] = mod
    return mod.__name__


@pytest.fixture
def fake_cfg(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    cfg = Config(root=tmp_path)

    us_clubs = [Club(team_id="understat:1", name="Alpha FC"), Club(team_id="understat:2", name="Beta United"),
                Club(team_id="understat:3", name="Gamma Town")]
    fd_clubs = [Club(team_id="football_data:Alpha FC", name="Alpha FC"),
                Club(team_id="football_data:Beta United", name="Beta United"),
                Club(team_id="football_data:Gamma Town", name="Gamma Town")]
    us_m1 = Match(match_key="us-1", league=EPL, season=SEASON, date="2023-08-12", home_id="understat:1",
                  away_id="understat:2", home_goals=2, away_goals=1, home_xg=1.8, away_xg=0.7)
    us_m2 = Match(match_key="us-2", league=EPL, season=SEASON, date="2023-08-19", home_id="understat:3",
                  away_id="understat:1", home_goals=0, away_goals=0, home_xg=0.4, away_xg=1.1)
    fd_m1 = Match(match_key="fd-1", league=EPL, season=SEASON, date="2023-08-12",
                  home_id="football_data:Alpha FC", away_id="football_data:Beta United",
                  home_goals=2, away_goals=0, home_goals_ht=1, away_goals_ht=0, p_home=0.5, p_draw=0.3,
                  p_away=0.2, referee="A. Fictional")
    # same fixture as us_m2 but dated a day later (local vs UTC date)
    fd_m2 = Match(match_key="fd-2", league=EPL, season=SEASON, date="2023-08-20",
                  home_id="football_data:Gamma Town", away_id="football_data:Alpha FC",
                  home_goals=0, away_goals=0)
    us_shots = [
        Shot(shot_id="u1", match_key="us-1", team_id="understat:1", player_id="understat:10",
             assist_player_id=None, minute=12, x=95.0, y=34.0, xg=0.5, result="goal", situation="open_play",
             body_part="right_foot", last_action=None, home_score_before=0, away_score_before=0),
    ]
    us_stats = [TeamMatchStats(match_key="us-1", team_id="understat:1", is_home=True, goals=2, xg=1.8, ppda=9.0),
                TeamMatchStats(match_key="us-1", team_id="understat:2", is_home=False, goals=1, xg=0.7, ppda=12.0)]
    fd_stats = [TeamMatchStats(match_key="fd-1", team_id="football_data:Alpha FC", is_home=True, goals=2,
                               fouls=10, yellows=2, reds=0)]
    us_players = [Player(player_id="understat:10", name="Fictional Nine")]

    us = _fake_source("understat", us_clubs, [us_m1, us_m2], shots=us_shots, team_match_stats=us_stats,
                      players=us_players)
    fd = _fake_source("football_data", fd_clubs, [fd_m1, fd_m2], team_match_stats=fd_stats)
    broken = types.ModuleType("fake_leaguedata_broken")

    def boom(cfg):
        raise RuntimeError("no raw files")

    broken.normalize = boom  # type: ignore[attr-defined]
    sys.modules[broken.__name__] = broken
    monkeypatch.setattr(build_mod, "SOURCES", {"understat": us, "football_data": fd, "clubelo": broken.__name__})
    return cfg


def test_build_writes_parquet_and_round_trips(fake_cfg):
    counts = build_mod.build(fake_cfg)
    assert counts["matches"] == 2  # fd-2 merged into us-2 despite the one-day date difference
    assert counts["clubs"] == 3 and counts["shots"] == 1 and counts["team_match_stats"] == 2
    assert (fake_cfg.tables_dir / "matches.parquet").exists()
    assert not (fake_cfg.tables_dir / "lineups.parquet").exists()  # empty tables are not written

    matches = build_mod.load_table(fake_cfg, "matches")
    assert len(matches) == 2
    m1 = next(m for m in matches if m["date"] == "2023-08-12")
    assert m1["match_key"].startswith("EPL:2023-24:2023-08-12:")
    assert (m1["home_goals"], m1["away_goals"]) == (2, 0)  # football-data wins the result
    assert m1["home_xg"] == pytest.approx(1.8) and m1["away_xg"] == pytest.approx(0.7)  # Understat xG kept
    assert m1["p_home"] == pytest.approx(0.5) and m1["referee"] == "A. Fictional"
    assert m1["home_goals_ht"] == 1
    assert isinstance(m1["source_ids"], dict)

    # children follow the canonical key and the canonical club / player ids
    shot = build_mod.rows(fake_cfg, "shots", TABLES["shots"])[0]
    assert shot.match_key == m1["match_key"]
    assert shot.team_id == m1["home_id"] and not shot.team_id.startswith("understat:")
    assert not shot.player_id.startswith("understat:") or shot.player_id == "understat:10"
    stats = build_mod.rows(fake_cfg, "team_match_stats")
    home = next(s for s in stats if s.team_id == m1["home_id"] and s.match_key == m1["match_key"])
    assert home.ppda == 9.0 and home.fouls == 10 and home.xg == pytest.approx(1.8)  # merged field by field

    models = build_mod.rows(fake_cfg, "matches", Match)
    assert all(isinstance(m, Match) for m in models)


def test_build_records_score_conflicts(fake_cfg):
    report = build_mod.build_report(fake_cfg)
    assert report.counts["conflicts"] == 1
    c = report.conflicts[0]
    assert c["field"] == "score" and c["chosen"] == "2-0"
    assert c["values"] == {"football_data": "2-0", "understat": "2-1"}
    assert build_mod.conflicts(fake_cfg) == report.conflicts
    assert "clubelo" in report.skipped  # the source whose normalize() raised


def test_build_sources_filter(fake_cfg):
    counts = build_mod.build(fake_cfg, ["understat"])
    assert counts["matches"] == 2 and counts["conflicts"] == 0
    m = build_mod.load_table(fake_cfg, "matches")
    assert all(row["p_home"] is None for row in m)


def test_build_fills_elo_from_clubelo(fake_cfg, monkeypatch):
    clubs = [Club(team_id="clubelo:Alpha FC", name="Alpha FC"), Club(team_id="clubelo:Beta United", name="Beta United")]
    mod = types.ModuleType("fake_leaguedata_clubelo")
    mod.normalize = lambda cfg: {"clubs": clubs}  # type: ignore[attr-defined]
    mod.elo_lookup = lambda cfg: {("Alpha FC", "2023-08-07"): 1800.0, ("Beta United", "2023-08-07"): 1700.0}  # type: ignore[attr-defined]
    mod.elo_at = lambda lk, club, day: lk.get((club, "2023-08-07")) if day >= "2023-08-07" else None  # type: ignore[attr-defined]
    sys.modules[mod.__name__] = mod
    monkeypatch.setitem(build_mod.SOURCES, "clubelo", mod.__name__)
    build_mod.build(fake_cfg)
    m1 = next(m for m in build_mod.load_table(fake_cfg, "matches") if m["date"] == "2023-08-12")
    assert (m1["home_elo"], m1["away_elo"]) == (1800.0, 1700.0)
    m2 = next(m for m in build_mod.load_table(fake_cfg, "matches") if m["date"] != "2023-08-12")
    assert m2["home_elo"] is None and m2["away_elo"] == 1800.0
