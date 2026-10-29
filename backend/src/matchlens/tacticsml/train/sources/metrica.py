"""Metrica Sports sample data: two anonymised games at 25 Hz with events (parity / smoke source only).

Source: https://github.com/metrica-sports/sample-data (CC BY-NC 4.0 on the data, attribution to Metrica
Sports). Cached under ``backend/data/local/tacticsml/raw/metrica``. Games 1 and 2 are CSV (events from
``RawEventsData.csv``); game 3 is EPTS tracking with Metrica JSON events, read through kloppy.

LICENCE: the data is CC BY-NC 4.0, i.e. non-commercial. It is for research and evaluation only and must not
ship in, or be used to train weights for, a commercial build without a separate licence from Metrica Sports.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Iterator

from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.labels import LEvent
from matchlens.tacticsml.train.sources._common import (
    Item, TrackingTable, build_from_iter, download, snap_events, table_from_kloppy, tracking_samples,
)

SOURCE = "metrica"
RAW = schema.RAW_ROOT / "metrica"
BASE = "https://raw.githubusercontent.com/metrica-sports/sample-data/master/data"
GAMES = ("1", "2", "3")
EPTS_GAMES = ("3",)
SAMPLE_RATE = 1 / 5


def fetch(game: str, root: Path = RAW) -> tuple[Path, Path, Path]:
    name = f"Sample_Game_{game}"
    files = (f"{name}_RawTrackingData_Home_Team.csv", f"{name}_RawTrackingData_Away_Team.csv",
             f"{name}_RawEventsData.csv")
    return tuple(download(f"{BASE}/{name}/{f}", root / name / f) for f in files)  # type: ignore[return-value]


def to_levents(rows: list[dict[str, str]]) -> list[LEvent]:
    out: list[LEvent] = []
    for r in rows:
        team = {"Home": "home", "Away": "away"}.get(r["Team"])
        if team is None:
            continue
        kind, sub = r["Type"], r["Subtype"]
        period, t = int(r["Period"]), float(r["Start Time [s]"])
        pid = r["From"]
        if kind == "PASS":
            rec = r["To"]
            out.append(LEvent(t=t, team_id=team, player_id=pid, kind="pass", period=period,
                              receiver_id=rec or None, outcome="success" if rec else "fail"))
        elif kind == "SHOT":
            out.append(LEvent(t=t, team_id=team, player_id=pid, kind="shot", period=period,
                              outcome="goal" if "GOAL" in sub else "miss"))
        elif kind == "RECOVERY":
            out.append(LEvent(t=t, team_id=team, player_id=pid, kind="gain", period=period))
    return out


def fetch_epts(game: str, root: Path = RAW) -> tuple[Path, Path, Path]:
    name = f"Sample_Game_{game}"
    files = (f"{name}_metadata.xml", f"{name}_tracking.txt", f"{name}_events.json")
    return tuple(download(f"{BASE}/{name}/{f}", root / name / f) for f in files)  # type: ignore[return-value]


def epts_levents(events_ds) -> list[LEvent]:
    """kloppy event dataset of an EPTS/JSON game -> ``LEvent`` (team ids are the tracking team ids)."""
    out: list[LEvent] = []
    for e in events_ds.records:
        et = str(getattr(e.event_type, "value", e.event_type)).upper()
        if e.player is None or e.team is None:
            continue
        team, pid, t, per = str(e.team.team_id), str(e.player.player_id), e.timestamp.total_seconds(), int(e.period.id)
        res = str(getattr(e.result, "value", e.result) or "").upper()
        if et == "PASS":
            rec = getattr(e, "receiver_player", None)
            out.append(LEvent(t=t, team_id=team, player_id=pid, kind="pass", period=per,
                              receiver_id=str(rec.player_id) if rec else None,
                              outcome="success" if res == "COMPLETE" else "fail"))
        elif et == "SHOT":
            out.append(LEvent(t=t, team_id=team, player_id=pid, kind="shot", period=per,
                              outcome="goal" if res == "GOAL" else "miss"))
        elif et == "CARRY":
            out.append(LEvent(t=t, team_id=team, player_id=pid, kind="carry", period=per))
        elif et == "RECOVERY":
            out.append(LEvent(t=t, team_id=team, player_id=pid, kind="gain", period=per))
    return sorted(out, key=lambda x: (x.period, x.t))


def iter_match_epts(game: str, root: Path = RAW, random_hz: float = 0.5) -> Iterator[Item]:
    from kloppy import metrica

    meta, track, events = fetch_epts(game, root)
    ds = metrica.load_tracking_epts(meta_data=str(meta), raw_data=str(track), sample_rate=SAMPLE_RATE)
    table = table_from_kloppy(ds, lambda fr: fr.timestamp.total_seconds())
    ev = metrica.load_event(event_data=str(events), meta_data=str(meta))
    levents = snap_events(table, epts_levents(ev))
    yield from tracking_samples(SOURCE, f"game{game}", table, levents, random_hz, note="Metrica sample data (EPTS)")


def iter_match(game: str, root: Path = RAW, random_hz: float = 0.5) -> Iterator[Item]:
    from kloppy import metrica

    if game in EPTS_GAMES:
        yield from iter_match_epts(game, root, random_hz)
        return
    home, away, events = fetch(game, root)
    ds = metrica.load_tracking_csv(home_data=str(home), away_data=str(away), sample_rate=SAMPLE_RATE)
    table = table_from_kloppy(ds, lambda fr: fr.timestamp.total_seconds())
    with open(events, newline="") as fh:
        rows = list(csv.DictReader(fh))
    # Metrica ids players "Player12" in events and "home_12" in tracking; map them
    levents = [_rename(e) for e in to_levents(rows)]
    levents = snap_events(table, levents)
    yield from tracking_samples(SOURCE, f"game{game}", table, levents, random_hz, note="Metrica sample data")


def _rename(e: LEvent) -> LEvent:
    from dataclasses import replace

    def pid(p: str | None) -> str | None:
        if not p:
            return p
        n = p.replace("Player", "")
        return f"{e.team_id}_{n}"

    return replace(e, player_id=pid(e.player_id), receiver_id=pid(e.receiver_id))


def iter_samples(games=None, limit: int | None = None, root: Path = RAW, random_hz: float = 0.5) -> Iterator[Item]:
    for g in list(games or GAMES)[:limit]:
        try:
            yield from iter_match(g, root, random_hz)
        except RuntimeError as exc:
            print(f"metrica: skipping game {g}: {exc}", file=sys.stderr)


def build_shards(games=None, limit: int | None = None, root: Path | None = None, progress: bool = False,
                 random_hz: float = 0.5) -> list[schema.ShardEntry]:
    return build_from_iter(SOURCE, iter_samples(games, limit, random_hz=random_hz), root, "Metrica", progress)
