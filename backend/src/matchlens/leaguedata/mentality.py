"""How a team behaves under pressure, computed from results and match timelines only.

``compute_mentality`` is a pure function of schema rows: nothing is estimated by a model
and a metric is ``None`` whenever the data needed for it is missing.

Definitions (a team-season, optionally restricted to one league):

* A *timeline* is the sequence of goals of a match from ``MatchEvent`` rows (goals, penalty
  goals, own goals), falling back to goal ``Shot`` rows. It is only used when it adds up to
  the final score; otherwise the match is left out of every timeline metric.
* ``comeback_rate``: matches in which the team trailed at some point and did not lose, over
  matches with a timeline.
* ``points_from_losing``: mean points (3/1/0) in the matches where the team trailed.
* ``lead_protection``: of the matches in which the team led at some point, the share it won.
* ``late_goal_share`` / ``late_concede_share``: goals scored / conceded from the 75th minute
  on, over all goals scored / conceded.
* ``response_after_conceding``: shots in the 15 minutes after conceding relative to the
  team's own shots per 15 minutes in the same matches (1.0 = no change); needs shot data.
* ``big_game_ppg``: points per match against the top four of the league-season table
  (domestic league matches in the data), excluding the team itself.
* ``knockout_record``: Champions League knockout ties won-lost (aggregate, then shoot-out,
  then which side reached the next round).
* ``red_cards_per_match``: red and second-yellow cards per match.
* ``finishing_streak``: goals minus xG over the last (up to) 10 matches that have xG.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence

from matchlens.leaguedata.contracts import Mentality
from matchlens.leaguedata.schema import League, Match, MatchEvent, Shot, TeamMatchStats

LATE_FROM = 75
RESPONSE_WINDOW = 15
FINISHING_WINDOW = 10
TOP_N = 4

STAGE_ORDER = ("playoff", "round_of_32", "round_of_16", "quarter_final", "semi_final", "final")
_GOAL_KINDS = {"goal", "penalty_goal", "own_goal"}
_RED_KINDS = {"red", "second_yellow"}


def stage_key(stage: str | None) -> str | None:
    """Normalise a provider stage label to one of ``STAGE_ORDER`` (None for group/league phase)."""
    if not stage:
        return None
    s = re.sub(r"[^a-z0-9]", "", stage.lower())
    if "playoff" in s:
        return "playoff"
    if "32" in s:
        return "round_of_32"
    if "16" in s:
        return "round_of_16"
    if "quarter" in s:
        return "quarter_final"
    if "semi" in s:
        return "semi_final"
    if s.endswith("final") or s == "final":
        return "final"
    return None


def _points(own: int, opp: int) -> int:
    return 3 if own > opp else 1 if own == opp else 0


# ------------------------------------------------------------------ timelines


def _timeline(match: Match, events: Sequence[MatchEvent], shots: Sequence[Shot]) -> list[tuple[int, str]] | None:
    """Goals as ``(minute, "home"|"away")`` in order, or None when no source adds up to the score.

    The side of an own goal is ambiguous between providers, so both readings are tried, as is
    dropping goals after the 90th minute (extra time) when the stored score is the 90-minute one.
    """
    sources: list[list[tuple[int, str, str, str]]] = []  # (minute, team_id, kind, sort id)
    ev = [(e.minute, e.team_id, e.kind, e.event_id) for e in events if e.kind in _GOAL_KINDS]
    sources.append(ev)
    sh = [(s.minute, s.team_id, "own_goal" if s.result == "own_goal" else "goal", s.shot_id)
          for s in shots if s.result in ("goal", "own_goal")]
    sources.append(sh)
    target = (match.home_goals, match.away_goals)
    for goals in sources:
        if not goals and target != (0, 0):
            continue
        for flip, cut in itertools.product((False, True), repeat=2):
            seq: list[tuple[int, str, str]] = []
            for minute, team, kind, ident in goals:
                if cut and minute > 90:
                    continue
                side = "home" if team == match.home_id else "away"
                if kind == "own_goal" and flip:
                    side = "away" if side == "home" else "home"
                seq.append((minute, ident, side))
            totals = (sum(s == "home" for _, _, s in seq), sum(s == "away" for _, _, s in seq))
            if totals == target:
                return [(minute, side) for minute, _, side in sorted(seq)]
    return None


class _Game:
    """One match from the team's point of view."""

    __slots__ = ("goals", "match", "opp", "own", "points")

    def __init__(self, match: Match, team_id: str, goals: list[tuple[int, str]] | None):
        home = match.home_id == team_id
        self.match = match
        self.own = match.home_goals if home else match.away_goals
        self.opp = match.away_goals if home else match.home_goals
        mine = "home" if home else "away"
        self.goals = None if goals is None else [(minute, side == mine) for minute, side in goals]
        self.points = _points(self.own, self.opp)

    def max_deficit(self) -> int:
        assert self.goals is not None
        score = worst = 0
        for _, scored in self.goals:
            score += 1 if scored else -1
            worst = min(worst, score)
        return -worst

    def led(self) -> bool:
        assert self.goals is not None
        score = 0
        for _, scored in self.goals:
            score += 1 if scored else -1
            if score > 0:
                return True
        return False


# ------------------------------------------------------------------ league tables


def _top_teams(matches: Iterable[Match], season: str, league: League | None) -> set[str]:
    """Top-four club ids of every domestic league-season present in ``matches``."""
    table: dict[League, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for m in matches:
        if m.season != season or m.league == League.UCL or (league is not None and m.league != league):
            continue
        for team, own, opp in ((m.home_id, m.home_goals, m.away_goals), (m.away_id, m.away_goals, m.home_goals)):
            row = table[m.league][team]
            row[0] += _points(own, opp)
            row[1] += own - opp
            row[2] += own
    top: set[str] = set()
    for rows in table.values():
        ranked = sorted(rows, key=lambda t: (-rows[t][0], -rows[t][1], -rows[t][2], t))
        top.update(ranked[:TOP_N])
    return top


# ------------------------------------------------------------------ knockouts


def _knockout_record(team_id: str, games: Sequence[Match], all_matches: Sequence[Match],
                     season: str) -> tuple[int, int, list[str]]:
    """(ties won, ties lost, descriptions) over the team's decided Champions League knockout ties."""
    in_stage: dict[str, set[str]] = defaultdict(set)
    for m in all_matches:
        key = stage_key(m.stage)
        if m.league == League.UCL and m.season == season and key:
            in_stage[key].update((m.home_id, m.away_id))
    ties: dict[tuple[str, frozenset[str]], list[Match]] = defaultdict(list)
    for m in games:
        key = stage_key(m.stage)
        if m.league == League.UCL and key:
            ties[(key, frozenset((m.home_id, m.away_id)))].append(m)
    won = lost = 0
    notes: list[str] = []
    for (stage, pair), legs in sorted(ties.items(), key=lambda kv: (STAGE_ORDER.index(kv[0][0]), sorted(kv[0][1]))):
        opponent = next(iter(pair - {team_id}))
        own = opp = 0
        for leg in sorted(legs, key=lambda m: m.date):
            h_aet, a_aet = getattr(leg, "home_goals_aet", None), getattr(leg, "away_goals_aet", None)
            hg, ag = (h_aet, a_aet) if h_aet is not None and a_aet is not None else (leg.home_goals, leg.away_goals)
            if leg.home_id == team_id:
                own, opp = own + hg, opp + ag
            else:
                own, opp = own + ag, opp + hg
        verdict: bool | None
        if own != opp:
            verdict = own > opp
        else:
            last = max(legs, key=lambda m: m.date)
            hp, ap = getattr(last, "home_pens", None), getattr(last, "away_pens", None)
            if hp is not None and ap is not None and hp != ap:
                mine, theirs = (hp, ap) if last.home_id == team_id else (ap, hp)
                verdict = mine > theirs
            else:
                later = STAGE_ORDER[STAGE_ORDER.index(stage) + 1:]
                team_on = any(team_id in in_stage[s] for s in later)
                opp_on = any(opponent in in_stage[s] for s in later)
                verdict = True if team_on and not opp_on else False if opp_on and not team_on else None
        if verdict is None:
            continue
        won += verdict
        lost += not verdict
        notes.append(f"{stage}: {'won' if verdict else 'lost'} {own}-{opp} on aggregate" if own != opp
                     else f"{stage}: {'won' if verdict else 'lost'} (level on aggregate)")
    return won, lost, notes


# ------------------------------------------------------------------ main


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def compute_mentality(team_id: str, season: str, matches: Sequence[Match], events: Sequence[MatchEvent],
                      shots: Sequence[Shot], stats: Sequence[TeamMatchStats],
                      league: League | None = None) -> Mentality:
    """Mentality of ``team_id`` in ``season`` from every row passed in (other teams' rows are ignored)."""
    mine = sorted((m for m in matches if m.season == season and team_id in (m.home_id, m.away_id)
                   and (league is None or m.league == league)), key=lambda m: (m.date, m.match_key))
    keys = {m.match_key for m in mine}
    ev_by: dict[str, list[MatchEvent]] = defaultdict(list)
    for e in events:
        if e.match_key in keys:
            ev_by[e.match_key].append(e)
    shots_by: dict[str, list[Shot]] = defaultdict(list)
    for s in shots:
        if s.match_key in keys:
            shots_by[s.match_key].append(s)
    stats_by = {s.match_key: s for s in stats if s.team_id == team_id and s.match_key in keys}

    games: list[_Game] = []
    for m in mine:
        has_data = m.match_key in ev_by or m.match_key in shots_by
        goals = _timeline(m, ev_by.get(m.match_key, []), shots_by.get(m.match_key, [])) if has_data else None
        games.append(_Game(m, team_id, goals))
    timed = [g for g in games if g.goals is not None]

    notes: list[str] = []

    # --- comebacks, leads
    trailed = [g for g in timed if g.max_deficit() > 0]
    comebacks = [g for g in trailed if g.points > 0]
    led = [g for g in timed if g.led()]
    led_won = [g for g in led if g.points == 3]
    comeback_rate = _ratio(len(comebacks), len(timed))
    points_from_losing = round(sum(g.points for g in trailed) / len(trailed), 3) if trailed else None
    lead_protection = _ratio(len(led_won), len(led))
    if comebacks:
        wins = sum(g.points == 3 for g in comebacks)
        notes.append(f"{len(comebacks)} comeback{'s' if len(comebacks) != 1 else ''} from losing positions "
                     f"({wins} won, {len(comebacks) - wins} drawn) in {len(trailed)} matches in which they trailed")
        deepest = max(g.max_deficit() for g in comebacks)
        notes.append(f"largest deficit overcome without losing: {deepest} goal{'s' if deepest != 1 else ''}")
    elif trailed:
        notes.append(f"trailed in {len(trailed)} matches and never came back to avoid defeat")
    dropped = [g for g in led if g.points < 3]
    if led:
        notes.append(f"won {len(led_won)} of {len(led)} matches in which they led"
                     + (f"; dropped points from a lead in {len(dropped)}" if dropped else ""))

    # --- late goals
    scored_min = [minute for g in timed for minute, mine_ in (g.goals or []) if mine_]
    conceded_min = [minute for g in timed for minute, mine_ in (g.goals or []) if not mine_]
    late_for = sum(minute >= LATE_FROM for minute in scored_min)
    late_against = sum(minute >= LATE_FROM for minute in conceded_min)
    late_goal_share = _ratio(late_for, len(scored_min))
    late_concede_share = _ratio(late_against, len(conceded_min))
    if scored_min:
        notes.append(f"{late_for} of {len(scored_min)} goals scored from the {LATE_FROM}th minute on")
    if conceded_min:
        notes.append(f"{late_against} of {len(conceded_min)} goals conceded from the {LATE_FROM}th minute on")

    # --- response after conceding
    response: float | None = None
    shot_games = [g for g in timed if g.match.match_key in shots_by]
    if shot_games:
        base_shots = sum(1 for g in shot_games for s in shots_by[g.match.match_key]
                         if s.team_id == team_id and s.minute <= 90)
        base_rate = base_shots / (len(shot_games) * 90 / RESPONSE_WINDOW)
        resp_shots = covered = windows = 0
        for g in shot_games:
            for minute, scored in g.goals or []:
                if scored or minute >= 90:
                    continue
                length = min(RESPONSE_WINDOW, 90 - minute)
                resp_shots += sum(1 for s in shots_by[g.match.match_key]
                                  if s.team_id == team_id and minute < s.minute <= minute + length)
                covered += length
                windows += 1
        if windows and base_rate > 0:
            resp_rate = resp_shots / covered * RESPONSE_WINDOW
            response = round(resp_rate / base_rate, 3)
            notes.append(f"{resp_rate:.2f} shots per {RESPONSE_WINDOW}' after conceding vs {base_rate:.2f} "
                         f"normally ({windows} goals conceded)")

    # --- big games
    top = _top_teams(matches, season, league)
    big = [g for g in games if (g.match.away_id if g.match.home_id == team_id else g.match.home_id) in top
           and (g.match.away_id if g.match.home_id == team_id else g.match.home_id) != team_id]
    big_game_ppg = round(sum(g.points for g in big) / len(big), 3) if big else None
    if big:
        notes.append(f"{sum(g.points for g in big)} points from {len(big)} matches against the league-season top {TOP_N}")

    # --- knockouts
    ko_won, ko_lost, _ = _knockout_record(team_id, mine, matches, season)
    knockout_record = f"{ko_won}-{ko_lost}" if ko_won + ko_lost else None
    if knockout_record:
        notes.append(f"Champions League knockout ties won-lost: {knockout_record}")

    # --- red cards
    red_rows = [stats_by[g.match.match_key].reds for g in games
                if g.match.match_key in stats_by and stats_by[g.match.match_key].reds is not None]
    if red_rows:
        reds_total, red_n = sum(r for r in red_rows if r is not None), len(red_rows)
    else:
        carded = [g for g in games if g.match.match_key in ev_by]
        reds_total = sum(1 for g in carded for e in ev_by[g.match.match_key]
                         if e.team_id == team_id and e.kind in _RED_KINDS)
        red_n = len(carded)
    red_cards_per_match = round(reds_total / red_n, 3) if red_n else None
    if red_n and reds_total:
        notes.append(f"{reds_total} red card{'s' if reds_total != 1 else ''} in {red_n} matches")

    # --- finishing
    with_xg = [stats_by[g.match.match_key] for g in games
               if g.match.match_key in stats_by and stats_by[g.match.match_key].xg is not None][-FINISHING_WINDOW:]
    finishing_streak = None
    if with_xg:
        finishing_streak = round(sum(s.goals - (s.xg or 0.0) for s in with_xg), 2)
        notes.append(f"{finishing_streak:+.1f} goals vs xG over the last {len(with_xg)} matches with xG")

    return Mentality(
        team_id=team_id, season=season, league=league, matches=len(mine),
        comeback_rate=comeback_rate, points_from_losing=points_from_losing, lead_protection=lead_protection,
        late_goal_share=late_goal_share, late_concede_share=late_concede_share,
        response_after_conceding=response, big_game_ppg=big_game_ppg, knockout_record=knockout_record,
        red_cards_per_match=red_cards_per_match, finishing_streak=finishing_streak, notes=notes)
