"""Curated dataset on a tiny fictional tables folder."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("duckdb")

from matchlens.leaguedata import build as build_mod  # noqa: E402
from matchlens.leaguedata import curate as cur  # noqa: E402
from matchlens.leaguedata.config import Config  # noqa: E402
from matchlens.leaguedata.schema import (  # noqa: E402
    Club,
    IdLink,
    League,
    LineupEntry,
    Match,
    MatchEvent,
    Player,
    Shot,
    SourceIds,
    TeamMatchStats,
    TextDoc,
)

SEASON = "2023-24"


def key(date: str, home: str, away: str, league: str = "EPL") -> str:
    return f"{league}:{SEASON}:{date}:{home}-{away}"


def match(date, home, away, hg=1, ag=0, league=League.EPL, **kw) -> Match:
    return Match(match_key=key(date, home, away, league.value), league=league, season=SEASON, date=date,
                 home_id=home, away_id=away, home_goals=hg, away_goals=ag, **kw)


def shot(match_key: str, sid: str, **kw) -> Shot:
    base = dict(shot_id=sid, match_key=match_key, team_id="aldermoor", player_id="p1", assist_player_id=None,
                minute=10, x=94.0, y=34.0, xg=0.2, result="saved", situation="open_play", body_part="right_foot",
                last_action=None, home_score_before=0, away_score_before=0)
    return Shot(**{**base, **kw})


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config(root=tmp_path)
    m1 = match("2023-08-12", "aldermoor", "brightholm", home_xg=1.4, away_xg=0.6, home_formation="4-3-3",
               away_formation="4-4-2", p_home=0.5, p_draw=0.3, p_away=0.2, home_elo=1700.0, away_elo=1650.0,
               source_ids=SourceIds(understat="u1", transfermarkt="t1"))
    m2 = match("2023-08-19", "carrowby", "aldermoor", 2, 2, source_ids=SourceIds(football_data="f2"))
    m3 = match("2023-08-26", "brightholm", "carrowby", source_ids=SourceIds(statsbomb="sb3", wikidata="Q3"))
    bad = Match.model_construct(  # an invalid row that got into the table (negative goals, backwards key)
        match_key=key("2023-09-02", "aldermoor", "carrowby"), league=League.EPL, season=SEASON, date="2023-09-02",
        home_id="aldermoor", away_id="carrowby", home_goals=-1, away_goals=0, source_ids=SourceIds())
    dup = m2.model_copy(update={"referee": "A. Fictional"})  # same key, one more field set
    tables = {
        "matches": [m1, m2, dup, m3, bad],
        "lineups": [
            LineupEntry(match_key=m1.match_key, team_id="aldermoor", player_id="p1", starter=True, position="GK"),
            LineupEntry(match_key="EPL:2023-24:2000-01-01:ghost-town", team_id="ghost", player_id="p9",
                        starter=True, position="GK"),  # orphan
        ],
        "shots": [shot(m1.match_key, "understat:1", xg=0.31),
                  shot(m3.match_key, "statsbomb:7", xg=0.1),
                  shot(m1.match_key, "understat:2", x=140.0)],  # off the pitch
        "match_events": [MatchEvent(event_id="transfermarkt:1", match_key=m1.match_key, team_id="aldermoor",
                                    minute=30, kind="goal", player_id="p1")],
        "team_match_stats": [TeamMatchStats(match_key=m1.match_key, team_id="aldermoor", is_home=True, goals=1,
                                            xg=1.4, ppda=9.0, possession=0.55)],
        "players": [Player(player_id="p1", name="Test Player One", main_position="Forward",
                           source_ids=SourceIds(wikidata="Q11")),
                    Player(player_id="p2", name="Test Player Two")],
        "clubs": [Club(team_id="aldermoor", name="Aldermoor FC", country="Fictland", stadium="Alder Park",
                       source_ids=SourceIds(wikidata="Q1", understat="55")),
                  Club(team_id="brightholm", name="Brightholm", source_ids=SourceIds(understat="56"))],
        "text_docs": [TextDoc(entity_kind="club", entity_id="aldermoor", title="Aldermoor FC", text="A club.",
                              url="https://example.test/wiki/Aldermoor", licence="CC BY-SA 4.0",
                              retrieved="2026-01-01")],
        "id_links": [
            IdLink(entity_kind="club", entity_id="aldermoor", source="understat", source_id="55",
                   confidence=1.0, method="exact_id"),
            IdLink(entity_kind="club", entity_id="brightholm", source="understat", source_id="56",
                   confidence=0.7, method="fuzzy"),
            IdLink(entity_kind="player", entity_id="p1", source="wikidata", source_id="Q11",
                   confidence=0.9, method="name_birth"),
        ],
    }
    build_mod._write_tables(c, tables)  # noqa: SLF001
    build_mod._write_conflicts(c, [{"match_key": m2.match_key, "field": "score",  # noqa: SLF001
                                    "values": {"football_data": "2-2", "understat": "1-2"}, "chosen": "2-2"}])
    return c


def keys(_c: Config):
    return (key("2023-08-12", "aldermoor", "brightholm"), key("2023-08-19", "carrowby", "aldermoor"),
            key("2023-08-26", "brightholm", "carrowby"))


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_curate_validates_dedupes_and_rejects(cfg):
    report = cur.curate(cfg)
    assert report.version == 1 and report.path == cfg.curated_dir / "v1"
    assert report.counts["matches"] == 3  # bad dropped, duplicate merged
    assert report.duplicates["matches"] == 1
    assert report.rejected["matches"] == 1
    rejects = jsonl(report.path / "rejects" / "matches.jsonl")
    assert len(rejects) == 1 and "negative goals" in rejects[0]["reason"]
    # orphan lineup and off-pitch shot are rejected with reasons
    assert [r["reason"] for r in jsonl(report.path / "rejects" / "lineups.jsonl")] == ["match_key not in matches"]
    assert jsonl(report.path / "rejects" / "shots.jsonl")[0]["reason"] == "shot outside the pitch"
    assert report.counts["lineups"] == 1 and report.counts["shots"] == 2
    # the duplicate keeps the row with the extra field
    row = {m.match_key: m for m in cur.read_curated(cfg, "matches")}[keys(cfg)[1]]
    assert row.referee == "A. Fictional"


def test_quality_flags_and_conflict(cfg):
    cur.curate(cfg)
    m1, m2, m3 = (next(m for m in cur.read_curated(cfg, "matches") if m.match_key == k) for k in keys(cfg))
    assert (m1.has_xg, m1.has_lineup, m1.has_formation, m1.has_events, m1.has_shots, m1.has_odds, m1.has_elo) \
        == (True,) * 7
    assert m1.sources_present == 2 and not m1.source_conflict
    assert not any((m2.has_xg, m2.has_lineup, m2.has_formation, m2.has_events, m2.has_odds, m2.has_elo))
    assert m2.source_conflict and m2.sources_present == 1
    assert m3.has_shots and not m3.has_xg and m3.sources_present == 2


def test_identity_report(cfg):
    report = cur.curate(cfg)
    ident = json.loads((report.path / "identity_report.json").read_text())
    club = ident["kinds"]["club"]
    assert club["entities"] == 2 and club["sources"]["understat"]["linked_pct"] == 100.0
    assert club["sources"]["understat"]["mean_confidence"] == pytest.approx(0.85)
    assert club["sources"]["understat"]["fuzzy_below_0_8"] == 1
    assert ident["fuzzy_review"][0]["entity_id"] == "brightholm"
    assert ident["kinds"]["player"]["sources"]["wikidata"]["linked_pct"] == 50.0
    assert (report.path / "id_links.parquet").exists()


def test_coverage_counts_and_expected(cfg):
    report = cur.curate(cfg)
    cov = json.loads((report.path / "coverage.json").read_text())["cells"][f"EPL:{SEASON}"]
    assert cov["matches"] == 3 and cov["expected_matches"] == 380 and cov["completeness"] == pytest.approx(3 / 380, abs=1e-4)
    assert cov["sources"] == {"understat": 1, "transfermarkt": 1, "wikidata": 1, "statsbomb": 1, "football_data": 1}
    assert cov["missing_rate"]["xg"] == pytest.approx(2 / 3, abs=1e-4)
    assert cov["missing_rate"]["lineups"] == pytest.approx(2 / 3, abs=1e-4)
    assert cov["missing_rate"]["possession"] == 1.0  # only one team row has possession
    assert cov["rows"]["shots"] == 2 and cov["shots_by_source"] == {"statsbomb": 1, "understat": 1}
    assert cov["source_conflicts"] == 1


def test_expected_matches_rules():
    assert cur.expected_matches("EPL", "2023-24") == (380, True)
    assert cur.expected_matches("BUNDESLIGA", "2021-22") == (306, True)
    assert cur.expected_matches("LIGUE_1", "2019-20") == (279, True)
    assert cur.expected_matches("LIGUE_1", "2021-22") == (380, True)
    assert cur.expected_matches("LIGUE_1", "2024-25") == (306, True)
    assert cur.expected_matches("UCL", "2024-25")[1] is False


def test_splits_are_deterministic_across_versions(cfg):
    r1 = cur.curate(cfg)
    r2 = cur.curate(cfg)
    assert (r1.version, r2.version) == (1, 2)
    s1 = json.loads((r1.path / "splits.json").read_text())["splits"]
    s2 = json.loads((r2.path / "splits.json").read_text())["splits"]
    assert s1 == s2 and set(s1) == set(keys(cfg))
    assert set(s1.values()) <= {"train", "val", "test"}
    assert all(cur.split_of(k) == v for k, v in s1.items())
    # roughly 80/10/10 over many keys
    sample = [cur.split_of(f"EPL:{SEASON}:2023-01-01:t{i}-u{i}") for i in range(2000)]
    assert 0.76 < sample.count("train") / 2000 < 0.84
    assert 0.07 < sample.count("val") / 2000 < 0.13


def test_manifest_hashes_and_row_counts(cfg):
    report = cur.curate(cfg)
    manifest = json.loads((report.path / "manifest.json").read_text())
    by_path = {f["path"]: f for f in manifest["files"]}
    assert {"DATASET_CARD.md", "coverage.json", "splits.json", "identity_report.json",
            "share/SHARE_LICENSE.md", "tables/matches.parquet"} <= set(by_path)
    assert "manifest.json" not in by_path
    for rel, entry in by_path.items():
        assert hashlib.sha256((report.path / rel).read_bytes()).hexdigest() == entry["sha256"]
    assert by_path["tables/matches.parquet"]["rows"] == 3
    assert by_path["rejects/matches.jsonl"]["rows"] == 1


def test_share_excludes_understat_and_untraceable(cfg):
    import duckdb

    report = cur.curate(cfg)
    share = report.path / "share"
    assert (share / "SHARE_LICENSE.md").exists()
    tables = share / "tables"
    cols = [d[0] for d in duckdb.connect().execute(f"select * from read_parquet('{tables / 'matches.parquet'}')").description]
    assert not {"home_xg", "p_home", "home_elo", "home_formation"} & set(cols)
    matches = duckdb.sql(f"select match_key, statsbomb_id from read_parquet('{tables / 'matches.parquet'}')").fetchall()
    assert matches == [(keys(cfg)[2], "sb3")]  # only the StatsBomb-identified match
    shots = duckdb.sql(f"select shot_id from read_parquet('{tables / 'shots.parquet'}')").fetchall()
    assert shots == [("statsbomb:7",)]
    clubs = duckdb.sql(f"select * from read_parquet('{tables / 'clubs.parquet'}')").fetchall()
    assert len(clubs) == 1 and "Alder Park" not in str(clubs)  # Wikidata club, stadium column not shared
    links = duckdb.sql(f"select source from read_parquet('{tables / 'id_links.parquet'}')").fetchall()
    assert links == [("wikidata",)]
    for name in ("lineups", "team_match_stats", "player_matches", "player_valuations"):
        assert not (tables / f"{name}.parquet").exists()
    assert "lineups" in (share / "SHARE_LICENSE.md").read_text()
    assert report.share_counts["text_docs"] == 1


def test_latest_pointer_and_read_curated(cfg):
    with pytest.raises(FileNotFoundError):
        cur.latest(cfg)
    cur.curate(cfg)
    r2 = cur.curate(cfg)
    assert (cfg.curated_dir / "LATEST").read_text().strip() == "2"
    assert cur.latest(cfg) == r2.path
    assert len(cur.read_curated(cfg, "matches")) == 3
    assert len(cur.read_curated(cfg, "matches", version=1)) == 3
    assert cur.read_curated(cfg, "honours") == []  # empty table
    players = cur.read_curated(cfg, "players", model=Player)
    assert {p.player_id for p in players} == {"p1", "p2"}
    assert cur.read_curated(cfg, "matches")[0].home_id  # CuratedMatch rows carry the flags
    with pytest.raises(FileExistsError):
        cur.curate(cfg, version=1)
    r9 = cur.curate(cfg, version=9)
    assert r9.path.name == "v9" and cur.latest(cfg) == r9.path
    assert cur.curate(cfg).version == 10


def test_dataset_card_and_cli(cfg, capsys):
    assert cur.main(cfg, SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "curated v1" in out and "matches" in out
    card = (cur.latest(cfg) / "DATASET_CARD.md").read_text()
    for needle in ("Understat", "CC BY-SA 4.0", "football-data", "No free tracking", "Known gaps", "EPL 2023-24"):
        assert needle.lower() in card.lower()
