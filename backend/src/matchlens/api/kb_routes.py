"""Read-only API over the league knowledge bundle (``MATCHLENS_KB_DIR``, see leaguedata/kb.py).

Every route answers 404 ``{"detail": "knowledge base not configured"}`` while the bundle is not set up.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from matchlens.leaguedata import kb as kb_module
from matchlens.leaguedata.contracts import PatternTag
from matchlens.leaguedata.kb import KnowledgeBase
from matchlens.leaguedata.schema import League

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])

MAX_MATCHES = 200


def knowledge_base() -> KnowledgeBase:
    kb = kb_module.get_kb()
    if kb is None:
        raise HTTPException(status_code=404, detail="knowledge base not configured")
    return kb


KB = Annotated[KnowledgeBase, Depends(knowledge_base)]


def _dump(model) -> dict | None:
    return None if model is None else model.model_dump(mode="json")


@router.get("/teams")
def list_teams(kb: KB) -> list[dict]:
    return kb.list_teams()


@router.get("/teams/{team_id}")
def get_team(team_id: str, kb: KB, season: str | None = None) -> dict:
    resolved = team_id if kb.seasons(team_id) else kb.resolve_team(team_id)
    style = kb.team_style(resolved, season) if resolved else None
    if style is None:
        raise HTTPException(status_code=404, detail=f"unknown team {team_id!r}")
    pb = kb.playbook(style.team_id, style.season)
    manager_id = (pb.manager_id if pb else None) or style.manager_id
    return {
        "team_id": style.team_id,
        "name": style.team_name,
        "seasons": kb.seasons(style.team_id),
        "style": _dump(style),
        "mentality": _dump(kb.mentality(style.team_id, style.season)),
        "playbook": _dump(pb),
        "club": _dump(kb.club(style.team_id)),
        "manager": _dump(kb.manager(manager_id)) if manager_id else None,
    }


@router.get("/players/{player_id}")
def get_player(player_id: str, kb: KB, season: str | None = None) -> dict:
    player = kb.player(player_id, season)
    if player is None:
        raise HTTPException(status_code=404, detail=f"unknown player {player_id!r}")
    return {"player": _dump(player), "tags": kb.player_tags(player_id)}


@router.get("/matches")
def list_matches(
    kb: KB,
    team: str | None = None,
    season: str | None = None,
    opponent: str | None = None,
    tags: Annotated[str | None, Query(description="comma-separated pattern tags, all required")] = None,
    result: Literal["win", "draw", "loss"] | None = None,
    league: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_MATCHES)] = 50,
) -> list[dict]:
    wanted: list[str] = []
    for raw in (tags or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            wanted.append(PatternTag(raw).value)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown pattern tag {raw!r}") from None
    if league is not None:
        try:
            League(league.upper())
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown league {league!r}") from None
    if result and not team:
        raise HTTPException(status_code=422, detail="result needs team")
    rows = kb.matches(team=team, season=season, opponent=opponent, tags=wanted or None, result=result,
                      league=league.upper() if league else None, limit=limit)
    return [r.model_dump(mode="json") for r in rows]


@router.get("/search")
def search(kb: KB, q: Annotated[str, Query(min_length=1, max_length=200)], limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict]:
    return [h.model_dump(mode="json") for h in kb.search(q, limit=limit)]
