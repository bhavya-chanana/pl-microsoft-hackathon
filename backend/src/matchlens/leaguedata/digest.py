"""Match digest: a compact, deterministic evidence pack for the match reasoner.

``build_digest`` turns one match of the curated tables into a ``Digest``: header,
shots (S01..), match events (E01..), xG timeline in 15-minute bins (T1..T6),
game-state segments (G1..), team stats, lineups, how this match differed from the
team's own pre-match form (``M:<metric>``) and a one-line mentality context. The
reasoning model may cite only those ids.

Blind mode (default) hides identities so the model cannot recall the real match:
clubs become ``{H}`` / ``{A}``, players ``{P:p3f2a1}`` and managers ``{M:m9c0de}``
(opaque, stable aliases of the ids, because our ids are name slugs), and the date
shrinks to its month. ``Digest.names`` maps every placeholder back to the real name
(``reasoner.restore_names``) and is never sent to the model.

What the model sees is ``Digest.payload()``; ``digest_hash`` is the sha256 of its
canonical JSON, so identical evidence gives an identical hash. Tables are sent as
``{"cols": [...], "rows": [[...]]}`` to keep the pack at about 3-6k tokens
(``estimate_tokens``: characters / 4). Anything the data cannot say is listed in
``limitations`` (no xG for the Champions League, no possession, no baseline, ...).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.schema import (
    TABLES,
    Club,
    League,
    LineupEntry,
    Manager,
    Match,
    MatchEvent,
    Player,
    Shot,
    TeamMatchStats,
)
from matchlens.leaguedata.style import BINS, STYLE_KEYS, StyleBuilder

log = logging.getLogger(__name__)

SIDES = ("H", "A")
MAX_TOKENS = 6500  # above this, low-value shots are dropped (see _XG_FLOORS)
_XG_FLOORS = (0.0, 0.03, 0.05, 0.08, 0.12)
MIN_PRIOR_MATCHES = 5  # mentality needs this many earlier matches of the team in the season
MAX_DELTAS = 6
MIN_DELTA_Z = 0.8
DELTA_KEYS = tuple(  # per-match metrics that mean something for a single match
    k for k in STYLE_KEYS
    if not re.fullmatch(r"xg_\d+_\d+", k) and not k.startswith(("xg90_", "shots90_"))
    and k not in {"sit_back_index", "elo_mean", "odds_win_prob_mean"}
)
FIXED_EVIDENCE = frozenset({"SUM", "ST"})  # match summary block and team-stats block

SHOT_COLS = ["id", "min", "side", "player", "xg", "sit", "act", "body", "res", "score"]
EVENT_COLS = ["id", "min", "side", "kind", "player", "related"]
SEGMENT_COLS = ["id", "from", "to", "score", "xgH", "xgA", "shotsH", "shotsA"]
LINEUP_COLS = ["player", "pos", "on", "off"]
STAT_FIELDS = (("xg", "xg"), ("npxg", "npxg"), ("shots", "shots"), ("shots_on_target", "sot"),
               ("corners", "corners"), ("fouls", "fouls"), ("yellows", "yellows"), ("reds", "reds"),
               ("ppda", "ppda"), ("deep", "deep"), ("possession", "possession"),
               ("xg_chain", "xg_chain"), ("xg_buildup", "xg_buildup"))
BODY = {"right_foot": "rf", "left_foot": "lf", "head": "head", "other": "other"}


class Digest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_key: str
    league: str
    season: str
    blind: bool
    home_id: str
    away_id: str
    score: str
    header: dict[str, Any]
    summary: dict[str, Any]
    shots: dict[str, Any] | None = None
    events: dict[str, Any] | None = None
    xg_timeline: dict[str, Any] | None = None
    segments: dict[str, Any] | None = None
    team_stats: dict[str, Any] | None = None
    lineups: dict[str, Any] | None = None
    style_delta: dict[str, Any] | None = None
    mentality: dict[str, str] | None = None
    limitations: list[str]
    names: dict[str, str]  # placeholder -> real name (blind mode only); never sent to the model
    ids: dict[str, str]  # placeholder -> real player / manager id (blind mode only)
    digest_hash: str

    # ------------------------------------------------------------ what the model sees
    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"blind": self.blind, "header": self.header, "summary": self.summary}
        for key in ("shots", "events", "xg_timeline", "segments", "team_stats", "lineups", "style_delta",
                    "mentality"):
            value = getattr(self, key)
            if value:
                out[key] = value
        out["limitations"] = self.limitations
        return out

    # ------------------------------------------------------------ evidence index
    def _rows(self, table: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not table:
            return []
        cols = table["cols"]
        return [dict(zip(cols, row, strict=True)) for row in table["rows"]]

    def shot_rows(self) -> dict[str, dict[str, Any]]:
        return {r["id"]: r for r in self._rows(self.shots)}

    def event_rows(self) -> dict[str, dict[str, Any]]:
        return {r["id"]: r for r in self._rows(self.events)}

    def segment_rows(self) -> dict[str, dict[str, Any]]:
        return {r["id"]: r for r in self._rows(self.segments)}

    def metric_keys(self, side: str) -> set[str]:
        return set((self.style_delta or {}).get(side, {}))

    def evidence_ids(self) -> set[str]:
        ids = set(FIXED_EVIDENCE) | set(self.shot_rows()) | set(self.event_rows()) | set(self.segment_rows())
        if self.xg_timeline:
            ids |= {f"T{i + 1}" for i in range(len(self.xg_timeline["bins"]))}
        for side in SIDES:
            ids |= self.metric_keys(side)
        return ids

    def placeholders(self) -> set[str]:
        return ({"{H}", "{A}"} | set(self.names)) if self.blind else set()


def estimate_tokens(digest: Digest) -> int:
    """Rough token count of what the model receives (characters / 4)."""
    return math.ceil(len(_canonical(digest.payload())) / 4)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ------------------------------------------------------------------ tables


@dataclass
class Tables:
    """Curated rows with the indexes the digest needs."""

    matches: list[Match]
    lineups: list[LineupEntry] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)
    events: list[MatchEvent] = field(default_factory=list)
    stats: list[TeamMatchStats] = field(default_factory=list)
    players: list[Player] = field(default_factory=list)
    clubs: list[Club] = field(default_factory=list)
    managers: list[Manager] = field(default_factory=list)
    source: str = "memory"

    def __post_init__(self) -> None:
        self.matches = sorted(self.matches, key=lambda m: (m.date, m.match_key))
        self._by_key = {m.match_key: m for m in self.matches}
        self._lineups: dict[str, list[LineupEntry]] = defaultdict(list)
        for row in self.lineups:
            self._lineups[row.match_key].append(row)
        self._shots: dict[str, list[Shot]] = defaultdict(list)
        for s in self.shots:
            self._shots[s.match_key].append(s)
        self._events: dict[str, list[MatchEvent]] = defaultdict(list)
        for e in self.events:
            self._events[e.match_key].append(e)
        self._stats = {(s.match_key, s.team_id): s for s in self.stats}
        self._players = {p.player_id: p for p in self.players}
        self._clubs = {c.team_id: c for c in self.clubs}
        self._managers = {m.manager_id: m for m in self.managers}
        self._by_season: dict[tuple[str, str], list[Match]] = defaultdict(list)
        self._by_team_season: dict[tuple[str, str], list[Match]] = defaultdict(list)
        for m in self.matches:
            self._by_season[(m.league.value, m.season)].append(m)
            for team in (m.home_id, m.away_id):
                self._by_team_season[(team, m.season)].append(m)
        self._builder: StyleBuilder | None = None

    # -- loading
    @classmethod
    def load(cls, cfg: Config) -> Tables:
        """The newest curated dataset (``curated/v<N>``) when present, else the build tables."""
        try:
            from matchlens.leaguedata import curate

            curate.latest(cfg)

            def read(name: str) -> list[Any]:
                return curate.read_curated(cfg, name)

            source = "curated"
        except FileNotFoundError:
            from matchlens.leaguedata import build

            def read(name: str) -> list[Any]:
                return build.rows(cfg, name)

            source = "tables"
        names = {"matches": "matches", "lineups": "lineups", "shots": "shots", "events": "match_events",
                 "stats": "team_match_stats", "players": "players", "clubs": "clubs", "managers": "managers"}
        assert set(names.values()) <= set(TABLES)
        return cls(**{attr: read(table) for attr, table in names.items()}, source=source)

    # -- lookups
    def match(self, key: str) -> Match:
        try:
            return self._by_key[key]
        except KeyError:
            raise KeyError(f"unknown match {key!r}") from None

    def lineup_of(self, key: str) -> list[LineupEntry]:
        return self._lineups.get(key, [])

    def shots_of(self, key: str) -> list[Shot]:
        return self._shots.get(key, [])

    def events_of(self, key: str) -> list[MatchEvent]:
        return self._events.get(key, [])

    def stats_of(self, key: str, team_id: str) -> TeamMatchStats | None:
        return self._stats.get((key, team_id))

    def team_name(self, team_id: str) -> str:
        club = self._clubs.get(team_id)
        return club.name if club else team_id

    def player_name(self, player_id: str) -> str:
        p = self._players.get(player_id)
        return p.name if p else player_id

    def manager_name(self, manager_id: str) -> str:
        m = self._managers.get(manager_id)
        return m.name if m else manager_id

    def prior_league_matches(self, m: Match) -> list[Match]:
        """Same-season matches of the match's competition strictly before its date."""
        return [x for x in self._by_season[(m.league.value, m.season)] if x.date < m.date]

    def prior_team_matches(self, team_id: str, m: Match) -> list[Match]:
        return [x for x in self._by_team_season.get((team_id, m.season), []) if x.date < m.date]

    def style_builder(self) -> StyleBuilder:
        if self._builder is None:
            self._builder = StyleBuilder(
                self.matches, self.stats, self.shots, self.lineups,
                {c.team_id: c.name for c in self.clubs},
            )
        return self._builder

    def match_keys(self, leagues: Iterable[League | str] | None = None,
                   seasons: Iterable[str] | None = None, limit: int | None = None) -> list[str]:
        """Match keys in date order; ``limit`` caps each league-season."""
        want_l = {League(x) for x in leagues} if leagues else None
        want_s = set(seasons) if seasons else None
        out: list[str] = []
        for (league, season), ms in sorted(self._by_season.items()):
            if want_l is not None and League(league) not in want_l:
                continue
            if want_s is not None and season not in want_s:
                continue
            out.extend(m.match_key for m in (ms[:limit] if limit else ms))
        return out


# ------------------------------------------------------------------ labels


class _Labels:
    """Blind placeholders (or real names) for clubs, players and managers of one match."""

    def __init__(self, tables: Tables, m: Match, blind: bool) -> None:
        self.t, self.m, self.blind = tables, m, blind
        self.names: dict[str, str] = {}
        self.ids: dict[str, str] = {}
        self.club = {m.home_id: "{H}" if blind else tables.team_name(m.home_id),
                     m.away_id: "{A}" if blind else tables.team_name(m.away_id)}
        if blind:
            self.names["{H}"] = tables.team_name(m.home_id)
            self.names["{A}"] = tables.team_name(m.away_id)

    @staticmethod
    def _alias(prefix: str, ident: str) -> str:
        return prefix + hashlib.sha1(f"{prefix}:{ident}".encode()).hexdigest()[:6]

    def player(self, player_id: str | None) -> str | None:
        if not player_id:
            return None
        name = self.t.player_name(player_id)
        if not self.blind:
            return name
        label = "{P:%s}" % self._alias("p", player_id)
        self.names[label] = name
        self.ids[label] = player_id
        return label

    def manager(self, manager_id: str | None) -> str | None:
        if not manager_id:
            return None
        name = self.t.manager_name(manager_id)
        if not self.blind:
            return name
        label = "{M:%s}" % self._alias("m", manager_id)
        self.names[label] = name
        self.ids[label] = manager_id
        return label

    def side(self, side: str) -> str:
        return self.club[self.m.home_id if side == "H" else self.m.away_id]


# ------------------------------------------------------------------ goals timeline


def _goal_timeline(m: Match, shots: Sequence[Shot], events: Sequence[MatchEvent]
                   ) -> tuple[list[tuple[int, str, str]], str, dict[str, str]] | None:
    """Goals as ``(minute, "H"|"A", ref)`` that add up to the 90-minute or the after-extra-time score.

    Returns (goals, source, own-goal beneficiary by ref). An own-goal event's ``team_id`` is the team
    that benefits; for shot rows the providers differ, so both readings are tried, as is dropping extra-time goals when the stored score
    is the 90-minute one. None when nothing adds up.
    """
    targets = [(m.home_goals, m.away_goals)]
    if m.home_goals_aet is not None and m.away_goals_aet is not None:
        targets.append((m.home_goals_aet, m.away_goals_aet))
    target = targets[0]
    shot_goals = [(s.minute, s.team_id, s.result == "own_goal", s.shot_id)
                  for s in sorted(shots, key=lambda s: (s.minute, s.shot_id)) if s.result in ("goal", "own_goal")]
    event_goals = [(e.minute, e.team_id, e.kind == "own_goal", e.event_id)
                   for e in sorted(events, key=lambda e: (e.minute, e.event_id))
                   if e.kind in ("goal", "penalty_goal", "own_goal")]
    for source, goals in (("shots", shot_goals), ("events", event_goals)):
        if not goals and (0, 0) not in targets:
            continue
        for flip, cut in itertools.product((False, True), repeat=2):
            seq: list[tuple[int, str, str]] = []
            og: dict[str, str] = {}
            for minute, team, own, ref in goals:
                if cut and minute > 90:
                    continue
                side = "H" if team == m.home_id else "A"
                if own:  # the row's team is the scorer's or the beneficiary, depending on the provider
                    side = ("A" if side == "H" else "H") if flip else side
                    og[ref] = side
                seq.append((minute, side, ref))
            if (sum(s == "H" for _, s, _ in seq), sum(s == "A" for _, s, _ in seq)) in targets:
                return seq, source, og
    return None


# ------------------------------------------------------------------ build


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def _bin_index(minute: int) -> int:
    for i, (lo, hi) in enumerate(BINS):
        if lo <= minute < hi:
            return i
    return len(BINS) - 1


def _style_delta(tables: Tables, builder: StyleBuilder, m: Match, labels: _Labels
                 ) -> tuple[dict[str, dict[str, list[float]]], list[str]]:
    """This match's metrics against each team's pre-match form: ``{side: {"M:key": [match, form, z]}}``.

    z = (this match - pre-match form) / spread of that form among the league-season's teams, so it
    is "how unusual was this for them", not a significance test.
    """
    limits: list[str] = []
    try:
        baseline = builder.league_styles(m.league, m.season, as_of_match=m.match_key)
    except (KeyError, ValueError) as exc:
        log.debug("no baseline for %s: %s", m.match_key, exc)
        return {}, ["No pre-match form baseline for this match."]
    single = StyleBuilder(
        [m], [s for s in (tables.stats_of(m.match_key, m.home_id), tables.stats_of(m.match_key, m.away_id)) if s],
        tables.shots_of(m.match_key),
    ).league_styles(m.league, m.season)
    spread: dict[str, float] = {}
    for key in DELTA_KEYS:
        vals = [mt.value for ts in baseline.values() if (mt := ts.metric(key)) is not None and mt.value is not None]
        if len(vals) >= 3:
            sd = statistics.pstdev(vals)
            if sd > 1e-9:
                spread[key] = sd
    out: dict[str, dict[str, list[float]]] = {}
    for side, team in (("H", m.home_id), ("A", m.away_id)):
        base, now = baseline.get(team), single.get(team)
        if base is None or now is None or base.matches == 0:
            limits.append(f"No pre-match form baseline for {labels.side(side)}.")
            continue
        scored: list[tuple[float, str, float, float]] = []
        for key, sd in spread.items():
            mb, mn = base.metric(key), now.metric(key)
            if mb is None or mn is None or mb.value is None or mn.value is None:
                continue
            z = (mn.value - mb.value) / sd
            if abs(z) >= MIN_DELTA_Z:
                scored.append((abs(z), key, mn.value, mb.value))
        scored.sort(key=lambda t: (-t[0], t[1]))
        out[side] = {
            f"M:{key}": [_r(v, 3), _r(b, 3), _r((v - b) / spread[key], 1)]
            for _, key, v, b in scored[:MAX_DELTAS]
        }
    return out, limits


def _mentality(tables: Tables, m: Match, labels: _Labels) -> dict[str, str] | None:
    try:
        from matchlens.leaguedata.mentality import compute_mentality
    except ImportError:
        return None
    prior_league = tables.prior_league_matches(m)
    out: dict[str, str] = {}
    for side, team in (("H", m.home_id), ("A", m.away_id)):
        mine = tables.prior_team_matches(team, m)
        if len(mine) < MIN_PRIOR_MATCHES:
            continue
        keys = {x.match_key for x in mine}
        try:
            ment = compute_mentality(
                team, m.season, prior_league + [x for x in mine if x.league != m.league],
                [e for k in keys for e in tables.events_of(k)],
                [s for k in keys for s in tables.shots_of(k)],
                [s for k in keys if (s := tables.stats_of(k, team)) is not None],
                league=None if m.league == League.UCL else m.league,
            )
        except Exception as exc:  # noqa: BLE001 - mentality is optional context
            log.debug("mentality failed for %s: %s", team, exc)
            continue
        if ment.notes:
            out[side] = f"before this match ({ment.matches} played): " + "; ".join(ment.notes[:3])
    return out or None


def build_digest(match_key: str, tables: Tables, style_builder: StyleBuilder | None = None, *,
                 blind: bool = True) -> Digest:
    """The evidence pack of one match (see the module docstring)."""
    builder = style_builder or tables.style_builder()
    digest = _build(match_key, tables, builder, blind, 0.0)
    for floor in _XG_FLOORS[1:]:
        if estimate_tokens(digest) <= MAX_TOKENS:
            break
        digest = _build(match_key, tables, builder, blind, floor)
    return digest


def _build(match_key: str, tables: Tables, builder: StyleBuilder, blind: bool, xg_floor: float) -> Digest:
    m = tables.match(match_key)
    labels = _Labels(tables, m, blind)
    limits: list[str] = []
    shots_all = [s for s in tables.shots_of(match_key) if s.team_id in (m.home_id, m.away_id)]
    events_all = [e for e in tables.events_of(match_key) if e.team_id in (m.home_id, m.away_id)]
    has_shots = bool(shots_all)
    side_of = {m.home_id: "H", m.away_id: "A"}

    # ---- goals timeline and shot sides
    timeline = _goal_timeline(m, shots_all, events_all)
    og_side = timeline[2] if timeline else {}
    goals = timeline[0] if timeline else None
    goal_source = timeline[1] if timeline else None

    def shot_side(s: Shot) -> str:
        return og_side.get(s.shot_id) or side_of[s.team_id]

    # ---- shots table
    ordered = sorted(shots_all, key=lambda s: (s.minute, s.shot_id))
    kept = [s for s in ordered if s.xg >= xg_floor or s.result in ("goal", "own_goal") or s.situation == "penalty"]
    if len(kept) < len(ordered):
        limits.append(f"{len(ordered) - len(kept)} shots under {xg_floor:g} xG are omitted from the shots table "
                      "(totals still count them).")
    shot_id: dict[str, str] = {}
    shot_rows: list[list[Any]] = []
    for i, s in enumerate(kept, 1):
        sid = f"S{i:02d}"
        shot_id[s.shot_id] = sid
        shot_rows.append([sid, s.minute, shot_side(s), labels.player(s.player_id), _r(s.xg), s.situation,
                          s.last_action or "", BODY.get(s.body_part, s.body_part), s.result,
                          f"{s.home_score_before}-{s.away_score_before}"])

    # ---- events table (cards, subs; goals only when the shots do not carry them)
    event_rows: list[list[Any]] = []
    wanted = {"yellow", "second_yellow", "red", "sub"}
    if goal_source != "shots":
        wanted |= {"goal", "penalty_goal", "own_goal"}
    event_id: dict[str, str] = {}
    n = 0
    for e in sorted(events_all, key=lambda e: (e.minute, e.event_id)):
        if e.kind not in wanted:
            continue
        n += 1
        eid = f"E{n:02d}"
        event_id[e.event_id] = eid
        side = side_of[e.team_id]
        if e.kind == "own_goal" and goals is not None:
            side = next((g[1] for g in goals if g[2] == e.event_id), side)
        event_rows.append([eid, e.minute, side, e.kind, labels.player(e.player_id),
                           labels.player(e.related_player_id)])

    # ---- xG per side
    real = [s for s in shots_all if s.result != "own_goal"]
    xg_side = {sd: sum(s.xg for s in real if side_of[s.team_id] == sd) for sd in SIDES}
    has_xg = has_shots or (m.home_xg is not None and m.away_xg is not None)
    if not has_shots and m.home_xg is not None and m.away_xg is not None:
        xg_side = {"H": m.home_xg, "A": m.away_xg}
    if not has_xg:
        limits.append("No xG or shot data for this match: judge quality only from the score, cards and "
                      "team statistics.")
    elif not has_shots:
        limits.append("No shot-level data: xG totals only, no shot map, timeline or game-state split.")

    # ---- summary
    summary: dict[str, Any] = {"score": f"{m.home_goals}-{m.away_goals}"}
    if m.home_goals_ht is not None and m.away_goals_ht is not None:
        summary["ht"] = f"{m.home_goals_ht}-{m.away_goals_ht}"
    if m.home_goals_aet is not None and m.away_goals_aet is not None:
        summary["aet"] = f"{m.home_goals_aet}-{m.away_goals_aet}"
    if m.home_pens is not None and m.away_pens is not None:
        summary["pens"] = f"{m.home_pens}-{m.away_pens}"
    if has_xg:
        summary["xg"] = {sd: _r(xg_side[sd]) for sd in SIDES}
        summary["xg_diff"] = _r(xg_side["H"] - xg_side["A"])
    if has_shots:
        summary["shots"] = {sd: sum(side_of[s.team_id] == sd for s in real) for sd in SIDES}
        summary["by_situation"] = {
            sd: {sit: [sum(1 for s in real if side_of[s.team_id] == sd and s.situation == sit),
                       _r(sum(s.xg for s in real if side_of[s.team_id] == sd and s.situation == sit))]
                 for sit in sorted({s.situation for s in real if side_of[s.team_id] == sd})}
            for sd in SIDES
        }

    # ---- xG timeline
    xg_timeline: dict[str, Any] | None = None
    if has_shots:
        per = {sd: [0.0] * len(BINS) for sd in SIDES}
        for s in real:
            per[side_of[s.team_id]][_bin_index(s.minute)] += s.xg
        diff = [per["H"][i] - per["A"][i] for i in range(len(BINS))]
        cum = {sd: list(itertools.accumulate(per[sd])) for sd in SIDES}
        xg_timeline = {
            "bins": [f"{lo}-{hi}" for lo, hi in BINS],
            "H": [_r(v) for v in per["H"]], "A": [_r(v) for v in per["A"]],
            "diff": [_r(v) for v in diff],
            "cumH": [_r(v) for v in cum["H"]], "cumA": [_r(v) for v in cum["A"]],
        }

    # ---- game-state segments
    segments: dict[str, Any] | None = None
    if goals is None and (m.home_goals + m.away_goals) > 0:
        limits.append("The goal timeline does not add up to the final score: no game-state segments.")
    elif goals is not None:
        ordered_goals = sorted(goals, key=lambda g: g[0])
        edges = [0] + [g[0] for g in ordered_goals]
        last = max([90] + [s.minute for s in shots_all] + [e.minute for e in events_all])
        score = [0, 0]
        seg_rows: list[list[Any]] = []
        seg_by_score: dict[tuple[int, int], int] = {}
        for i in range(len(ordered_goals) + 1):
            lo, hi = edges[i], (edges[i + 1] if i < len(ordered_goals) else last)
            seg_by_score[(score[0], score[1])] = len(seg_rows)
            row: list[Any] = [f"G{len(seg_rows) + 1}", lo, hi, f"{score[0]}-{score[1]}"]
            seg_rows.append(row)
            if i < len(ordered_goals):
                score[0 if ordered_goals[i][1] == "H" else 1] += 1
        if has_shots:
            acc = [[0.0, 0.0, 0, 0] for _ in seg_rows]
            for s in real:
                idx = seg_by_score.get((s.home_score_before, s.away_score_before))
                if idx is None:
                    idx = max((i for i, r in enumerate(seg_rows) if r[1] <= s.minute), default=0)
                k = 0 if side_of[s.team_id] == "H" else 1
                acc[idx][k] += s.xg
                acc[idx][2 + k] += 1
            for row, (xh, xa, nh, na) in zip(seg_rows, acc, strict=True):
                row += [_r(xh), _r(xa), nh, na]
            cols = SEGMENT_COLS
        else:
            cols = SEGMENT_COLS[:4]
        if len(seg_rows) > 1 or has_shots:
            segments = {"cols": cols, "rows": seg_rows}

    # ---- team stats
    team_stats: dict[str, Any] = {}
    for sd, team in (("H", m.home_id), ("A", m.away_id)):
        st = tables.stats_of(match_key, team)
        vals: dict[str, Any] = {}
        if st is not None:
            for attr, key in STAT_FIELDS:
                v = getattr(st, attr)
                if v is not None:
                    vals[key] = _r(v, 2) if isinstance(v, float) else v
        if has_shots:
            vals.setdefault("shots", summary["shots"][sd])
            vals.setdefault("sot", sum(1 for s in real if side_of[s.team_id] == sd and s.result in ("goal", "saved")))
            vals.setdefault("xg", _r(xg_side[sd]))
        if vals:
            team_stats[sd] = vals
    if not any("possession" in v for v in team_stats.values()):
        limits.append("No possession data.")
    if not any("ppda" in v for v in team_stats.values()):
        limits.append("No pressing (PPDA) data for this match.")

    # ---- lineups
    lineup_rows = tables.lineup_of(match_key)
    lineups: dict[str, Any] | None = None
    if lineup_rows:
        lineups = {}
        for sd, team in (("H", m.home_id), ("A", m.away_id)):
            mine = [r for r in lineup_rows if r.team_id == team and (r.starter or r.minute_on > 0)]
            mine.sort(key=lambda r: (not r.starter, r.minute_on, r.player_id))
            lineups[sd] = {
                "cols": LINEUP_COLS,
                "rows": [[labels.player(r.player_id), r.position or "", r.minute_on, r.minute_off] for r in mine],
            }
    else:
        limits.append("No lineups.")

    # ---- header
    header: dict[str, Any] = {"league": m.league.value, "season": m.season}
    if m.stage:
        header["stage"] = m.stage
    if blind:
        header["month"] = m.date[:7]  # the exact date would help recall the real match
    else:
        header["date"] = m.date
    header["home"], header["away"] = labels.side("H"), labels.side("A")
    formations = {sd: f for sd, f in (("H", m.home_formation), ("A", m.away_formation)) if f}
    if formations:
        header["formation"] = formations
    else:
        limits.append("No formation data.")
    managers = {sd: labels.manager(mid) for sd, mid in (("H", m.home_manager_id), ("A", m.away_manager_id)) if mid}
    if managers:
        header["manager"] = managers
    if m.home_elo is not None and m.away_elo is not None:
        header["elo"] = {"H": round(m.home_elo), "A": round(m.away_elo)}
    if m.p_home is not None and m.p_draw is not None and m.p_away is not None:
        header["odds_p"] = {"H": _r(m.p_home), "D": _r(m.p_draw), "A": _r(m.p_away)}
    if m.referee and not blind:
        header["referee"] = m.referee

    # ---- style delta and mentality
    style_delta, delta_limits = _style_delta(tables, builder, m, labels)
    limits += delta_limits
    mentality = _mentality(tables, m, labels)

    digest = Digest(
        match_key=match_key, league=m.league.value, season=m.season, blind=blind,
        home_id=m.home_id, away_id=m.away_id, score=summary["score"],
        header=header, summary=summary,
        shots={"cols": SHOT_COLS, "rows": shot_rows} if shot_rows else None,
        events={"cols": EVENT_COLS, "rows": event_rows} if event_rows else None,
        xg_timeline=xg_timeline, segments=segments, team_stats=team_stats or None, lineups=lineups,
        style_delta=style_delta or None, mentality=mentality,
        limitations=sorted(set(limits)), names=labels.names, ids=labels.ids, digest_hash="",
    )
    return digest.model_copy(update={"digest_hash": hashlib.sha256(_canonical(digest.payload()).encode()).hexdigest()})
