"""The Tactics Board service: freeze a moment, try ideas on a copy of it, rank them, explain the winner.

    request ─▶ Situation + SimSnapshot (a copy of the frame: the shared match never pauses)
                   │
                   ├─▶ baseline Monte-Carlo (nothing changes)                      ~50 ms
                   ├─▶ rule-based recipe cards, each simulated                      <1 s
                   └─▶ persona tactics streamed from ``tactician.ideate``, each checked, simulated, summarised
                          and ranked as it lands                                    4-9 s
               select_diverse ─▶ Gaffer's verdict ─▶ done                           ~15 s, hard stop at 25 s

A board lives in memory (an LRU of ``MAX_BOARDS``), runs as one asyncio task and publishes a new immutable
``TacticsBoard`` after every change, so ``get`` always returns a consistent snapshot. Every failure degrades:
what is ready when something breaks, or when the deadline arrives, is ranked and shown.

Seeds are paired: rollout ``i`` of the baseline and of every tactic of a board uses the same random seed
(``seed_for(base_seed, "baseline", i)``), so a lift compares like with like. ``base_seed`` is a fingerprint of the
frozen moment, so the same request on the same moment gives the same rankings.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import secrets
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from matchlens.agents.llm import ModelClients
from matchlens.domain.models import (
    EventType,
    MatchMeta,
    Outcome,
    TrackingFrame,
    minute_label,
)
from matchlens.domain.pitch import to_relative
from matchlens.simulator.fork import RolloutTrace, SimSnapshot, snapshot_from_frame
from matchlens.simulator.style import SimStyle
from matchlens.tactics import evaluate, playbook, rollout
from matchlens.tactics.drawings import intent_drawings, rollout_drawings
from matchlens.tactics.intent import _LANE_Y, _THIRD_X, TacticIntent
from matchlens.tactics.models import (
    OutcomeStats,
    PlayerSpot,
    RolloutView,
    Situation,
    TacticResult,
    TacticsBoard,
    Verdict,
)
from matchlens.tactics.styles import build_provider
from matchlens.tactics.validate import check_intent
from matchlens.tacticsml.registry import get_tactical_model

log = logging.getLogger(__name__)

MAX_BOARDS = 20
DEFAULT_K = 24
DEFAULT_DEADLINE_S = 25.0
IDEATE_SHARE = 0.6  # persona calls get this share of the deadline
PIPELINE_SHARE = 0.7  # simulation stops here; the rest of the deadline is for the verdict
IDEATE_N = 10  # personas may land more ideas than the board shows; select_diverse picks the n
MAX_RECIPE_CARDS = 3
MAX_CANDIDATES = 24  # ideas simulated per board
MAX_USER_TACTICS = 8  # hand-written tactics a board keeps for replay
PAIRED_ID = rollout.BASELINE_ID  # the seed key shared by the baseline and every tactic of a board
SET_PIECE_WINDOW_S = 3.0
TRANSITION_WINDOW_S = 5.0

_POSSESSION_TYPES = frozenset({
    EventType.KICKOFF, EventType.PASS, EventType.CARRY, EventType.POSSESSION_CHANGE, EventType.SHOT,
    EventType.FREE_KICK, EventType.THROW_IN, EventType.GOAL_KICK, EventType.CORNER, EventType.GOAL,
})
_SET_PIECES = frozenset({EventType.CORNER, EventType.FREE_KICK, EventType.THROW_IN, EventType.GOAL_KICK})
_BOX_X = 105.0 - 16.5
_BOX_Y = (34.0 - 20.16, 34.0 + 20.16)

class BoardNotFound(Exception):
    """The board, the tactic on it, or the moment asked for does not exist (404)."""


class BadRequest(Exception):
    """The request names something that cannot be built, e.g. an unknown team (422)."""


class NotReady(Exception):
    """The tactic is on the board but has not been simulated yet (409)."""


Risk = Literal["safe", "balanced", "bold"]
Variant = Literal["typical", "best", "worst"]


def default_k() -> int:
    return max(4, int(os.getenv("MATCHLENS_TACTICS_K", str(DEFAULT_K))))


def default_deadline_s() -> float:
    return float(os.getenv("MATCHLENS_TACTICS_DEADLINE_S", str(DEFAULT_DEADLINE_S)))


# ================================================================ request models


class Source(BaseModel):
    """Which moment to freeze: the latest live frame, the moment of an insight, or the frame nearest a time."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["live", "moment", "elapsed"]
    insight_id: str | None = Field(default=None, max_length=128)
    elapsed: float | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def _complete(self) -> Source:
        if self.kind == "moment" and not self.insight_id:
            raise ValueError("a moment source needs insight_id")
        if self.kind == "elapsed" and self.elapsed is None:
            raise ValueError("an elapsed source needs elapsed (match seconds)")
        return self


class BoardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    team_id: str = Field(min_length=1, max_length=64)
    prompt: str | None = Field(default=None, max_length=400)
    n: int = Field(default=3, ge=3, le=5)
    horizon_s: float = Field(default=10.0, ge=5, le=10)
    risk: Risk = "balanced"
    language: Literal["en", "es", "hi"] = "en"


# ================================================================ the frozen moment


def _frame_for(session: Any, source: Source) -> TrackingFrame:
    engine = session.engine
    if source.kind == "live":
        if session.last_frame is not None:
            return TrackingFrame.model_validate(session.last_frame)
        frame = engine.frame_near(engine.c.elapsed)
        if frame is None:
            raise BoardNotFound("no tracking frame has arrived yet")
        return frame
    if source.kind == "moment":
        signal = next((s for s in engine.signals() if s.signal_id == source.insight_id), None)
        if signal is None:
            raise BoardNotFound("unknown moment")
        at = signal.elapsed
    else:
        assert source.elapsed is not None
        at = source.elapsed
    near = engine.frames_between(at - 3.0, at + 3.0)
    if not near:
        raise BoardNotFound("no tracking frame near that time")
    return min(near, key=lambda f: (abs(f.elapsed - at), f.elapsed))


def zone_of(x: float, y: float, attacking_right: bool) -> str:
    """The ``tactics.intent`` zone of a point for a team: ``box`` inside the penalty area, else ``lane:third``."""
    rx, ry = to_relative(x, y, attacking_right)
    if rx >= _BOX_X and _BOX_Y[0] <= ry <= _BOX_Y[1]:
        return "box"
    third = min(_THIRD_X, key=lambda t: abs(_THIRD_X[t] - rx))
    lane = min(_LANE_Y, key=lambda ln: abs(_LANE_Y[ln] - ry))
    return f"{lane}:{third}"


def terminal_of(trace: RolloutTrace, team_id: str, meta: MatchMeta) -> str:
    """How a rollout ended for the coached team, in the board's vocabulary: goal, shot, turnover, box_entry,
    blocked (a restart: corner, throw-in or goal kick after the ball ran out), or retained."""
    f = evaluate.facts(trace, team_id, meta)
    if f.goal:
        return "goal"
    if f.shots:
        return "shot"
    started = trace.start_possession or (team_id if f.team_had_ball else meta.opponent(team_id).team_id)
    if f.lost_ball or evaluate.possession_changed(trace, started):  # lost, or won back
        return "turnover"
    if f.box_entry:
        return "box_entry"
    if trace.terminal.startswith("restart"):
        return "blocked"
    return "retained"


def _phase(events: list, t: float, possession: str, since: float, coaching: str, ball_rel_x: float) -> str:
    last = events[-1] if events else None
    if last is not None and last.type in _SET_PIECES and t - last.elapsed <= SET_PIECE_WINDOW_S:
        return "set_piece"
    if t - since < TRANSITION_WINDOW_S and since > 0:
        return "transition"
    if possession != coaching:
        return "defending"
    return "build_up" if ball_rel_x < 35.0 else "progression" if ball_rel_x < 70.0 else "final_third"


def situation_from_session(
    session: Any, source: Source, coaching_team_id: str, provider: Any | None = None
) -> tuple[Situation, SimSnapshot]:
    """Freeze a moment of ``session`` into a ``Situation`` (what the UI and the tactician see) and a
    ``SimSnapshot`` (what the simulator forks from). The session itself is only read.

    Raises ``BoardNotFound`` when the moment does not exist (404) and ``BadRequest`` for a bad team (422).
    """
    meta: MatchMeta = session.meta
    engine = session.engine
    if coaching_team_id not in (meta.home.team_id, meta.away.team_id):
        raise BadRequest(f"unknown team {coaching_team_id}; use {meta.home.team_id} or {meta.away.team_id}")
    frame = _frame_for(session, source)
    t = frame.elapsed
    events = [e for e in engine.events(until=t) if e.type != EventType.CORRECTION]

    score = {meta.home.team_id: 0, meta.away.team_id: 0}
    booked: set[str] = set()
    sent_off: set[str] = set()
    for e in events:
        if e.type == EventType.GOAL and e.team_id in score:
            score[e.team_id] += 1
        elif e.type == EventType.CARD and e.player_id:
            booked.add(e.player_id)
            if e.outcome in (Outcome.RED, Outcome.SECOND_YELLOW):
                sent_off.add(e.player_id)

    ball = next(((en.x, en.y) for en in frame.entities if en.entity_id == "ball"), (52.5, 34.0))
    holders = [(e.elapsed, e.team_id) for e in events if e.type in _POSSESSION_TYPES and e.team_id]
    if holders:
        possession = holders[-1][1]
        since = holders[-1][0]
        for at, team in reversed(holders):
            if team != possession:
                break
            since = at
    else:  # nothing has happened yet: the side nearest the ball
        squad = {p: tm.team_id for tm in (meta.home, meta.away) for p in tm.starting_xi + tm.bench}
        near = min((en for en in frame.entities if en.entity_id in squad),
                   key=lambda en: (en.x - ball[0]) ** 2 + (en.y - ball[1]) ** 2, default=None)
        possession = squad[near.entity_id] if near else meta.home.team_id
        since = 0.0

    snap = snapshot_from_frame(meta, frame, possession=possession, carrier=None, score=score, sent_off=sent_off,
                               booked=booked, recent_events=events[-50:], seed=7)
    state = snap.state
    provider = provider or build_provider(engine)
    attacking_right = {meta.home.team_id: True, meta.away.team_id: False}  # the home side attacks x=105 all match
    players = [
        PlayerSpot(player_id=pid, name=meta.players[pid].name, team_id=team_id, role=meta.players[pid].role,
                   x=round(state.positions[pid][0], 2), y=round(state.positions[pid][1], 2),
                   tags=provider.player_tags(pid))
        for team_id, pids in state.on_pitch.items() for pid in pids if pid in state.positions
    ]
    rel_x = to_relative(*ball, attacking_right[possession])[0]
    situation = Situation(
        match_id=meta.match_id, elapsed=t, minute_label=minute_label(frame.period, frame.clock), period=frame.period,
        score=score, possession_team_id=state.possession, carrier_id=state.carrier or None,
        ball=(round(ball[0], 2), round(ball[1], 2)), ball_zone=zone_of(*ball, attacking_right[possession]),
        phase=_phase(events, t, possession, since, coaching_team_id, rel_x), coaching_team_id=coaching_team_id,
        attacking_right=attacking_right, players=players, frame=frame)
    return situation, snap


# ================================================================ collaborators (guarded)


def _tactician() -> Any | None:
    """``agents.tactician`` (persona ideas, diverse selection, the verdict), or None while it is unavailable."""
    try:
        from matchlens.agents import tactician

        return tactician
    except Exception:  # noqa: BLE001 - the board must still complete with recipes and a template verdict
        log.warning("agents.tactician unavailable; the board uses rule-based recipes only", exc_info=True)
        return None


def _knowledge_base() -> Any | None:
    try:
        from matchlens.leaguedata.kb import get_kb

        return get_kb()
    except Exception:  # noqa: BLE001 - league data is optional
        return None


class _KbForMatch:
    """Lets a league-data knowledge base answer for the match's teams, whatever ids the feed uses: team names
    are resolved through ``kb.resolve_team``. Anything it cannot place falls back to the match-derived value."""

    def __init__(self, kb: Any, meta: MatchMeta) -> None:
        self.kb, self.meta = kb, meta
        self._ids: dict[str, str | None] = {}

    def _kb_id(self, team_id: str) -> str | None:
        if team_id not in self._ids:
            found: Any = None
            try:
                found = self.kb.resolve_team(self.meta.team(team_id).name)
            except Exception:  # noqa: BLE001
                pass
            self._ids[team_id] = str(getattr(found, "team_id", found)) if found else None
        return self._ids[team_id]

    def team_style(self, team_id: str, season: str | None = None) -> Any:
        kb_id = self._kb_id(team_id)
        return self.kb.team_style(kb_id, season=season) if kb_id else None

    def team_context(self, team_id: str) -> Any:
        kb_id = self._kb_id(team_id)
        return self.kb.team_context(kb_id) if kb_id else None

    def player_tags(self, player_id: str) -> Any:
        return self.kb.player_tags(player_id)


def _recorded(clients: ModelClients) -> bool:
    """True when the answers come from the demo cassette."""
    if clients.cassette is None:
        return False
    return bool(getattr(clients.creative_client, "enabled", False))


def _shortfall(errors: list[str], got: int, wanted: int) -> str | None:
    """One plain sentence saying why fewer ideas than asked for are shown, or None when all are.

    English only, like the rest of the board's built-in text. The reason is read from the error log but
    worded without internal terms."""
    if got >= wanted:
        return None
    text = " ".join(errors).lower()
    if "timeout after" in text or "no time left" in text or "deadline reached" in text:
        why = "the AI ran out of time, so fewer ideas were tested."
    elif "rejected" in text or "no valid tactics" in text:
        why = "some of the AI's ideas did not pass the checks."
    elif "ideate" in text or "no answer" in text or "unavailable" in text:
        why = "the AI could not be reached, so only the built-in recipes were tested."
    else:
        why = "not enough distinct ideas were ready."
    return f"{got} of {wanted} ideas ready: {why}"


def _fallback_verdict(final: list[TacticResult], baseline: OutcomeStats | None, language: str) -> Verdict:
    """A data-only verdict: used when the model is unavailable, too slow or contradicts the statistics."""
    scored = [r for r in final if r.outcome is not None]
    if not scored or baseline is None:
        return Verdict(headline="No clear winner", cited_metrics=[], recommended_tactic_id=None,
                       text="None of the ideas could be simulated, so there is nothing to recommend yet.")
    winner = evaluate.clear_winner(scored, baseline)
    if winner is None:
        top = scored[0]
        out = top.outcome
        assert out is not None
        return Verdict(
            headline="No clear winner", recommended_tactic_id=None, cited_metrics=["p_shot", "p_concede_shot"],
            text=(f"None of the {len(scored)} ideas clearly beats doing nothing: the best-ranked, {top.intent.title}, "
                  f"gives a shot chance of {out.p_shot.value:.0%} against {baseline.p_shot.value:.0%} if nothing "
                  f"changes and a {out.p_concede_shot.value:.0%} chance of conceding a shot (baseline "
                  f"{baseline.p_concede_shot.value:.0%}), but the ranges overlap. Based on {out.rollouts} simulations "
                  f"of {out.horizon_s:g} s; the simulator is a model, not a prediction."))
    best = winner
    out = best.outcome
    assert out is not None
    return Verdict(
        headline=f"Try: {best.intent.title}"[:100], recommended_tactic_id=best.tactic_id,
        cited_metrics=["p_shot", "mean_xg", "p_concede_shot"],
        text=(f"{best.intent.title} ({best.intent.mechanism.value.replace('_', ' ')}) is the best of the "
              f"{len(scored)} ideas tested: shot chance {out.p_shot.value:.0%} against {baseline.p_shot.value:.0%} "
              f"if nothing changes, and a {out.p_concede_shot.value:.0%} chance of conceding a shot "
              f"(baseline {baseline.p_concede_shot.value:.0%}). Based on {out.rollouts} simulations of "
              f"{out.horizon_s:g} s; the simulator is a model, not a prediction."))


# ================================================================ the service


@dataclass
class _Record:
    board: TacticsBoard
    request: BoardRequest
    meta: MatchMeta
    snap: SimSnapshot
    styles: dict[str, SimStyle]
    provider_context: dict[str, Any]
    base_seed: str
    k: int
    clients: ModelClients
    evaluator: Any
    results: dict[str, TacticResult] = field(default_factory=dict)  # arrival order
    intents: dict[str, TacticIntent] = field(default_factory=dict)
    user: dict[str, TacticResult] = field(default_factory=dict)  # hand-written tactics: replayable, never ranked
    views: dict[tuple[str, str], RolloutView] = field(default_factory=dict)  # (tactic_id, variant) -> picked replay
    cancel: threading.Event = field(default_factory=threading.Event)
    errors: list[str] = field(default_factory=list)
    cite_keys: set[str] | None = None
    task: asyncio.Task[None] | None = None
    finished: bool = False
    started: float = field(default_factory=time.monotonic)


class BoardService:
    """Builds and serves Tactics Boards. One instance per process; boards are kept in an LRU."""

    def __init__(self, clients: Any | None = None, *, max_boards: int = MAX_BOARDS) -> None:
        self._clients = clients  # ModelClients, or a callable returning them
        self.max_boards = max_boards
        self._boards: OrderedDict[str, _Record] = OrderedDict()

    # ------------------------------------------------------------ wiring

    def clients(self) -> ModelClients:
        c = self._clients() if callable(self._clients) and not isinstance(self._clients, ModelClients) \
            else self._clients
        if c is None:
            from matchlens.agents.llm import build_clients

            c = self._clients = build_clients()
        return c

    @staticmethod
    def _stop(rec: _Record) -> None:
        """Cancel a board: no further rollouts are scheduled and the running ones stop between simulations."""
        rec.cancel.set()
        if rec.task is not None:
            rec.task.cancel()

    def set_clients(self, clients: Any) -> None:
        """Use the app's model clients (so the recorded-demo switch and labels are shared with the other agents)."""
        self._clients = clients

    async def close(self) -> None:
        for rec in list(self._boards.values()):
            self._stop(rec)
        self._boards.clear()

    # ------------------------------------------------------------ public API

    def get(self, board_id: str) -> TacticsBoard | None:
        rec = self._boards.get(board_id)
        if rec is None:
            return None
        self._boards.move_to_end(board_id)
        return rec.board

    def _provider(self, session: Any) -> Any:
        kb = _knowledge_base()
        return build_provider(session.engine, kb=_KbForMatch(kb, session.meta) if kb is not None else None)

    @staticmethod
    def _provider_context(provider: Any, meta: MatchMeta) -> tuple[dict[str, SimStyle], dict[str, Any]]:
        styles: dict[str, SimStyle] = {}
        context: dict[str, Any] = {}
        for team in (meta.home, meta.away):
            styles[team.team_id] = provider.style(team.team_id)
            ctx = dict(provider.context(team.team_id) or {})
            ctx.setdefault("style", styles[team.team_id].model_dump())
            context[team.team_id] = ctx
        return styles, context

    async def create(self, session: Any, request: BoardRequest,
                     admit: Callable[[], None] | None = None) -> TacticsBoard:
        """Freeze the moment and start the board. Returns at once with status ``thinking``.

        ``admit`` is called once the request has proved valid and before any work starts (rate metering); it may
        raise to refuse."""
        provider = self._provider(session)
        situation, snap = situation_from_session(session, request.source, request.team_id, provider)
        styles, context = self._provider_context(provider, session.meta)
        if admit is not None:
            admit()
        clients = self.clients()
        evaluator = get_tactical_model()
        board = TacticsBoard(
            board_id=f"b-{secrets.token_hex(5)}", status="thinking", situation=situation, prompt=request.prompt,
            risk=request.risk, horizon_s=request.horizon_s, evaluator=evaluator.version, model_label=clients.label,
            recorded=_recorded(clients), requested=request.n)
        rec = _Record(board=board, request=request, meta=session.meta, snap=snap, styles=styles,
                      provider_context=context, base_seed=rollout.snapshot_digest(snap), k=default_k(),
                      clients=clients, evaluator=evaluator)
        self._boards[board.board_id] = rec
        while len(self._boards) > self.max_boards:
            _, old = self._boards.popitem(last=False)
            self._stop(old)
        rec.task = asyncio.create_task(self._run(rec), name=f"tactics:{board.board_id}")
        return board

    async def simulate(self, session: Any, intent: TacticIntent, source: Source | None, horizon_s: float = 10.0,
                       k: int | None = None, board_id: str | None = None,
                       admit: Callable[[], None] | None = None) -> TacticResult:
        """Try one hand-written tactic. With ``board_id`` it runs on that board's frozen moment and is kept beside
        the board (replayable, never ranked among its cards); otherwise ``source`` is frozen just for this call."""
        if board_id is not None:
            rec = self._boards.get(board_id)
            if rec is None:
                raise BoardNotFound("unknown board")
            self._boards.move_to_end(board_id)
            if admit is not None:
                admit()
            result = await self._evaluate_custom(rec.snap, rec.board.situation, rec.meta, rec.styles, rec.base_seed,
                                                 intent, rec.board.horizon_s, rec.k, rec.board.risk, rec.evaluator,
                                                 rec.board.baseline, rec.cite_keys, None)
            if result.status == "ready" and (result.tactic_id in rec.user or len(rec.user) < MAX_USER_TACTICS):
                rec.user[result.tactic_id] = result.model_copy(update={"persona": "custom"})
                rec.intents[result.tactic_id] = intent
            return result
        if source is None:
            raise BadRequest("give a source or a board_id")
        provider = self._provider(session)
        situation, snap = situation_from_session(session, source, intent.team_id, provider)
        styles, _ = self._provider_context(provider, session.meta)
        if admit is not None:
            admit()
        return await self._evaluate_custom(snap, situation, session.meta, styles, rollout.snapshot_digest(snap),
                                           intent, horizon_s, k or default_k(), "balanced", get_tactical_model(),
                                           None, None, None)

    async def rollout_view(self, board_id: str, tactic_id: str, variant: Variant = "typical") -> RolloutView:
        """Replay one rollout of a tactic (``baseline`` for the do-nothing branch) at 5 Hz. Picked replays are kept
        on the board, so asking again costs nothing."""
        rec = self._boards.get(board_id)
        if rec is None:
            raise BoardNotFound("unknown board")
        self._boards.move_to_end(board_id)
        cached = rec.views.get((tactic_id, variant))
        if cached is not None:
            return cached
        if tactic_id == rollout.BASELINE_ID:
            intent = None
        else:
            res = rec.results.get(tactic_id) or rec.user.get(tactic_id)
            intent = rec.intents.get(tactic_id)
            if res is None or intent is None:
                raise BoardNotFound("that tactic is not on this board")
            if res.outcome is None:
                raise NotReady("that tactic has not been simulated yet")
        team = rec.board.situation.coaching_team_id
        try:
            traces = await rollout.run_monte_carlo_async(
                rec.snap, intent, rec.styles, k=rec.k, horizon_s=rec.board.horizon_s, base_seed=rec.base_seed,
                tactic_id=PAIRED_ID)
        except rollout.RolloutCancelled as exc:  # pragma: no cover - not passed here; kept for safety
            raise BoardNotFound("the board was closed") from exc
        pick = await asyncio.to_thread(evaluate.pick_variants, traces, team, rec.meta)
        for name, trace in pick.items():  # one pass picks all three; keep them
            index = next(i for i, t in enumerate(traces) if t is trace)
            drawings = (intent_drawings(intent, rec.snap, rec.meta) if intent is not None else []) + \
                rollout_drawings(trace)
            rec.views[(tactic_id, name)] = RolloutView(
                tactic_id=tactic_id, variant=name, seed=rollout.seed_for(rec.base_seed, PAIRED_ID, index),
                frames=trace.frames, events=trace.events, drawings=drawings,
                terminal=terminal_of(trace, team, rec.meta))
        return rec.views[(tactic_id, variant)]

    # ------------------------------------------------------------ simulation helpers

    async def _summarise(self, snap: SimSnapshot, intent: TacticIntent | None, styles: dict[str, SimStyle],
                         base_seed: str, k: int, horizon_s: float, team: str, meta: MatchMeta,
                         evaluator: Any, cancel: threading.Event | None = None) -> OutcomeStats:
        traces: list[RolloutTrace] = await rollout.run_monte_carlo_async(
            snap, intent, styles, k=k, horizon_s=horizon_s, base_seed=base_seed, tactic_id=PAIRED_ID, cancel=cancel)

        def reduce() -> OutcomeStats:
            stats = evaluate.summarise(traces, team, evaluator, meta)
            terminals = Counter(terminal_of(t, team, meta) for t in traces)
            return stats.model_copy(update={"terminals": dict(terminals)})

        return await asyncio.to_thread(reduce)

    async def _evaluate_custom(self, snap: SimSnapshot, situation: Situation, meta: MatchMeta,
                               styles: dict[str, SimStyle], base_seed: str, intent: TacticIntent, horizon_s: float,
                               k: int, risk: Risk, evaluator: Any, baseline: OutcomeStats | None,
                               cite_keys: set[str] | None, cancel: threading.Event | None) -> TacticResult:
        tid = rollout.tactic_id_of(intent)
        problems = check_intent(meta, situation, intent, horizon_s, cite_keys or set(playbook.PLAYBOOK_KEYS))
        if intent.team_id != situation.coaching_team_id and not problems:
            problems = [f"team_id must be the coached team {situation.coaching_team_id}"]
        result = TacticResult(tactic_id=tid, persona="custom", intent=intent)
        if problems:
            return result.model_copy(update={"status": "rejected", "problems": problems})
        team = intent.team_id
        if baseline is None:
            baseline = await self._summarise(snap, None, styles, base_seed, k, horizon_s, team, meta, evaluator, cancel)
        outcome = await self._summarise(snap, intent, styles, base_seed, k, horizon_s, team, meta, evaluator,
                                        cancel)
        ranked = evaluate.rank([result.model_copy(update={"outcome": outcome, "status": "ready"})], baseline, risk)
        return ranked[0]

    # ------------------------------------------------------------ the board's life

    def _publish(self, rec: _Record, **changes: Any) -> None:
        board = rec.board
        baseline = changes.get("baseline", board.baseline)
        results = list(rec.results.values())
        tactics = evaluate.rank(results, baseline, board.risk) if baseline is not None else results
        rec.board = board.model_copy(update={"tactics": tactics, "errors": list(rec.errors), **changes})

    async def _run(self, rec: _Record) -> None:
        deadline = default_deadline_s()
        try:
            await asyncio.wait_for(self._pipeline(rec, deadline), timeout=deadline * PIPELINE_SHARE)
        except TimeoutError:
            rec.cancel.set()  # in-flight simulations stop between rollouts; no new ones start
            rec.errors.append(f"deadline reached after {deadline * PIPELINE_SHARE:g}s; showing what was ready")
        except (asyncio.CancelledError, rollout.RolloutCancelled):
            rec.cancel.set()
            raise
        except Exception as exc:  # noqa: BLE001 - a broken board must still finish
            log.exception("tactics board %s failed", rec.board.board_id)
            rec.errors.append(f"board: {type(exc).__name__}: {exc}")
        try:
            await self._conclude(rec)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("tactics board %s could not be concluded", rec.board.board_id)
            rec.errors.append(f"conclude: {type(exc).__name__}: {exc}")
            self._publish(rec, status="done" if rec.board.baseline else "failed")
        rec.cancel.set()  # nothing more is scheduled for this board
        rec.finished = True

    async def _pipeline(self, rec: _Record, deadline: float) -> None:
        board = rec.board
        situation = board.situation
        team = situation.coaching_team_id
        horizon = board.horizon_s
        tactician = _tactician()

        baseline = await self._summarise(rec.snap, None, rec.styles, rec.base_seed, rec.k, horizon, team, rec.meta,
                                         rec.evaluator, rec.cancel)
        self._publish(rec, baseline=baseline, status="evaluating")
        if tactician is not None:
            rec.cite_keys = set(tactician.context_keys(situation, rec.provider_context, baseline)) | set(
                playbook.PLAYBOOK_KEYS)

        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def admit(persona: str, intent: TacticIntent) -> None:
            tid = rollout.tactic_id_of(intent)
            if tid in rec.results or len(rec.results) >= MAX_CANDIDATES:
                return
            rec.results[tid] = TacticResult(tactic_id=tid, persona=persona, intent=intent, status="thinking")
            rec.intents[tid] = intent
            self._publish(rec)
            await queue.put(tid)

        async def worker() -> None:
            while (tid := await queue.get()) is not None:
                await self._evaluate_one(rec, tid, baseline)

        worker_task = asyncio.create_task(worker())
        try:
            # Instant cards first: the rule-based recipes, so the board is never empty.
            try:
                recipes = playbook.recipes(situation, team, rec.meta, horizon)
            except Exception as exc:  # noqa: BLE001
                rec.errors.append(f"recipes: {type(exc).__name__}")
                recipes = []
            mechanisms: set[str] = set()
            for intent in recipes:
                if len(mechanisms) < MAX_RECIPE_CARDS and intent.mechanism.value not in mechanisms:
                    mechanisms.add(intent.mechanism.value)
                    await admit("recipe", intent)
            if tactician is not None:
                for persona, intent in await self._ideate(rec, tactician, baseline, admit, deadline):
                    await admit(persona, intent)  # anything the stream missed
            await queue.put(None)
            await worker_task
        finally:
            worker_task.cancel()

    async def _ideate(self, rec: _Record, tactician: Any, baseline: OutcomeStats, admit: Any,
                      deadline: float) -> list[tuple[str, TacticIntent]]:
        request = rec.request
        try:
            landed, errors = await tactician.ideate(
                rec.clients, rec.board.situation, rec.provider_context, meta=rec.meta, prompt=request.prompt,
                n=IDEATE_N, language=request.language, deadline_s=max(3.0, deadline * IDEATE_SHARE),
                horizon_s=rec.board.horizon_s, baseline=baseline, snapshot_digest=rec.base_seed, on_tactic=admit)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - recipes already cover the board
            log.warning("ideate failed", exc_info=True)
            rec.errors.append(f"ideate: {type(exc).__name__}: {exc}")
            return []
        rec.errors.extend(errors)
        return landed

    async def _evaluate_one(self, rec: _Record, tid: str, baseline: OutcomeStats) -> None:
        """Check, simulate and summarise one tactic, then re-rank the board."""
        result = rec.results[tid].model_copy(update={"status": "evaluating"})
        rec.results[tid] = result
        self._publish(rec)
        intent = result.intent
        board = rec.board
        try:
            problems = check_intent(rec.meta, board.situation, intent, board.horizon_s, rec.cite_keys)
            if problems:
                result = result.model_copy(update={"status": "rejected", "problems": problems})
            else:
                outcome = await self._summarise(rec.snap, intent, rec.styles, rec.base_seed, rec.k, board.horizon_s,
                                                intent.team_id, rec.meta, rec.evaluator, rec.cancel)
                result = result.model_copy(update={"outcome": outcome, "status": "ready"})
        except (asyncio.CancelledError, rollout.RolloutCancelled):
            raise
        except Exception as exc:  # noqa: BLE001 - one bad tactic must not stop the others
            log.warning("tactic %s could not be simulated", tid, exc_info=True)
            rec.errors.append(f"{intent.title}: simulation failed ({type(exc).__name__})")
            result = result.model_copy(update={"status": "rejected", "problems": ["the simulation failed"]})
        rec.results[tid] = result
        self._publish(rec)

    async def _conclude(self, rec: _Record) -> None:
        """Keep the best diverse set, write the verdict and finish. Safe to call after a timeout."""
        board = rec.board
        baseline = board.baseline
        if baseline is None:
            self._publish(rec, status="failed")
            return
        tactician = _tactician()
        # Anything still being worked on when time ran out is dropped; so are rejected ideas.
        ready = [r for r in rec.results.values() if r.status == "ready" and r.outcome is not None]
        ranked = evaluate.rank(ready, baseline, board.risk)
        if tactician is not None:
            final = tactician.select_diverse(ranked, rec.request.n)
        else:
            final = ranked[: rec.request.n]
        final = evaluate.rank(list(final), baseline, board.risk)
        for r in rec.results.values():
            if r.status == "rejected":
                rec.errors.append(f"{r.intent.title}: rejected ({'; '.join(r.problems[:2])})")
        kept = {r.tactic_id for r in final}
        rec.results = {tid: r for tid, r in rec.results.items() if tid in kept}
        rec.intents = {tid: i for tid, i in rec.intents.items() if tid in kept or tid in rec.user}
        for r in final:
            rec.results[r.tactic_id] = r
        self._publish(rec, status="evaluating", shortfall=_shortfall(rec.errors, len(final), rec.request.n))

        remaining = default_deadline_s() - (time.monotonic() - rec.started)
        verdict: Verdict | None = None
        if tactician is not None and remaining > 2.0 and final:
            try:
                verdict = await asyncio.wait_for(
                    tactician.verdict(rec.clients, board.situation, final, baseline, rec.request.language,
                                      meta=rec.meta, prompt=rec.request.prompt, snapshot_digest=rec.base_seed,
                                      timeout_s=max(2.0, remaining - 1.0), errors=rec.errors, risk=board.risk),
                    timeout=max(2.0, remaining))
            except Exception as exc:  # noqa: BLE001 - includes the timeout
                rec.errors.append(f"gaffer: {type(exc).__name__}")
        if verdict is None:
            verdict = self._template_verdict(tactician, rec, final, baseline)
        self._publish(rec, status="done", verdict=verdict)

    @staticmethod
    def _template_verdict(tactician: Any, rec: _Record, final: list[TacticResult],
                          baseline: OutcomeStats) -> Verdict:
        if tactician is not None and final:
            try:
                payload = tactician.verdict_payload(rec.board.situation, final, baseline, rec.request.language,
                                                    meta=rec.meta, prompt=rec.request.prompt,
                                                    snapshot_digest=rec.base_seed, risk=rec.board.risk)
                return Verdict(**tactician.offline_verdict(payload).model_dump())
            except Exception:  # noqa: BLE001
                log.warning("tactician template verdict failed", exc_info=True)
        return _fallback_verdict(final, baseline, rec.request.language)
