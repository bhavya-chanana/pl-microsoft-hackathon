"""Idempotent ingest, fusion into the event ledger, and physical snapshot aggregation.

The simulator and real devices share these functions; the HTTP layer only validates and forwards.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict

from .db import Database
from .models import BallBatch, ClockSync, CommentaryBatch, EventRef, OperatorBatch, VestBatch, VisionBatch

PHYSICAL_SNAPSHOT_MS = 15_000
HSR_MPS = 5.5      # 19.8 km/h
SPRINT_MPS = 7.0   # 25.2 km/h


class DuplicateBatch(Exception):
    """The idempotency key was already applied; the caller treats this as success."""


def _claim(db: Database, key: str | None, match_id: str, kind: str, source: str, items: int) -> None:
    if not key:
        return
    if db.query("SELECT 1 FROM ingest_batches WHERE idempotency_key = ?", (key,)):
        raise DuplicateBatch(key)
    db.execute("INSERT INTO ingest_batches VALUES (?, ?, ?, ?, ?, ?)", (key, match_id, kind, source, items, time.time()))


# --- Ledger -----------------------------------------------------------------------------------------------------

def append_revision(db: Database, match_id: str, ref: EventRef, *, status: str, media_ms: int, available_ms: int,
                    note: str | None, evidence: dict, source: str) -> int:
    """Append a revision to the canonical event; earlier revisions are never changed."""
    row = db.query("SELECT MAX(revision) AS r FROM event_revisions WHERE match_id = ? AND event_id = ?", (match_id, ref.id))
    revision = (row[0]["r"] or 0) + 1
    body = {"critical": ref.critical, **ref.attributes}
    db.execute(
        "INSERT INTO event_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (match_id, ref.id, revision, ref.type, status, media_ms, available_ms, ref.team, json.dumps(ref.player_ids), note,
         json.dumps(evidence), source, json.dumps(body)),
    )
    return revision


# --- Sensors --------------------------------------------------------------------------------------------------------

def ingest_ball(db: Database, match_id: str, batch: BallBatch, key: str | None = None) -> int:
    _claim(db, key, match_id, "ball", batch.source, len(batch.events))
    for e in batch.events:
        db.execute("INSERT OR IGNORE INTO sensor_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (e.id, match_id, e.kind, e.media_ms, e.available_ms, e.team, e.player_id, e.target_player_id,
                    json.dumps(e.attributes), batch.source))
        if e.kind == "shot":
            db.execute("INSERT OR IGNORE INTO shots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (match_id, e.id, e.media_ms, e.available_ms, e.team, e.player_id, e.event.id if e.event else None,
                        json.dumps(e.attributes)))
        if e.event:
            # Fusion: the ball sensor sees a line cross before anyone confirms it, so goals start provisional.
            status = "provisional" if e.kind == "goal_line_cross" else "confirmed"
            note = "Ball crossed the line (ball sensor)" if e.kind == "goal_line_cross" else None
            append_revision(db, match_id, e.event, status=status, media_ms=e.media_ms, available_ms=e.available_ms, note=note,
                            evidence={"sensor_event_ids": [e.id]}, source="fusion")
    db.commit()
    return len(batch.events)


def ingest_vision(db: Database, match_id: str, batch: VisionBatch, key: str | None = None) -> int:
    _claim(db, key, match_id, "vision", batch.source, len(batch.observations))
    for o in batch.observations:
        db.execute("INSERT OR IGNORE INTO vision_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (o.id, match_id, o.kind, o.media_from_ms, o.media_to_ms, o.available_ms, o.team, json.dumps(o.player_ids),
                    o.confidence, json.dumps({"description": o.description}), batch.source))
        if not o.event:
            continue
        if o.kind == "goal_confirmed":
            status, note = "confirmed", "Restart kick-off confirmed on video"
        elif o.kind == "var_overturn":
            status, note = "overturned", o.description or "Overturned on review (video)"
        else:
            status, note = "confirmed", None
        existing = db.query("SELECT media_ms FROM event_revisions WHERE match_id = ? AND event_id = ? ORDER BY revision LIMIT 1",
                            (match_id, o.event.id))
        media_ms = existing[0]["media_ms"] if existing else o.media_from_ms
        append_revision(db, match_id, o.event, status=status, media_ms=media_ms, available_ms=o.available_ms, note=note,
                        evidence={"vision_observation_ids": [o.id], "confidence": o.confidence}, source="llm_vision")
    db.commit()
    return len(batch.observations)


def ingest_operator(db: Database, match_id: str, batch: OperatorBatch, key: str | None = None) -> int:
    _claim(db, key, match_id, "operator", batch.source, len(batch.events))
    # Vest-derived moments (e.g. a top-speed record) arrive here with source "sensor"; they are fusion facts.
    source = "fusion" if batch.source == "sensor" else "operator"
    for e in batch.events:
        append_revision(db, match_id, e.event, status=e.status, media_ms=e.media_ms, available_ms=e.available_ms, note=e.note,
                        evidence={source: True}, source=source)
    db.commit()
    return len(batch.events)


def ingest_commentary(db: Database, match_id: str, batch: CommentaryBatch, key: str | None = None) -> int:
    _claim(db, key, match_id, "commentary", "llm", len(batch.items))
    db.write_many("INSERT OR REPLACE INTO commentary VALUES (?, ?, ?, ?, ?, ?)",
                  [(match_id, c.event_id, c.available_ms, c.headline, json.dumps(c.copy_by_persona), c.model) for c in batch.items])
    return len(batch.items)


def set_clock_sync(db: Database, match_id: str, sync: ClockSync) -> None:
    db.execute("INSERT OR REPLACE INTO clock_sync VALUES (?, ?, ?, ?, ?)", (match_id, sync.source, sync.offset_ms, sync.method, time.time()))
    db.commit()


# --- Vests and physical snapshots -------------------------------------------------------------------------------

def ingest_vest(db: Database, match_id: str, batch: VestBatch, key: str | None = None) -> int:
    """Store frames, then roll every player's cumulative physical snapshot forward to each completed 15 s boundary."""
    _claim(db, key, match_id, "vest", batch.source, len(batch.frames))
    db.write_many("INSERT OR IGNORE INTO vest_frames VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  [(match_id, f.player_id, f.media_ms, f.x, f.y, f.speed_mps, f.heart_rate, int(f.on_pitch), batch.source)
                   for f in batch.frames])
    latest: dict[str, int] = defaultdict(int)
    for f in batch.frames:
        latest[f.player_id] = max(latest[f.player_id], f.media_ms)
    for player_id, last_ms in latest.items():
        _roll_physical(db, match_id, player_id, last_ms)
    db.commit()
    return len(batch.frames)


def _roll_physical(db: Database, match_id: str, player_id: str, frames_through_ms: int) -> None:
    prev = db.query("SELECT * FROM physical_snapshots WHERE match_id = ? AND player_id = ? ORDER BY through_ms DESC LIMIT 1",
                    (match_id, player_id))
    state = dict(prev[0]) if prev else {"through_ms": 0, "minutes": 0.0, "distance_m": 0.0, "hsr_m": 0.0, "sprint_m": 0.0,
                                        "sprints": 0, "top_speed_kmh": None, "hr_sum": 0.0, "hr_n": 0}
    start = state["through_ms"]
    boundary = (frames_through_ms // PHYSICAL_SNAPSHOT_MS) * PHYSICAL_SNAPSHOT_MS
    if boundary <= start and prev:
        return
    frames = db.query("SELECT media_ms, speed_mps, heart_rate, on_pitch FROM vest_frames WHERE match_id = ? AND player_id = ? "
                      "AND media_ms > ? AND media_ms <= ? ORDER BY media_ms", (match_id, player_id, start - 2000, boundary))
    before = [f for f in frames if f["media_ms"] <= start]
    frames = [f for f in frames if f["media_ms"] > start]
    prev_ms = before[-1]["media_ms"] if before else None
    sprinting = bool(before and before[-1]["speed_mps"] >= SPRINT_MPS)
    rows = []
    next_boundary = start + PHYSICAL_SNAPSHOT_MS if prev else PHYSICAL_SNAPSHOT_MS
    if not prev:
        rows.append((match_id, player_id, 0, 0.0, 0.0, 0.0, 0.0, 0, None, 0.0, 0))
    for f in frames:
        while f["media_ms"] > next_boundary:
            rows.append(_snapshot_row(match_id, player_id, next_boundary, state))
            next_boundary += PHYSICAL_SNAPSHOT_MS
        dt = min(2.0, (f["media_ms"] - prev_ms) / 1000) if prev_ms is not None else 1.0
        prev_ms = f["media_ms"]
        if not f["on_pitch"]:
            sprinting = False
            continue
        v = f["speed_mps"]
        state["minutes"] += dt / 60
        state["distance_m"] += v * dt
        if v >= HSR_MPS:
            state["hsr_m"] += v * dt
        if v >= SPRINT_MPS:
            state["sprint_m"] += v * dt
            if not sprinting:
                state["sprints"] += 1
        sprinting = v >= SPRINT_MPS
        kmh = v * 3.6
        state["top_speed_kmh"] = kmh if state["top_speed_kmh"] is None else max(state["top_speed_kmh"], kmh)
        if f["heart_rate"]:
            state["hr_sum"] += f["heart_rate"]
            state["hr_n"] += 1
    while next_boundary <= boundary:
        rows.append(_snapshot_row(match_id, player_id, next_boundary, state))
        next_boundary += PHYSICAL_SNAPSHOT_MS
    db.write_many("INSERT OR REPLACE INTO physical_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def _snapshot_row(match_id: str, player_id: str, through_ms: int, s: dict) -> tuple:
    return (match_id, player_id, through_ms, s["minutes"], s["distance_m"], s["hsr_m"], s["sprint_m"], s["sprints"],
            s["top_speed_kmh"], s["hr_sum"], s["hr_n"])
