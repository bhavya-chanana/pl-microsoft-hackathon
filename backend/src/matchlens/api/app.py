"""HTTP and WebSocket API (Ingest output, Render input)."""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import importlib.util
import io
import json
import logging
import os
import time
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from matchlens.agents import pundit
from matchlens.agents.contracts import FanProfile, Language, Mode
from matchlens.agents.llm import build_clients
from matchlens.agents.pipeline import AgentTeam, InsightPipeline
from matchlens.agents.pundit import MAX_QUESTION
from matchlens.agents.scenario import MAX_PROMPT, write_storyline
from matchlens.agents.scenario import roster as scenario_roster
from matchlens.agents.services import CATCHUP_WINDOWS, FanServices, narration_text, personalise_feed
from matchlens.domain.models import EventType, SyntheticMatch
from matchlens.ingest.adapters import ReplayControl, SyntheticReplayAdapter
from matchlens.interpret.moments import moment
from matchlens.interpret.pulse import pulse
from matchlens.mcp_server.server import mcp
from matchlens.runtime.fans import VOTER_PATTERN
from matchlens.runtime.manager import manager
from matchlens.runtime.session import MatchSession, event_payload
from matchlens.simulator.roster import default_match_meta
from matchlens.simulator.scenarios import PRESETS, check_storyline, presets, script_vs_reality
from matchlens.simulator.storyline import Storyline, comeback_storyline
from matchlens.speech import Narrator
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
        self.narrator = Narrator()

    async def on_signal(self, signal, session) -> None:
        await self.pipeline.handle(signal, session)

    async def on_resume(self, session: MatchSession, run: dict[str, Any]) -> None:
        state = await session.recorder.get("runs", "pipeline")
        decisions = await session.recorder.load("decisions", order_by="seq")
        requeued = await self.pipeline.restore(session, state, decisions)
        log.info("pipeline restored: %d decisions, %d agent jobs re-queued", len(decisions), requeued)


runtime: AgentRuntime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runtime
    runtime = AgentRuntime()
    store = await open_store()
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


app = FastAPI(title="Matchlens", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("MATCHLENS_CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/mcp", mcp.streamable_http_app())

# Local-only extensions (a package that exists only on never-pushed local branches).
if importlib.util.find_spec("matchlens.local"):
    from matchlens.local import routers as _local_routers

    for _router in _local_routers:
        app.include_router(_router)


def _session():
    try:
        return manager.require()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="no active match") from exc


class ReplayRequest(BaseModel):
    seed: int = 7
    speed: float = Field(default=1.0, gt=0, le=60)
    start_minute: float = Field(default=0.0, ge=0, le=100)


# Recorded matches, e.g. from video auto-eventing (python -m matchlens.vision). Local data
# only: the image ships none, so in the cloud the list is empty.
FEEDS_DIR = Path(os.getenv("MATCHLENS_FEEDS_DIR", Path(__file__).resolve().parents[3] / "data" / "local" / "feeds"))


class FeedRequest(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    speed: float = Field(default=1.0, gt=0, le=60)
    start_minute: float = Field(default=0.0, ge=0, le=100)


class ControlRequest(BaseModel):
    speed: float | None = Field(default=None, gt=0, le=60)
    paused: bool | None = None


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
        "speech": runtime.narrator.mode if runtime else None,
    }


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


@app.get("/api/speech")
async def speech(
    insight_id: str, mode: Mode = Mode.CASUAL, language: Language = Language.EN, club_id: str | None = None,
    player_id: str | None = None,
) -> Response:
    """An insight read aloud (MP3) in the fan's mode and language, through Azure AI Speech."""
    narrator = _runtime().narrator
    if not narrator.available:
        raise HTTPException(503, "speech is not configured; use the browser's voices")
    text = narration_text(_session(), insight_id, _profile(mode, language, club_id, player_id, None))
    if text is None:
        raise HTTPException(404, "unknown insight")
    try:
        audio = await narrator.speak(text, language.value)
    except Exception as exc:  # noqa: BLE001 - the browser falls back to its own voices
        log.warning("speech synthesis failed: %s", exc)
        raise HTTPException(502, "speech synthesis failed") from exc
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})


class VoteRequest(BaseModel):
    voter_id: str = Field(pattern=VOTER_PATTERN, description="The browser's anonymous voter id")
    option_id: str = Field(max_length=64)


class RatingRequest(BaseModel):
    voter_id: str = Field(pattern=VOTER_PATTERN)
    player_id: str = Field(max_length=64)
    score: int = Field(ge=1, le=10)


def _fan_error(exc: Exception) -> HTTPException:
    status = 404 if isinstance(exc, LookupError) else 409 if isinstance(exc, PermissionError) else 422
    return HTTPException(status, str(exc))


@app.get("/api/polls")
async def polls() -> list[dict[str, Any]]:
    """Fan polls opened so far, with live tallies."""
    return _session().fans.public_polls()


@app.post("/api/polls/{poll_id}/vote")
async def vote(poll_id: str, req: VoteRequest) -> dict[str, Any]:
    s = _session()
    try:
        poll = s.fans.vote(poll_id, req.voter_id, req.option_id)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _fan_error(exc) from exc
    s.fans_changed()
    return poll.public()


@app.get("/api/ratings")
async def ratings() -> list[dict[str, Any]]:
    """Data rating and fan rating for every player who has played."""
    s = _session()
    return s.fans.ratings(s.engine)


@app.post("/api/ratings")
async def rate(req: RatingRequest) -> dict[str, Any]:
    s = _session()
    try:
        row = s.fans.rate(req.player_id, req.voter_id, req.score, s.engine)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _fan_error(exc) from exc
    s.fans_changed()
    return row


@app.get("/api/agents/decisions")
async def decisions(limit: int = Query(default=100, le=1000)) -> list[dict[str, Any]]:
    """Coordinator routing decisions, for transparency in the demo."""
    return _runtime().pipeline.decisions[-limit:]


@app.post("/api/replay")
async def start_replay(req: ReplayRequest) -> dict[str, Any]:
    s = await manager.start_synthetic(seed=req.seed, speed=req.speed, start_minute=req.start_minute)
    return {"match_id": s.meta.match_id, "run_id": s.run_id, "speed": req.speed}


@app.get("/api/feeds")
async def feeds() -> list[str]:
    """Recorded matches that can be replayed."""
    return sorted(p.stem for p in FEEDS_DIR.glob("*.json")) if FEEDS_DIR.is_dir() else []


@app.post("/api/replay/feed")
async def start_recorded(req: FeedRequest) -> dict[str, Any]:
    path = FEEDS_DIR / f"{req.name}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such recorded match")
    match = SyntheticMatch.model_validate_json(await asyncio.to_thread(path.read_text))
    control = ReplayControl(speed=req.speed, start_elapsed=req.start_minute * 60)
    s = await manager.start_feed(SyntheticReplayAdapter(match, control), control, source="recorded")
    return {"match_id": s.meta.match_id, "run_id": s.run_id, "speed": req.speed}


@app.get("/api/runs")
async def runs(limit: int = Query(default=20, le=100)) -> list[dict[str, Any]]:
    """Recent replays stored in the database, newest first."""
    return [{k: v for k, v in r.items() if k != "state"} for r in await manager.repo.list_runs(limit)]


@app.post("/api/runs/{run_id}/open")
async def open_run(run_id: str) -> dict[str, Any]:
    """Reopen a stored run: a live demo replay carries on, anything else opens as it was left."""
    try:
        s = await manager.open_run(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="no such run") from exc
    return {"match_id": s.meta.match_id, "run_id": s.run_id, "archived": s.archived}


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
    return {**s.engine.player_stats(player_id), "card": s.fans.card(player_id, s.engine)}


@app.get("/api/teams/{team_id}")
async def team(team_id: str) -> dict[str, Any]:
    s = _session()
    if team_id not in (s.meta.home.team_id, s.meta.away.team_id):
        raise HTTPException(404, "unknown team")
    return s.engine.team_stats(team_id)


@app.get("/api/signals")
async def signals() -> list[dict[str, Any]]:
    return [x.model_dump(mode="json") for x in _session().engine.signals()]


@app.get("/api/moments/{insight_id}")
async def moment_replay(insight_id: str) -> dict[str, Any]:
    """Replay of one moment: tracking frames around it, its events and telestrator drawings."""
    s = _session()
    signal = next((x for x in s.engine.signals() if x.signal_id == insight_id), None)
    if signal is None:
        raise HTTPException(404, "unknown moment")
    return moment(s.engine, signal)


@app.get("/api/pulse")
async def match_pulse() -> dict[str, Any]:
    """Match Pulse: momentum, control or chaos, pressing and tempo per minute, with the key moments pinned."""
    return pulse(_session().engine)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=MAX_QUESTION)
    mode: Mode = Mode.CASUAL
    language: Language = Language.EN
    club_id: str | None = None
    player_id: str | None = None


# Ask the Pundit costs a model call per new question: cap the whole service, not per client.
ASK_LIMIT = int(os.getenv("MATCHLENS_ASK_PER_MINUTE", "40"))
_asked: deque[float] = deque()


@app.post("/api/ask")
async def ask_pundit(req: AskRequest) -> dict[str, Any]:
    """Ask the Pundit: a fan's question answered from the match data and fact-checked."""
    now = time.monotonic()
    while _asked and now - _asked[0] > 60:
        _asked.popleft()
    if len(_asked) >= ASK_LIMIT:
        raise HTTPException(429, "the pundit is busy; ask again in a moment")
    _asked.append(now)
    profile = _profile(req.mode, req.language, req.club_id, req.player_id, None)
    result = await pundit.ask(_runtime().team, _session(), req.question, profile)
    return result.model_dump(mode="json")


class ScenarioDraftRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=MAX_PROMPT)
    language: Language = Language.EN


class KickoffRequest(BaseModel):
    preset_id: str | None = None
    storyline: Storyline | None = None
    seed: int = Field(default=7, ge=0, le=1_000_000)
    speed: float = Field(default=15.0, gt=0, le=60)


@app.get("/api/scenarios")
async def scenario_presets() -> dict[str, Any]:
    """Preset storylines for Scenario Lab, and the roster a custom storyline may use."""
    return {"presets": presets(), "roster": scenario_roster(default_match_meta())}


@app.post("/api/scenarios/draft")
async def draft_scenario(req: ScenarioDraftRequest) -> dict[str, Any]:
    """Turn a match described in words into a storyline (strong model), checked before it is offered."""
    rt = _runtime()
    if not rt.clients.settings.online:
        raise HTTPException(503, "writing a match from a description needs the live models; pick a preset")
    story, problems = await write_storyline(rt.team, default_match_meta(), req.prompt, req.language.value)
    if story is None:
        raise HTTPException(422, {"message": "the storyline could not be made playable", "problems": problems})
    return {"storyline": story.model_dump(mode="json")}


@app.post("/api/scenarios/kickoff")
async def kickoff_scenario(req: KickoffRequest) -> dict[str, Any]:
    """Play a preset or a checked storyline from kick-off. Replaces the current match for every viewer."""
    seed = req.seed
    if req.preset_id:
        if req.preset_id not in PRESETS:
            raise HTTPException(404, "unknown preset")
        factory, seed = PRESETS[req.preset_id]
        story = factory()
    elif req.storyline:
        story = req.storyline
    else:
        raise HTTPException(422, "give a preset_id or a storyline")
    problems = check_storyline(default_match_meta(), story)
    if problems:
        raise HTTPException(422, {"message": "the storyline cannot be played", "problems": problems})
    # The default comeback stays the default storyline, so the recorded demo and resume keep working.
    is_default = story == comeback_storyline()
    s = await manager.start_synthetic(seed=seed, speed=req.speed, storyline=None if is_default else story)
    return {"match_id": s.meta.match_id, "run_id": s.run_id, "title": story.title, "speed": req.speed}


@app.get("/api/scenarios/check")
async def scenario_check() -> dict[str, Any]:
    """Script vs reality: whether each scripted moment of the current storyline has happened."""
    s = _session()
    if manager.seed is None:
        raise HTTPException(404, "this match was not played from a storyline")
    return script_vs_reality(s.engine, manager.storyline or comeback_storyline())


@app.get("/api/dataset")
async def dataset_download() -> Response:
    """The current match as a dataset: metadata, every event as delivered (corrections included), tracking."""
    s = _session()
    engine = s.engine
    story = manager.storyline or (comeback_storyline() if manager.seed is not None else None)

    def build() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            meta = {"match": s.meta.model_dump(mode="json"), "seed": manager.seed, "run_id": s.run_id,
                    "storyline": story.model_dump(mode="json") if story else None, "score": engine.score(),
                    "status": engine.state()["status"], "synthetic": manager.seed is not None,
                    "schemas": "docs/schema in the Matchlens repository"}
            z.writestr("meta.json", json.dumps(meta, indent=2))
            z.writestr("events.jsonl", "".join(e.model_dump_json() + "\n" for e in engine.feed_events()))
            frames = "".join(f.model_dump_json() + "\n" for f in engine.frames_between(0, engine.c.elapsed))
            z.writestr("tracking.jsonl.gz", gzip.compress(frames.encode()))
        return buf.getvalue()

    data = await asyncio.to_thread(build)
    name = f"{s.run_id}.zip"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


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
