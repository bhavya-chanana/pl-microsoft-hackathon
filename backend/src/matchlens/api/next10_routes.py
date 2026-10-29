"""Read API and live WebSocket for the next-10-seconds companion (``MATCHLENS_NEXT10=1``).

Mounted by ``mount(app)``; ``api/app.py`` calls it, and it does nothing unless the flag is set. The router opens a
``Next10Store`` (database ``matchlens_next10``, never the deployed app's) when the app starts and closes it on
shutdown. If the store is unreachable the app still starts and these routes answer 503.

    GET /api/next10/matches                                   matches, newest kickoff first
    GET /api/next10/matches/{match_id}                        {match, score}
    GET /api/next10/matches/{match_id}/predictions?kind=&team=&from_s=&to_s=&last=1   by match clock, t_issued
    GET /api/next10/matches/{match_id}/frames?from_s=&to_s=&last=1   tracking frames in a window (at most 3000)
    GET /api/next10/matches/{match_id}/moments?from_s=&to_s=  key moments (play it out)
    GET /api/next10/matches/{match_id}/jobs                   video jobs and live-run stats for the match
    GET /api/next10/matches/{match_id}/fotmob                 FotMob's header, events, stats, momentum, shots, line-ups,
                                                              zones, h2h and form (cached file; fotmob:<id> matches)
    GET /api/next10/verify                                    every match's score document
    WS  /api/next10/live/{match_id}                           {type: prediction|frame|stats|hello, doc}
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from matchlens.next10 import fotmob_view
from matchlens.next10.hub import hub
from matchlens.next10.repo import MAX_FRAMES, CosmosRepo, Repo
from matchlens.store.next10 import Next10Settings, Next10Store

log = logging.getLogger(__name__)

FLAG = "MATCHLENS_NEXT10"
POLL_S = 2.0  # fallback for writers in other processes
SEEN_MAX = 20000


def enabled() -> bool:
    return os.getenv(FLAG) == "1"


class _State:
    store: Next10Store | None = None
    repo: Repo | None = None


state = _State()


@contextlib.asynccontextmanager
async def lifespan(_: Any) -> AsyncIterator[None]:
    try:
        state.store = await Next10Store(Next10Settings.from_env()).open()
        state.repo = CosmosRepo(state.store)
    except Exception as exc:  # the emulator is not running, Azure is unreachable: the rest of the app is unaffected
        log.warning("next10 store unavailable (%s); /api/next10 answers 503", exc)
        state.store = state.repo = None
    try:
        yield
    finally:
        if state.store is not None:
            await state.store.close()
        state.store = state.repo = None


router = APIRouter(prefix="/api/next10", tags=["next10"], lifespan=lifespan)


def get_repo() -> Repo:
    if state.repo is None:
        raise HTTPException(503, "the next10 store is not available")
    return state.repo


REPO = Depends(get_repo)


@router.get("/matches")
async def list_matches(limit: int = Query(200, ge=1, le=1000), repo: Repo = REPO) -> list[dict[str, Any]]:
    return await repo.matches(limit)


@router.get("/matches/{match_id}")
async def get_match(match_id: str, repo: Repo = REPO) -> dict[str, Any]:
    match = await repo.match(match_id)
    if match is None:
        raise HTTPException(404, f"unknown match {match_id!r}")
    return {"match": match, "score": await repo.score(match_id)}


@router.get("/matches/{match_id}/predictions")
async def list_predictions(match_id: str, kind: str | None = None, team: str | None = None,
                           from_s: float | None = None, to_s: float | None = None, last: bool = False,
                           repo: Repo = REPO) -> list[dict[str, Any]]:
    """``from_s``/``to_s`` window on the match clock (``t_s``, or minute * 60); ``last`` keeps only the newest."""
    return await repo.predictions(match_id, kind, team, from_s=from_s, to_s=to_s, last=last)


@router.get("/matches/{match_id}/frames")
async def list_frames(match_id: str, from_s: float | None = None, to_s: float | None = None, last: bool = False,
                      repo: Repo = REPO) -> list[dict[str, Any]]:
    if from_s is not None and to_s is not None and to_s < from_s:
        raise HTTPException(422, "to_s is before from_s")
    return await repo.frames(match_id, from_s, to_s, MAX_FRAMES, last)


@router.get("/matches/{match_id}/moments")
async def list_moments(match_id: str, from_s: float | None = None, to_s: float | None = None,
                       repo: Repo = REPO) -> list[dict[str, Any]]:
    return await repo.moments(match_id, from_s, to_s)


@router.get("/matches/{match_id}/jobs")
async def list_jobs(match_id: str, repo: Repo = REPO) -> list[dict[str, Any]]:
    return await repo.jobs(match_id)


@router.get("/matches/{match_id}/fotmob")
async def get_fotmob(match_id: str) -> dict[str, Any]:
    """FotMob's other data for the match page (header, events, stats, momentum, shot map, line-ups, zones, h2h, form),
    trimmed, from the cached file only. A ``fotmob:<id>`` match reads its own file; a video match whose document has a
    ``fotmob_id`` (the clip) reads that match's. 404 for a match with no FotMob id or no cached file yet."""
    fid = fotmob_view.parse_match_id(match_id)
    if fid is None and state.repo is not None:
        doc = await state.repo.match(match_id)
        fid = fotmob_view.parse_match_id(str((doc or {}).get("fotmob_id") or ""))
    if fid is None:
        raise HTTPException(404, f"{match_id!r} has no FotMob match")
    view = await asyncio.to_thread(fotmob_view.load_view, f"fotmob:{fid}")
    if view is None:
        raise HTTPException(404, f"no FotMob data cached for fotmob:{fid} yet")
    return {**view, "match_id": match_id}


@router.get("/verify")
async def verify(repo: Repo = REPO) -> list[dict[str, Any]]:
    return await repo.scores()


class _Seen:
    """Ids already sent on one socket, so the hub and the poll never repeat a document."""

    def __init__(self) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()

    def add(self, kind: str, doc: dict[str, Any]) -> bool:
        key = f"{kind}:{doc.get('id')}"
        if key in self._ids:
            return False
        self._ids[key] = None
        if len(self._ids) > SEEN_MAX:
            self._ids.popitem(last=False)
        return True


@router.websocket("/live/{match_id}")
async def live(ws: WebSocket, match_id: str) -> None:
    await ws.accept()
    repo = state.repo
    if repo is None:
        await ws.close(code=1013, reason="the next10 store is not available")
        return
    seen = _Seen()
    async with hub.subscribe(match_id) as queue:
        try:
            latest: dict[str, dict[str, Any]] = {}
            # shadow docs are per team (the newest of each); a Next10 stream is one doc a second (only the newest)
            for p in (*await repo.predictions(match_id, kind="shadow_event"),
                      *await repo.predictions(match_id, kind="next10", last=True)):
                latest[p.get("team_side") or p.get("team") or p.get("kind") or ""] = p
            after = max((p.get("t_issued") or "" for p in latest.values()), default=None) or None
            last_s = await repo.latest_frame_s(match_id)
            jobs = sorted(await repo.jobs(match_id), key=lambda j: j.get("updated_at") or "")
            stats = next((j["stats"] for j in reversed(jobs) if j.get("stats")), None)
            await ws.send_json({"type": "hello", "doc": {"match_id": match_id, "poll_s": POLL_S,
                                                          "last_frame_s": last_s, "stats": stats}})
            for p in latest.values():
                seen.add("prediction", p)
                await ws.send_json({"type": "prediction", "doc": p})
            polled = time.monotonic()
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), POLL_S)
                    batch = [message]
                    while not queue.empty():
                        batch.append(queue.get_nowait())
                except TimeoutError:
                    batch = []
                for message in batch:
                    doc = message["doc"]
                    if seen.add(message["type"], doc):
                        await ws.send_json(message)
                        if message["type"] == "prediction":
                            after = max(after or "", doc.get("t_issued") or "") or None
                        elif message["type"] == "frame":
                            last_s = max(last_s if last_s is not None else doc["t_s"], doc["t_s"])
                if time.monotonic() - polled >= POLL_S:  # writers in other processes only reach us via the store
                    polled = time.monotonic()
                    for p in await repo.predictions(match_id, after=after):
                        if seen.add("prediction", p):
                            await ws.send_json({"type": "prediction", "doc": p})
                            after = max(after or "", p.get("t_issued") or "") or None
                    for f in await repo.frames(match_id, from_s=-1.0 if last_s is None else last_s, limit=MAX_FRAMES):
                        if seen.add("frame", f):
                            await ws.send_json({"type": "frame", "doc": f})
                            last_s = f["t_s"] if last_s is None else max(last_s, f["t_s"])
        except WebSocketDisconnect:
            return
        except Exception:
            log.exception("next10 live socket for %s failed", match_id)
            with contextlib.suppress(Exception):
                await ws.close(code=1011)


def mount(app: FastAPI) -> bool:
    """Include the router when ``MATCHLENS_NEXT10=1``. Returns whether it did."""
    if not enabled():
        return False
    app.include_router(router)
    return True
