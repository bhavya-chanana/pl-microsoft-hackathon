"""SkillCorner open data: broadcast tracking at 10 fps for 20 A-League matches, plus "dynamic events".

Source: https://github.com/SkillCorner/opendata (MIT licence, copyright SkillCorner). Raw files are cached
under ``backend/data/local/tacticsml/raw/skillcorner`` (about 100 MB per match, gitignored).

Tracking is broadcast-derived (extrapolated for players off camera). Events come from the dynamic-events
table: each ``player_possession`` row becomes a pass (receiver = the targeted player), a shot (goal when it
led to one), a carry start and a possession gain when the team changes.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Iterator

from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.labels import LEvent
from matchlens.tacticsml.train.sources._common import (
    Item, TrackingTable, build_from_iter, download, table_from_kloppy, tracking_samples,
)

SOURCE = "skillcorner"
RAW = schema.RAW_ROOT / "skillcorner"
BASE = "https://github.com/SkillCorner/opendata/raw/master/data/matches"
MATCH_IDS = ("1925299", "1874553", "1886347", "1899585", "1927964", "1953632", "1959846", "1986691", "1996435",
             "1996436", "2006229", "2006363", "2007448", "2007721", "2010085", "2011166", "2013725", "2015213",
             "2016236", "2017461")
FPS = 10.0
SAMPLE_RATE = 1 / 2  # kloppy keeps every 2nd frame (5 Hz) of the 10 fps tracking


def fetch(match_id: str, root: Path = RAW) -> tuple[Path, Path, Path]:
    d = root / match_id
    files = {"meta": f"{match_id}_match.json", "track": f"{match_id}_tracking_extrapolated.jsonl",
             "events": f"{match_id}_dynamic_events.csv"}
    return tuple(download(f"{BASE}/{match_id}/{name}", d / name) for name in files.values())  # type: ignore[return-value]


def to_levents(rows: list[dict[str, str]]) -> list[LEvent]:
    """``player_possession`` rows of the dynamic-events CSV -> ``LEvent``s on a frames / FPS clock."""
    out: list[LEvent] = []
    prev_team: str | None = None
    for r in sorted((r for r in rows if r["event_type"] == "player_possession"), key=lambda r: int(r["index"])):
        team, player, period = r["team_id"], r["player_id"], int(r["period"])
        t0, t1 = int(r["frame_start"]) / FPS, int(r["frame_end"]) / FPS
        if team != prev_team:
            out.append(LEvent(t=t0, team_id=team, player_id=None, kind="gain", period=period))
        prev_team = team
        if r.get("carry") == "True":
            out.append(LEvent(t=t0, team_id=team, player_id=player, kind="carry", outcome="success", period=period))
        end = r["end_type"]
        if end == "pass":
            ok = r["pass_outcome"] == "successful"
            target = r.get("player_targeted_id") or ""
            receiver = str(int(float(target))) if ok and target else None
            out.append(LEvent(t=t1, team_id=team, player_id=player, kind="pass", receiver_id=receiver,
                              outcome="success" if ok else "fail", period=period))
        elif end == "shot":
            goal = r.get("lead_to_goal") == "True"
            out.append(LEvent(t=t1, team_id=team, player_id=player, kind="shot",
                              outcome="goal" if goal else "miss", period=period))
    return out


def load_table(meta: Path, track: Path, sample_rate: float = SAMPLE_RATE) -> TrackingTable:
    from kloppy import skillcorner

    ds = skillcorner.load(meta_data=str(meta), raw_data=str(track), sample_rate=sample_rate)
    return table_from_kloppy(ds, lambda fr: fr.frame_id / FPS)


def iter_match(match_id: str, root: Path = RAW, random_hz: float = 0.5) -> Iterator[Item]:
    meta, track, events = fetch(match_id, root)
    table = load_table(meta, track)
    with open(events, newline="") as fh:
        levents = to_levents(list(csv.DictReader(fh)))
    yield from tracking_samples(SOURCE, match_id, table, levents, random_hz, note="SkillCorner open data")


def iter_samples(match_ids=None, limit: int | None = None, root: Path = RAW, random_hz: float = 0.5) -> Iterator[Item]:
    ids = list(match_ids or MATCH_IDS)[:limit]
    for mid in ids:
        try:
            yield from iter_match(mid, root, random_hz)
        except RuntimeError as exc:
            print(f"skillcorner: skipping {mid}: {exc}", file=sys.stderr)


def build_shards(match_ids=None, limit: int | None = None, root: Path | None = None, progress: bool = False,
                 random_hz: float = 0.5) -> list[schema.ShardEntry]:
    return build_from_iter(SOURCE, iter_samples(match_ids, limit, random_hz=random_hz), root, "SkillCorner", progress)
