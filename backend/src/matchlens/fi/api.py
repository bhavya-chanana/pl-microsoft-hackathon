"""HTTP surface for Football Intelligence under /api/fi.

Browser-facing reads always take the viewer's playhead and are filtered server-side, so a client can never
fetch a goal, card or stat from later in the match than the video it is showing. Ingest routes are for
devices and the simulator only; the frontend proxy does not expose them.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from ..auth import create_session, decode_session
from . import accounts
from .db import Database, default_path
from .engine import Engine, Period, physical_through, snapshot_through
from .ingest import (DuplicateBatch, ingest_ball, ingest_commentary, ingest_operator, ingest_vest, ingest_vision,
                     set_clock_sync)
from .models import BallBatch, ClockSync, CommentaryBatch, LoginRequest, OperatorBatch, Preferences, VestBatch, VisionBatch
from .live import MATCH_ID as LIVE_MATCH_ID, LiveRunner
from .simulator import ensure_seeded

router = APIRouter(prefix="/api/fi", tags=["football-intelligence"])
COOKIE = "fi_session"
VIEWING_DELAY_MS = 90_000


class State:
    def __init__(self) -> None:
        self.db: Database | None = None
        self.engine: Engine | None = None
        self.ready = threading.Event()
        self.error: str | None = None
        self.secret = ""
        self.live: LiveRunner | None = None

    def start(self, path: str | None = None, seed: bool = True) -> None:
        self.db = Database(path or default_path())
        self.engine = Engine(self.db)
        self.secret = accounts.session_secret(default_path().parent)
        if not seed:
            self.ready.set()
            return

        def run() -> None:
            try:
                ensure_seeded(self.db)
                clip = default_path().parent / "sportsmot" / "live_match.json"
                if clip.exists():  # real-footage live match (scripts/sportsmot_to_live.py)
                    self.live = LiveRunner(self.db, clip)
                    self.live.start()
            except Exception as exc:  # surfaced through /api/fi/health
                self.error = repr(exc)
            finally:
                self.ready.set()
        threading.Thread(target=run, name="fi-seed", daemon=True).start()


state = State()


def engine() -> Engine:
    if state.engine is None:
        raise HTTPException(503, "Match data is not initialised")
    if not state.ready.wait(timeout=60):
        raise HTTPException(503, "Match data is still loading")
    return state.engine


def db() -> Database:
    engine()
    assert state.db is not None
    return state.db


# --- Auth -----------------------------------------------------------------------------------------------------------

def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE)
    claims = decode_session(token, state.secret) if token else None
    if not claims or claims.get("kind") != "fi":
        raise HTTPException(401, "Sign in required")
    return {"id": int(claims["sub"]), "username": claims["name"]}


User = Annotated[dict, Depends(current_user)]


@router.post("/auth/login")
def login(req: LoginRequest, response: Response, request: Request) -> dict:
    try:
        user, created = accounts.sign_in(db(), req.username, req.password)
    except accounts.WrongPassword:
        raise HTTPException(401, "That password doesn't match this username.") from None
    token = create_session({"sub": str(user["id"]), "name": user["username"], "kind": "fi"}, state.secret)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=7 * 24 * 3600,
                        secure=os.getenv("MATCHLENS_COOKIE_SECURE", "0") in {"1", "true", "yes"})
    return {"user": user, "created": created, "preferences": accounts.get_preferences(db(), user["id"]).model_dump()}


@router.get("/auth/me")
def me(user: User) -> dict:
    return {"user": user, "preferences": accounts.get_preferences(db(), user["id"]).model_dump()}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.post("/me/preferences")
def save_preferences(prefs: Preferences, user: User) -> dict:
    return accounts.save_preferences(db(), user["id"], prefs).model_dump()


# --- Reads ------------------------------------------------------------------------------------------------------

ViewMode = Literal["live", "as_live", "full_review"]


def _match_or_404(match_id: str) -> dict:
    m = engine().match(match_id)
    if not m:
        raise HTTPException(404, "Unknown match")
    return m


def _live_fields(match_id: str) -> dict:
    return state.live.live_fields() if state.live and match_id == LIVE_MATCH_ID else {}


def _cutoff(match: dict, playhead_ms: int, mode: ViewMode) -> int:
    """The latest moment this request may see. Full review is only for finished matches."""
    if state.live and match["id"] == LIVE_MATCH_ID:
        if mode != "live":
            raise HTTPException(400, "This match is live")
        return max(0, min(playhead_ms, state.live.edge_ms))  # the server's live edge, not the client's
    if mode == "full_review":
        if match["status"] != "completed":
            raise HTTPException(400, "Full review is only available after full time")
        return match["durationMs"]
    if match["status"] == "scheduled":
        return 0
    return max(0, min(playhead_ms, match["durationMs"]))


@router.get("/health")
def health() -> dict:
    return {"ready": state.ready.is_set(), "error": state.error}


@router.get("/matches")
def matches(user: User, spoilers: bool = False) -> list[dict]:
    out = []
    for m in engine().matches():
        score = None
        live = _live_fields(m["id"])
        if live:
            score = engine().score(m["id"], max(0, live["liveEdgeMs"] - m["viewingDelayMs"]))
        elif m["status"] == "live":
            score = engine().score(m["id"], max(0, m.get("liveEdgeStartMs", 0) - VIEWING_DELAY_MS))
        elif m["status"] == "completed" and spoilers:
            score = engine().score(m["id"], m["durationMs"])
        out.append({**m, **live, "score": score})
    return out


@router.get("/matches/{match_id}")
def match_detail(match_id: str, user: User) -> dict:
    return {**_match_or_404(match_id), **_live_fields(match_id), "players": engine().public_roster(match_id)}


@router.get("/matches/{match_id}/view")
def view(match_id: str, user: User, playhead_ms: int = Query(ge=0), mode: ViewMode = "live") -> dict:
    m = _match_or_404(match_id)
    t = _cutoff(m, playhead_ms, mode)
    e = engine()
    # Live: analysis that already exists (by the server's edge) about moments on screen may be shown.
    known = state.live.known_ms if state.live and match_id == LIVE_MATCH_ID else t
    return {"matchId": match_id, "playheadMs": t, "score": e.score(match_id, t, known), "events": e.events(match_id, t, known),
            "pulse": e.pulse(match_id, t), "chips": e.chips(match_id, t), "statsThroughMs": snapshot_through(t, e.timing(match_id))}


@router.get("/matches/{match_id}/stats")
def stats(match_id: str, user: User, playhead_ms: int = Query(ge=0), mode: ViewMode = "live", period: Period = "all") -> dict:
    m = _match_or_404(match_id)
    t = _cutoff(m, playhead_ms, mode)
    e = engine()
    timing = e.timing(match_id)
    through = snapshot_through(t, timing) if mode != "full_review" else t
    if period == "h2" and through <= 45 * 60_000:
        period = "all"
    data = e.sections(match_id, through, period)
    return {"playheadMs": t, "throughMs": through, "physicalThroughMs": physical_through(t, timing), "period": period,
            "refreshedAt": datetime.now(timezone.utc).isoformat(), **data, "shots": e.shots(match_id, through, period),
            "players": e.player_rows(match_id, through, period, t)}


@router.get("/matches/{match_id}/players/{player_id}")
def player(match_id: str, player_id: str, user: User, playhead_ms: int = Query(ge=0), mode: ViewMode = "live") -> dict:
    m = _match_or_404(match_id)
    t = _cutoff(m, playhead_ms, mode)
    e = engine()
    p = next((q for q in e.public_roster(match_id) if q["id"] == player_id), None)
    if not p:
        raise HTTPException(404, "Unknown player")
    through = snapshot_through(t, e.timing(match_id))
    return {"player": p, "throughMs": through, "physicalThroughMs": physical_through(t, e.timing(match_id)),
            "refreshedAt": datetime.now(timezone.utc).isoformat(),
            "stats": e.player_stats(match_id, player_id, t), "heatmap": e.heatmap(match_id, player_id, t),
            "fantasy": e.fantasy_points(match_id, player_id, t),
            "moments": [ev for ev in e.events(match_id, through) if player_id in ev["playerIds"]]}


# --- Ingest (devices and simulator) ---------------------------------------------------------------------------------

def ingest_auth(x_ingest_token: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("MATCHLENS_INGEST_TOKEN")
    if expected and not (x_ingest_token and hmac.compare_digest(x_ingest_token, expected)):
        raise HTTPException(401, "Invalid ingest token")


Ingest = Annotated[None, Depends(ingest_auth)]


def _ingest(fn, match_id: str, batch, key: str | None) -> dict:
    _match_or_404(match_id)
    started = time.perf_counter()
    try:
        n = fn(db(), match_id, batch, key)
    except DuplicateBatch:
        return {"accepted": 0, "duplicate": True}
    return {"accepted": n, "duplicate": False, "ms": round((time.perf_counter() - started) * 1000)}


@router.post("/ingest/{match_id}/vest", status_code=202)
def post_vest(match_id: str, batch: VestBatch, _: Ingest, idempotency_key: Annotated[str | None, Header()] = None) -> dict:
    return _ingest(ingest_vest, match_id, batch, idempotency_key)


@router.post("/ingest/{match_id}/ball", status_code=202)
def post_ball(match_id: str, batch: BallBatch, _: Ingest, idempotency_key: Annotated[str | None, Header()] = None) -> dict:
    return _ingest(ingest_ball, match_id, batch, idempotency_key)


@router.post("/ingest/{match_id}/vision", status_code=202)
def post_vision(match_id: str, batch: VisionBatch, _: Ingest, idempotency_key: Annotated[str | None, Header()] = None) -> dict:
    return _ingest(ingest_vision, match_id, batch, idempotency_key)


@router.post("/ingest/{match_id}/operator", status_code=202)
def post_operator(match_id: str, batch: OperatorBatch, _: Ingest, idempotency_key: Annotated[str | None, Header()] = None) -> dict:
    return _ingest(ingest_operator, match_id, batch, idempotency_key)


@router.post("/ingest/{match_id}/commentary", status_code=202)
def post_commentary(match_id: str, batch: CommentaryBatch, _: Ingest, idempotency_key: Annotated[str | None, Header()] = None) -> dict:
    return _ingest(ingest_commentary, match_id, batch, idempotency_key)


@router.post("/ingest/{match_id}/clock-sync")
def post_clock_sync(match_id: str, sync: ClockSync, _: Ingest) -> dict:
    _match_or_404(match_id)
    set_clock_sync(db(), match_id, sync)
    return {"ok": True}
