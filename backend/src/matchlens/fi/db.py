"""SQLite storage for the Football Intelligence data model (Postgres-shaped; swap the connection for production)."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS preferences (
  user_id INTEGER PRIMARY KEY REFERENCES users(id), body TEXT NOT NULL, updated_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS matches (
  id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roster (
  match_id TEXT NOT NULL, player_id TEXT NOT NULL, team TEXT NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (match_id, player_id));

-- Every ingest call; the idempotency key makes retries safe.
CREATE TABLE IF NOT EXISTS ingest_batches (
  idempotency_key TEXT PRIMARY KEY, match_id TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
  items INTEGER NOT NULL, received_at REAL NOT NULL);

-- Raw sensor data.
CREATE TABLE IF NOT EXISTS vest_frames (
  match_id TEXT NOT NULL, player_id TEXT NOT NULL, media_ms INTEGER NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, speed_mps REAL NOT NULL, heart_rate INTEGER, on_pitch INTEGER NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (match_id, player_id, media_ms));
CREATE TABLE IF NOT EXISTS sensor_events (
  id TEXT NOT NULL, match_id TEXT NOT NULL, kind TEXT NOT NULL, media_ms INTEGER NOT NULL, available_ms INTEGER NOT NULL,
  team TEXT, player_id TEXT, target_player_id TEXT, body TEXT NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (match_id, id));
CREATE INDEX IF NOT EXISTS sensor_events_time ON sensor_events (match_id, kind, media_ms);
CREATE TABLE IF NOT EXISTS vision_observations (
  id TEXT NOT NULL, match_id TEXT NOT NULL, kind TEXT NOT NULL, media_from_ms INTEGER NOT NULL, media_to_ms INTEGER NOT NULL,
  available_ms INTEGER NOT NULL, team TEXT, player_ids TEXT NOT NULL, confidence REAL NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (match_id, id));
CREATE TABLE IF NOT EXISTS clock_sync (
  match_id TEXT NOT NULL, source TEXT NOT NULL, offset_ms INTEGER NOT NULL, method TEXT NOT NULL, set_at REAL NOT NULL,
  PRIMARY KEY (match_id, source));

-- Canonical, append-only event ledger.
CREATE TABLE IF NOT EXISTS event_revisions (
  match_id TEXT NOT NULL, event_id TEXT NOT NULL, revision INTEGER NOT NULL, type TEXT NOT NULL, status TEXT NOT NULL,
  media_ms INTEGER NOT NULL, available_ms INTEGER NOT NULL, team TEXT, player_ids TEXT NOT NULL, note TEXT,
  evidence TEXT NOT NULL, source TEXT NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (match_id, event_id, revision));
CREATE INDEX IF NOT EXISTS event_revisions_avail ON event_revisions (match_id, available_ms);

-- Shots (ball sensor strike + fusion outcome), the shot map's source.
CREATE TABLE IF NOT EXISTS shots (
  match_id TEXT NOT NULL, id TEXT NOT NULL, media_ms INTEGER NOT NULL, available_ms INTEGER NOT NULL, team TEXT NOT NULL,
  player_id TEXT NOT NULL, event_id TEXT, body TEXT NOT NULL, PRIMARY KEY (match_id, id));

-- Physical snapshots from vest frames, one row per player per 15 s boundary (cumulative).
CREATE TABLE IF NOT EXISTS physical_snapshots (
  match_id TEXT NOT NULL, player_id TEXT NOT NULL, through_ms INTEGER NOT NULL,
  minutes REAL NOT NULL, distance_m REAL NOT NULL, hsr_m REAL NOT NULL, sprint_m REAL NOT NULL, sprints INTEGER NOT NULL,
  top_speed_kmh REAL, hr_sum REAL NOT NULL, hr_n INTEGER NOT NULL,
  PRIMARY KEY (match_id, player_id, through_ms));

-- AI commentary per event and persona (mock LLM in the demo).
CREATE TABLE IF NOT EXISTS commentary (
  match_id TEXT NOT NULL, event_id TEXT NOT NULL, available_ms INTEGER NOT NULL, headline TEXT NOT NULL, copy TEXT NOT NULL,
  model TEXT NOT NULL, PRIMARY KEY (match_id, event_id));
"""


def default_path() -> Path:
    return Path(os.getenv("MATCHLENS_FI_DB", Path(__file__).resolve().parents[3] / ".data" / "fi.sqlite"))


class Database:
    """One connection guarded by a lock: simple and safe for a single FastAPI process."""

    def __init__(self, path: Path | str | None = None) -> None:
        path = Path(path) if path not in (None, ":memory:") else path
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path or default_path()), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.version = 0  # bumped on every write; read caches key on it

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def write_many(self, sql: str, rows: list[tuple]) -> None:
        with self.lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()
            self.version += 1

    def commit(self) -> None:
        with self.lock:
            self.conn.commit()
            self.version += 1
