"""HTTP and WebSocket API (Ingest output, Render input)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from matchlens.auth import AuthSettings, create_session, decode_session, verify_google_credential

from matchlens.agents.contracts import FanProfile, Language, Mode
from matchlens.agents.llm import build_clients
from matchlens.agents.pipeline import AgentTeam, InsightPipeline
from matchlens.agents.services import CATCHUP_WINDOWS, FanServices, personalise_feed
from matchlens.domain.models import EventType
from matchlens.fi.api import router as fi_router, state as fi_state
from matchlens.mcp_server.server import mcp
from matchlens.runtime.manager import manager
from matchlens.runtime.session import MatchSession, event_payload
from matchlens.store.base import open_store
from matchlens.store.repository import Repository
from matchlens.store.writer import WriteBehind

log = logging.getLogger(__name__)

# Keep the console readable: Azure SDK request dumps, per-superstep workflow
# chatter and the "publish is a dead end" validation note (it is the sink by design).
for noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.identity", "azure.cosmos",
              "agent_framework._workflows._runner", "agent_framework._workflows._validation"):
    logging.getLogger(noisy).setLevel(logging.ERROR)


class AgentRuntime:
    """Agent team, live insight pipeline and fan services, created at startup."""

    def __init__(self) -> None:
        self.clients = build_clients()
        self.team = AgentTeam.build(self.clients)
        self.pipeline = InsightPipeline(self.team)
        self.fans = FanServices(self.team)

    async def on_signal(self, signal, session) -> None:
        await self.pipeline.handle(signal, session)

    async def on_resume(self, session: MatchSession, run: dict[str, Any]) -> None:
        state = await session.recorder.get("runs", "pipeline")
        decisions = await session.recorder.load("decisions", order_by="seq")
        requeued = await self.pipeline.restore(session, state, decisions)
        log.info("pipeline restored: %d decisions, %d agent jobs re-queued", len(decisions), requeued)


runtime: AgentRuntime | None = None
auth_settings = AuthSettings.from_env()
store_backend = None
SESSION_COOKIE = "matchlens_session"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runtime, store_backend
    fi_state.start()  # sensor-first match data; seeds the demo match in the background
    runtime = AgentRuntime()
    store = await open_store()
    store_backend = store
    writer = WriteBehind(store)
    writer.start()
    manager.attach(Repository(store, writer))
    manager.add_signal_handler(runtime.on_signal)
    manager.add_resume_hook(runtime.on_resume)
    manager.add_reset_hook(runtime.pipeline.reset)
    manager.add_reset_hook(lambda: runtime.clients.use_cassette(
        manager.seed, manager.storyline.title if manager.storyline else None))
    async with mcp.session_manager.run():
        if os.getenv("MATCHLENS_AUTOSTART", "1") == "1":
            await manager.resume_or_start(
                seed=int(os.getenv("MATCHLENS_SEED", "7")),
                speed=float(os.getenv("MATCHLENS_SPEED", "1")),
            )
        yield
        await manager.stop()
        await writer.close()  # flush what the match wrote last
        await store.close()
        store_backend = None


app = FastAPI(title="Matchlens", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("MATCHLENS_CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/mcp", mcp.streamable_http_app())
app.include_router(fi_router)


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


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=20, max_length=10_000)


class FanProfileRequest(BaseModel):
    mode: Mode = Mode.CASUAL
    language: Language = Language.EN
    club_id: str | None = Field(default=None, max_length=80)
    player_id: str | None = Field(default=None, max_length=80)
    metric: str | None = Field(default=None, max_length=80)


class VoteRequest(BaseModel):
    match_id: str = Field(min_length=1, max_length=120)
    choice: str = Field(pattern="^(home|away|close-call)$")


class RatingRequest(BaseModel):
    match_id: str = Field(min_length=1, max_length=120)
    player_id: str = Field(min_length=1, max_length=80)
    score: int = Field(ge=1, le=5)


def _auth_user(request: Request) -> dict[str, Any]:
    if not auth_settings.session_secret:
        raise HTTPException(503, "Sign-in is not configured on this server")
    token = request.cookies.get(SESSION_COOKIE)
    claims = decode_session(token, auth_settings.session_secret) if token else None
    if not claims:
        raise HTTPException(401, "Sign in to continue")
    return claims


def _fan_store():
    if store_backend is None:
        raise HTTPException(503, "Fan account storage is not ready")
    return store_backend


def _account_doc_id(user_id: str, *parts: str) -> str:
    import hashlib
    return hashlib.sha256(": ".join((user_id, *parts)).encode()).hexdigest()


def _runtime() -> AgentRuntime:
    if runtime is None:
        raise HTTPException(503, "agents not ready")
    return runtime


def _p95(values: list[float]) -> float | None:
    v = sorted(values)
    return round(v[max(0, int(len(v) * 0.95) - 1)], 2) if v else None


@app.get("/api/health")
async def health() -> dict[str, Any]:
    s = manager.session
    agent_lat = [i["latency_ms"] for i in (s.insights.values() if s else []) if i.get("latency_ms")]
    return {
        "status": "ok",
        "match_active": s is not None,
        "llm": runtime.clients.label if runtime else None,
        "processing_ms_p95": _p95(s.latency_ms[-500:]) if s else None,
        "agent_insight_ms_p95": _p95(agent_lat),
        "run_id": s.run_id if s else None,
        "store": manager.repo.stats(),
    }


@app.post("/api/auth/google")
async def google_login(req: GoogleLoginRequest, response: Response) -> dict[str, Any]:
    if not auth_settings.google_client_id or not auth_settings.session_secret:
        raise HTTPException(503, "Set GOOGLE_CLIENT_ID and MATCHLENS_AUTH_SECRET to enable Google sign-in")
    try:
        user = verify_google_credential(req.credential, auth_settings.google_client_id)
    except Exception as exc:  # Google verification errors should not disclose token details.
        log.info("Google sign-in rejected: %s", type(exc).__name__)
        raise HTTPException(401, "Google sign-in could not be verified") from exc
    expires = 60 * 60 * 24 * 7
    response.set_cookie(SESSION_COOKIE, create_session(user, auth_settings.session_secret, expires),
                        max_age=expires, httponly=True, secure=auth_settings.secure_cookie,
                        samesite="lax", path="/")
    store = _fan_store()
    profile = await store.read("fan_profiles", user["sub"], "profile")
    if profile is None:
        profile = {"mode": Mode.CASUAL.value, "language": Language.EN.value,
                   "club_id": None, "player_id": None, "metric": None}
        await store.upsert_many("fan_profiles", user["sub"], [{"id": "profile", "run_id": user["sub"], **profile}])
    return {"user": user, "profile": profile}


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    user = _auth_user(request)
    profile = await _fan_store().read("fan_profiles", user["sub"], "profile")
    return {"user": {k: user.get(k, "") for k in ("sub", "email", "name", "picture")},
            "profile": profile or {"mode": "casual", "language": "en", "club_id": None,
                                   "player_id": None, "metric": None}}


@app.post("/api/auth/logout")
async def auth_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=auth_settings.secure_cookie,
                           samesite="lax", path="/")
    return {"ok": True}


@app.post("/api/profile")
async def save_fan_profile(req: FanProfileRequest, request: Request) -> dict[str, Any]:
    user = _auth_user(request)
    profile = req.model_dump(mode="json")
    await _fan_store().upsert_many("fan_profiles", user["sub"],
                                   [{"id": "profile", "run_id": user["sub"], **profile}])
    return profile


@app.get("/api/engagement")
async def fan_engagement(request: Request, match_id: str = Query(min_length=1, max_length=120)) -> dict[str, Any]:
    user = _auth_user(request)
    store = _fan_store()
    vote_doc = await store.read("fan_poll_votes", match_id, _account_doc_id(user["sub"], match_id))
    rows = await store.query("fan_player_ratings", match_id)
    ratings = {row["player_id"]: row["score"] for row in rows if row.get("user_id") == user["sub"]}
    rating_totals: dict[str, list[int]] = {}
    for row in rows:
        if row.get("player_id") and isinstance(row.get("score"), int):
            totals = rating_totals.setdefault(row["player_id"], [0, 0])
            totals[0] += row["score"]
            totals[1] += 1
    rating_averages = {player: {"average": round(total / count, 1), "count": count}
                       for player, (total, count) in rating_totals.items()}
    counts = {"home": 0, "away": 0, "close-call": 0}
    for row in await store.query("fan_poll_votes", match_id):
        choice = row.get("choice")
        if choice in counts:
            counts[choice] += 1
    return {"vote": vote_doc.get("choice") if vote_doc else None, "ratings": ratings,
            "poll_counts": counts, "rating_averages": rating_averages}


@app.post("/api/engagement/vote")
async def cast_fan_vote(req: VoteRequest, request: Request) -> dict[str, Any]:
    user = _auth_user(request)
    session = _session()
    if req.match_id != session.meta.match_id:
        raise HTTPException(409, "The poll is for a different match")
    doc_id = _account_doc_id(user["sub"], req.match_id)
    from matchlens.store.repository import now_iso
    doc = {"id": doc_id, "run_id": req.match_id, "user_id": user["sub"],
           "choice": req.choice, "updated_at": now_iso()}
    await _fan_store().upsert_many("fan_poll_votes", req.match_id, [doc])
    return {"vote": req.choice}


@app.post("/api/engagement/rating")
async def rate_fan_player(req: RatingRequest, request: Request) -> dict[str, Any]:
    user = _auth_user(request)
    session = _session()
    if req.match_id != session.meta.match_id or req.player_id not in session.meta.players:
        raise HTTPException(404, "Unknown player or match")
    doc_id = _account_doc_id(user["sub"], req.match_id, req.player_id)
    from matchlens.store.repository import now_iso
    doc = {"id": doc_id, "run_id": req.match_id, "user_id": user["sub"],
           "player_id": req.player_id, "score": req.score, "updated_at": now_iso()}
    await _fan_store().upsert_many("fan_player_ratings", req.match_id, [doc])
    return {"player_id": req.player_id, "score": req.score}


def _profile(mode: Mode, language: Language, club_id: str | None, player_id: str | None,
             metric: str | None) -> FanProfile:
    return FanProfile(mode=mode, language=language, club_id=club_id, player_id=player_id, metric=metric)


@app.get("/api/insights")
async def insights(
    mode: Mode = Mode.CASUAL, language: Language = Language.EN, club_id: str | None = None,
    player_id: str | None = None, metric: str | None = None, limit: int = Query(default=50, le=200),
) -> list[dict[str, Any]]:
    """Personalised insight feed (FR-035 to FR-037)."""
    return personalise_feed(_session(), _profile(mode, language, club_id, player_id, metric), limit)


@app.get("/api/insights/{insight_id}")
async def insight(insight_id: str) -> dict[str, Any]:
    """Full insight with every variant, evidence ids and the fact-check report."""
    s = _session()
    found = s.insights.get(insight_id) or await s.recorder.get("insights", insight_id)
    if not found:
        raise HTTPException(404, "unknown insight")
    return found


@app.get("/api/overlays")
async def overlays(active_at: float | None = None) -> list[dict[str, Any]]:
    """Overlay JSON for the broadcast renderer; optionally only those on screen at a match time."""
    rows = [o for o in _session().overlays.values() if o["stage"] != "retracted"]
    if active_at is not None:
        rows = [o for o in rows if o["start_elapsed"] <= active_at < o["start_elapsed"] + o["duration_s"]]
    return sorted(rows, key=lambda o: o["start_elapsed"])


@app.get("/api/catchup")
async def catchup(
    minutes: int = 15, mode: Mode = Mode.CASUAL, language: Language = Language.EN, club_id: str | None = None,
    player_id: str | None = None, metric: str | None = None,
) -> dict[str, Any]:
    """What did I miss? (FR-039)"""
    if minutes not in CATCHUP_WINDOWS:
        raise HTTPException(422, f"minutes must be one of {list(CATCHUP_WINDOWS)}")
    result = await _runtime().fans.catchup(_session(), minutes, _profile(mode, language, club_id, player_id, metric))
    return result.model_dump(mode="json")


@app.get("/api/recap")
async def recap(
    mode: Mode = Mode.CASUAL, language: Language = Language.EN, club_id: str | None = None,
    player_id: str | None = None, metric: str | None = None,
) -> dict[str, Any]:
    """Half-time, full-time or on-demand recap (FR-040)."""
    result = await _runtime().fans.recap(_session(), _profile(mode, language, club_id, player_id, metric))
    return result.model_dump(mode="json")


@app.get("/api/coach")
async def coach_brief(team_id: str, language: Language = Language.EN) -> dict[str, Any]:
    """Coach persona: ranked tactical brief for one side, with evidence, trade-off and confidence."""
    s = _session()
    if team_id not in (s.meta.home.team_id, s.meta.away.team_id):
        raise HTTPException(404, "unknown team")
    result = await _runtime().fans.coach_brief(s, team_id, language.value)
    return result.model_dump(mode="json")


@app.get("/api/agents/decisions")
async def decisions(limit: int = Query(default=100, le=1000)) -> list[dict[str, Any]]:
    """Coordinator routing decisions, for transparency in the demo."""
    return _runtime().pipeline.decisions[-limit:]


@app.post("/api/replay")
async def start_replay(req: ReplayRequest) -> dict[str, Any]:
    s = await manager.start_synthetic(seed=req.seed, speed=req.speed, start_minute=req.start_minute)
    return {"match_id": s.meta.match_id, "run_id": s.run_id, "speed": req.speed}


@app.get("/api/runs")
async def runs(limit: int = Query(default=20, le=100)) -> list[dict[str, Any]]:
    """Recent replays stored in the database, newest first."""
    return [{k: v for k, v in r.items() if k != "state"} for r in await manager.repo.list_runs(limit)]


@app.post("/api/replay/control")
async def control_replay(req: ControlRequest) -> dict[str, Any]:
    _session()
    manager.set_control(speed=req.speed, paused=req.paused)
    ctl = manager.control
    assert ctl is not None
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
