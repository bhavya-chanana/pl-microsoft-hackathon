"""Team style metrics, computed in plain Python from the normalised league tables.

``TeamStyle`` describes how a team plays over a season or, with ``as_of_match``,
over its last matches *before* a given match (no leakage). Percentiles and
z-scores are taken within the league-season: ``pct`` is the mid-rank
percentile (0..100) of the raw value, ``z`` the population z-score. Neither is
sign-flipped, so a high ``ppda`` percentile means a *passive* press.

Free data has no pass-level events for recent seasons, so some metrics are
proxies (``Metric.is_proxy``):

=====================  =========================================================  =====
key                    definition                                                  proxy
=====================  =========================================================  =====
ppda                   own passes-per-defensive-action allowed (Understat)         no
ppda_allowed           opponents' PPDA against us (their press intensity vs us)    no
deep_share             deep / (deep + deep_allowed)  (field-tilt stand-in)         yes
directness             1 - sum(xg_buildup) / sum(xg_chain)                         yes
xg_for90, xg_against90 team xG for / against per match                             no
npxg_for90             non-penalty xG per match                                    no
xg_per_shot            non-penalty xG / non-penalty shots                          no
shot_distance_mean     mean distance (m) to goal centre, non-penalty shots         no
box_shot_share         non-penalty shots inside the box / non-penalty shots        no
header_share           headed share of non-penalty shots                           no
shot_share_<sit>       share of all shots by situation (sums to 1)                 no
xg_share_<sit>         share of all xG by situation (sums to 1)                    no
create_<kind>          share of non-penalty shots whose last action was a          no
                       cross / through_ball / take_on / rebound
counter_proxy          fast_break share of shots when the data has fast breaks,    yes
                       else (through_ball + take_on) share of non-penalty shots
xg90_<state>           xG per 90 minutes spent leading / level / trailing          no
shots90_<state>        shots per 90 minutes in that state                          no
sit_back_index         xG share while level - xG share while leading               no
                       (positive: loses the territorial edge after taking a lead)
xg_<a>_<b>             xG per match scored in the 15-minute bin a..b               no
fouls90, cards90       fouls, yellows + reds per match                             no
elo_mean               mean pre-match ClubElo rating                               no
odds_win_prob_mean     mean closing-odds win probability                           no
possession             mean possession (only when the data has it)                 no
=====================  =========================================================  =====

Game-state minutes are reconstructed from the shot score-before fields (a goal
is assumed to be the shot preceding the score change), over 90 minutes, and a
state needs at least ``MIN_STATE_MINUTES`` of exposure to be reported.

Rolling window (``as_of_match``): the ``ROLLING_MATCHES`` most recent matches
strictly before the reference match's date, weighted ``RECENCY_DECAY ** age``
(newest first). Percentiles for that cut-off use every team's rolling window as
of the same date, so nothing after the reference match ever enters.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from matchlens.leaguedata.contracts import FormationShare, Metric, TeamStyle
from matchlens.leaguedata.schema import (
    League,
    LineupEntry,
    Match,
    Shot,
    TeamMatchStats,
)

ROLLING_MATCHES = 6
RECENCY_DECAY = 0.8
MIN_STATE_MINUTES = 30.0
MATCH_MINUTES = 90.0

GOAL_X, GOAL_Y = 105.0, 34.0
BOX_X, BOX_Y0, BOX_Y1 = 88.5, 13.84, 54.16

SITUATIONS = ("open_play", "corner", "set_piece", "direct_free_kick", "penalty", "fast_break")
CREATION_KINDS = ("cross", "through_ball", "take_on", "rebound")
STATES = ("leading", "level", "trailing")
BINS = ((0, 15), (15, 30), (30, 45), (45, 60), (60, 75), (75, 90))

STYLE_KEYS: tuple[str, ...] = (
    "ppda",
    "ppda_allowed",
    "deep_share",
    "directness",
    "xg_for90",
    "xg_against90",
    "npxg_for90",
    "xg_per_shot",
    "shot_distance_mean",
    "box_shot_share",
    "header_share",
    *(f"shot_share_{s}" for s in SITUATIONS),
    *(f"xg_share_{s}" for s in SITUATIONS),
    *(f"create_{k}" for k in CREATION_KINDS),
    "counter_proxy",
    *(f"xg90_{s}" for s in STATES),
    *(f"shots90_{s}" for s in STATES),
    "sit_back_index",
    *(f"xg_{a}_{b}" for a, b in BINS),
    "fouls90",
    "cards90",
    "elo_mean",
    "odds_win_prob_mean",
    "possession",
)

_PROXY = {"deep_share", "directness", "counter_proxy"}
_SOURCE_FD = {"fouls90", "cards90", "odds_win_prob_mean"}


def _source(key: str) -> str:
    if key in _SOURCE_FD:
        return "football_data"
    if key == "elo_mean":
        return "clubelo"
    if key == "possession":
        return "espn"
    return "understat"


# ---------------------------------------------------------------- helpers


def _norm_action(action: str | None) -> str | None:
    if not action:
        return None
    a = "".join(ch for ch in action.lower() if ch.isalnum())
    return {
        "cross": "cross",
        "throughball": "through_ball",
        "takeon": "take_on",
        "rebound": "rebound",
    }.get(a, a)


def _distance(s: Shot) -> float:
    return math.hypot(GOAL_X - s.x, GOAL_Y - s.y)


def _in_box(s: Shot) -> bool:
    return s.x >= BOX_X and BOX_Y0 <= s.y <= BOX_Y1


def _date_of(match_key: str, by_key: Mapping[str, Match]) -> str | None:
    m = by_key.get(match_key)
    if m is not None:
        return m.date
    parts = match_key.split(":")
    return parts[2] if len(parts) >= 3 else None


def _state_of(diff: int) -> str:
    return "leading" if diff > 0 else "trailing" if diff < 0 else "level"


def _game_state_segments(shots: Sequence[Shot], home_id: str) -> list[tuple[float, float, int]]:
    """Home-minus-away score difference over time: [(start, end, diff)] covering 0..90.

    The score is read off ``*_score_before``; a change between two consecutive
    shots is placed at the earlier shot (the goal), and a final change after the
    last shot is not visible here (the caller passes the final score).
    """
    ordered = sorted(shots, key=lambda s: (s.minute, s.shot_id))
    segs: list[tuple[float, float, int]] = []
    cur_start, cur_diff = 0.0, 0
    for i, s in enumerate(ordered):
        diff = s.home_score_before - s.away_score_before
        if diff != cur_diff:
            t = float(ordered[i - 1].minute) if i > 0 else float(s.minute)
            t = min(max(t, cur_start), MATCH_MINUTES)
            if t > cur_start:
                segs.append((cur_start, t, cur_diff))
            cur_start, cur_diff = t, diff
    segs.append((cur_start, MATCH_MINUTES, cur_diff))
    return [g for g in segs if g[1] > g[0]]


# ------------------------------------------------------------ per-match atoms


@dataclass
class _TM:
    """One team's view of one match."""

    match: Match
    team_id: str
    is_home: bool
    stats_for: TeamMatchStats | None
    stats_opp: TeamMatchStats | None
    shots_for: list[Shot]
    shots_opp: list[Shot]

    @property
    def opp_id(self) -> str:
        return self.match.away_id if self.is_home else self.match.home_id


def _xg_sum(shots: Iterable[Shot]) -> float:
    return sum(s.xg for s in shots)


def _atoms(tm: _TM) -> dict[str, float]:
    """Additive building blocks of every metric for one match (absent = unknown)."""
    a: dict[str, float] = {}
    sf, so = tm.stats_for, tm.stats_opp
    has_shots = bool(tm.shots_for or tm.shots_opp)
    real_for = [s for s in tm.shots_for if s.result != "own_goal"]
    real_opp = [s for s in tm.shots_opp if s.result != "own_goal"]
    np_for = [s for s in real_for if s.situation != "penalty"]

    if sf is not None and sf.ppda is not None:
        a["ppda"], a["n_ppda"] = sf.ppda, 1.0
    ppda_allowed = sf.ppda_allowed if sf is not None and sf.ppda_allowed is not None else (
        so.ppda if so is not None else None
    )
    if ppda_allowed is not None:
        a["ppda_allowed"], a["n_ppda_allowed"] = ppda_allowed, 1.0

    if sf is not None and sf.deep is not None:
        deep_allowed = sf.deep_allowed if sf.deep_allowed is not None else (so.deep if so is not None else None)
        if deep_allowed is not None:
            a["deep"], a["deep_total"] = float(sf.deep), float(sf.deep + deep_allowed)
    if sf is not None and sf.xg_chain is not None and sf.xg_buildup is not None:
        a["xg_buildup"], a["xg_chain"] = sf.xg_buildup, sf.xg_chain

    xg_for = sf.xg if sf is not None and sf.xg is not None else (_xg_sum(real_for) if has_shots else None)
    xg_against = so.xg if so is not None and so.xg is not None else (_xg_sum(real_opp) if has_shots else None)
    if xg_for is not None:
        a["xg_for"], a["n_xg_for"] = xg_for, 1.0
    if xg_against is not None:
        a["xg_against"], a["n_xg_against"] = xg_against, 1.0
    npxg = sf.npxg if sf is not None and sf.npxg is not None else (_xg_sum(np_for) if has_shots else None)
    if npxg is not None:
        a["npxg"], a["n_npxg"] = npxg, 1.0

    if np_for:
        a["np_shots"] = float(len(np_for))
        a["np_xg"] = _xg_sum(np_for)
        a["np_dist"] = sum(_distance(s) for s in np_for)
        a["np_box"] = float(sum(_in_box(s) for s in np_for))
        a["np_head"] = float(sum(s.body_part == "head" for s in np_for))
        for kind in CREATION_KINDS:
            a[f"la_{kind}"] = float(sum(_norm_action(s.last_action) == kind for s in np_for))
    if real_for:
        a["all_shots"] = float(len(real_for))
        for sit in SITUATIONS:
            a[f"sit_n_{sit}"] = float(sum(s.situation == sit for s in real_for))
        total_xg = _xg_sum(real_for)
        if total_xg > 0:
            a["sit_xg_total"] = total_xg
            for sit in SITUATIONS:
                a[f"sit_xg_{sit}"] = _xg_sum(s for s in real_for if s.situation == sit)

    if has_shots:
        a["n_shotdata"] = 1.0
        for i, (lo, hi) in enumerate(BINS):
            last = i == len(BINS) - 1
            a[f"bin_{i}"] = sum(
                s.xg for s in real_for if s.minute >= lo and (s.minute < hi or last)
            )
        _game_state_atoms(tm, a, real_for, real_opp)

    if sf is not None:
        if sf.fouls is not None:
            a["fouls"], a["n_fouls"] = float(sf.fouls), 1.0
        if sf.yellows is not None:
            a["cards"] = float(sf.yellows + (sf.reds or 0))
            a["n_cards"] = 1.0
        if sf.possession is not None:
            a["possession"], a["n_possession"] = sf.possession, 1.0

    elo = tm.match.home_elo if tm.is_home else tm.match.away_elo
    if elo is not None:
        a["elo"], a["n_elo"] = elo, 1.0
    p = tm.match.p_home if tm.is_home else tm.match.p_away
    if p is not None:
        a["odds_p"], a["n_odds_p"] = p, 1.0
    return a


def _game_state_atoms(tm: _TM, a: dict[str, float], real_for: list[Shot], real_opp: list[Shot]) -> None:
    all_shots = tm.shots_for + tm.shots_opp
    segs = _game_state_segments(all_shots, tm.match.home_id)
    sign = 1 if tm.is_home else -1

    for start, end, diff in segs:
        a[f"min_{_state_of(sign * diff)}"] = a.get(f"min_{_state_of(sign * diff)}", 0.0) + (end - start)
    for state in STATES:
        a.setdefault(f"min_{state}", 0.0)
        a.setdefault(f"gs_xg_{state}", 0.0)
        a.setdefault(f"gs_shots_{state}", 0.0)
        a.setdefault(f"gs_opp_xg_{state}", 0.0)
    for s in real_for:
        own, opp = (s.home_score_before, s.away_score_before) if tm.is_home else (s.away_score_before, s.home_score_before)
        st = _state_of(own - opp)
        a[f"gs_xg_{st}"] += s.xg
        a[f"gs_shots_{st}"] += 1.0
    for s in real_opp:
        own, opp = (s.home_score_before, s.away_score_before) if tm.is_home else (s.away_score_before, s.home_score_before)
        a[f"gs_opp_xg_{_state_of(own - opp)}"] += s.xg
    if a["min_leading"] > 0:
        a["sb_level_own"] = a["gs_xg_level"]
        a["sb_level_tot"] = a["gs_xg_level"] + a["gs_opp_xg_level"]
        a["sb_lead_own"] = a["gs_xg_leading"]
        a["sb_lead_tot"] = a["gs_xg_leading"] + a["gs_opp_xg_leading"]


def _ratio(A: Mapping[str, float], num: str, den: str, *, min_den: float = 0.0) -> float | None:
    n, d = A.get(num), A.get(den)
    if n is None or d is None or d <= min_den:
        return None
    return n / d


def _values(A: Mapping[str, float], *, has_fast_break: bool) -> dict[str, float | None]:
    v: dict[str, float | None] = {}
    v["ppda"] = _ratio(A, "ppda", "n_ppda")
    v["ppda_allowed"] = _ratio(A, "ppda_allowed", "n_ppda_allowed")
    v["deep_share"] = _ratio(A, "deep", "deep_total")
    bu = _ratio(A, "xg_buildup", "xg_chain")
    v["directness"] = None if bu is None else 1.0 - bu
    v["xg_for90"] = _ratio(A, "xg_for", "n_xg_for")
    v["xg_against90"] = _ratio(A, "xg_against", "n_xg_against")
    v["npxg_for90"] = _ratio(A, "npxg", "n_npxg")
    v["xg_per_shot"] = _ratio(A, "np_xg", "np_shots")
    v["shot_distance_mean"] = _ratio(A, "np_dist", "np_shots")
    v["box_shot_share"] = _ratio(A, "np_box", "np_shots")
    v["header_share"] = _ratio(A, "np_head", "np_shots")
    for sit in SITUATIONS:
        v[f"shot_share_{sit}"] = _ratio(A, f"sit_n_{sit}", "all_shots")
        v[f"xg_share_{sit}"] = _ratio(A, f"sit_xg_{sit}", "sit_xg_total")
    for kind in CREATION_KINDS:
        v[f"create_{kind}"] = _ratio(A, f"la_{kind}", "np_shots")
    if has_fast_break:
        v["counter_proxy"] = _ratio(A, "sit_n_fast_break", "all_shots")
    elif "np_shots" in A and A["np_shots"] > 0:
        v["counter_proxy"] = (A.get("la_through_ball", 0.0) + A.get("la_take_on", 0.0)) / A["np_shots"]
    else:
        v["counter_proxy"] = None
    for state in STATES:
        minutes = A.get(f"min_{state}")
        if minutes is None or minutes < MIN_STATE_MINUTES:
            v[f"xg90_{state}"] = v[f"shots90_{state}"] = None
        else:
            v[f"xg90_{state}"] = A[f"gs_xg_{state}"] / minutes * MATCH_MINUTES
            v[f"shots90_{state}"] = A[f"gs_shots_{state}"] / minutes * MATCH_MINUTES
    lvl = _ratio(A, "sb_level_own", "sb_level_tot")
    lead = _ratio(A, "sb_lead_own", "sb_lead_tot")
    v["sit_back_index"] = None if lvl is None or lead is None else lvl - lead
    n = A.get("n_shotdata", 0.0)
    for i, (lo, hi) in enumerate(BINS):
        v[f"xg_{lo}_{hi}"] = A[f"bin_{i}"] / n if n > 0 and f"bin_{i}" in A else None
    v["fouls90"] = _ratio(A, "fouls", "n_fouls")
    v["cards90"] = _ratio(A, "cards", "n_cards")
    v["elo_mean"] = _ratio(A, "elo", "n_elo")
    v["odds_win_prob_mean"] = _ratio(A, "odds_p", "n_odds_p")
    v["possession"] = _ratio(A, "possession", "n_possession")
    return v


# ------------------------------------------------------------------ builder


@dataclass
class _Table:
    matches: list[Match]
    by_team: dict[str, list[_TM]] = field(default_factory=dict)
    has_fast_break: bool = False


class StyleBuilder:
    """Indexes the tables once; reuse it for many ``team_style`` / ``league_styles`` calls."""

    def __init__(
        self,
        matches: Iterable[Match],
        stats: Iterable[TeamMatchStats] = (),
        shots: Iterable[Shot] = (),
        lineups: Iterable[LineupEntry] = (),
        names: Mapping[str, str] | None = None,
    ) -> None:
        self._matches = list(matches)
        self._by_key = {m.match_key: m for m in self._matches}
        self._stats: dict[tuple[str, str], TeamMatchStats] = {(s.match_key, s.team_id): s for s in stats}
        self._shots: dict[str, list[Shot]] = defaultdict(list)
        for s in shots:
            self._shots[s.match_key].append(s)
        self._lineups = list(lineups)  # reserved: minutes-weighting / rotation (not used by the metrics)
        self._names = dict(names or {})
        self._tables: dict[tuple[League, str], _Table] = {}

    # -- indexing
    def _table(self, league: League, season: str) -> _Table:
        key = (league, season)
        if key in self._tables:
            return self._tables[key]
        ms = sorted(
            (m for m in self._matches if m.league == league and m.season == season),
            key=lambda m: (m.date, m.match_key),
        )
        t = _Table(matches=ms)
        for m in ms:
            shots = self._shots.get(m.match_key, [])
            for team_id, opp_id, is_home in ((m.home_id, m.away_id, True), (m.away_id, m.home_id, False)):
                t.by_team.setdefault(team_id, []).append(
                    _TM(
                        match=m,
                        team_id=team_id,
                        is_home=is_home,
                        stats_for=self._stats.get((m.match_key, team_id)),
                        stats_opp=self._stats.get((m.match_key, opp_id)),
                        shots_for=[s for s in shots if s.team_id == team_id],
                        shots_opp=[s for s in shots if s.team_id != team_id],
                    )
                )
            if any(s.situation == "fast_break" for s in shots):
                t.has_fast_break = True
        return t

    # -- sampling
    def _sample(self, tms: list[_TM], cutoff: str | None) -> list[tuple[_TM, float]]:
        """(match, weight) pairs: whole season, or the last ROLLING_MATCHES strictly before ``cutoff``."""
        if cutoff is None:
            return [(tm, 1.0) for tm in tms]
        prior = [tm for tm in tms if tm.match.date < cutoff]
        recent = prior[-ROLLING_MATCHES:]
        n = len(recent)
        return [(tm, RECENCY_DECAY ** (n - 1 - i)) for i, tm in enumerate(recent)]

    def _team_metrics(self, table: _Table, team_id: str, cutoff: str | None) -> tuple[dict[str, float | None], list[_TM]]:
        sample = self._sample(table.by_team.get(team_id, []), cutoff)
        A: dict[str, float] = defaultdict(float)
        # an atom is only summed in matches where it is known; ratios pair atoms that are added together
        for tm, w in sample:
            for k, val in _atoms(tm).items():
                A[k] += w * val
        return _values(A, has_fast_break=table.has_fast_break), [tm for tm, _ in sample]

    def _cutoff(self, league: League, season: str, as_of_match: str | None) -> str | None:
        if as_of_match is None:
            return None
        date = _date_of(as_of_match, self._by_key)
        if date is None:
            raise ValueError(f"cannot determine the date of {as_of_match!r}")
        return date

    # -- public
    def league_styles(
        self, league: League, season: str, as_of_match: str | None = None
    ) -> dict[str, TeamStyle]:
        """Every team's style in the league-season, with percentiles/z-scores among them."""
        table = self._table(league, season)
        cutoff = self._cutoff(league, season, as_of_match)
        raw: dict[str, dict[str, float | None]] = {}
        used: dict[str, list[_TM]] = {}
        for team_id in sorted(table.by_team):
            raw[team_id], used[team_id] = self._team_metrics(table, team_id, cutoff)

        out: dict[str, TeamStyle] = {}
        stats_by_key = {key: _pct_z([raw[t][key] for t in raw]) for key in STYLE_KEYS}
        for i, team_id in enumerate(raw):
            tms = used[team_id]
            metrics: list[Metric] = []
            for key in STYLE_KEYS:
                value = raw[team_id][key]
                if key == "possession" and value is None:
                    continue
                pct, z = (None, None) if value is None else stats_by_key[key][i]
                metrics.append(
                    Metric(key=key, value=value, pct=pct, z=z, is_proxy=key in _PROXY, source=_source(key))
                )
            out[team_id] = TeamStyle(
                team_id=team_id,
                team_name=self._names.get(team_id, team_id),
                league=league,
                season=season,
                as_of_match=as_of_match,
                matches=len(tms),
                formations=_formations(tms),
                manager_id=_manager(tms),
                metrics=metrics,
            )
        return out

    def team_style(
        self, team_id: str, league: League, season: str, as_of_match: str | None = None
    ) -> TeamStyle:
        styles = self.league_styles(league, season, as_of_match)
        if team_id not in styles:
            raise KeyError(f"{team_id} has no matches in {league.value} {season}")
        return styles[team_id]


def _formations(tms: list[_TM]) -> list[FormationShare]:
    counts: dict[str, int] = defaultdict(int)
    for tm in tms:
        f = tm.match.home_formation if tm.is_home else tm.match.away_formation
        if f:
            counts[f] += 1
    total = sum(counts.values())
    return [
        FormationShare(formation=f, share=c / total)
        for f, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _manager(tms: list[_TM]) -> str | None:
    for tm in reversed(tms):
        mid = tm.match.home_manager_id if tm.is_home else tm.match.away_manager_id
        if mid:
            return mid
    return None


def _pct_z(values: Sequence[float | None]) -> list[tuple[float | None, float | None]]:
    """Mid-rank percentile (0..100) and population z-score of each value among the non-None ones."""
    present = [v for v in values if v is not None]
    n = len(present)
    if n == 0:
        return [(None, None)] * len(values)
    mean = sum(present) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in present) / n)
    out: list[tuple[float | None, float | None]] = []
    for v in values:
        if v is None:
            out.append((None, None))
            continue
        less = sum(p < v for p in present)
        equal = sum(p == v for p in present)
        pct = 100.0 * (less + 0.5 * equal) / n
        z = 0.0 if std < 1e-12 else (v - mean) / std
        out.append((pct, z))
    return out


# --------------------------------------------------------- module-level API


def league_styles(
    league: League,
    season: str,
    matches: Iterable[Match],
    stats: Iterable[TeamMatchStats] = (),
    shots: Iterable[Shot] = (),
    lineups: Iterable[LineupEntry] = (),
    as_of_match: str | None = None,
    names: Mapping[str, str] | None = None,
) -> dict[str, TeamStyle]:
    """Styles of all teams in a league-season (percentiles/z among them)."""
    return StyleBuilder(matches, stats, shots, lineups, names).league_styles(league, season, as_of_match)


def team_style(
    team_id: str,
    league: League,
    season: str,
    matches: Iterable[Match],
    stats: Iterable[TeamMatchStats] = (),
    shots: Iterable[Shot] = (),
    lineups: Iterable[LineupEntry] = (),
    as_of_match: str | None = None,
    names: Mapping[str, str] | None = None,
) -> TeamStyle:
    """One team's style. For many calls over the same tables build a ``StyleBuilder`` once."""
    return StyleBuilder(matches, stats, shots, lineups, names).team_style(team_id, league, season, as_of_match)
