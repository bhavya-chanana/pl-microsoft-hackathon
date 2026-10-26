"""HTTP routes of the Tactics Board.

    POST /api/tactics/boards                          freeze a moment and start a board (202)
    GET  /api/tactics/boards/{id}                     the board so far; poll until status is done or failed
    GET  /api/tactics/boards/{id}/rollouts/{tid}      replay one simulated branch ("baseline" is the do-nothing one)
    POST /api/tactics/simulate                        try one hand-written tactic

Each request is metered like the Ask the Pundit service: the whole service shares one per-minute cap
(``MATCHLENS_TACTICS_PER_MINUTE``, default 10), because a board costs model calls and CPU.
"""

from __future__ import annotations

import os
import time
from collections import deque
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from matchlens.runtime.manager import manager
from matchlens.runtime.session import MatchSession
from matchlens.tactics.board import BadRequest, BoardNotFound, BoardRequest, BoardService, NotReady, Source
from matchlens.tactics.intent import TacticIntent
from matchlens.tactics.models import RolloutView, TacticResult, TacticsBoard

router = APIRouter(prefix="/api/tactics", tags=["tactics"])
service = BoardService()

_called: deque[float] = deque()


def _limit() -> int:
    return int(os.getenv("MATCHLENS_TACTICS_PER_MINUTE", "10"))


def _meter() -> None:
    now = time.monotonic()
    while _called and now - _called[0] > 60:
        _called.popleft()
    if len(_called) >= _limit():
        raise HTTPException(429, "the tactics board is busy; try again in a moment")
    _called.append(now)


def reset_meter() -> None:
    _called.clear()


def _session() -> MatchSession:
    try:
        return manager.require()
    except LookupError as exc:
        raise HTTPException(404, "no active match") from exc


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: TacticIntent
    source: Source
    horizon_s: float = Field(default=10.0, ge=5, le=10)
    k: int = Field(default=24, ge=4, le=64)
    board_id: str | None = Field(default=None, max_length=64)  # join an existing board's frozen moment


@router.post("/boards", status_code=202)
async def create_board(req: BoardRequest) -> JSONResponse:
    session = _session()
    try:
        board = await service.create(session, req, admit=_meter)
    except BoardNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except BadRequest as exc:
        raise HTTPException(422, str(exc)) from exc
    return JSONResponse({"board_id": board.board_id, "situation": board.situation.model_dump(mode="json")},
                        status_code=202)


@router.get("/boards/{board_id}", response_model=TacticsBoard)
async def get_board(board_id: str) -> TacticsBoard:
    board = service.get(board_id)
    if board is None:
        raise HTTPException(404, "unknown board")
    return board


@router.get("/boards/{board_id}/rollouts/{tactic_id}", response_model=RolloutView)
async def get_rollout(board_id: str, tactic_id: str,
                      variant: Literal["typical", "best", "worst"] = Query(default="typical")) -> RolloutView:
    try:
        return await service.rollout_view(board_id, tactic_id, variant)
    except BoardNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except NotReady as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/simulate", response_model=TacticResult)
async def simulate(req: SimulateRequest) -> TacticResult:
    session = _session()
    try:
        return await service.simulate(session, req.intent, req.source, req.horizon_s, req.k, req.board_id,
                                      admit=_meter)
    except BoardNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except BadRequest as exc:
        raise HTTPException(422, str(exc)) from exc
