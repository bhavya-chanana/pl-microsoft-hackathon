"""Record Tactics Boards for the hero moments of the demo match into a cassette (NFR §10.5).

For each chosen moment of the seed-7 match this builds the frozen ``Situation`` and simulator snapshot,
runs the baseline and the persona ideas through the simulator, ranks them and asks the Gaffer, storing
every model answer under its cassette key (``tactics|snapshot|persona|prompt|language``, and
``tactics_verdict|...``). Replayed later with ``MATCHLENS_CASSETTE=<out>``, the board is answered with no
model and no waiting.

Run (needs AZURE_OPENAI_ENDPOINT and az login):
    uv run python -m matchlens.agents.record_tactics --out demo/hero-tactics.json

Re-running with the same --out resumes: recorded answers are reused and only missing ones are asked for.
``--offline`` records the data-only templates instead (a dry run for tests; never use it for the demo file).

The cassette key contains the snapshot digest, so the live board must freeze a moment exactly like this
module does: use ``build_moment`` (same frame, same seed, same score and sent-off players) and the same
``provider_context`` (``DerivedStyleProvider.context`` per team).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Recording has no live deadline: give slow answers room rather than lose them.
os.environ.setdefault("MATCHLENS_AGENT_TIMEOUT_S", "180")

from matchlens.agents import tactician  # noqa: E402
from matchlens.agents.cassette import Cassette, CassetteChatClient  # noqa: E402
from matchlens.agents.llm import LlmSettings, ModelClients, OfflineChatClient, build_clients  # noqa: E402
from matchlens.domain.models import (  # noqa: E402
    EventType,
    Outcome,
    SyntheticMatch,
    minute_label,
)
from matchlens.domain.pitch import to_relative  # noqa: E402
from matchlens.interpret.engine import MatchEngine  # noqa: E402
from matchlens.simulator.engine import generate_match  # noqa: E402
from matchlens.simulator.fork import SimSnapshot, snapshot_from_frame  # noqa: E402
from matchlens.tactics import evaluate, rollout  # noqa: E402
from matchlens.tactics.models import PlayerSpot, Situation, TacticResult  # noqa: E402
from matchlens.tactics.styles import DerivedStyleProvider  # noqa: E402
from matchlens.tacticsml.registry import get_tactical_model  # noqa: E402

log = logging.getLogger("matchlens.record_tactics")

HORIZON_S = 10.0
SPACING_S = 150.0  # hero moments are at least this far apart
LEAD_S = 7.0  # freeze this long before the shot or goal, while the move is still building


@dataclass(frozen=True)
class HeroMoment:
    label: str
    elapsed: float
    coach_defending: bool  # coach the side without the ball (a pressing / low-block board)


def hero_moments(match: SyntheticMatch, count: int = 8) -> list[HeroMoment]:
    """The goals and best chances of the match, frozen as the move builds, plus a few quiet spells.

    Deterministic. Every fourth moment coaches the team without the ball.
    """
    shots = [e for e in match.events if e.type in (EventType.GOAL, EventType.SHOT)]
    shots.sort(key=lambda e: (e.type != EventType.GOAL, -float(e.attrs.get("xg", 0.0)), e.elapsed))
    chosen: list[tuple[str, float]] = []

    def far_enough(t: float) -> bool:
        return all(abs(t - c) >= SPACING_S for _, c in chosen)

    for e in shots:
        t = max(30.0, e.elapsed - LEAD_S)
        if len(chosen) < count - 2 and far_enough(t):
            chosen.append((f"before the {e.type.value} at {e.minute_label}", t))
    final = max(f.elapsed for f in match.tracking)
    for k in range(1, 12):  # quiet spells fill the rest
        t = final * k / 12
        if len(chosen) < count and far_enough(t):
            chosen.append((f"open play at {int(t // 60) + 1}'", t))
    chosen.sort(key=lambda c: c[1])
    return [HeroMoment(label, t, coach_defending=(i % 4 == 3)) for i, (label, t) in enumerate(chosen[:count])]


def _ball_zone(x: float, y: float) -> str:
    lane = "left" if y >= 54 else "left_half" if y >= 40 else "centre" if y >= 28 else "right_half" if y >= 14 else "right"
    third = "def" if x < 28 else "mid" if x < 55 else "att" if x < 80 else "final"
    return f"{lane}:{third}"


def feed_of(match: SyntheticMatch) -> list[tuple[float, int, int, Any]]:
    """Events and frames in the order a live feed delivers them."""
    return sorted([(e.elapsed, 1, e.sequence, e) for e in match.events]
                  + [(f.elapsed, 0, 0, f) for f in match.tracking], key=lambda t: (t[0], t[1], t[2]))


def feed_until(engine: MatchEngine, feed: list[tuple[float, int, int, Any]], cursor: int, elapsed: float) -> int:
    """Ingest events and frames up to ``elapsed`` (call with moments in time order). Returns the new cursor."""
    while cursor < len(feed) and feed[cursor][0] <= elapsed:
        _, kind, _, item = feed[cursor]
        engine.ingest_event(item) if kind else engine.ingest_frame(item)
        cursor += 1
    return cursor


def build_moment(
    match: SyntheticMatch, engine: MatchEngine, moment: HeroMoment, provider: DerivedStyleProvider,
) -> tuple[Situation, SimSnapshot]:
    """Freeze ``moment`` into a Situation and snapshot. ``engine`` must have ingested the feed up to it."""
    meta = match.meta
    frame = max((f for f in match.tracking if f.elapsed <= moment.elapsed), key=lambda f: f.elapsed)
    past = [e for e in match.events if e.elapsed <= frame.elapsed]
    possessions = [e for e in past if e.team_id and e.type in (
        EventType.PASS, EventType.CARRY, EventType.SHOT, EventType.GOAL, EventType.POSSESSION_CHANGE)]
    possession = possessions[-1].team_id if possessions else meta.home.team_id
    score = engine.score()
    sent_off = {e.player_id for e in past if e.type == EventType.CARD and e.player_id
                and e.outcome in (Outcome.RED, Outcome.SECOND_YELLOW)}
    booked = {e.player_id for e in past if e.type == EventType.CARD and e.player_id}
    snap = snapshot_from_frame(meta, frame, possession=possession, carrier=None, score=score,
                               sent_off=sent_off, booked=booked, recent_events=past[-50:], seed=7)
    state = snap.state
    coaching = meta.opponent(possession).team_id if moment.coach_defending else possession
    right = possession == meta.home.team_id
    bx, by = to_relative(*state.ball, right)
    recent_change = any(e.type == EventType.POSSESSION_CHANGE and frame.elapsed - e.elapsed <= 6 for e in past)
    if coaching != possession:
        phase = "defending"
    elif recent_change:
        phase = "transition"
    else:
        phase = "build_up" if bx < 35 else "progression" if bx < 70 else "final_third"
    spots = [PlayerSpot(player_id=p, name=meta.players[p].name, team_id=t, role=state.roles.get(p, meta.players[p].role),
                        x=state.positions[p][0], y=state.positions[p][1], tags=provider.player_tags(p))
             for t, pids in state.on_pitch.items() for p in pids]
    situation = Situation(
        match_id=meta.match_id, elapsed=frame.elapsed, minute_label=minute_label(frame.period, frame.clock),
        period=frame.period, score=dict(state.score), possession_team_id=possession, carrier_id=state.carrier,
        ball=state.ball, ball_zone=_ball_zone(bx, by), phase=phase, coaching_team_id=coaching, players=spots,
        frame=frame)
    return situation, snap


async def record_board(
    clients: ModelClients, match: SyntheticMatch, situation: Situation, snap: SimSnapshot,
    provider: DerivedStyleProvider, *, k: int, language: str, prompt: str | None,
) -> dict[str, Any]:
    """Ideate, simulate, rank and judge one moment so every model answer lands in the cassette."""
    meta = match.meta
    team = situation.coaching_team_id
    digest = rollout.snapshot_digest(snap)
    styles = {t.team_id: provider.style(t.team_id) for t in (meta.home, meta.away)}
    context = {t.team_id: provider.context(t.team_id) for t in (meta.home, meta.away)}
    evaluator = get_tactical_model()
    base_traces = await rollout.run_monte_carlo_async(snap, None, styles, k=k, horizon_s=HORIZON_S, base_seed=digest)
    baseline = evaluate.summarise(base_traces, team, evaluator, meta)
    ideas, errors = await tactician.ideate(
        clients, situation, context, meta=meta, prompt=prompt, language=language, horizon_s=HORIZON_S,
        baseline=baseline, snapshot_digest=digest, deadline_s=240.0)
    results: list[TacticResult] = []
    for persona, intent in ideas:
        tid = rollout.tactic_id_of(intent)
        traces = await rollout.run_monte_carlo_async(snap, intent, styles, k=k, horizon_s=HORIZON_S,
                                                     base_seed=digest, tactic_id=tid)
        results.append(TacticResult(tactic_id=tid, persona=persona, intent=intent, status="ready",
                                    outcome=evaluate.summarise(traces, team, evaluator, meta)))
    ranked = evaluate.rank(results, baseline, "balanced")
    shown = tactician.select_diverse(ranked, 6)
    gaffer = await tactician.verdict(clients, situation, shown, baseline, language, meta=meta, prompt=prompt,
                                     snapshot_digest=digest, timeout_s=240.0, errors=errors)
    return {"digest": digest, "tactics": len(ranked), "mechanisms": len({r.intent.mechanism for r in ranked}),
            "verdict": gaffer.headline, "errors": errors}


async def record(out: Path, seed: int, count: int, k: int, languages: list[str], prompts: list[str | None],
                 offline: bool) -> None:
    if offline:
        settings = LlmSettings(endpoint=None, fast_model="offline", strong_model="offline", mode="offline")
        live = ModelClients(fast=OfflineChatClient(), strong=OfflineChatClient(), settings=settings)
    else:
        settings = LlmSettings.from_env()
        if not settings.online:
            raise SystemExit("Recording needs a live model: set AZURE_OPENAI_ENDPOINT (and az login), "
                             "or pass --offline for a dry run.")
        os.environ["MATCHLENS_CASSETTE"] = "off"  # build the live clients only; this module wraps them
        live = build_clients(settings)
    match = generate_match(seed=seed)
    title = match.meta.storyline_title or ""
    cassette = Cassette.load(out) if out.exists() else Cassette(seed=seed, storyline=title)
    if not cassette.matches(seed, title):
        raise SystemExit(f"{out} was recorded for seed {cassette.seed}; pick another --out")
    cassette.path = out
    before = len(cassette.entries)

    wrapped: dict[int, CassetteChatClient] = {}

    def wrap(client):
        if id(client) not in wrapped:
            wrapped[id(client)] = CassetteChatClient(client, cassette, live_misses=True, record=True)
        return wrapped[id(client)]

    clients = ModelClients(fast=wrap(live.fast), strong=wrap(live.strong), settings=settings, cassette=cassette,
                           creative=wrap(live.creative_client))
    engine = MatchEngine(match.meta)
    provider = DerivedStyleProvider(engine)
    feed = feed_of(match)
    cursor = 0
    started = time.monotonic()
    try:
        for moment in hero_moments(match, count):
            cursor = feed_until(engine, feed, cursor, moment.elapsed)
            situation, snap = build_moment(match, engine, moment, provider)
            for language in languages:
                for prompt in prompts:
                    summary = await record_board(clients, match, situation, snap, provider, k=k,
                                                 language=language, prompt=prompt)
                    log.info("%s [%s] %s", moment.label, language, summary)
    finally:
        cassette.save()
    calls = sum(c.misses for c in wrapped.values())
    print(f"recorded {len(cassette.entries) - before} new answers ({len(cassette.entries)} total, {calls} model "
          f"calls) in {time.monotonic() - started:.0f}s -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("demo/hero-tactics.json"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--moments", type=int, default=8)
    parser.add_argument("--k", type=int, default=24, help="rollouts per tactic (must match the live board)")
    parser.add_argument("--languages", nargs="+", default=["en"], choices=["en", "hi", "es"])
    parser.add_argument("--prompts", nargs="*", default=[],
                        help="scenario prompts to record besides the empty one, e.g. 'attack down the left'")
    parser.add_argument("--offline", action="store_true", help="dry run with the data-only templates")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("azure", "httpx", "httpx2", "agent_framework"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(record(args.out, args.seed, args.moments, args.k, args.languages, [None, *args.prompts],
                       args.offline))


if __name__ == "__main__":
    main()
