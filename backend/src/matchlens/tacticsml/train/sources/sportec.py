"""DFL / IDSSE open data: seven Bundesliga and 2. Bundesliga matches, 25 Hz tracking plus events.

Source: Bassek et al., "An integrated dataset of spatiotemporal and event data in elite soccer", Sci Data 12,
195 (2025), https://doi.org/10.1038/s41597-025-04505-y. Licence CC BY 4.0 (attribution required). Files are
fetched through kloppy's IDSSE URLs and cached under ``backend/data/local/tacticsml/raw/sportec``.

Tracking is kept at 5 Hz (kloppy ``sample_rate``) and re-sampled to the 1 s history spacing. Passes (with
the receiving player) and shots come from the events; there are no carry events. Two matches
(``schema.HELD_OUT_SPORTEC``) are the held-out test set.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

from matchlens.tacticsml.train import schema
from matchlens.tacticsml.train.labels import LEvent
from matchlens.tacticsml.train.sources._common import (
    Item, TrackingTable, build_from_iter, download, snap_events, table_from_kloppy, tracking_samples,
)

SOURCE = "sportec"
RAW = schema.ROOT / "raw" / "sportec"
MATCH_IDS = ("J03WPY", "J03WMX", "J03WR9", "J03WN1", "J03WOH", "J03WQQ", "J03WOY")
SAMPLE_RATE = 1 / 5


def fetch(match_id: str, root: Path = RAW) -> tuple[Path, Path, Path]:
    from kloppy._providers.sportec import get_IDSSE_url

    out = []
    for kind in ("meta", "event", "tracking"):
        url = get_IDSSE_url(match_id, kind)
        out.append(download(url, root / match_id / f"{kind}.xml"))
    return tuple(out)  # type: ignore[return-value]


def to_levents(dataset) -> list[LEvent]:
    out: list[LEvent] = []
    for e in dataset.events:
        if e.team is None or e.period is None:
            continue
        name = e.event_name
        result = getattr(e.result, "name", str(e.result)) if e.result is not None else ""
        t = e.timestamp.total_seconds()
        team = str(e.team.team_id)
        player = str(e.player.player_id) if e.player else None
        if name == "pass":
            ok = result == "COMPLETE"
            rec = getattr(e, "receiver_player", None)
            out.append(LEvent(t=t, team_id=team, player_id=player, kind="pass", period=e.period.id,
                              receiver_id=str(rec.player_id) if ok and rec else None,
                              outcome="success" if ok else "fail"))
        elif name == "shot":
            out.append(LEvent(t=t, team_id=team, player_id=player, kind="shot", period=e.period.id,
                              outcome="goal" if result == "GOAL" else "miss"))
        elif name == "recovery":
            out.append(LEvent(t=t, team_id=team, player_id=player, kind="gain", period=e.period.id))
    return out


def load(match_id: str, root: Path = RAW, sample_rate: float = SAMPLE_RATE) -> tuple[TrackingTable, list[LEvent]]:
    from kloppy import sportec

    meta, event, track = fetch(match_id, root)
    ds = sportec.load_tracking(meta_data=str(meta), raw_data=str(track), sample_rate=sample_rate)
    table = table_from_kloppy(ds, lambda fr: fr.timestamp.total_seconds())
    ev = sportec.load_event(event_data=str(event), meta_data=str(meta))
    return table, snap_events(table, to_levents(ev))


def iter_match(match_id: str, root: Path = RAW, random_hz: float = 0.5) -> Iterator[Item]:
    table, levents = load(match_id, root)
    yield from tracking_samples(SOURCE, match_id, table, levents, random_hz, note="DFL / IDSSE (CC BY 4.0)")


def iter_samples(match_ids=None, limit: int | None = None, root: Path = RAW, random_hz: float = 0.5) -> Iterator[Item]:
    for mid in list(match_ids or MATCH_IDS)[:limit]:
        try:
            yield from iter_match(mid, root, random_hz)
        except RuntimeError as exc:
            print(f"sportec: skipping {mid}: {exc}", file=sys.stderr)


def build_shards(match_ids=None, limit: int | None = None, root: Path | None = None, progress: bool = False,
                 random_hz: float = 0.5) -> list[schema.ShardEntry]:
    return build_from_iter(SOURCE, iter_samples(match_ids, limit, random_hz=random_hz), root, "DFL IDSSE", progress)
