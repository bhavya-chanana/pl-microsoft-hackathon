"""Where a team's simulator style comes from.

Two sources, one result type (``SimStyle``):

* ``derive_style`` reads the play observed so far in a ``MatchEngine`` (pressure rate, long-pass
  share, crosses, tempo, carries, shot volume). It is the stub provider: no league data needed.
* ``from_team_style`` maps the season z-scores of a ``leaguedata`` ``TeamStyle`` onto the knobs.

Both clamp to the bounds of ``SimStyle`` and stay neutral when there is little to go on, so a
missing or thin source never produces an extreme team.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from matchlens.domain.models import EventType
from matchlens.domain.pitch import to_relative
from matchlens.interpret.engine import MatchEngine
from matchlens.leaguedata.contracts import TeamStyle
from matchlens.simulator.style import NEUTRAL, SimStyle
from matchlens.tactics.models import StyleProvider

# ------------------------------------------------------------------ derived from play

# What a typical team of the simulator does; a team doing exactly this gets the neutral style.
REF_PRESS_RATE = 0.32  # (pressures + tackles + interceptions) per opposition pass
REF_LONG_SHARE = 0.18  # passes of 30 m or more
REF_CROSS_SHARE = 0.03  # passes from a wide attacking position into the box
REF_TEMPO = 9.0  # passes per minute of possession
REF_CARRY_SHARE = 0.15  # carries / (carries + passes)
REF_SHOTS_PER_100 = 3.5  # shots per 100 passes
LONG_PASS_M = 30.0
MIN_PASSES = 40  # below this the style is neutral
SHRINK_PASSES = 80.0  # weight n / (n + SHRINK_PASSES) on the observation, the rest on neutral

# Team-relative box and wide-channel limits for crosses.
_BOX_X = 88.0
_BOX_Y = (20.0, 48.0)
_WIDE_Y = 14.0
_CROSS_FROM_X = 60.0


def _ratio(value: float, ref: float, gain: float, lo: float, hi: float, weight: float) -> float:
    """1.0 at the reference, moved by ``gain`` of the relative difference, shrunk to neutral by
    ``weight`` and clamped."""
    raw = 1.0 + gain * (value / ref - 1.0)
    return min(max(1.0 + weight * (raw - 1.0), lo), hi)


def _clamp(value: float, field: str) -> float:
    meta = SimStyle.model_fields[field]
    lo = next(m.ge for m in meta.metadata if hasattr(m, "ge"))
    hi = next(m.le for m in meta.metadata if hasattr(m, "le"))
    return min(max(value, lo), hi)


def derive_style(engine: MatchEngine, team_id: str) -> SimStyle:
    """The team's style as observed so far in this match. Neutral when fewer than 40 passes are known."""
    meta = engine.meta
    if team_id not in (meta.home.team_id, meta.away.team_id):
        return NEUTRAL
    right = team_id == meta.home.team_id
    opp = meta.opponent(team_id).team_id
    mine = engine.events(team_id=team_id)
    passes = [e for e in mine if e.type == EventType.PASS]
    if len(passes) < MIN_PASSES:
        return NEUTRAL
    n = len(passes)
    weight = n / (n + SHRINK_PASSES)

    long_n = cross_n = 0
    for e in passes:
        if e.start is None or e.end is None:
            continue
        if math.hypot(e.end.x - e.start.x, e.end.y - e.start.y) >= LONG_PASS_M:
            long_n += 1
        sx, sy = to_relative(e.start.x, e.start.y, right)
        ex, ey = to_relative(e.end.x, e.end.y, right)
        wide = sy <= _WIDE_Y or sy >= 68.0 - _WIDE_Y
        if wide and sx >= _CROSS_FROM_X and ex >= _BOX_X and _BOX_Y[0] <= ey <= _BOX_Y[1]:
            cross_n += 1
    carries = sum(e.type == EventType.CARRY for e in mine)
    shots = sum(e.type == EventType.SHOT for e in mine)
    opp_passes = max(1, sum(e.type == EventType.PASS for e in engine.events(team_id=opp)))
    def_actions = sum(e.type in (EventType.PRESSURE, EventType.TACKLE, EventType.INTERCEPTION) for e in mine)
    possession_s = engine.c.teams[team_id].possession_s if team_id in engine.c.teams else 0.0

    press = _ratio(def_actions / opp_passes, REF_PRESS_RATE, 1.6, 0.6, 1.6, weight)
    long_share = long_n / n
    cross_share = cross_n / n
    carry_share = carries / max(1, carries + n)
    shots_per_100 = 100.0 * shots / n
    tempo = _ratio(60.0 * n / possession_s, REF_TEMPO, 0.8, 0.8, 1.3, weight) if possession_s > 60 else 1.0

    directness = _ratio(long_share, REF_LONG_SHARE, 1.2, 0.5, 2.0, weight)
    return SimStyle(
        press_intensity=press,
        block_height=_clamp(weight * 6.0 * (press - 1.0), "block_height"),  # pressing teams hold a higher block
        directness=directness,
        verticality=_ratio(long_share, REF_LONG_SHARE, 0.5, 0.7, 1.4, weight),
        tempo=tempo,
        crossing=_ratio(cross_share, REF_CROSS_SHARE, 0.6, 0.5, 2.0, weight),
        dribbling=_ratio(carry_share, REF_CARRY_SHARE, 1.0, 0.5, 2.0, weight),
        shot_appetite=_ratio(shots_per_100, REF_SHOTS_PER_100, 0.8, 0.6, 1.8, weight),
    )


# ------------------------------------------------------------------ league data

# (knob, metric key, sign, gain per z-score, neutral value). A negative sign means a lower metric
# (e.g. PPDA, passes allowed per defensive action) moves the knob up.
_Z = 3.0  # z-scores beyond +-3 add nothing


def _z(style: TeamStyle, key: str) -> float:
    m = style.metric(key)
    if m is None or m.z is None or not math.isfinite(m.z):
        return 0.0
    return min(max(m.z, -_Z), _Z)


def _mult(z: float, per_z: float) -> float:
    """Exponential map of a z-score to a multiplier: z=0 is 1.0, symmetric in log space."""
    return math.exp(per_z * z)


def from_team_style(style: TeamStyle) -> SimStyle:
    """Map season z-scores to simulator knobs: a team one z-score above the league on a trait gets a
    visibly stronger version of it. Missing metrics count as league average."""
    z_ppda = _z(style, "ppda")
    z_deep = _z(style, "deep_share")
    z_direct = _z(style, "directness")
    z_cross = _z(style, "create_cross")
    z_takeon = _z(style, "create_take_on")
    z_shots = _z(style, "shots90_level") or _z(style, "xg_for90")
    z_xgps = _z(style, "xg_per_shot")
    z_counter = _z(style, "counter_proxy")

    values: dict[str, float] = {
        "press_intensity": _mult(-z_ppda, 0.22),  # low PPDA = hard press
        "block_height": 3.5 * z_deep - 1.5 * z_ppda,
        "line_height": 2.0 * z_deep,
        "directness": _mult(z_direct, 0.30),
        "verticality": _mult(z_direct, 0.15) * _mult(z_counter, 0.05),
        "crossing": _mult(z_cross, 0.30),
        "dribbling": _mult(z_takeon, 0.30),
        "shot_appetite": _mult(z_shots - 0.5 * z_xgps, 0.18),  # many low-value shots = high appetite
        "counter_boost": max(0.0, 0.15 * z_counter + 0.1),
        "tempo": _mult(z_direct, 0.05) * _mult(-z_ppda, 0.04),
    }
    return SimStyle(**{k: _clamp(v, k) for k, v in values.items()})


# ------------------------------------------------------------------ providers


def _tag_player(engine: MatchEngine, player_id: str) -> list[str]:
    """Role tags read from the player's observed stats this match (conservative; empty when unsure)."""
    meta = engine.meta
    if player_id not in meta.players:
        return []
    stats = engine.player_stats(player_id)
    tags: list[str] = []
    passes = int(stats.get("passes") or 0)
    if int(stats.get("key_passes") or 0) >= 2:
        tags.append("creator")
    if int(stats.get("shots") or 0) >= 3:
        tags.append("shooter")
    if int(stats.get("carries") or 0) >= 8:
        tags.append("ball-carrier")
    if int(stats.get("progressive_passes") or 0) >= 8 and passes:
        tags.append("ball-progressor")
    if int(stats.get("pressures") or 0) >= 8:
        tags.append("presser")
    top = stats.get("top_speed_kmh")
    if isinstance(top, (int, float)) and top >= 33.0:
        tags.append("pace")
    elif meta.players[player_id].top_speed >= 9.3:
        tags.append("pace")
    return tags


class DerivedStyleProvider:
    """Styles and tags read from the match being played (no external data)."""

    def __init__(self, engine: MatchEngine) -> None:
        self.engine = engine

    def style(self, team_id: str) -> SimStyle:
        return derive_style(self.engine, team_id)

    def player_tags(self, player_id: str) -> list[str]:
        return _tag_player(self.engine, player_id)

    def context(self, team_id: str) -> dict[str, object]:
        style = self.style(team_id)
        try:
            stats = self.engine.team_stats(team_id)
        except KeyError:
            stats = {}
        keys = ("possession_pct", "shots", "xg", "pass_accuracy", "pressures", "box_entries")
        return {
            "source": "observed play this match",
            "style": style.model_dump(),
            "stats": {k: stats.get(k) for k in keys if k in stats},
        }


class LeagueStyleProvider:
    """Styles and context from a league-data knowledge object.

    ``kb`` is duck-typed: it needs ``team_style(team_id, season=None)`` (a ``TeamStyle``),
    ``player_tags(player_id)`` (list of str) and ``team_context(team_id)`` (JSON-able dict). Any of
    them may raise or return None; the provider then falls back to the match-derived value when it was
    given an ``engine`` and to the neutral style otherwise.
    """

    def __init__(self, kb: Any, engine: MatchEngine | None = None, season: str | None = None) -> None:
        self.kb = kb
        self.season = season
        self.fallback = DerivedStyleProvider(engine) if engine is not None else None

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        fn: Callable[..., Any] | None = getattr(self.kb, name, None)
        if fn is None:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a thin knowledge base must never break the board
            return None

    def style(self, team_id: str) -> SimStyle:
        team_style = self._call("team_style", team_id, season=self.season)
        if isinstance(team_style, TeamStyle):
            return from_team_style(team_style)
        return self.fallback.style(team_id) if self.fallback else NEUTRAL

    def player_tags(self, player_id: str) -> list[str]:
        tags = self._call("player_tags", player_id)
        if tags:
            return [str(t) for t in tags]
        return self.fallback.player_tags(player_id) if self.fallback else []

    def context(self, team_id: str) -> dict[str, object]:
        ctx = self._call("team_context", team_id)
        if isinstance(ctx, dict):
            return ctx
        return self.fallback.context(team_id) if self.fallback else {}


def build_provider(session_or_engine: Any, kb: Any | None = None) -> StyleProvider:
    """The best provider available: league data when a knowledge object is given, else observed play.

    ``session_or_engine`` is a ``MatchEngine`` or anything with an ``engine`` attribute (a live session)."""
    engine = session_or_engine if isinstance(session_or_engine, MatchEngine) else getattr(
        session_or_engine, "engine", None)
    if kb is not None:
        return LeagueStyleProvider(kb, engine=engine)
    if engine is None:
        raise TypeError("build_provider needs a MatchEngine (or an object with an .engine) when no kb is given")
    return DerivedStyleProvider(engine)
