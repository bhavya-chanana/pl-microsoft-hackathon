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
    for frame, lab, meta in items:
        s = F.build_sample(frame, observed_ids=meta.get("observed_ids"))
        samples.append(s)
        recv.append(receiver_index(lab, s.node_ids))
        for k in labs:
            labs[k].append(int(getattr(lab, k)))
        kind.append(int(meta.get("kind", schema.KIND_OPEN)))
        futures.append(_canonical_future(meta.get("target")))
        set_piece.append(schema.SET_PIECE_CODES.get(frame.set_piece, 0) if meta.get("knows_set_piece")
                         else schema.SET_PIECE_UNKNOWN)
    if not samples:
        return None
    tp, tv = schema.target_positions(samples, futures)
    arrays = schema.pack(samples, recv, labs, kind, tp, tv, set_piece)
    split = items[0][2].get("split")
    return schema.write_shard(source, match_id, arrays, split=split, root=root, note=note)


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


@dataclass
class TrackingTable:
    """Tracking of one match, per period, as arrays."""

    players: list[PlayerMeta]
    periods: dict[int, _Period] = field(default_factory=dict)

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
        alive = str(getattr(fr.ball_state, "value", fr.ball_state)).lower() == "alive"
        raw.setdefault(int(fr.period.id), []).append((float(time_fn(fr)), xy, ball, alive))
    table = TrackingTable(players=[metas[p] for p in order])
    for pid, rows in raw.items():
        rows.sort(key=lambda r: r[0])
        table.periods[pid] = _Period(times=np.array([r[0] for r in rows]), xy=np.stack([r[1] for r in rows]),
                                     ball=np.stack([r[2] for r in rows]), alive=np.array([r[3] for r in rows]))
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
    rng = random.Random(seed if seed is not None else int.from_bytes(f"{source}:{match_id}".encode()[:8], "big"))
    base = {"source": source, "match_id": match_id, "split": split, "note": note}

    def make(t: float, period: int, team: str, carrier: str | None, kind: int, ev: LEvent | None) -> Item | None:
        frame = table.tframe(t, period, team, carrier)
        if frame is None:
            return None
        lab = stream.labels_at(t, period, team, ev)
        meta = dict(base, elapsed=frame.elapsed, period=period, kind=kind, target=table.future(t, period, team))
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
