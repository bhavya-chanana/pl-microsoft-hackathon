"""Mentality, club/manager knowledge and the openfootball / Wikidata / Wikipedia adapters.

Offline: fixtures use fictional clubs and people, and nothing here touches the network.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.knowledge import (
    Knowledge,
    club_profile,
    manager_formations,
    manager_profile,
)
from matchlens.leaguedata.mentality import compute_mentality, stage_key
from matchlens.leaguedata.schema import League, Match, MatchEvent, Shot, TeamMatchStats
from matchlens.leaguedata.sources import openfootball, wikidata, wikipedia

FIXTURES = Path(__file__).parent / "fixtures" / "leaguedata"


def _cfg(tmp_path: Path, *sources: str) -> Config:
    for source in sources:
        shutil.copytree(FIXTURES / source, tmp_path / "raw" / source)
    return Config(root=tmp_path)


# ---------------------------------------------------------------- hand-built timelines

T = "t"  # the team under test
SEASON = "2023-24"


def upd(row, **changes):
    return row.model_copy(update=changes)


def match(n: int, opp: str, home: bool, own: int, against: int, *, league=League.EPL, stage=None,
          date: str | None = None) -> Match:
    h, a = (T, opp) if home else (opp, T)
    hg, ag = (own, against) if home else (against, own)
    d = date or f"2023-09-{n:02d}"
    return Match(match_key=f"{league.value}:{SEASON}:{d}:{h}-{a}", league=league, season=SEASON, date=d, stage=stage,
                 home_id=h, away_id=a, home_goals=hg, away_goals=ag)


def goal(m: Match, minute: int, scorer: str, kind: str = "goal", team: str | None = None) -> MatchEvent:
    return MatchEvent(event_id=f"{m.match_key}#{minute}{scorer}", match_key=m.match_key, team_id=team or scorer,
                      minute=minute, kind=kind, player_id=None)


def shot(m: Match, minute: int, team: str, n: int = 0) -> Shot:
    return Shot(shot_id=f"{m.match_key}/s{minute}{n}", match_key=m.match_key, team_id=team, player_id="p",
                assist_player_id=None, minute=minute, x=90.0, y=34.0, xg=0.1, result="saved", situation="open_play",
                body_part="right_foot", last_action=None, home_score_before=0, away_score_before=0)


def stat(m: Match, goals: int, xg: float, reds: int) -> TeamMatchStats:
    return TeamMatchStats(match_key=m.match_key, team_id=T, is_home=m.home_id == T, goals=goals, xg=xg, reds=reds)


@pytest.fixture(scope="module")
def season_rows():
    m1 = match(1, "a", True, 2, 1)    # 0-1 down, won 2-1 (comeback win, led, late goal)
    m2 = match(2, "b", False, 1, 1)   # led, B equalised late (lead dropped)
    m3 = match(3, "c", True, 0, 2)    # trailed, lost
    m4 = match(4, "d", False, 2, 2)   # 0-2 down, drew 2-2 (comeback draw), 90' equaliser
    m5 = match(5, "e", True, 1, 0)    # own goal, offender's id on the event
    m6 = match(6, "g", False, 1, 0)   # own goal, credited team's id on the event
    m7 = match(7, "f", True, 3, 0)    # no event data at all
    matches = [m1, m2, m3, m4, m5, m6, m7]
    events = [
        goal(m1, 10, "a"), goal(m1, 50, T), goal(m1, 80, T),
        goal(m2, 20, T), goal(m2, 88, "b"),
        goal(m3, 30, "c"), goal(m3, 60, "c"),
        goal(m4, 5, "d"), goal(m4, 15, "d"), goal(m4, 40, T), goal(m4, 90, T),
        goal(m5, 33, "e", kind="own_goal"),                 # e's player scored it for T
        goal(m6, 66, T, kind="own_goal"),                    # credited to T
    ]
    shots = [  # match 1 and 3 have shot data: 7 team shots -> 7 / (2 * 6 windows) per 15'
        shot(m1, 12, T), shot(m1, 20, T), shot(m1, 50, T), shot(m1, 80, T), shot(m1, 14, "a"),
        shot(m3, 35, T), shot(m3, 70, T), shot(m3, 85, T),
    ]
    stats = [stat(m1, 2, 1.5, 0), stat(m2, 1, 1.2, 1), stat(m3, 0, 0.8, 0), stat(m4, 2, 1.1, 0)]
    return matches, events, shots, stats


@pytest.fixture(scope="module")
def mentality(season_rows):
    matches, events, shots, stats = season_rows
    return compute_mentality(T, SEASON, matches, events, shots, stats, league=League.EPL)


def test_comebacks_and_points_from_losing(mentality):
    assert mentality.matches == 7
    # timelines for 6 matches (no data for m7); trailed in m1, m3, m4; did not lose in m1, m4
    assert mentality.comeback_rate == pytest.approx(2 / 6, abs=1e-3)
    assert mentality.points_from_losing == pytest.approx((3 + 0 + 1) / 3, abs=1e-3)


def test_lead_protection_counts_leads_that_ended_in_wins(mentality):
    # led in m1, m2, m5, m6; won m1, m5, m6 (m2 was drawn)
    assert mentality.lead_protection == pytest.approx(0.75)


def test_late_goal_shares(mentality):
    # scored: 50 80 20 40 90 33 66 -> 80' and 90' are late; conceded: 10 88 30 60 5 15 -> only 88'
    assert mentality.late_goal_share == pytest.approx(2 / 7, abs=1e-3)
    assert mentality.late_concede_share == pytest.approx(1 / 6, abs=1e-3)


def test_response_after_conceding(mentality):
    # windows: m1 (10,25] 2 shots; m3 (30,45] 1 and (60,75] 1 -> 4 shots / 45 min = 1.333 per 15'
    # baseline: 7 shots / (2 matches * 6 windows) = 0.5833 per 15'
    assert mentality.response_after_conceding == pytest.approx(1.3333 / 0.58333, abs=1e-3)


def test_big_game_ppg_uses_league_table_top_four(mentality):
    # table: T 14, C 3, D (1, gf 2), B (1, gf 1) are the top four; T took 0 + 1 + 1 from them
    assert mentality.big_game_ppg == pytest.approx(2 / 3, abs=1e-3)


def test_red_cards_and_finishing_come_from_stats(mentality):
    assert mentality.red_cards_per_match == pytest.approx(0.25)
    assert mentality.finishing_streak == pytest.approx(5 - 4.6, abs=1e-2)  # goals 5, xG 4.6
    assert mentality.knockout_record is None


def test_notes_are_computed_from_the_data(mentality):
    text = " | ".join(mentality.notes)
    assert "2 comebacks from losing positions (1 won, 1 drawn) in 3 matches" in text
    assert "largest deficit overcome without losing: 2 goals" in text
    assert "dropped points from a lead in 1" in text
    assert "2 of 7 goals scored from the 75th minute on" in text
    assert "1 red card in 4 matches" in text


def test_missing_data_gives_none_not_zero(season_rows):
    matches, *_ = season_rows
    bare = compute_mentality(T, SEASON, matches, [], [], [])
    assert bare.matches == 7
    for name in ("comeback_rate", "points_from_losing", "lead_protection", "late_goal_share",
                 "late_concede_share", "response_after_conceding", "red_cards_per_match", "finishing_streak"):
        assert getattr(bare, name) is None, name
    assert bare.big_game_ppg is not None  # results alone are enough for this one
    assert compute_mentality("nobody", SEASON, matches, [], [], []).matches == 0


def test_timeline_that_does_not_add_up_is_ignored():
    m = match(1, "a", True, 2, 0)
    wrong = [goal(m, 10, T)]  # one goal for a 2-0 result
    out = compute_mentality(T, SEASON, [m], wrong, [], [])
    assert out.comeback_rate is None and out.late_goal_share is None


def test_goal_shots_are_a_fallback_timeline():
    m = match(1, "a", True, 1, 1)
    goals = [upd(shot(m, 30, "a"), result="goal"), upd(shot(m, 80, T), result="goal")]
    out = compute_mentality(T, SEASON, [m], [], goals, [])
    assert out.comeback_rate == 1.0 and out.late_goal_share == 1.0


def test_red_cards_fall_back_to_events():
    m1, m2 = match(1, "a", True, 0, 0), match(2, "b", True, 0, 0)
    cards = [MatchEvent(event_id="r1", match_key=m1.match_key, team_id=T, minute=40, kind="red", player_id=None),
             MatchEvent(event_id="r2", match_key=m2.match_key, team_id=T, minute=60, kind="second_yellow", player_id=None),
             MatchEvent(event_id="r3", match_key=m2.match_key, team_id="b", minute=70, kind="red", player_id=None)]
    assert compute_mentality(T, SEASON, [m1, m2], cards, [], []).red_cards_per_match == 1.0


# ---------------------------------------------------------------- knockout ties


def test_stage_key_normalises_provider_labels():
    assert [stage_key(s) for s in ("Round of 16", "round_of_16", "Quarterfinals", "semi_final", "Final", "Playoffs",
                                   "Group A", None)] == \
        ["round_of_16", "round_of_16", "quarter_final", "semi_final", "final", "playoff", None, None]


def _tie(stage, opp, legs, *, league=League.UCL):
    out = []
    for i, (home, own, against) in enumerate(legs, 1):
        out.append(match(i, opp, home, own, against, league=league, stage=stage,
                         date=f"2024-0{3 if stage == 'round_of_16' else 4}-0{i}"))
    return out


def test_knockout_ties_aggregate_and_next_round_presence():
    r16_won = _tie("round_of_16", "x", [(True, 2, 0), (False, 0, 1)])       # 2-1 aggregate
    qf_level = _tie("quarter_final", "y", [(True, 1, 0), (False, 0, 1)])    # level: who reached the semi decides
    semi = [Match(match_key="UCL:s:semi", league=League.UCL, season=SEASON, date="2024-05-01", stage="semi_final",
                  home_id="y", away_id="w", home_goals=1, away_goals=0)]
    out = compute_mentality(T, SEASON, r16_won + qf_level + semi, [], [], [])
    assert out.knockout_record == "1-1"  # won the R16 tie, lost the QF tie (y went through)
    assert any("Champions League knockout ties won-lost: 1-1" in n for n in out.notes)


def test_undecidable_tie_is_skipped_not_guessed():
    level = _tie("round_of_16", "x", [(True, 1, 0), (False, 0, 1)])
    assert compute_mentality(T, SEASON, level, [], [], []).knockout_record is None


# ---------------------------------------------------------------- openfootball


def test_openfootball_parses_league_json_and_champions_league_text(tmp_path):
    rows = openfootball.normalize(_cfg(tmp_path, "openfootball"))
    matches = rows["matches"]
    league = [m for m in matches if m.league == League.EPL]
    assert len(league) == 3  # the unplayed fixture is skipped
    first = next(m for m in league if m.home_id.endswith("rovermere_fc") and m.date == "2023-08-12")
    assert (first.home_goals, first.away_goals, first.home_goals_ht, first.away_goals_ht) == (2, 1, 1, 0)
    assert first.match_key == "EPL:2023-24:2023-08-12:rovermere_fc-eastbridge_athletic"
    assert first.home_id == "openfootball:rovermere_fc"

    ucl = {(m.date, m.home_id.split(":")[1], m.away_id.split(":")[1]): m for m in matches if m.league == League.UCL}
    assert len(ucl) == 10
    assert {m.date[:4] for m in ucl.values()} == {"2023", "2024"}  # the year rolls over with the dates
    assert ucl[("2023-09-20", "norwick_town", "1_fc_southgate")].stage == "group"
    pens_leg = ucl[("2024-03-05", "1_fc_southgate", "eastbridge_athletic")]
    assert (pens_leg.home_goals, pens_leg.away_goals) == (1, 0)  # 90-minute score
    assert (pens_leg.home_goals_ht, pens_leg.away_goals_ht) == (1, 0)
    aet_leg = ucl[("2024-03-06", "greystone_city", "foxdale_rangers")]
    assert (aet_leg.home_goals, aet_leg.away_goals) == (1, 0)
    assert {m.stage for m in ucl.values()} == {"group", "round_of_16", "semi_final"}
    if "home_pens" in Match.model_fields:
        assert (pens_leg.home_pens, pens_leg.away_pens) == (3, 5)
        assert (aet_leg.home_goals_aet, aet_leg.away_goals_aet) == (2, 3)
    assert {c.name for c in rows["clubs"]} >= {"Rovermere FC", "1. FC Southgate", "Foxdale Rangers"}


def test_openfootball_knockout_record_from_normalised_rows(tmp_path):
    matches = openfootball.normalize(_cfg(tmp_path, "openfootball"))["matches"]
    rec = {}
    for club in ("eastbridge_athletic", "norwick_town", "rovermere_fc"):
        out = compute_mentality(f"openfootball:{club}", SEASON, matches, [], [], [], league=League.UCL)
        rec[club] = out.knockout_record
    # Eastbridge: level with Southgate (shoot-out / reached the semi), then beat Norwick 3-2 on aggregate
    assert rec == {"eastbridge_athletic": "2-0", "norwick_town": "1-1", "rovermere_fc": "0-1"}


def test_openfootball_fetch_skips_leagues_it_does_not_cover(tmp_path):
    cfg = Config(root=tmp_path)
    assert openfootball.fetch(cfg, [League.UCL], [], limit=0) == 0


# ---------------------------------------------------------------- wikidata


def test_wikidata_sparql_json_is_normalised(tmp_path):
    rows = wikidata.normalize(_cfg(tmp_path, "wikidata"))
    clubs = {c.source_ids.wikidata: c for c in rows["clubs"]}
    rover = clubs["Q900001"]
    assert (rover.team_id, rover.name, rover.founded, rover.country) == ("wikidata:Q900001", "Rovermere FC", 1886,
                                                                          "Fictionland")
    assert (rover.stadium, rover.stadium_capacity) == ("Rovermere Arena", 45000)  # largest stadium, earliest year
    assert clubs["Q900002"].stadium is None

    managers = {m.source_ids.wikidata: m for m in rows["managers"]}
    assert managers["Q910001"].birth_date == "1968-02-03"
    spells = {(s.manager_id, s.team_id): s for s in rows["manager_spells"]}
    current = spells[("wikidata:Q910001", "wikidata:Q900001")]
    assert (current.start, current.end, current.source) == ("2016-07-01", None, "wikidata")
    assert spells[("wikidata:Q910002", "wikidata:Q900001")].end == "2016-06-15"
    assert ("wikidata:Q910003", "wikidata:Q900001") not in spells  # no start date: not invented
    assert len(spells) == 3

    honours = {(h.team_id, h.competition, h.season) for h in rows["honours"]}
    assert honours == {("wikidata:Q900001", "Premier League", "2019-20"), ("wikidata:Q900001", "Serie A", "2021-22"),
                       ("wikidata:Q900002", "UEFA Champions League", "2017-18")}

    players = {p.source_ids.wikidata: p for p in rows["players"]}
    assert (players["Q920001"].height_cm, players["Q920001"].birth_date) == (185, "1999-04-05")  # metres -> cm
    assert players["Q920002"].height_cm == 178


def test_wikidata_raw_exposes_article_titles_and_key_players(tmp_path):
    raw = wikidata.load_raw(_cfg(tmp_path, "wikidata"))
    assert raw.clubs["Q900001"]["article"] == "Rovermere F.C."
    assert wikidata.key_players(raw, "Q900001") == ["Q920001"]  # only players with an article
    assert wikidata.season_label("2019-08-09T00:00:00Z") == "2019-20"
    assert wikidata.season_label("1999-08-01T00:00:00Z") == "1999-00"


def test_wikidata_queries_are_valid_looking_batches():
    q = wikidata.managers_query(["Q1", "Q2"])
    assert "VALUES ?club { wd:Q1 wd:Q2 }" in q and "P286" in q and "P580" in q
    assert "wd:Q9448" in wikidata.honours_query() and "wikibase:label" in wikidata.clubs_query("Q9448")


# ---------------------------------------------------------------- wikipedia


def test_wikipedia_docs_keep_attribution(tmp_path):
    docs = wikipedia.normalize(_cfg(tmp_path, "wikipedia"))["text_docs"]
    by_title = {d.title: d for d in docs}
    club = by_title["Rovermere F.C."]
    assert (club.entity_kind, club.entity_id) == ("club", "wikidata:Q900001")
    assert club.url == "https://en.wikipedia.org/wiki/Rovermere_F.C."
    assert (club.licence, club.retrieved) == ("CC BY-SA 4.0", "2026-10-01")
    assert all(d.licence == "CC BY-SA 4.0" and d.url.startswith("https://en.wikipedia.org/wiki/") for d in docs)
    assert not any("disambiguation" in d.title for d in docs)  # disambiguation pages are dropped

    history = by_title["Rovermere F.C.#History"]
    assert history.text.startswith("The club was founded in 1886")
    assert "Early years." in history.text and "Some honours" not in history.text
    assert len(history.text) <= wikipedia.SECTION_CAP
    assert history.url.endswith("#History")
    assert by_title["Alda Verrin#Coaching style"].text == "Verrin favours a high press and a back three."
    assert "Dorian Falk#Style of play" not in by_title  # no such section: nothing invented


def test_section_text_caps_at_a_sentence():
    text = "== History ==\n" + " ".join(f"Sentence number {i} is here." for i in range(400))
    heading, out = wikipedia.section_text(text, ["history"], cap=500)
    assert heading == "History" and len(out) <= 500 and out.endswith(".")
    assert wikipedia.section_text("== Other ==\nx", ["history"]) is None


def test_wikipedia_targets_come_from_wikidata_sitelinks(tmp_path):
    cfg = _cfg(tmp_path, "wikidata")
    titles = {(t["kind"], t["title"]) for t in wikipedia.targets(cfg)}
    assert titles == {("club", "Rovermere F.C."), ("club", "Eastbridge Athletic F.C."),
                      ("player", "Dorian Falk"), ("manager", "Alda Verrin")}


# ---------------------------------------------------------------- knowledge profiles


@pytest.fixture()
def knowledge(tmp_path):
    cfg = _cfg(tmp_path, "wikidata", "wikipedia")
    tables = {**wikidata.normalize(cfg), **wikipedia.normalize(cfg)}
    rover = "wikidata:Q900001"
    tables["matches"] = [
        upd(match(1, "o", True, 1, 0), home_id=rover, home_manager_id="wikidata:Q910001", home_formation="4-3-3"),
        upd(match(2, "o", False, 1, 0), away_id=rover, away_manager_id="wikidata:Q910001", away_formation="4-3-3"),
        upd(match(3, "o", True, 1, 0), home_id=rover, home_manager_id="wikidata:Q910001", home_formation="3-4-3"),
        upd(match(4, "o", True, 1, 0), home_id=rover, home_manager_id="wikidata:Q910001"),  # formation unknown
        upd(match(5, "o", True, 1, 0), home_id=rover, home_manager_id="someone-else", home_formation="5-4-1"),
    ]
    return Knowledge.from_tables(tables)


def test_club_profile_assembles_facts_text_and_attribution(knowledge):
    p = knowledge.club("wikidata:Q900001")
    assert (p.name, p.country, p.founded, p.stadium) == ("Rovermere FC", "Fictionland", 1886, "Rovermere Arena")
    assert p.honours == ["Serie A 2021-22", "Premier League 2019-20"]  # newest first
    assert p.manager_timeline == ["Alda Verrin (2016-07 to present)", "Bram Kestel (2010-07 to 2016-06)"]
    assert p.rivals == []
    assert p.history.startswith("Rovermere Football Club is a fictional club founded in 1886.")
    assert "The club was founded in 1886 by fictional millers." in p.history
    assert any("https://en.wikipedia.org/wiki/Rovermere_F.C." in a and "CC BY-SA 4.0" in a and "2026-10-01" in a
               for a in p.attribution)
    assert any("Wikidata" in a and "CC0" in a for a in p.attribution)
    assert len(p.attribution) == len(set(p.attribution))


def test_club_without_text_has_no_history(knowledge):
    p = knowledge.club("wikidata:Q900002")
    assert p.history is None and p.honours == ["UEFA Champions League 2017-18"]
    assert p.manager_timeline == ["Alda Verrin (2012-07 to 2016-06)"]


def test_rivals_only_when_supplied(knowledge):
    rows = knowledge
    p = club_profile("wikidata:Q900001", clubs=rows.clubs, rivals={"wikidata:Q900001": ["Eastbridge Athletic"]})
    assert p.rivals == ["Eastbridge Athletic"]


def test_manager_profile_tenures_trophies_formations_philosophy(knowledge):
    p = knowledge.manager("wikidata:Q910001")
    assert p.name == "Alda Verrin" and p.nationality == "Fictionland"
    assert p.tenures == ["Rovermere FC (2016-07 to present)", "Eastbridge Athletic (2012-07 to 2016-06)"]
    # in post at Rovermere when 2019-20 / 2021-22 ended; Eastbridge's 2017-18 title came after he left
    assert p.trophies == ["Serie A 2021-22 with Rovermere FC", "Premier League 2019-20 with Rovermere FC"]
    assert [(f.formation, f.share) for f in p.formations] == [("4-3-3", 0.667), ("3-4-3", 0.333)]
    assert p.philosophy.startswith("Alda Verrin is a fictional manager known for pressing.")
    assert "high press and a back three" in p.philosophy
    assert any("Alda_Verrin" in a for a in p.attribution)


def test_manager_without_documents_has_no_philosophy(knowledge):
    p = knowledge.manager("wikidata:Q910002")
    assert p.philosophy is None and p.formations == [] and p.trophies == []
    assert p.tenures == ["Rovermere FC (2010-07 to 2016-06)"]


def test_unknown_ids_raise(knowledge):
    with pytest.raises(KeyError):
        knowledge.club("nope")
    with pytest.raises(KeyError):
        manager_profile("nope", managers=knowledge.managers)


def test_manager_formations_ignore_matches_of_other_managers(knowledge):
    shares = manager_formations("someone-else", knowledge.matches)
    assert [(f.formation, f.share) for f in shares] == [("5-4-1", 1.0)]


def test_honour_season_end_is_june_of_second_year():
    from matchlens.leaguedata.knowledge import _season_end
    assert _season_end("2019-20") == "2020-06-30" and _season_end("1999-00") == "2000-06-30"
