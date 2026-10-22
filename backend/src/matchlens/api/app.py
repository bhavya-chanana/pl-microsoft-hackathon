"""HTTP and WebSocket API (Ingest output, Render input)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from matchlens.domain.models import EventType
from matchlens.mcp_server.server import mcp
from matchlens.runtime.manager import manager
from matchlens.runtime.session import event_payload

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        if os.getenv("MATCHLENS_AUTOSTART", "1") == "1":
            await manager.start_synthetic(
                seed=int(os.getenv("MATCHLENS_SEED", "7")),
                speed=float(os.getenv("MATCHLENS_SPEED", "1")),
            )
        yield
        await manager.stop()


app = FastAPI(title="Matchlens", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("MATCHLENS_CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/mcp", mcp.streamable_http_app())


def _session():
    try:
        return manager.require()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="no active match") from exc


class ReplayRequest(BaseModel):
    seed: int = 7
    speed: float = Field(default=1.0, gt=0, le=60)
    start_minute: float = Field(default=0.0, ge=0, le=100)


class ControlRequest(BaseModel):
    speed: float | None = Field(default=None, gt=0, le=60)
    paused: bool | None = None


@app.get("/api/health")
async def health() -> dict[str, Any]:
    s = manager.session
    lat = sorted(s.latency_ms[-500:]) if s and s.latency_ms else []
    return {
        "status": "ok",
        "match_active": s is not None,
        "processing_ms_p95": round(lat[int(len(lat) * 0.95) - 1], 2) if lat else None,
    }


@app.post("/api/replay")
async def start_replay(req: ReplayRequest) -> dict[str, Any]:
    s = await manager.start_synthetic(seed=req.seed, speed=req.speed, start_minute=req.start_minute)
    return {"match_id": s.meta.match_id, "speed": req.speed}


@app.post("/api/replay/control")
async def control_replay(req: ControlRequest) -> dict[str, Any]:
    _session()
    ctl = manager.control
    assert ctl is not None
    if req.speed is not None:
        ctl.speed = req.speed
    if req.paused is True:
        ctl.pause()
    elif req.paused is False:
        ctl.resume()
    return {"speed": ctl.speed, "paused": ctl.paused}


@app.get("/api/match")
async def match() -> dict[str, Any]:
    return _session().snapshot()


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return _session().engine.state()


@app.get("/api/timeline")
async def timeline(
    team_id: str | None = None,
    player_id: str | None = None,
    type: list[str] | None = Query(default=None),
) -> list[dict[str, Any]]:
    s = _session()
    types = {EventType(t) for t in type} if type else None
    rows = s.engine.events(team_id=team_id, player_id=player_id, types=types)
    if types is None:
        return [r for r in s.timeline() if (not team_id or r["team_id"] == team_id)
                and (not player_id or player_id in (r["player_id"], r.get("related_player_id")))]
    return [event_payload(s.engine, e) for e in rows]


@app.get("/api/players/{player_id}")
async def player(player_id: str) -> dict[str, Any]:
    s = _session()
    if player_id not in s.meta.players:
        raise HTTPException(404, "unknown player")
    return s.engine.player_stats(player_id)


@app.get("/api/teams/{team_id}")
async def team(team_id: str) -> dict[str, Any]:
    s = _session()
    if team_id not in (s.meta.home.team_id, s.meta.away.team_id):
        raise HTTPException(404, "unknown team")
    return s.engine.team_stats(team_id)


@app.get("/api/signals")
async def signals() -> list[dict[str, Any]]:
    return [x.model_dump(mode="json") for x in _session().engine.signals()]


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    session = manager.session
    if session is None:
        await socket.send_json({"type": "feed_status", "status": "no_match"})
        await socket.close()
        return
    queue = session.subscribe()
    try:
        await socket.send_json(session.snapshot())
        while True:
            # Re-subscribe if a new replay replaced the session.
            if manager.session is not session and manager.session is not None:
                session.unsubscribe(queue)
                session = manager.session
                queue = session.subscribe()
                await socket.send_json(session.snapshot())
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                await socket.send_json({"type": "ping"})
                continue
            await socket.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        with contextlib.suppress(Exception):
            session.unsubscribe(queue)
