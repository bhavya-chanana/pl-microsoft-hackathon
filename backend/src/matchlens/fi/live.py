"""Live runner: replays a real-footage clip through ingest on the wall clock, as if the vests were on the pitch now.

Input is ``.data/sportsmot/live_match.json`` (made by ``scripts/sportsmot_to_live.py``). Every second the runner posts
the vest frames captured up to the live edge, raises a fusion event the first time a player passes sprint speed, and
adds template commentary for it. When the clip has been fully watched it clears the match and starts again, so the
demo always has a live match. Reads never see past ``edge_ms``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from .db import Database
from .ingest import SPRINT_MPS, ingest_commentary, ingest_operator, ingest_vest
from .insights import (CHECK_EVERY_MS, SHAPE_COOLDOWN_MS, WINDOW_MS, InsightWriter, post_window_event, shape_reasons,
                       sprint_reason)
from .models import Commentary, CommentaryBatch, EventRef, OperatorBatch, OperatorEvent, VestBatch, VestFrame

log = logging.getLogger(__name__)

MATCH_ID = "smot-live"
VIEWING_DELAY_MS = 15_000  # short clip, so a short delay; still covers typical model latency
RESTART_AFTER_MS = 25_000  # pause after the clip ends before going live again
MATCH_TABLES = ("vest_frames", "sensor_events", "vision_observations", "event_revisions", "shots", "physical_snapshots",
                "commentary", "clock_sync", "ingest_batches", "roster")


class LiveRunner:
    def __init__(self, db: Database, path: Path) -> None:
        self.db = db
        self.data = json.loads(path.read_text())
        self.duration = int(self.data["durationMs"])
        self.epoch = 0
        self.started = time.monotonic()
        self._posted_ms = -1
        self._sprinted: set[str] = set()
        self._windows: set[int] = set()
        self._last_shape: dict | None = None
        self._last_shape_ms = -SHAPE_COOLDOWN_MS
        self._fastest_kmh = 0.0
        self.insights = InsightWriter(db, MATCH_ID, self.data["players"])
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def summary(self) -> dict:
        return {
            "id": MATCH_ID, "competition": "Bundesliga · real footage (SportsMOT)", "venue": "Red Bull Arena · positions from video tracking",
            "kickoffLabel": "Live now", "status": "live",
            "home": {"id": "home", "name": "RB Leipzig", "short": "RBL"}, "away": {"id": "away", "name": "Bayern Munich", "short": "FCB"},
            "durationMs": self.duration, "viewingDelayMs": VIEWING_DELAY_MS, "serverClock": True,
            "snapshot": {"fullMs": 5_000, "lagMs": 3_000, "physicalMs": 5_000},
            "sourceNote": self.data["source"],
        }

    @property
    def edge_ms(self) -> int:
        return min(self.duration, int((time.monotonic() - self.started) * 1000))

    @property
    def known_ms(self) -> int:
        """Real time since kick-off on the media clock. Keeps running after the clip ends, so analysis that finishes
        then still counts as known (it only describes moments already played)."""
        return int((time.monotonic() - self.started) * 1000)

    def live_fields(self) -> dict:
        return {"liveEdgeMs": self.edge_ms, "epoch": self.epoch}

    # --- Lifecycle ----------------------------------------------------------------------------------------------

    def start(self) -> None:
        self._reset()
        threading.Thread(target=self._run, name="fi-live", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _reset(self) -> None:
        with self._lock, self.db.lock:
            for table in MATCH_TABLES:
                column = "match_id"
                self.db.conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (MATCH_ID,))
            self.db.conn.execute("INSERT OR REPLACE INTO matches VALUES (?, ?)", (MATCH_ID, json.dumps(self.summary)))
            for p in self.data["players"]:
                self.db.conn.execute("INSERT OR REPLACE INTO roster VALUES (?, ?, ?, ?)", (MATCH_ID, p["id"], p["team"], json.dumps(p)))
            self.db.conn.commit()
            self.db.version += 1
            self.epoch += 1
            self.started = time.monotonic()
            self._posted_ms = -1
            self._sprinted.clear()
            self._windows.clear()
            self._last_shape, self._last_shape_ms, self._fastest_kmh = None, -SHAPE_COOLDOWN_MS, 0.0
        self._post_operator(0, "kickoff", "Live feed started", {
            "diehard": "We're live. Tracking every player from the broadcast feed.",
            "casual": "Live now: this clip is being tracked as it plays.",
            "tactical": "Live window open. Positions estimated from broadcast video (homography + pan correction).",
            "fantasy": "Live. Physical numbers will build up as the clip plays.",
        })

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            try:
                elapsed = int((time.monotonic() - self.started) * 1000)
                if elapsed > self.duration + VIEWING_DELAY_MS + RESTART_AFTER_MS:
                    log.info("live clip finished; restarting")
                    self._reset()
                    continue
                self._tick(min(elapsed, self.duration))
            except Exception:  # keep the demo alive; the next tick retries
                log.exception("live runner tick failed")

    def _tick(self, edge: int) -> None:
        with self._lock:
            new = [f for f in self.data["frames"] if self._posted_ms < f["media_ms"] <= edge]
            if not new:
                return
            ingest_vest(self.db, MATCH_ID, VestBatch(source="sensor", frames=[VestFrame(**f) for f in new]),
                        key=f"{MATCH_ID}:{self.epoch}:vest:{edge}")
            previous, self._posted_ms = self._posted_ms, edge
            epoch = self.epoch
        for f in new:
            if f["speed_mps"] < SPRINT_MPS or f["player_id"] in self._sprinted:
                continue
            self._sprinted.add(f["player_id"])
            kmh = round(f["speed_mps"] * 3.6, 1)
            reason = sprint_reason(kmh, self._fastest_kmh)
            self._fastest_kmh = max(self._fastest_kmh, kmh)
            if reason:
                self._sprint_event(f, epoch, reason)
        # Team shape is checked every 5 s but only shown when it changes in a way a viewer would notice.
        for b in range((previous // CHECK_EVERY_MS + 1) * CHECK_EVERY_MS, edge + 1, CHECK_EVERY_MS):
            if b in self._windows or b < WINDOW_MS:
                continue
            self._windows.add(b)
            facts = self.insights.window_facts(b)
            reasons = shape_reasons(self._last_shape, facts)
            if not reasons or b - self._last_shape_ms < SHAPE_COOLDOWN_MS:
                continue
            payload = {**facts, "why_now": reasons}
            if self._last_shape:  # lets the model compare with what viewers saw last time
                payload["previous_window"] = {k: self._last_shape[k] for k in ("window", "teams", "more_compact",
                                              "centroid_gap_along_pitch_m") if k in self._last_shape}
            self._last_shape, self._last_shape_ms = facts, b
            event_id = post_window_event(self.db, MATCH_ID, epoch, b)
            self.insights.submit(event_id, b, "team shape change", payload, fallback=Commentary(
                event_id=event_id, available_ms=b + 2000, headline="Shape check", model="template",
                copy_by_persona={p: "Shape snapshot for RB Leipzig and Bayern Munich from tracking; AI analysis unavailable right now." for p in
                                 ("diehard", "casual", "tactical", "fantasy")}))

    # --- Derived events -----------------------------------------------------------------------------------------

    def _player(self, pid: str) -> dict:
        return next(p for p in self.data["players"] if p["id"] == pid)

    def _sprint_event(self, frame: dict, epoch: int, reason: str) -> None:
        p = self._player(frame["player_id"])
        kmh = round(frame["speed_mps"] * 3.6, 1)
        event_id = f"sprint-{epoch}-{p['id']}"
        ref = EventRef(id=event_id, type="sprint", team=p["team"], player_ids=[p["id"]])
        at = frame["media_ms"]
        ingest_operator(self.db, MATCH_ID, OperatorBatch(source="sensor", events=[
            OperatorEvent(media_ms=at, available_ms=at + 1000, event=ref)]), key=f"{MATCH_ID}:{epoch}:{event_id}")
        name = p["name"]
        self.insights.submit(event_id, at, "sprint", {**self.insights.sprint_facts(frame), "why_now": [reason]}, fallback=Commentary(
            event_id=event_id, available_ms=at + 3000, headline=f"Sprint — {name} {kmh} km/h", model="template",
            copy_by_persona={
                "diehard": f"{name} hits {kmh} km/h — first burst over sprint speed.",
                "casual": f"{name} sprints at {kmh} km/h.",
                "tactical": f"{name} crosses 25 km/h ({kmh}); speed from tracked positions over ~1 s, pan-corrected.",
                "fantasy": f"{name}: {kmh} km/h sprint. No points; physical data only.",
            }))

    def _post_operator(self, at: int, kind: str, headline: str, copy: dict[str, str]) -> None:
        event_id = f"{kind}-{self.epoch}"
        ingest_operator(self.db, MATCH_ID, OperatorBatch(source="operator", events=[
            OperatorEvent(media_ms=at, available_ms=at, event=EventRef(id=event_id, type=kind, critical=True))]),
            key=f"{MATCH_ID}:{self.epoch}:{event_id}")
        ingest_commentary(self.db, MATCH_ID, CommentaryBatch(items=[Commentary(
            event_id=event_id, available_ms=at, headline=headline, copy_by_persona=copy, model="template")]),
            key=f"{MATCH_ID}:{self.epoch}:c-{event_id}")
