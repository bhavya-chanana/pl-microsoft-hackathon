"""Understat, football-data and ClubElo adapters, the CLI, on fictional fixtures (no network)."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from matchlens.leaguedata import __main__ as cli
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import FetchError
from matchlens.leaguedata.schema import DEFAULT_LEAGUES, SEASONS, TABLES, League
from matchlens.leaguedata.sources import SOURCES, clubelo, footballdata, understat

FIXTURES = Path(__file__).parent / "fixtures" / "leaguedata"


@pytest.fixture
def cfg(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "raw")
    return Config(root=tmp_path)


# ------------------------------------------------------------------ understat


def test_understat_rows_are_valid_tables(cfg):
    rows = understat.normalize(cfg)
    assert set(rows) <= set(TABLES)
    for table, items in rows.items():
        assert all(isinstance(i, TABLES[table]) for i in items)
    assert len(rows["matches"]) == 1  # the unplayed fixture is skipped
    match = rows["matches"][0]
    assert (match.season, match.date, match.kickoff_utc) == ("2023-24", "2023-08-12", "2023-08-12T14:00:00Z")
    assert (match.home_goals, match.away_goals, match.home_xg, match.away_xg) == (2, 1, 2.1, 0.9)
    assert match.home_id == "understat:1" and match.source_ids.understat == "9001"
    assert {c.name for c in rows["clubs"]} == {"Northvale FC", "Eastbrook Town"}


def test_shot_coordinates_attack_towards_x105_for_both_teams(cfg):
    shots = {s.shot_id: s for s in understat.normalize(cfg)["shots"]}
    goal = shots["understat:5002"]  # home header, X=0.92 Y=0.50
    assert goal.x == pytest.approx(0.92 * 105) and goal.x > 90
    assert goal.y == pytest.approx(34.0)
    away = shots["understat:5004"]  # away shot: Understat is already shooter-oriented
    assert away.team_id == "understat:2" and away.x > 90
    assert all(0 <= s.x <= 105 and 0 <= s.y <= 68 for s in shots.values())


def test_understat_value_mapping(cfg):
    shots = {s.shot_id: s for s in understat.normalize(cfg)["shots"]}
    assert shots["understat:5001"].result == "missed"
    assert shots["understat:5003"].result == "saved" and shots["understat:5003"].situation == "corner"
    assert shots["understat:5003"].body_part == "left_foot" and shots["understat:5003"].last_action is None
    assert shots["understat:5002"].body_part == "head" and shots["understat:5002"].last_action == "Cross"
    assert shots["understat:5005"].result == "own_goal"
    assert shots["understat:5006"].situation == "penalty" and shots["understat:5006"].result == "goal"
    # assist names resolve through the same-team roster, unknown names stay None
    assert shots["understat:5002"].assist_player_id == "understat:13"
    assert shots["understat:5004"].assist_player_id is None


def test_score_before_each_shot_counts_goals_and_own_goals(cfg):
    shots = sorted(understat.normalize(cfg)["shots"], key=lambda s: s.minute)
    assert [(s.minute, s.home_score_before, s.away_score_before) for s in shots] == [
        (10, 0, 0), (20, 0, 0), (30, 1, 0), (55, 1, 0), (70, 2, 0), (75, 2, 1)]
    # the own goal (by an away player) counted for the home side: final 2-1 reached
    assert (shots[-1].home_score_before, shots[-1].away_score_before) == (2, 1)


def test_team_stats_ppda_and_deep(cfg):
    stats = {s.team_id: s for s in understat.normalize(cfg)["team_match_stats"]}
    home, away = stats["understat:1"], stats["understat:2"]
    assert home.is_home and not away.is_home
    assert home.ppda == pytest.approx(12.0) and home.ppda_allowed == pytest.approx(25.0)
    assert (home.deep, home.deep_allowed, home.xg, home.npxg) == (7, 3, 2.1, 1.9)
    assert away.ppda_allowed is None  # zero defensive actions: undefined, not infinite
    assert home.xg_chain == pytest.approx(2.2) and home.xg_buildup == pytest.approx(0.9)


def test_lineups_and_player_matches(cfg):
    rows = understat.normalize(cfg)
    lineup = {(e.team_id, e.player_id): e for e in rows["lineups"]}
    striker = lineup[("understat:1", "understat:12")]
    assert striker.starter and (striker.minute_on, striker.minute_off) == (0, 60)
    sub = lineup[("understat:1", "understat:14")]
    assert not sub.starter and (sub.minute_on, sub.minute_off) == (60, None)
    assert ("understat:1", "understat:15") not in lineup  # never played
    assert sum(e.starter for e in rows["lineups"] if e.team_id == "understat:1") == 3
    pm = {p.player_id: p for p in rows["player_matches"]}
    assert pm["understat:13"].assists == 1 and pm["understat:13"].xa == 0.4 and pm["understat:13"].key_passes == 2
    assert "understat:15" not in pm
    assert {p.name for p in rows["players"]} >= {"Bram Holloway", "Finn Thorley"}


def test_understat_fetch_paths_and_failures(cfg, monkeypatch, tmp_path):
    calls = []

    def fake(url, cache, config, **kw):
        calls.append((url, kw.get("headers")))
        if "getMatchData/9001" in url:
            raise FetchError("boom")
        return __import__("json").loads((FIXTURES / "understat" / "league_EPL_2023.json").read_text())

    monkeypatch.setattr(understat, "fetch_json", fake)
    empty = Config(root=tmp_path / "empty")
    assert understat.fetch(empty, [League.EPL, League.UCL], ["2023-24"], limit=1) == 1
    urls = [u for u, _ in calls]
    assert urls == ["https://understat.com/getLeagueData/EPL/2023", "https://understat.com/getMatchData/9001"]
    assert all(h["X-Requested-With"] == "XMLHttpRequest" for _, h in calls)  # UCL skipped silently


# ------------------------------------------------------------------ football-data


def test_football_data_parses_latin1_bom_and_trailing_columns(cfg):
    rows = footballdata.normalize(cfg)
    matches = {m.match_key: m for m in rows["matches"]}
    assert len(matches) == 2  # unplayed fixture and blank row dropped
    first = matches["EPL:2023-24:2023-08-12:northvale-eastbrook"]
    assert first.referee == "J Müller"
    assert (first.home_goals, first.away_goals, first.home_goals_ht, first.away_goals_ht) == (2, 1, 1, 0)
    assert first.home_id == "football_data:Northvale" and first.kickoff_utc is None
    second = next(m for m in matches.values() if m.date == "2023-08-13")  # dd/mm/yy
    assert second.referee is None


def test_closing_odds_are_devigged_to_sum_to_one(cfg):
    matches = {m.date: m for m in footballdata.normalize(cfg)["matches"]}
    pinnacle = matches["2023-08-12"]
    assert pinnacle.p_home + pinnacle.p_draw + pinnacle.p_away == pytest.approx(1.0, abs=1e-5)
    raw = [1 / 2.00, 1 / 3.60, 1 / 4.00]
    assert pinnacle.p_home == pytest.approx(raw[0] / sum(raw), abs=1e-5)
    fallback = matches["2023-08-13"]  # only the market-average columns are present
    assert fallback.p_home + fallback.p_draw + fallback.p_away == pytest.approx(1.0, abs=1e-5)
    assert fallback.p_home == pytest.approx((1 / 3.0) / (1 / 3.0 + 1 / 3.2 + 1 / 2.5), abs=1e-5)
    assert footballdata.implied_probabilities({"PSCH": "", "B365CH": "0.9"}) is None


def test_football_data_team_stats(cfg):
    stats = {(s.match_key, s.is_home): s for s in footballdata.normalize(cfg)["team_match_stats"]}
    home = stats[("EPL:2023-24:2023-08-12:northvale-eastbrook", True)]
    away = stats[("EPL:2023-24:2023-08-12:northvale-eastbrook", False)]
    assert (home.shots, home.shots_on_target, home.corners, home.fouls) == (14, 6, 7, 10)
    assert (home.yellows, home.reds, away.yellows, away.reds) == (1, 0, 2, 1)
    assert footballdata.season_code("2023-24") == "2324" and footballdata.season_label("2324") == "2023-24"


def test_football_data_fetch_rejects_html_error_pages(tmp_path, monkeypatch):
    def fake(url, cache, config, **kw):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"<html>Not found</html>")
        return b"<html>Not found</html>"

    monkeypatch.setattr(footballdata, "fetch_bytes", fake)
    c = Config(root=tmp_path)
    assert footballdata.fetch(c, [League.EPL], ["2023-24"]) == 0
    assert not footballdata.raw_path(c, "E0", "2023-24").exists()


# ------------------------------------------------------------------ clubelo


def test_clubelo_clubs_and_lookup(cfg):
    clubs = {c.name: c for c in clubelo.normalize(cfg)["clubs"]}
    assert set(clubs) == {"NorthvaleFC", "EastbrookTown", "Faraway United"}
    assert clubs["NorthvaleFC"].country == "ENG"
    table = clubelo.elo_lookup(cfg)
    assert table[("NorthvaleFC", "2023-08-14")] == 1900.5
    assert clubelo.elo_at(table, "NorthvaleFC", "2023-08-17") == 1900.5  # latest snapshot on or before
    assert clubelo.elo_at(table, "NorthvaleFC", "2023-08-21") == 1912.0
    assert clubelo.elo_at(table, "NorthvaleFC", "2023-08-01") is None
    assert clubelo.elo_at(table, "Nobody", "2023-08-21") is None


def test_season_mondays_cover_august_to_june():
    days = clubelo.season_mondays("2023-24", today=date(2030, 1, 1))
    assert days[0] == date(2023, 8, 7) and days[-1] <= date(2024, 6, 30)
    assert all(d.weekday() == 0 for d in days)
    assert clubelo.season_mondays("2023-24", today=date(2023, 9, 1))[-1] == date(2023, 8, 28)


# ------------------------------------------------------------------ CLI


def test_cli_defaults_and_overrides():
    args = cli.build_parser().parse_args(["fetch"])
    assert args.leagues == list(DEFAULT_LEAGUES) and args.seasons == list(SEASONS)
    assert args.sources == list(SOURCES) and args.limit is None and not args.refresh
    args = cli.build_parser().parse_args(
        ["fetch", "--leagues", "EPL,la_liga", "--seasons", "2023-24", "--sources", "understat,football_data",
         "--limit", "3", "--refresh"])
    assert args.leagues == [League.EPL, League.LA_LIGA] and args.seasons == ["2023-24"]
    assert args.sources == ["understat", "football_data"] and args.limit == 3 and args.refresh


@pytest.mark.parametrize("bad", [["--leagues", "MARS"], ["--seasons", "23/24"], ["--sources", "nope"]])
def test_cli_rejects_bad_values(bad):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["fetch", *bad])


def test_a_failing_source_does_not_stop_the_others(tmp_path, monkeypatch, capsys):
    seen = []

    class Good:
        @staticmethod
        def fetch(cfg, leagues, seasons, *, limit=None, refresh=False):
            seen.append((leagues, seasons, limit, refresh))
            return 4

    class Raises:
        def __init__(self, exc):
            self.exc = exc

        def fetch(self, *a, **kw):
            raise self.exc

    modules = {SOURCES["understat"]: Raises(FetchError("down")), SOURCES["football_data"]: Raises(NotImplementedError("todo")),
               SOURCES["clubelo"]: Raises(RuntimeError("bug")), SOURCES["fpl"]: Good}
    real = cli.importlib.import_module

    def fake_import(name):
        if name == SOURCES["transfermarkt"]:
            raise ImportError("missing optional dependency")
        return modules[name] if name in modules else real(name)

    monkeypatch.setattr(cli.importlib, "import_module", fake_import)
    code = cli.main(["fetch", "--data-dir", str(tmp_path), "--sources", "understat,football_data,clubelo,transfermarkt,fpl",
                     "--leagues", "EPL", "--seasons", "2023-24", "--limit", "2"])
    assert code == 1
    assert seen == [([League.EPL], ["2023-24"], 2, False)]
    assert "fpl: 4 new files" in capsys.readouterr().out


def test_missing_stage_module_is_reported(tmp_path, monkeypatch, capsys):
    def fake_import(name):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.setattr(cli.importlib, "import_module", fake_import)
    assert cli.main(["build", "--data-dir", str(tmp_path)]) == 2
    assert "not available" in capsys.readouterr().err


def test_stage_receives_config_and_args(tmp_path, monkeypatch):
    got = {}

    class Stage:
        @staticmethod
        def main(cfg, args):
            got.update(root=cfg.root, command=args.command, leagues=args.leagues)

    monkeypatch.setattr(cli.importlib, "import_module", lambda name: Stage)
    assert cli.main(["curate", "--data-dir", str(tmp_path), "--leagues", "SERIE_A"]) == 0
    assert got == {"root": tmp_path, "command": "curate", "leagues": [League.SERIE_A]}


def test_status_counts_raw_files(cfg, capsys):
    assert cli.main(["status", "--data-dir", str(cfg.root)]) == 0
    out = capsys.readouterr().out
    assert "understat" in out and "2 files" in out and "none yet" in out
