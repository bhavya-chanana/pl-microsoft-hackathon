"""Player profiles: per-90 production, shot profile, age, value and heuristic role tags.

Per-90 metrics need ``MIN_MINUTES`` (450) in the league-season, otherwise their
values are ``None``. Percentiles are taken among all players of the same
league-season who reach that minutes threshold (all positions together).

Role tags (heuristic; the thresholds are deliberately simple and documented):

==================  ================================================================
poacher             >= 70% of non-penalty shots inside the box AND npxg90 in the top
                    quartile (pct >= 75) of the league-season
creator             xa90 or key_passes90 in the top quartile (pct >= 75)
ball-progressor     xg_buildup90 in the top quintile (pct >= 80)
aerial threat       >= 25% of non-penalty shots are headers, from >= 8 such shots
long-range shooter  >= 50% of non-penalty shots outside the box, from >= 10 shots
set-piece taker     >= 3 direct free-kick shots in the season
==================  ================================================================

Shot-profile tags (aerial, long-range, set-piece) need no minutes threshold,
only the shot counts above; percentile-based tags (poacher, creator,
ball-progressor) need a player who reaches ``MIN_MINUTES``.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date

from matchlens.leaguedata.contracts import Metric, PlayerProfile
from matchlens.leaguedata.schema import (
    League,
    LineupEntry,
    Player,
    PlayerMatch,
    PlayerValuation,
    Shot,
    SourceIds,
)
from matchlens.leaguedata.style import BOX_X, BOX_Y0, BOX_Y1, SITUATIONS, _norm_action, _pct_z

MIN_MINUTES = 450

TOP_QUARTILE = 75.0
TOP_QUINTILE = 80.0
POACHER_BOX_SHARE = 0.70
AERIAL_HEADER_SHARE, AERIAL_MIN_SHOTS = 0.25, 8
LONG_RANGE_OUTSIDE_SHARE, LONG_RANGE_MIN_SHOTS = 0.50, 10
SET_PIECE_MIN_DFK = 3

PER90_KEYS = ("npxg90", "xa90", "shots90", "key_passes90", "xg_chain90", "xg_buildup90")


def season_start(season: str) -> date:
    return date(int(season[:4]), 8, 1)


def season_end(season: str) -> date:
    return date(int(season[:4]) + 1, 6, 30)


def _per90(total: float | None, minutes: int) -> float | None:
    return None if total is None or minutes <= 0 else total / minutes * 90.0


class _Stats:
    """One player's season totals within one league-season."""

    def __init__(self) -> None:
        self.minutes = 0
        self.xg_np: float | None = None
        self.xa: float | None = None
        self.shots = 0
        self.key_passes = 0
        self.xg_chain: float | None = None
        self.xg_buildup: float | None = None
        self.np_shots: list[Shot] = []
        self.team_minutes: Counter[str] = Counter()

    def per90(self) -> dict[str, float | None]:
        if self.minutes < MIN_MINUTES:
            return dict.fromkeys(PER90_KEYS)
        return {
            "npxg90": _per90(self.xg_np, self.minutes),
            "xa90": _per90(self.xa, self.minutes),
            "shots90": _per90(float(self.shots), self.minutes),
            "key_passes90": _per90(float(self.key_passes), self.minutes),
            "xg_chain90": _per90(self.xg_chain, self.minutes),
            "xg_buildup90": _per90(self.xg_buildup, self.minutes),
        }


def _add(cur: float | None, x: float | None) -> float | None:
    if x is None:
        return cur
    return x if cur is None else cur + x


def _shot_profile(shots: Sequence[Shot]) -> dict[str, float | None]:
    n = len(shots)
    out: dict[str, float | None] = {}
    if n == 0:
        for k in ("box_shot_share", "header_share", "xg_per_shot", "shot_distance_mean"):
            out[k] = None
    else:
        out["box_shot_share"] = sum(s.x >= BOX_X and BOX_Y0 <= s.y <= BOX_Y1 for s in shots) / n
        out["header_share"] = sum(s.body_part == "head" for s in shots) / n
        out["xg_per_shot"] = sum(s.xg for s in shots) / n
        out["shot_distance_mean"] = sum(math.hypot(105.0 - s.x, 34.0 - s.y) for s in shots) / n
    return out


class ProfileBuilder:
    """Indexes the tables once; reuse it for many ``player_profile`` calls."""

    def __init__(
        self,
        player_matches: Iterable[PlayerMatch],
        shots: Iterable[Shot],
        lineups: Iterable[LineupEntry],
        players: Iterable[Player],
        valuations: Iterable[PlayerValuation],
    ) -> None:
        self._pm: dict[str, list[PlayerMatch]] = defaultdict(list)
        for r in player_matches:
            self._pm[r.player_id].append(r)
        self._shots: dict[str, list[Shot]] = defaultdict(list)
        for s in shots:
            self._shots[s.player_id].append(s)
        self._lineups: dict[str, list[LineupEntry]] = defaultdict(list)
        for e in lineups:
            self._lineups[e.player_id].append(e)
        self._players = {p.player_id: p for p in players}
        self._values: dict[str, list[PlayerValuation]] = defaultdict(list)
        for v in valuations:
            self._values[v.player_id].append(v)
        self._peers: dict[tuple[League, str], dict[str, _Stats]] = {}
        self._pct: dict[tuple[League, str], dict[str, dict[str, float | None]]] = {}

    # -- aggregation
    @staticmethod
    def _prefix(league: League, season: str) -> str:
        return f"{league.value}:{season}:"

    def _stats(self, player_id: str, league: League, season: str, team_id: str | None = None) -> _Stats:
        prefix = self._prefix(league, season)
        st = _Stats()
        keys: set[str] = set()
        shot_xg_np: float | None = None
        for r in self._pm.get(player_id, []):
            if not r.match_key.startswith(prefix) or (team_id is not None and r.team_id != team_id):
                continue
            keys.add(r.match_key)
            st.minutes += r.minutes
            st.team_minutes[r.team_id] += r.minutes
            st.xa = _add(st.xa, r.xa)
            st.shots += r.shots
            st.key_passes += r.key_passes
            st.xg_chain = _add(st.xg_chain, r.xg_chain)
            st.xg_buildup = _add(st.xg_buildup, r.xg_buildup)
            st.xg_np = _add(st.xg_np, r.xg)  # replaced below when shot rows exist
        for s in self._shots.get(player_id, []):
            if s.match_key in keys or (not keys and s.match_key.startswith(prefix)):
                if s.result == "own_goal":
                    continue
                if team_id is not None and s.team_id != team_id:
                    continue
                if s.situation != "penalty":
                    st.np_shots.append(s)
                    shot_xg_np = (shot_xg_np or 0.0) + s.xg
        if keys and st.np_shots:
            # npxG from the shot table: player_matches.xg includes penalties
            st.xg_np = shot_xg_np
        elif keys and st.xg_np is not None and not st.np_shots:
            pass  # only the (penalty-inclusive) match totals exist
        return st

    def _peer_stats(self, league: League, season: str) -> dict[str, _Stats]:
        key = (league, season)
        if key not in self._peers:
            prefix = self._prefix(league, season)
            ids = [pid for pid, rows in self._pm.items() if any(r.match_key.startswith(prefix) for r in rows)]
            stats = {pid: self._stats(pid, league, season) for pid in sorted(ids)}
            self._peers[key] = {pid: st for pid, st in stats.items() if st.minutes >= MIN_MINUTES}
        return self._peers[key]

    def _percentiles(self, league: League, season: str) -> dict[str, dict[str, float | None]]:
        """metric key -> {player_id: percentile} among the eligible peers."""
        key = (league, season)
        if key not in self._pct:
            peers = self._peer_stats(league, season)
            ids = list(peers)
            table: dict[str, dict[str, float | None]] = {}
            per90 = {pid: st.per90() for pid, st in peers.items()}
            for k in PER90_KEYS:
                scored = _pct_z([per90[pid][k] for pid in ids])
                table[k] = {pid: scored[i][0] for i, pid in enumerate(ids)}
            self._pct[key] = table
        return self._pct[key]

    def _z(self, league: League, season: str, key: str) -> dict[str, float | None]:
        peers = self._peer_stats(league, season)
        ids = list(peers)
        scored = _pct_z([peers[pid].per90()[key] for pid in ids])
        return {pid: scored[i][1] for i, pid in enumerate(ids)}

    # -- profile
    def player_profile(self, player_id: str, team_id: str, league: League, season: str) -> PlayerProfile:
        st = self._stats(player_id, league, season, team_id)
        player = self._players.get(player_id)
        per90 = st.per90()
        eligible = st.minutes >= MIN_MINUTES
        pcts = self._percentiles(league, season) if eligible else {}
        zs = {k: self._z(league, season, k) for k in PER90_KEYS} if eligible else {}

        per90_metrics = [
            Metric(
                key=k,
                value=per90[k],
                pct=pcts[k].get(player_id) if eligible and per90[k] is not None else None,
                z=zs[k].get(player_id) if eligible and per90[k] is not None else None,
                is_proxy=k in ("xg_chain90", "xg_buildup90"),
                source="understat",
            )
            for k in PER90_KEYS
        ]

        prof = _shot_profile(st.np_shots)
        n_np = len(st.np_shots)
        shot_metrics = [Metric(key=k, value=v, source="understat") for k, v in prof.items()]
        shot_metrics.append(Metric(key="np_shots", value=float(n_np), source="understat"))
        all_season_shots = [
            s for s in self._shots.get(player_id, [])
            if s.match_key.startswith(self._prefix(league, season)) and s.team_id == team_id and s.result != "own_goal"
        ]
        for sit in SITUATIONS:
            c = sum(s.situation == sit for s in all_season_shots)
            shot_metrics.append(
                Metric(
                    key=f"shot_share_{sit}",
                    value=c / len(all_season_shots) if all_season_shots else None,
                    source="understat",
                )
            )
        dfk = sum(s.situation == "direct_free_kick" for s in all_season_shots)
        shot_metrics.append(Metric(key="direct_free_kick_shots", value=float(dfk), source="understat"))
        creation = Counter(_norm_action(s.last_action) for s in st.np_shots)
        for kind in ("cross", "through_ball", "take_on", "rebound"):
            shot_metrics.append(
                Metric(key=f"create_{kind}", value=creation[kind] / n_np if n_np else None, source="understat")
            )

        tags = _role_tags(prof, n_np, dfk, per90_metrics if eligible else [])

        return PlayerProfile(
            player_id=player_id,
            name=player.name if player else player_id,
            ids=player.source_ids if player else SourceIds(),
            team_id=team_id,
            season=season,
            league=league,
            positions=self._positions(player, player_id, league, season, team_id),
            minutes=st.minutes,
            age=self._age(player, season),
            height_cm=player.height_cm if player else None,
            foot=player.foot if player else None,
            market_value_eur=self._value(player_id, season),
            per90=per90_metrics,
            shot_profile=shot_metrics,
            role_tags=tags,
        )

    def league_profiles(self, league: League, season: str) -> list[PlayerProfile]:
        """Profiles for every (player, team) pair with minutes in the league-season."""
        prefix = self._prefix(league, season)
        pairs = sorted(
            {(r.player_id, r.team_id) for rows in self._pm.values() for r in rows if r.match_key.startswith(prefix)}
        )
        return [self.player_profile(pid, tid, league, season) for pid, tid in pairs]

    def _positions(self, player: Player | None, player_id: str, league: League, season: str, team_id: str) -> list[str]:
        prefix = self._prefix(league, season)
        counts = Counter(
            e.position
            for e in self._lineups.get(player_id, [])
            if e.match_key.startswith(prefix) and e.team_id == team_id and e.position
        )
        positions = [p for p, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        if not positions and player and player.main_position:
            positions = [player.main_position]
        return positions

    @staticmethod
    def _age(player: Player | None, season: str) -> float | None:
        if player is None or not player.birth_date:
            return None
        try:
            born = date.fromisoformat(player.birth_date[:10])
        except ValueError:
            return None
        return round((season_start(season) - born).days / 365.25, 2)

    def _value(self, player_id: str, season: str) -> int | None:
        """Latest market value on or before the end of the season (no look-ahead)."""
        end = season_end(season).isoformat()
        known = [v for v in self._values.get(player_id, []) if v.date <= end]
        return max(known, key=lambda v: v.date).market_value_eur if known else None


def _role_tags(prof: dict[str, float | None], n_np: int, dfk: int, per90: Sequence[Metric]) -> list[str]:
    pct = {m.key: m.pct for m in per90}
    box, head = prof.get("box_shot_share"), prof.get("header_share")
    tags: list[str] = []
    if box is not None and box >= POACHER_BOX_SHARE and (pct.get("npxg90") or 0.0) >= TOP_QUARTILE:
        tags.append("poacher")
    if (pct.get("xa90") or 0.0) >= TOP_QUARTILE or (pct.get("key_passes90") or 0.0) >= TOP_QUARTILE:
        tags.append("creator")
    if (pct.get("xg_buildup90") or 0.0) >= TOP_QUINTILE:
        tags.append("ball-progressor")
    if head is not None and n_np >= AERIAL_MIN_SHOTS and head >= AERIAL_HEADER_SHARE:
        tags.append("aerial threat")
    if box is not None and n_np >= LONG_RANGE_MIN_SHOTS and (1.0 - box) >= LONG_RANGE_OUTSIDE_SHARE:
        tags.append("long-range shooter")
    if dfk >= SET_PIECE_MIN_DFK:
        tags.append("set-piece taker")
    return tags


def player_profile(
    player_id: str,
    team_id: str,
    league: League,
    season: str,
    player_matches: Iterable[PlayerMatch],
    shots: Iterable[Shot],
    lineups: Iterable[LineupEntry],
    players: Iterable[Player],
    valuations: Iterable[PlayerValuation],
) -> PlayerProfile:
    """One player's profile. For many calls build a ``ProfileBuilder`` once."""
    return ProfileBuilder(player_matches, shots, lineups, players, valuations).player_profile(
        player_id, team_id, league, season
    )
