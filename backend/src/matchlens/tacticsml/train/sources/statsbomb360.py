"""StatsBomb open data with 360 freeze frames (https://github.com/statsbomb/open-data).

Licence: StatsBomb open data user agreement (free for research and non-commercial use, attribution to
StatsBomb required, see the repository's LICENSE.pdf); nothing here is redistributed, raw files stay in
``backend/data/local/tacticsml/raw/statsbomb360`` (gitignored).

A 360 frame is a single snapshot of the *visible* players (no ids, no velocities, no history), taken at an
event. Players get anonymous ids; unseen players are left out (pad nodes). Receiver labels are unknown
(-1): the freeze frame carries no player identity. The shot, goal, concede and turnover labels come from
the event stream of the same match. Samples are Pass and Carry starts plus a share of Ball Receipt frames
as open-play moments. Euro 2024 is the held-out test set.
"""

from __future__ import annotations

import gzip
import json
import random
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from matchlens.domain.models import PITCH_LENGTH, PITCH_WIDTH
from matchlens.tacticsml.contract import TBall, TFrame, TPlayer
from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.labels import RECEIVER_UNKNOWN, EventStream, LEvent
from matchlens.tacticsml.train.sources._common import Item, build_from_iter

SOURCE = "statsbomb360"
URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
CACHE = schema.ROOT / "raw" / "statsbomb360"
SB_X, SB_Y = 120.0, 80.0
# (competition_id, season_id, label, split override); competitions with 360 data
COMPETITIONS: list[tuple[int, int, str, str | None]] = [
    (55, 282, "UEFA Euro 2024", "test"),
    (55, 43, "UEFA Euro 2020", None),
    (43, 106, "FIFA World Cup 2022", None),
    (9, 281, "1. Bundesliga 2023/2024", None),
    (11, 90, "La Liga 2020/2021", None),
    (7, 108, "Ligue 1 2021/2022", None),
    (7, 235, "Ligue 1 2022/2023", None),
    (44, 107, "Major League Soccer 2023", None),
]
OPEN_FRACTION = 0.4  # share of Ball Receipt frames kept as open-play samples


# ---------------------------------------------------------------- download + cache

def _fetch(url: str, retries: int = 4) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - retry transient network errors
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"cannot fetch {url}") from last


def _cached(kind: str, name: str, url: str, cache: Path = CACHE) -> Any:
    path = cache / kind / f"{name}.json.gz"
    if path.exists():
        with gzip.open(path, "rt") as fh:
            return json.load(fh)
    data = _fetch(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", compresslevel=6) as fh:
        json.dump(data, fh, separators=(",", ":"))
    tmp.replace(path)
    return data


def match_list(competition_id: int, season_id: int, cache: Path = CACHE) -> list[dict[str, Any]]:
    rows = _cached("matches", f"{competition_id}-{season_id}", f"{URL}/matches/{competition_id}/{season_id}.json", cache)
    return sorted((m for m in rows if m.get("match_status_360") == "available"),
                  key=lambda m: (m["match_date"], m["match_id"]))


def load_match(match_id: int, cache: Path = CACHE) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = _cached("events", str(match_id), f"{URL}/events/{match_id}.json", cache)
    frames = _cached("three-sixty", str(match_id), f"{URL}/three-sixty/{match_id}.json", cache)
    return events, frames


# ---------------------------------------------------------------- conversion

def _seconds(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def to_levents(events: list[dict[str, Any]]) -> tuple[list[LEvent], dict[int, float]]:
    """StatsBomb events -> ``LEvent``s (periods 1-4) and the last second seen in each period."""
    out: list[LEvent] = []
    ends: dict[int, float] = {}
    prev_team: int | None = None
    for e in events:
        period = e.get("period", 0)
        if period < 1 or period > 4:
            continue
        t = _seconds(e["timestamp"])
        ends[period] = max(ends.get(period, 0.0), t)
        team = (e.get("team") or {}).get("id")
        poss = (e.get("possession_team") or {}).get("id")
        kind = (e.get("type") or {}).get("name")
        if poss is not None and poss != prev_team:
            out.append(LEvent(t=t, team_id=str(poss), player_id=None, kind="gain", period=period))
        prev_team = poss if poss is not None else prev_team
        if team is None:
            continue
        pid = str((e.get("player") or {}).get("id")) if e.get("player") else None
        if kind == "Pass":
            p = e.get("pass", {})
            outcome = (p.get("outcome") or {}).get("name")
            ok = outcome is None
            rec = p.get("recipient")
            out.append(LEvent(t=t, team_id=str(team), player_id=pid, kind="pass", period=period,
                              receiver_id=str(rec["id"]) if rec and ok else None,
                              outcome="success" if ok else "fail"))
        elif kind == "Carry":
            out.append(LEvent(t=t, team_id=str(team), player_id=pid, kind="carry", period=period, outcome="success"))
        elif kind == "Shot":
            goal = ((e.get("shot") or {}).get("outcome") or {}).get("name") == "Goal"
            out.append(LEvent(t=t, team_id=str(team), player_id=pid, kind="shot", period=period,
                              outcome="goal" if goal else "miss"))
        elif kind == "Own Goal For":
            out.append(LEvent(t=t, team_id=str(team), player_id=pid, kind="goal", period=period, outcome="goal"))
    return out, ends


def to_tframe(freeze: dict[str, Any], event: dict[str, Any], other_team: str) -> TFrame | None:
    """One 360 frame at an event of the possession team, in absolute 105 x 68 m."""
    poss = str(event["team"]["id"])
    sx, sy = PITCH_LENGTH / SB_X, PITCH_WIDTH / SB_Y
    players: list[TPlayer] = []
    carrier: str | None = None
    counts = {True: 0, False: 0}
    for p in freeze["freeze_frame"]:
        mate = bool(p["teammate"])
        counts[mate] += 1
        pid = f"{'a' if mate else 'b'}{counts[mate]:02d}"
        x, y = p["location"][0] * sx, p["location"][1] * sy
        players.append(TPlayer(player_id=pid, team_id=poss if mate else other_team, is_gk=bool(p.get("keeper")),
                               x=min(max(x, 0.0), PITCH_LENGTH), y=min(max(y, 0.0), PITCH_WIDTH)))
        if p.get("actor") and carrier is None:
            carrier = pid
    loc = event.get("location")
    if len(players) < 4 or not loc or counts[True] < 2:
        return None
    bx, by = loc[0] * sx, loc[1] * sy
    if carrier is None:
        mates = [p for p in players if p.team_id == poss]
        carrier = min(mates, key=lambda p: (p.x - bx) ** 2 + (p.y - by) ** 2).player_id
    return TFrame(players=players, ball=TBall(x=min(max(bx, 0.0), PITCH_LENGTH), y=min(max(by, 0.0), PITCH_WIDTH)),
                  carrier_id=carrier, possession_team_id=poss, attacking_right={poss: True, other_team: False},
                  elapsed=_seconds(event["timestamp"]))


def iter_match(events: list[dict[str, Any]], frames: list[dict[str, Any]], match_id: str, split: str | None = None,
               open_fraction: float = OPEN_FRACTION) -> Iterator[Item]:
    levents, ends = to_levents(events)
    stream = EventStream(levents, ends)
    teams = sorted({str(e["team"]["id"]) for e in events if e.get("team")})
    if len(teams) != 2:
        return
    by_uuid = {f["event_uuid"]: f for f in frames}
    rng = random.Random(int(match_id) if match_id.isdigit() else 0)
    lev = {(e.period, e.t, e.kind, e.player_id): e for e in levents if e.kind in ("pass", "carry")}
    for e in events:
        kind = (e.get("type") or {}).get("name")
        period = e.get("period", 0)
        if kind not in ("Pass", "Carry", "Ball Receipt*") or period < 1 or period > 4 or e["id"] not in by_uuid:
            continue
        if kind == "Ball Receipt*" and rng.random() >= open_fraction:
            continue
        team = str(e["team"]["id"])
        other = teams[0] if teams[1] == team else teams[1]
        frame = to_tframe(by_uuid[e["id"]], e, other)
        if frame is None:
            continue
        t = _seconds(e["timestamp"])
        ev = None
        sample_kind = schema.KIND_OPEN
        if kind != "Ball Receipt*":
            pid = str(e["player"]["id"]) if e.get("player") else None
            ev = lev.get((period, t, "pass" if kind == "Pass" else "carry", pid))
            sample_kind = schema.KIND_PASS if kind == "Pass" else schema.KIND_CARRY
        lab = stream.labels_at(t, period, team, ev)
        lab = replace(lab, receiver_id=None, receiver_state=RECEIVER_UNKNOWN)  # anonymous players: no receiver
        ptype = ((e.get("pass") or {}).get("type") or {}).get("name") if kind == "Pass" else None
        sp = {"Corner": "corner", "Free Kick": "free_kick", "Throw-in": "throw_in", "Goal Kick": "goal_kick"}.get(ptype or "")
        if sp:
            frame = frame.model_copy(update={"set_piece": sp})
        meta = {"source": SOURCE, "match_id": match_id, "elapsed": t, "period": period, "kind": sample_kind,
                "split": split, "target": None, "knows_set_piece": True}
        yield frame, lab, meta


# ---------------------------------------------------------------- source API

def select_matches(competitions=None, limit: int | None = None, cache: Path = CACHE
                   ) -> list[tuple[dict[str, Any], str | None, str]]:
    """Matches round-robin across competitions (so a small ``limit`` still spans all of them)."""
    comps = competitions or COMPETITIONS
    lists = []
    for c in comps:
        cid, sid, label = c[0], c[1], c[2]
        split = c[3] if len(c) > 3 else None
        try:
            lists.append([(m, split, label) for m in match_list(cid, sid, cache)])
        except RuntimeError as exc:
            print(f"statsbomb360: skipping {label}: {exc}", file=sys.stderr)
    out: list[tuple[dict[str, Any], str | None, str]] = []
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x):
                out.append(x[i])
    return out[:limit] if limit is not None else out


def iter_samples(competitions=None, limit: int | None = None, cache: Path = CACHE,
                 open_fraction: float = OPEN_FRACTION, workers: int = 4) -> Iterator[Item]:
    """Samples of up to ``limit`` matches (downloaded once, then read from the cache)."""
    chosen = select_matches(competitions, limit, cache)

    def prefetch(entry) -> bool:
        m = entry[0]
        try:
            load_match(m["match_id"], cache)
            return True
        except RuntimeError as exc:
            print(f"statsbomb360: skipping match {m['match_id']}: {exc}", file=sys.stderr)
            return False

    with ThreadPoolExecutor(max_workers=workers) as pool:  # downloads run ahead, parsing stays one match at a time
        for entry, ok in zip(chosen, pool.map(prefetch, chosen)):
            if not ok:
                continue
            m, split, _label = entry
            events, frames = load_match(m["match_id"], cache)
            yield from iter_match(events, frames, str(m["match_id"]), split, open_fraction)


def build_shards(competitions=None, limit: int | None = None, root: Path | None = None, progress: bool = False,
                 open_fraction: float = OPEN_FRACTION) -> list[schema.ShardEntry]:
    items = iter_samples(competitions, limit, open_fraction=open_fraction)
    return build_from_iter(SOURCE, items, root, "StatsBomb 360", progress)
