"""``matches`` documents for matches that exist only as video (no FotMob id yet)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def video_match_doc(match_id: str, home: str, away: str, competition: str, *, kickoff_utc: str | None = None,
                    video: Path | None = None, status: str = "finished", score: dict[str, int] | None = None,
                    note: str | None = None, clock_from_s: float | None = None,
                    clock_to_s: float | None = None) -> dict[str, Any]:
    """A match doc shaped like the FotMob ones (``source: "video"``). With no ``kickoff_utc`` the recording's
    modification time stands in, flagged ``kickoff_approx`` so nobody reads it as the real kick-off."""
    approx = kickoff_utc is None
    if approx:
        mtime = video.stat().st_mtime if video is not None and video.exists() else datetime.now(UTC).timestamp()
        kickoff_utc = datetime.fromtimestamp(mtime, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    doc: dict[str, Any] = {"id": match_id, "match_id": match_id, "source": "video", "home": {"name": home},
                           "away": {"name": away}, "competition": competition, "kickoff_utc": kickoff_utc,
                           "kickoff_approx": approx, "status": status, "score": score}
    if video is not None:
        doc["video"] = video.name
    if clock_from_s is not None and clock_to_s is not None:  # the stretch of match clock the footage covers
        doc["clock_from_s"], doc["clock_to_s"] = round(clock_from_s, 2), round(clock_to_s, 2)
    if note:
        doc["note"] = note
    return doc
