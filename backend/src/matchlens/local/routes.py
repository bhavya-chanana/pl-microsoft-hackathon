"""Run a real FotMob match through the pipeline (see local/fotmob.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from matchlens.ingest.adapters import ReplayControl
from matchlens.local import fotmob
from matchlens.runtime.manager import manager

router = APIRouter()


class FotMobRequest(BaseModel):
    match_id: int | None = None
    path: str | None = Field(default=None, description="A saved matchDetails response under backend/data/local/")
    live: bool = Field(default=False, description="Poll a live match; a finished one is replayed")
    speed: float = Field(default=1.0, gt=0, le=60)
    start_minute: float = Field(default=0.0, ge=0, le=100)
    poll_s: float = Field(default=fotmob.DEFAULT_POLL_S, ge=fotmob.MIN_POLL_S, le=300)


def _require_fotmob() -> None:
    if not fotmob.enabled():
        raise HTTPException(404, "the FotMob feed is off; set MATCHLENS_FOTMOB=1")


@router.post("/api/feeds/fotmob")
async def start_fotmob(req: FotMobRequest) -> dict[str, Any]:
    _require_fotmob()
    path = None
    if req.path:
        path = Path(req.path).resolve()
        if not path.is_relative_to(fotmob.CACHE_DIR.parent):
            raise HTTPException(422, f"path must be under {fotmob.CACHE_DIR.parent}")
    if req.match_id is None and path is None:
        raise HTTPException(422, "give match_id or path")
    control = ReplayControl(speed=req.speed, start_elapsed=req.start_minute * 60)
    try:
        adapter = await fotmob.open_adapter(req.match_id, path=path, live=req.live, control=control,
                                            poll_s=req.poll_s)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(502, f"FotMob unavailable: {exc}") from exc
    s = await manager.start_feed(adapter, control, source="fotmob")
    return {"match_id": s.meta.match_id, "run_id": s.run_id, "live": adapter.live,
            "home": s.meta.home.name, "away": s.meta.away.name, "events": len(adapter.latest.events)}


@router.get("/api/feeds/fotmob/stats")
async def fotmob_stats() -> dict[str, Any]:
    """FotMob's own totals, ratings and momentum for the active FotMob run, for comparison."""
    _require_fotmob()
    stats = getattr(manager.require().adapter, "provider_stats", None)
    if stats is None:
        raise HTTPException(404, "the active match is not a FotMob feed")
    return stats()
