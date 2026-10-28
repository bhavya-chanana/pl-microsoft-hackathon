"""Machinery shared by the training-data sources: tracking tables, sample planning and shard building.

A source yields ``Item = (TFrame, Labels, meta)`` per training moment, ``meta`` being a dict with

* ``source``, ``match_id`` (str), ``elapsed`` (float), ``period`` (int), ``kind`` (schema.KIND_*)
* optional ``split`` override (held-out sets), ``target`` (a ``TFrame`` ~3 s later, same possession and
  orientation), ``observed_ids`` (frozenset of player ids that are really observed), ``note``

All frames are in absolute 105 x 68 m coordinates with ``possession_team_id`` and ``attacking_right`` set.
Real tracking is resampled onto a 1 Hz history spacing (4 earlier frames, 1 s apart) so it matches the
simulator.
"""

from __future__ import annotations

import math
import os
import random
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH
from matchlens.tacticsml import features as F
from matchlens.tacticsml.contract import TBall, TFrame, TPlayer
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.labels import EventStream, LEvent, Labels, receiver_index

Item = tuple[TFrame, Labels, dict[str, Any]]

HISTORY = 4
HISTORY_STEP = 1.0  # seconds between history frames
MIN_PER_TEAM = 8


# ---------------------------------------------------------------- shard building

def build_from_iter(source: str, items: Iterable[Item], root: Path | None = None, note: str = "",
                    progress: bool = False) -> list[schema.ShardEntry]:
    """Group consecutive items by match, convert to graph samples and write one shard per match."""
    entries: list[schema.ShardEntry] = []
    group: list[Item] = []
    current: str | None = None

    def flush() -> None:
        if not group:
            return
        entry = write_match(source, current or "", group, root, note)
        if entry is not None:
            entries.append(entry)
            if progress:
                print(f"  {source}/{entry.match_id}: {entry.n} samples ({entry.split})", flush=True)
        group.clear()

    for item in items:
        mid = str(item[2]["match_id"])
        if current is not None and mid != current:
            flush()
        current = mid
        group.append(item)
    flush()
    return entries


def write_match(source: str, match_id: str, items: list[Item], root: Path | None = None, note: str = ""
                ) -> schema.ShardEntry | None:
    samples: list[F.GraphSample] = []
    recv: list[int] = []
    labs: dict[str, list[int]] = {k: [] for k in schema.LABEL_KEYS[1:]}
    kind: list[int] = []
    futures: list[dict[str, tuple[float, float]] | None] = []
    set_piece: list[int] = []
    B = len(items)
    H, Nn = schema.HORIZONS, F.N_NODES
    fut_pos = np.zeros((B, H, Nn, 2), dtype=np.float16)
    fut_valid = np.zeros((B, H, Nn), dtype=bool)
    since_dead = np.full(B, np.nan, dtype=np.float32)
    poss10 = np.full(B, -1, dtype=np.int8)
    action_next = np.full(B, -1, dtype=np.int8)
    poss_team = np.full(B, "", dtype="<U48")
    opp_team = np.full(B, "", dtype="<U48")
    for b, (frame, lab, meta) in enumerate(items):
        s = F.build_sample(frame, observed_ids=meta.get("observed_ids"))
        samples.append(s)
        recv.append(receiver_index(lab, s.node_ids))
        for k in labs:
            labs[k].append(int(getattr(lab, k)))
        kind.append(int(meta.get("kind", schema.KIND_OPEN)))
        futures.append(_canonical_future(meta.get("target")))
        set_piece.append(schema.SET_PIECE_CODES.get(frame.set_piece, 0) if meta.get("knows_set_piece")
                         else schema.SET_PIECE_UNKNOWN)
        poss10[b] = lab.poss10
        action_next[b] = lab.action_next if meta.get("has_action_labels") else -1
        since_dead[b] = meta.get("since_dead", np.nan)
        poss_team[b], opp_team[b] = meta.get("poss_team", "")[:48], meta.get("opp_team", "")[:48]
        if "future" in meta:
            fut_pos[b], fut_valid[b] = _canonical_block(s.node_ids, meta)
    if not samples:
        return None
    tp, tv = schema.target_positions(samples, futures)
    extra = {"fut_pos": fut_pos, "fut_valid": fut_valid, "since_dead": since_dead, "poss10": poss10,
             "action_next": action_next, "poss_team": poss_team, "opp_team": opp_team,
             "period": np.array([int(m.get("period", 1)) for _, _, m in items], dtype=np.int8)}
    arrays = schema.pack(samples, recv, labs, kind, tp, tv, set_piece, extra)
    split = items[0][2].get("split")
    return schema.write_shard(source, match_id, arrays, split=split, root=root, note=note)


def _canonical_block(node_ids: list[str], meta: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Future positions ``(H, N, 2)`` in the sample's canonical frame and a validity mask ``(H, N)``."""
    fp, fb = meta["future"]
    col = {pid: j for j, pid in enumerate(meta["future_ids"])}
    H = fp.shape[0]
    out = np.zeros((H, F.N_NODES, 2), dtype=np.float32)
    ok = np.zeros((H, F.N_NODES), dtype=bool)
    sign = np.array([-1.0 if meta["flip"] else 1.0, 1.0])
    centre = np.array([F.HALF_L, F.HALF_W])
    for j, nid in enumerate(node_ids[: F.BALL]):
        c = col.get(nid)
        if c is None:
            continue
        xy = np.clip(fp[:, c], [0.0, 0.0], [PITCH_LENGTH, PITCH_WIDTH])
        out[:, j] = (xy - centre) * sign
        ok[:, j] = ~np.isnan(fp[:, c, 0])
    out[:, F.BALL] = (np.clip(fb, [0.0, 0.0], [PITCH_LENGTH, PITCH_WIDTH]) - centre) * sign
    ok[:, F.BALL] = ~np.isnan(fb[:, 0])
    out[:, F.GOAL_OWN] = F.GOAL_XY[F.GOAL_OWN]
    out[:, F.GOAL_TARGET] = F.GOAL_XY[F.GOAL_TARGET]
    ok[:, F.GOAL_OWN] = ok[:, F.GOAL_TARGET] = True
    return np.nan_to_num(out), ok


def _canonical_future(target: TFrame | None) -> dict[str, tuple[float, float]] | None:
    if target is None:
        return None
    c = F.canonicalize(target)
    out = {p.player_id: (p.x, p.y) for p in c.players}
    out["ball"] = (c.ball.x, c.ball.y)
    return out


# ---------------------------------------------------------------- tracking tables

@dataclass
class PlayerMeta:
    player_id: str
    team_id: str
    is_gk: bool = False


@dataclass
class _Period:
    times: np.ndarray  # (T,) seconds on the source's clock, increasing
    xy: np.ndarray  # (T, P, 2) absolute metres, NaN where the player is not tracked
    ball: np.ndarray  # (T, 2)
    alive: np.ndarray  # (T,) bool
    dead_t: np.ndarray = field(init=False, repr=False)  # (T,) time of the latest dead-ball frame so far

    def __post_init__(self) -> None:
        dead = ~self.alive | np.isnan(self.ball[:, 0])
        self.dead_t = np.maximum.accumulate(np.where(dead, self.times, -np.inf))


@dataclass
class TrackingTable:
    """Tracking of one match, per period, as arrays."""

    players: list[PlayerMeta]
    periods: dict[int, _Period] = field(default_factory=dict)
    team_names: dict[str, str] = field(default_factory=dict)  # team id -> display name (when the feed has one)

    @property
    def teams(self) -> list[str]:
        return sorted({p.team_id for p in self.players})

    def ends(self) -> dict[int, float]:
        return {k: float(v.times[-1]) for k, v in self.periods.items() if len(v.times)}

    def spacing(self) -> float:
        for v in self.periods.values():
            if len(v.times) > 2:
                return float(np.median(np.diff(v.times)))
        return 1.0

    def attacking_right(self, period: int) -> dict[str, bool]:
        """Direction per team in a period: the goalkeeper (else the team centroid) sits in its own half."""
        per = self.periods[period]
        out: dict[str, bool] = {}
        for team in self.teams:
            idx = [i for i, p in enumerate(self.players) if p.team_id == team]
            gk = [i for i in idx if self.players[i].is_gk]
            use = gk or idx
            x = np.nanmean(per.xy[:, use, 0]) if use else PITCH_LENGTH / 2
            out[team] = bool(x < PITCH_LENGTH / 2)
        return out

    def index(self, t: float, period: int, tol: float) -> int | None:
        per = self.periods.get(period)
        if per is None or not len(per.times):
            return None
        i = bisect_left(per.times, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(per.times) and (best is None or abs(per.times[j] - t) < abs(per.times[best] - t)):
                best = j
        return best if best is not None and abs(per.times[best] - t) <= tol else None

    def _frame(self, i: int, period: int, poss: str, carrier: str | None, attack: dict[str, bool],
               set_piece=None) -> TFrame | None:
        per = self.periods[period]
        players: list[TPlayer] = []
        for j, pm in enumerate(self.players):
            x, y = per.xy[i, j]
            if math.isnan(x) or math.isnan(y):
                continue
            players.append(TPlayer(player_id=pm.player_id, team_id=pm.team_id, is_gk=pm.is_gk,
                                   x=float(min(max(x, 0.0), PITCH_LENGTH)), y=float(min(max(y, 0.0), PITCH_WIDTH))))
        bx, by = per.ball[i]
        if math.isnan(bx) or math.isnan(by):
            return None
        counts = {t: sum(1 for p in players if p.team_id == t) for t in self.teams}
        if any(c < MIN_PER_TEAM for c in counts.values()):
            return None
        return TFrame(players=players, ball=TBall(x=float(bx), y=float(by)), carrier_id=carrier,
                      possession_team_id=poss, attacking_right=dict(attack), elapsed=float(per.times[i]),
                      set_piece=set_piece)

    def tframe(self, t: float, period: int, poss: str, carrier_id: str | None = None,
               tol: float | None = None) -> TFrame | None:
        """Frame at ``t`` with up to 4 history frames 1 s apart; None if the moment is not covered."""
        tol = tol if tol is not None else max(0.5 * self.spacing(), 0.12)
        if poss not in self.teams:
            return None
        attack = self.attacking_right(period) if period not in self._attack_cache else self._attack_cache[period]
        self._attack_cache[period] = attack
        i = self.index(t, period, tol)
        if i is None:
            return None
        mates = [(j, pm) for j, pm in enumerate(self.players) if pm.team_id == poss]
        per = self.periods[period]
        if carrier_id is None or all(pm.player_id != carrier_id or math.isnan(per.xy[i, j, 0]) for j, pm in mates):
            carrier_id = self._nearest_mate(i, period, poss)
        last = self._frame(i, period, poss, carrier_id, attack)
        if last is None:
            return None
        hist: list[TFrame] = []
        for k in range(HISTORY, 0, -1):
            h = self.index(t - k * HISTORY_STEP, period, tol)
            if h is None:
                continue
            fr = self._frame(h, period, poss, carrier_id, attack)
            if fr is not None:
                hist.append(fr)
        return last.model_copy(update={"history": hist})

    def future(self, t: float, period: int, poss: str, dt: float = schema.TARGET_DT) -> TFrame | None:
        tol = max(0.5 * self.spacing(), 0.12)
        i = self.index(t + dt, period, tol)
        if i is None or poss not in self.teams:
            return None
        return self._frame(i, period, poss, None, self.attacking_right(period))

    def since_dead(self, t: float, period: int) -> float:
        """Seconds since the ball was last dead (``inf`` if it has been alive since the start of the period)."""
        i = self.index(t, period, max(0.5 * self.spacing(), 0.12))
        if i is None:
            return float("nan")
        return float(t - self.periods[period].dead_t[i])

    def future_block(self, t: float, period: int, horizon: int = schema.HORIZONS
                     ) -> tuple[np.ndarray, np.ndarray]:
        """Absolute positions at ``t + 1 .. t + horizon`` s: players ``(H, P, 2)`` and ball ``(H, 2)`` (NaN where the
        tracking does not cover the instant, e.g. past the end of the period)."""
        per = self.periods[period]
        tol = max(0.5 * self.spacing(), 0.12)
        fp = np.full((horizon, len(self.players), 2), np.nan)
        fb = np.full((horizon, 2), np.nan)
        for k in range(1, horizon + 1):
            i = self.index(t + k, period, tol)
            if i is not None:
                fp[k - 1], fb[k - 1] = per.xy[i], per.ball[i]
        return fp, fb

    def _nearest_mate(self, i: int, period: int, poss: str) -> str | None:
        per = self.periods[period]
        best, best_d = None, 1e9
        for j, pm in enumerate(self.players):
            if pm.team_id != poss or math.isnan(per.xy[i, j, 0]):
                continue
            d = (per.xy[i, j, 0] - per.ball[i, 0]) ** 2 + (per.xy[i, j, 1] - per.ball[i, 1]) ** 2
            if d < best_d:
                best, best_d = pm.player_id, d
        return best

    _attack_cache: dict[int, dict[str, bool]] = field(default_factory=dict, repr=False)


def infer_alive(times: np.ndarray, ball: np.ndarray, still_speed: float = 0.5, still_seconds: float = 1.5,
                margin: float = 0.5) -> np.ndarray:
    """Ball-in-play flag for feeds without a ball state: dead when the ball is off the pitch or has barely moved
    for ``still_seconds`` (throw-ins, free kicks, kick-offs and goal kicks all start from a stationary ball)."""
    out_of_play = ((ball[:, 0] < -margin) | (ball[:, 0] > PITCH_LENGTH + margin)
                   | (ball[:, 1] < -margin) | (ball[:, 1] > PITCH_WIDTH + margin) | np.isnan(ball[:, 0]))
    dt = np.maximum(np.diff(times, prepend=times[0] - 1.0), 1e-3)
    speed = np.linalg.norm(np.diff(np.nan_to_num(ball), axis=0, prepend=np.nan_to_num(ball[:1])), axis=1) / dt
    slow = (speed < still_speed).astype(float)
    win = max(1, int(round(still_seconds / max(float(np.median(dt)), 1e-3))))
    run = np.convolve(slow, np.ones(win), mode="same") >= win  # every frame in the window is slow
    return ~(out_of_play | run)


def table_from_kloppy(ds, time_fn, normalised: bool = True) -> TrackingTable:
    """kloppy ``TrackingDataset`` -> ``TrackingTable``. ``time_fn(frame)`` gives the source clock in seconds
    (kloppy coordinates are normalised 0..1 unless ``normalised`` is False)."""
    sx, sy = (PITCH_LENGTH, PITCH_WIDTH) if normalised else (1.0, 1.0)
    metas: dict[str, PlayerMeta] = {}
    for team in ds.metadata.teams:
        for pl in team.players:
            pos = getattr(pl, "starting_position", None)
            label = f"{getattr(pos, 'name', '')} {getattr(pos, 'code', '')}".lower() if pos else ""
            metas[str(pl.player_id)] = PlayerMeta(str(pl.player_id), str(team.team_id),
                                                  "goalkeeper" in label or label.strip().endswith(" gk"))
    order = list(metas)
    col = {pid: i for i, pid in enumerate(order)}
    raw: dict[int, list[tuple[float, np.ndarray, np.ndarray, bool]]] = {}
    for fr in ds.frames:
        if not fr.players_data or fr.ball_coordinates is None:
            continue
        xy = np.full((len(order), 2), np.nan)
        for pl, d in fr.players_data.items():
            c = col.get(str(pl.player_id))
            if c is not None and d.coordinates is not None:
                xy[c] = (d.coordinates.x * sx, d.coordinates.y * sy)
        ball = np.array([fr.ball_coordinates.x * sx, fr.ball_coordinates.y * sy])
        state = getattr(fr.ball_state, "value", fr.ball_state)
        alive = None if state is None else str(state).lower() == "alive"  # None: the feed has no ball state
        raw.setdefault(int(fr.period.id), []).append((float(time_fn(fr)), xy, ball, alive))
    table = TrackingTable(players=[metas[p] for p in order],
                          team_names={str(t.team_id): str(getattr(t, "name", "") or t.team_id)
                                      for t in ds.metadata.teams})
    for pid, rows in raw.items():
        rows.sort(key=lambda r: r[0])
        times, ball = np.array([r[0] for r in rows]), np.stack([r[2] for r in rows])
        alive = (infer_alive(times, ball) if any(r[3] is None for r in rows)
                 else np.array([r[3] for r in rows], dtype=bool))
        table.periods[pid] = _Period(times=times, xy=np.stack([r[1] for r in rows]), ball=ball, alive=alive)
    # drop players that were never tracked
    keep = [i for i in range(len(order)) if any(not np.isnan(p.xy[:, i, 0]).all() for p in table.periods.values())]
    if len(keep) != len(order):
        table.players = [table.players[i] for i in keep]
        for p in table.periods.values():
            p.xy = p.xy[:, keep]
    return table


# ---------------------------------------------------------------- sample planning on tracking

def tracking_samples(source: str, match_id: str, table: TrackingTable, events: list[LEvent],
                     random_hz: float = 0.5, seed: int | None = None, split: str | None = None,
                     note: str = "") -> Iterator[Item]:
    """Pass/carry starts plus random alive-ball frames of a match with tracking and events."""
    stream = EventStream(events, table.ends())
    has_carry_events = any(e.kind == "carry" for e in events)
    rng = random.Random(seed if seed is not None else int.from_bytes(f"{source}:{match_id}".encode()[:8], "big"))
    base = {"source": source, "match_id": match_id, "split": split, "note": note}

    def make(t: float, period: int, team: str, carrier: str | None, kind: int, ev: LEvent | None) -> Item | None:
        frame = table.tframe(t, period, team, carrier)
        if frame is None:
            return None
        lab = stream.labels_at(t, period, team, ev)
        meta = dict(base, elapsed=frame.elapsed, period=period, kind=kind, target=table.future(t, period, team))
        fp, fb = table.future_block(t, period)
        other = [x for x in table.teams if x != team]
        meta.update(future=(fp, fb), future_ids=[p.player_id for p in table.players],
                    flip=not table.attacking_right(period)[team], since_dead=table.since_dead(t, period),
                    poss_team=table.team_names.get(team, team),
                    opp_team=table.team_names.get(other[0], other[0]) if other else "",
                    has_action_labels=has_carry_events)
        return frame, lab, meta

    for e in stream.events:
        if e.kind in ("pass", "carry") and e.period in table.periods:
            kind = schema.KIND_PASS if e.kind == "pass" else schema.KIND_CARRY
            item = make(e.t, e.period, e.team_id, e.player_id, kind, e)
            if item is not None:
                yield item
    spacing = table.spacing()
    stride = max(1, round(1.0 / max(random_hz, 1e-3) / spacing))
    for period, per in sorted(table.periods.items()):
        offset = rng.randrange(stride)
        for i in range(offset, len(per.times), stride):
            if not per.alive[i]:
                continue
            t = float(per.times[i])
            team = stream.possession_at(t, period)
            if team is None:
                continue
            item = make(t, period, team, None, schema.KIND_OPEN, None)
            if item is not None:
                yield item


# ---------------------------------------------------------------- download helper

def download(url: str, dest: Path, retries: int = 4) -> Path:
    """Stream ``url`` to ``dest`` once (atomic; an existing file is kept)."""
    import time
    import urllib.request

    if dest.exists() and dest.stat().st_size > 0:
        return dest
    if os.environ.get("MATCHLENS_TACTICSML_DOWNLOAD") != "1":  # fetching is opt-in
        raise RuntimeError(f"{dest} is missing; set MATCHLENS_TACTICSML_DOWNLOAD=1 to fetch {url}, or point "
                           "MATCHLENS_TACTICSML_RAW at an existing raw/ directory")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180) as resp, open(tmp, "wb") as fh:
                while chunk := resp.read(1 << 20):
                    fh.write(chunk)
            tmp.replace(dest)
            return dest
        except Exception as exc:  # noqa: BLE001 - retry transient network errors
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"cannot download {url}") from last


def snap_events(table: TrackingTable, events: list[LEvent], window: float = 1.5) -> list[LEvent]:
    """Re-time pass / carry / shot events to the moment the actor is closest to the ball within +-``window`` s.

    Manual event logs lag the tracking by a variable fraction of a second; a sample anchored on the wrong
    instant would have the ball away from the player who is supposed to be playing it.
    """
    from dataclasses import replace

    col = {p.player_id: i for i, p in enumerate(table.players)}
    out: list[LEvent] = []
    for e in events:
        per = table.periods.get(e.period)
        j = col.get(e.player_id or "")
        if e.kind not in ("pass", "carry", "shot") or per is None or j is None:
            out.append(e)
            continue
        lo = int(np.searchsorted(per.times, e.t - window))
        hi = int(np.searchsorted(per.times, e.t + window))
        if hi - lo < 2:
            out.append(e)
            continue
        d = np.linalg.norm(per.xy[lo:hi, j] - per.ball[lo:hi], axis=1)
        if np.isnan(d).all():
            out.append(e)
            continue
        out.append(replace(e, t=float(per.times[lo + int(np.nanargmin(d))])))
    return out
