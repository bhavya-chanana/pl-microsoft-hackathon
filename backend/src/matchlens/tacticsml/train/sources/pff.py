"""PFF FC World Cup 2022: 64 matches of 30 Hz tracking plus PFF events (the best open training set).

Gated download: the user must request it at https://www.blog.fc.pff.com/blog/pff-fc-release-2022-world-cup-data
(accepting PFF's terms, non-commercial research use) and unpack it to ``backend/data/local/tacticsml/raw/pff``:

    pff/Tracking Data/<game_id>.jsonl.bz2   (or the .jsonl)
    pff/Event Data/<game_id>.json
    pff/Metadata/<game_id>.json   (plus a rosters file per game: ``Rosters/<game_id>.json`` or ``rosters.json``)

The folder names are matched case-insensitively and loosely (any ``*track*``, ``*event*``, ``*meta*``,
``*roster*`` directory). Nothing is redistributed. Knockout-stage games (metadata ``week`` >= 4) are the
held-out test set.

NOTE: written against the published PFF schema without the data at hand; the event parser reads
``possessionEvents`` (type PA / CR pass, SH shot, outcome codes) and is the first place to look if the
live run reports odd label rates.
"""

from __future__ import annotations

import bz2
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.labels import LEvent
from matchlens.tacticsml.train.schema import DataUnavailable
from matchlens.tacticsml.train.sources._common import (
    Item, TrackingTable, build_from_iter, snap_events, table_from_kloppy, tracking_samples,
)

SOURCE = "pff"
RAW = schema.RAW_ROOT / "pff"
SAMPLE_RATE = 1 / 6  # 30 Hz -> 5 Hz
KNOCKOUT_FROM_WEEK = 4
INSTRUCTIONS = (
    "PFF FC World Cup 2022 data is not on this machine. It is a gated download: request it at "
    "https://www.blog.fc.pff.com/blog/pff-fc-release-2022-world-cup-data , accept PFF's terms, and unpack it "
    f"into {RAW} (folders 'Tracking Data', 'Event Data', 'Metadata', rosters). Then re-run the shards build."
)


def _dir(root: Path, *words: str) -> Path | None:
    for d in sorted(root.iterdir()) if root.exists() else []:
        if d.is_dir() and any(w in d.name.lower() for w in words):
            return d
    return None


def find_games(root: Path = RAW) -> dict[str, dict[str, Path]]:
    """game_id -> {tracking, events, meta, roster} paths for every complete game found under ``root``."""
    if not root.exists() or not any(root.iterdir()):
        raise DataUnavailable(INSTRUCTIONS)
    tdir, edir, mdir, rdir = (_dir(root, "track"), _dir(root, "event"), _dir(root, "meta"), _dir(root, "roster"))
    if tdir is None or edir is None:
        raise DataUnavailable(INSTRUCTIONS)
    games: dict[str, dict[str, Path]] = {}
    for f in sorted(tdir.iterdir()):
        gid = f.name.split(".")[0]
        if not gid.isdigit():
            continue
        ev = next((p for p in edir.glob(f"{gid}.json*")), None)
        meta = next((p for p in (mdir.glob(f"{gid}.json*") if mdir else []) if p.is_file()), None)
        roster = next((p for p in (rdir.glob(f"{gid}.json*") if rdir else []) if p.is_file()), None) \
            or next((p for p in root.rglob("*roster*.json") if p.is_file()), None)
        if ev and meta and roster:
            games[gid] = {"tracking": f, "events": ev, "meta": meta, "roster": roster}
    if not games:
        raise DataUnavailable("PFF folder found but no game has tracking + events + metadata + roster. " + INSTRUCTIONS)
    return games


def _json(path: Path) -> Any:
    opener = bz2.open if path.suffix == ".bz2" else open
    with opener(path, "rt") as fh:
        return json.load(fh)


def is_knockout(meta_path: Path) -> bool:
    raw = _json(meta_path)
    row = raw[0] if isinstance(raw, list) and raw else raw
    try:
        return int(row.get("week", 0)) >= KNOCKOUT_FROM_WEEK
    except (TypeError, ValueError):
        return False


_PASS_OK = {"C"}  # PFF passOutcomeType: C complete; everything else (D, B, I, O, S) is an incomplete pass


def to_levents(events: list[dict[str, Any]]) -> list[LEvent]:
    """PFF events -> ``LEvent``s on the period-relative clock."""
    out: list[LEvent] = []
    for ev in events:
        pe = ev.get("possessionEvents") or {}
        ge = ev.get("gameEvents") or {}
        kind = pe.get("possessionEventType")
        team = ge.get("teamId")
        period = ge.get("period") or ev.get("period")
        t = ev.get("startGameClock", ev.get("eventTime"))
        if kind not in ("PA", "CR", "SH") or team is None or period is None or t is None:
            continue
        team, period, t = str(team), int(period), float(t)
        if kind in ("PA", "CR"):
            ok = pe.get("passOutcomeType") in _PASS_OK
            rec = pe.get("receiverPlayerId") or pe.get("targetPlayerId")
            pid = pe.get("passerPlayerId") or ge.get("playerId")
            out.append(LEvent(t=t, team_id=team, player_id=str(pid) if pid else None, kind="pass", period=period,
                              receiver_id=str(rec) if ok and rec else None, outcome="success" if ok else "fail"))
        else:
            pid = pe.get("shooterPlayerId") or ge.get("playerId")
            out.append(LEvent(t=t, team_id=team, player_id=str(pid) if pid else None, kind="shot", period=period,
                              outcome="goal" if pe.get("shotOutcomeType") == "G" else "miss"))
    return out


def load(paths: dict[str, Path], sample_rate: float = SAMPLE_RATE) -> tuple[TrackingTable, list[LEvent]]:
    from kloppy import pff

    ds = pff.load_tracking(meta_data=str(paths["meta"]), roster_meta_data=str(paths["roster"]),
                           raw_data=str(paths["tracking"]), sample_rate=sample_rate, coordinates="kloppy")
    table = table_from_kloppy(ds, lambda fr: fr.timestamp.total_seconds())
    return table, snap_events(table, to_levents(_json(paths["events"])))


def iter_samples(game_ids=None, limit: int | None = None, root: Path = RAW, random_hz: float = 0.5) -> Iterator[Item]:
    games = find_games(root)  # raises DataUnavailable with instructions
    ids = [g for g in (game_ids or games) if g in games][:limit]
    for gid in ids:
        try:
            table, levents = load(games[gid])
        except Exception as exc:  # noqa: BLE001 - one bad game must not stop the run
            print(f"pff: skipping game {gid}: {exc}", file=sys.stderr)
            continue
        split = "test" if is_knockout(games[gid]["meta"]) else None
        yield from tracking_samples(SOURCE, gid, table, levents, random_hz, split=split, note="PFF FC WC2022")


def build_shards(game_ids=None, limit: int | None = None, root: Path | None = None, progress: bool = False,
                 random_hz: float = 0.5) -> list[schema.ShardEntry]:
    return build_from_iter(SOURCE, iter_samples(game_ids, limit, random_hz=random_hz), root, "PFF FC", progress)
