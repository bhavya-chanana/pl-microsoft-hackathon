"""The Tactics Board's creative agents (Track B, B3).

    paused moment ─▶ five persona calls in parallel ─▶ check_intent (+ one repair turn) ─▶ stream to the board
                     (positional, gegenpress, direct,        │
                      wide, maverick)                        ▼
                                           simulator rollouts + evaluator (tactics/*)
                                                             │
                              select_diverse (MMR) ◀─────────┘
                                                             ▼
                                         Gaffer's verdict (strong model, fact-checked)

You decide what to try; the simulator decides what happens. Each persona proposes two ``TacticIntent``
ideas for the coached team. gpt-5 deployments reject ``temperature`` (see ``agents/llm.py``), so diversity
comes from structure rather than sampling:

* a different style brief per persona;
* later calls are told which mechanisms earlier calls already claimed (calls are staggered so the
  early ones can land first);
* the board picks what to show with MMR over utility and mechanism/zone/player distance, and always keeps
  one maverick idea when a valid one exists.

Real club and player names are allowed for this feature, so the prompts carry no "fictional" clause.
Every failure degrades: a persona that fails, times out or cannot fix its draft is replaced by rule-based
recipes (``tactics.playbook``) and the verdict falls back to a data-only template, never to an error.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from agent_framework import Agent
from pydantic import Field

from matchlens.agents import prompts
from matchlens.agents.contracts import _Strict
from matchlens.agents.llm import ModelClients
from matchlens.agents.pipeline import call_agent
from matchlens.domain.models import MatchMeta
from matchlens.domain.pitch import distance, to_relative
from matchlens.simulator.style import SimStyle
from matchlens.tactics import playbook
from matchlens.tactics.intent import ZONES, Action, BallAction, Mechanism, PressTrigger, TacticIntent
from matchlens.tactics.models import OutcomeStats, Situation, TacticResult, Verdict
from matchlens.tactics.validate import check_intent

log = logging.getLogger(__name__)

PER_PERSONA = 2
STAGGER_S = 0.4  # later personas start a little later so they can see what earlier ones claimed
MIN_CALL_S = 3.0  # do not start a model call with less time than this left
MIN_REPAIR_S = 5.0
VERDICT_TIMEOUT_S = 30.0
MMR_LAMBDA = 0.5
WILDCARDS = frozenset({"maverick", "wildcard"})
OUTCOME_METRICS = ("p_shot", "mean_xg", "p_goal", "p_box_entry", "p_turnover", "p_concede_shot", "xt_delta",
                   "time_to_shot_s")


# ------------------------------------------------------------------ personas


@dataclass(frozen=True)
class Persona:
    name: str
    label: str
    brief: str
    mechanisms: tuple[Mechanism, ...]  # preferred mechanisms, in order (also the offline recipe preference)


PERSONAS: dict[str, Persona] = {p.name: p for p in (
    Persona(
        "positional", "Positional play",
        "Juego de posicion. Win the moment with structure: create overloads between the lines, find the third "
        "man, use the half-spaces, pull a defender out and play into the space he leaves. Patient, short, "
        "angled passes; the point is a free player in a dangerous zone, not a fast ball.",
        (Mechanism.THIRD_MAN, Mechanism.OVERLOAD, Mechanism.SWITCH, Mechanism.ISOLATION)),
    Persona(
        "gegenpress", "Gegenpress and transitions",
        "Win the ball back high and strike before the opponent resets. Counter-press triggers, pressing traps "
        "on the touchline, cutting the passing lane behind the first presser, and the first vertical pass "
        "after a regain. In possession, think about what happens if the ball is lost in the next 3 seconds.",
        (Mechanism.PRESS_TRAP, Mechanism.COUNTER, Mechanism.OVERLOAD, Mechanism.LOW_BLOCK)),
    Persona(
        "direct", "Direct play",
        "Go forward quickly. Runs in behind, long balls to a target man or a runner, flick-ons, second balls "
        "and knock-downs, an aerial threat attacking the near post. Accept risk for speed and territory.",
        (Mechanism.RUN_IN_BEHIND, Mechanism.COUNTER, Mechanism.SET_PIECE, Mechanism.ISOLATION)),
    Persona(
        "wide", "Wide overloads",
        "Use the full width of the pitch: overlaps and underlaps, a winger isolated one against one, early "
        "crosses and cut-backs, and the switch of play to the far side once the opponent has shifted over.",
        (Mechanism.OVERLOAD, Mechanism.SWITCH, Mechanism.CUTBACK, Mechanism.ISOLATION)),
    Persona(
        "maverick", "Maverick",
        "Something a coach would be brave to try, yet legal and plausible for these exact players: a false "
        "nine dropping into midfield to drag a centre-back out, an inverted full-back stepping into midfield, "
        "the goalkeeper as an extra passer, overloading the weak side, a deliberate foul-free press bait. "
        "Be surprising, but name the weakness it exploits.",
        (Mechanism.OVERLOAD, Mechanism.ISOLATION, Mechanism.RUN_IN_BEHIND, Mechanism.LOW_BLOCK)),
)}
PERSONA_ORDER: tuple[str, ...] = tuple(PERSONAS)


# --------------------------------------------------------------- LLM contracts


class TacticDraftSet(_Strict):
    """What one persona returns: exactly two tactics for the coached team, with different mechanisms."""

    tactics: list[TacticIntent] = Field(min_length=PER_PERSONA, max_length=PER_PERSONA)


class VerdictDraft(_Strict):
    headline: str = Field(max_length=100)
    text: str
    recommended_tactic_id: str | None
    cited_metrics: list[str]


# ------------------------------------------------------------------- payloads


def style_knob_ranges() -> dict[str, list[float]]:
    """knob -> [low, high, neutral] for the style_changes the model may propose."""
    out: dict[str, list[float]] = {}
    for name, field in SimStyle.model_fields.items():
        lo = next(m.ge for m in field.metadata if hasattr(m, "ge"))
        hi = next(m.le for m in field.metadata if hasattr(m, "le"))
        out[name] = [float(lo), float(hi), float(field.default)]
    return out


def _walk(prefix: str, value: Any, out: set[str], depth: int) -> None:
    out.add(prefix)
    if depth >= 4:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _walk(f"{prefix}.{key}", item, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                out.add(f"{prefix}:{item}")
            elif isinstance(item, dict):  # KB rows: patterns_for[{tag,...}], key_players[{name,...}], style[{key,...}]
                for k in ("tag", "key", "name", "player_id"):
                    if isinstance(item.get(k), str):
                        out.add(f"{prefix}:{item[k]}")


def context_keys(situation: Situation, provider_context: dict[str, Any],
                 baseline: OutcomeStats | None = None) -> list[str]:
    """Every key an intent may cite: ``{team_id}.{path}`` into that team's context, ``tag:{player_id}:{tag}``
    for a player's real-data tags and ``baseline.{metric}`` for the do-nothing outcome."""
    keys: set[str] = set()
    for team_id, ctx in (provider_context or {}).items():
        if isinstance(ctx, dict):
            _walk(str(team_id), ctx, keys, 0)
    for p in situation.players:
        for tag in p.tags:
            keys.add(f"tag:{p.player_id}:{tag}")
    if baseline is not None:
        keys.update(f"baseline.{m}" for m in OUTCOME_METRICS)
    return sorted(keys)[:240]


def situation_digest(situation: Situation) -> str:
    """Stand-in for the simulator snapshot digest when the caller does not supply one."""
    basis = repr((situation.match_id, round(situation.elapsed, 2), tuple(round(v, 1) for v in situation.ball),
                  sorted(situation.score.items()), situation.possession_team_id))
    return hashlib.blake2b(basis.encode(), digest_size=8).hexdigest()


def _team_block(meta: MatchMeta, team_id: str, coaching: str, situation: Situation,
                provider_context: dict[str, Any]) -> dict[str, Any]:
    team = meta.team(team_id)
    ctx = dict(provider_context.get(team_id) or {})
    style = ctx.pop("style", None)
    return {"team_id": team_id, "name": team.name, "side": team.side.value, "formation": team.formation,
            "role": "coached" if team_id == coaching else "opponent",
            "goals": situation.score.get(team_id, 0), "style": style, "context": ctx}


def _player_rows(meta: MatchMeta, situation: Situation, right: bool) -> list[dict[str, Any]]:
    ball = to_relative(*situation.ball, right)
    rows = []
    for p in situation.players:
        x, y = to_relative(p.x, p.y, right)
        info = meta.players.get(p.player_id)
        rows.append({
            "id": p.player_id, "name": p.name, "team_id": p.team_id, "role": p.role,
            "position": info.position.value if info else None,
            "x": round(x, 1), "y": round(y, 1), "dist_to_ball": round(distance((x, y), ball), 1),
            "top_speed": info.top_speed if info else None, "tags": list(p.tags),
            "has_ball": p.player_id == situation.carrier_id,
        })
    rows.sort(key=lambda r: (r["team_id"] != situation.coaching_team_id, r["x"], r["id"]))
    return rows


def build_payload(
    situation: Situation,
    provider_context: dict[str, Any],
    baseline: OutcomeStats | None,
    prompt: str | None,
    persona: Persona | str,
    n_per_persona: int = PER_PERSONA,
    claimed_mechanisms: Iterable[str] = (),
    language: str = "en",
    *,
    meta: MatchMeta,
    horizon_s: float = 10.0,
    snapshot_digest: str | None = None,
    already_on_board: list[dict[str, str]] | None = None,
    recipes: list[TacticIntent] | None = None,
) -> dict[str, Any]:
    """The JSON a persona call receives (task ``tactics``).

    ``provider_context`` maps team_id -> ``StyleProvider.context(team_id)``; add the team's ``SimStyle`` dump
    under its ``"style"`` key. Positions are in the coached team's frame (x towards the opposition goal,
    y=68 on its left). ``recipes`` (full intents) is only for the offline template and is left out online.
    """
    persona = PERSONAS[persona] if isinstance(persona, str) else persona
    coaching = situation.coaching_team_id
    right = coaching == meta.home.team_id
    opponent = meta.opponent(coaching).team_id
    bx, by = to_relative(*situation.ball, right)
    payload: dict[str, Any] = {
        "task": "tactics",
        "language": language,
        "snapshot_digest": snapshot_digest or situation_digest(situation),
        "prompt": (prompt or "").strip()[:400] or None,
        "persona": {"name": persona.name, "brief": persona.brief,
                    "focus_mechanisms": [m.value for m in persona.mechanisms]},
        "n": n_per_persona,
        "horizon_s": horizon_s,
        "moment": {
            "minute": situation.minute_label, "period": situation.period, "phase": situation.phase,
            "score": {meta.team(t).name: g for t, g in situation.score.items()},
            "possession_team_id": situation.possession_team_id, "carrier_id": situation.carrier_id,
            "coached_team_in_possession": situation.possession_team_id == coaching,
            "ball": {"x": round(bx, 1), "y": round(by, 1), "zone": situation.ball_zone},
        },
        "coaching": {"team_id": coaching, "name": meta.team(coaching).name, "opponent_team_id": opponent},
        "teams": [_team_block(meta, t, coaching, situation, provider_context) for t in (coaching, opponent)],
        "players": _player_rows(meta, situation, right),
        "baseline": baseline.model_dump() if baseline is not None else None,
        "claimed_mechanisms": list(dict.fromkeys(claimed_mechanisms)),
        "already_on_board": already_on_board or [],
        "allowed": {
            "zones": list(ZONES),
            "actions": [a.value for a in Action],
            "ball_actions": [a.value for a in BallAction],
            "mechanisms": [m.value for m in Mechanism],
            "press_triggers": [t.value for t in PressTrigger],
            "style_knobs": style_knob_ranges(),
            "context_keys": context_keys(situation, provider_context, baseline),
        },
    }
    if recipes is not None:
        payload["recipes"] = [r.model_dump(mode="json") for r in recipes]
    return payload


# --------------------------------------------------------------- recipe fallback


def pick_recipes(persona: Persona | str, recipes: list[TacticIntent], claimed: Iterable[str],
                 n: int = PER_PERSONA) -> list[TacticIntent]:
    """The ``n`` recipes that best fit a persona: unclaimed mechanisms first, then the persona's preference."""
    persona = PERSONAS[persona] if isinstance(persona, str) else persona
    claimed = set(claimed)
    preference = {m.value: i for i, m in enumerate(persona.mechanisms)}
    order = sorted(range(len(recipes)), key=lambda i: (
        recipes[i].mechanism.value in claimed, preference.get(recipes[i].mechanism.value, 99), i))
    chosen: list[TacticIntent] = []
    for i in order:
        if recipes[i].mechanism not in {c.mechanism for c in chosen}:
            chosen.append(recipes[i])
        if len(chosen) == n:
            return chosen
    for i in order:  # fewer than n distinct mechanisms: fill with what is left
        if recipes[i] not in chosen:
            chosen.append(recipes[i])
        if len(chosen) == n:
            break
    return chosen


def offline_tactics(payload: dict[str, Any]) -> TacticDraftSet:
    """Offline answer for a persona call: recipes (``payload["recipes"]``) chosen for the persona."""
    recipes = [TacticIntent.model_validate(r) for r in payload.get("recipes") or []]
    persona = (payload.get("persona") or {}).get("name", "positional")
    picked = pick_recipes(persona, recipes, payload.get("claimed_mechanisms") or [], PER_PERSONA)
    if not picked:
        raise ValueError("no playbook recipe applies to this moment")
    while len(picked) < PER_PERSONA:  # a single applicable recipe: the screen drops the duplicate
        picked.append(picked[0])
    return TacticDraftSet(tactics=picked)


def _recipes(situation: Situation, meta: MatchMeta, horizon_s: float) -> list[TacticIntent]:
    try:
        return playbook.recipes(situation, situation.coaching_team_id, meta, horizon_s)
    except Exception:  # noqa: BLE001 - recipes are a fallback; they must never break the board
        log.warning("playbook recipes failed", exc_info=True)
        return []


# ---------------------------------------------------------------------- ideate

OnTactic = Callable[[str, TacticIntent], Awaitable[None]]


_NUMBER = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)")
_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def numbers_of(text: str) -> list[float]:
    """Numbers written in text. Thousands separators count ("1,200" is 1200); a decimal comma is a decimal."""
    return [float(raw.replace(",", "")) if _GROUPED.fullmatch(raw) else float(raw.replace(",", "."))
            for raw in _NUMBER.findall(text)]


def _all_numbers(value: Any) -> set[float]:
    """Every number inside a nested structure of dicts, lists, strings and numbers."""
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return {float(value)}
    if isinstance(value, str):
        return set(numbers_of(value))
    if isinstance(value, dict):
        return set().union(*(_all_numbers(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple, set)):
        return set().union(*(_all_numbers(v) for v in value)) if value else set()
    return set()


def text_number_allowance(meta: MatchMeta, situation: Situation, horizon_s: float,
                          baseline: OutcomeStats | None) -> set[float]:
    """Numbers a tactic's title and idea may mention: shirt numbers, the minute, the horizon and the
    baseline statistics as they would be written. Anything else would be an invented statistic."""
    allowed = {float(meta.players[p.player_id].shirt) for p in situation.players if p.player_id in meta.players}
    allowed |= {float(n) for n in numbers_of(situation.minute_label)} | {float(horizon_s)}
    allowed |= {float(v) for v in situation.score.values()}
    if baseline is not None:
        allowed |= _stat_forms(baseline.model_dump(), set(OUTCOME_METRICS))
    return allowed


def text_number_problems(intent: TacticIntent, allowed: set[float]) -> list[str]:
    """Digits in the title or idea that are not a shirt number, a minute or a statistic we supplied."""
    own = {float(d.start_s) for d in intent.directives} | {float(d.end_s) for d in intent.directives}
    own |= {float(intent.rest_defence), float(len(intent.directives))}
    stray = sorted({v for v in numbers_of(f"{intent.title} {intent.idea}")
                    if not any(abs(v - a) < 1e-6 for a in allowed | own)})
    if not stray:
        return []
    return ["title/idea: numbers " + ", ".join(f"{v:g}" for v in stray) + " are not shirt numbers, minutes or "
            "supplied statistics; remove them (describe the idea in words)"]


def _screen(
    meta: MatchMeta,
    situation: Situation,
    intents: Iterable[TacticIntent],
    horizon_s: float,
    allowed_keys: set[str],
    held: set[Mechanism],
    text_numbers: set[float] | None = None,
) -> tuple[list[TacticIntent], list[tuple[TacticIntent, list[str]]]]:
    """Split a draft into valid intents and (intent, problems) pairs. ``held`` are mechanisms the
    persona already has, so the second tactic must differ from the first."""
    team = situation.coaching_team_id
    good: list[TacticIntent] = []
    bad: list[tuple[TacticIntent, list[str]]] = []
    used = set(held)
    for intent in intents:
        if intent in good:  # the same idea twice is a repeat, not a mistake worth a repair turn
            continue
        problems = check_intent(meta, situation, intent, horizon_s, allowed_keys)
        if intent.team_id != team:
            problems.insert(0, f"team_id must be the coached team {team}, not {intent.team_id}")
        if text_numbers is not None:
            problems.extend(text_number_problems(intent, text_numbers))
        if intent.mechanism in used:
            problems.append(f"mechanism {intent.mechanism.value} is already used by your other tactic; "
                            "give each tactic a different mechanism")
        if problems:
            bad.append((intent, problems))
        else:
            good.append(intent)
            used.add(intent.mechanism)
    return good, bad


def _make_agent(clients: ModelClients, persona: str) -> Agent:
    opts = clients.creative_options()
    return Agent(clients.creative_client, prompts.TACTICIAN, name=f"tactician_{persona}",
                 description=f"Proposes {persona} tactics for a paused moment", default_options=opts or None)


async def ideate(
    clients: ModelClients,
    situation: Situation,
    provider_context: dict[str, Any],
    *,
    meta: MatchMeta,
    prompt: str | None = None,
    n: int = len(PERSONAS) * PER_PERSONA,
    language: str = "en",
    deadline_s: float = 20.0,
    horizon_s: float = 10.0,
    baseline: OutcomeStats | None = None,
    snapshot_digest: str | None = None,
    on_tactic: OnTactic | None = None,
    personas: Iterable[str] = PERSONA_ORDER,
) -> tuple[list[tuple[str, TacticIntent]], list[str]]:
    """Run the persona calls concurrently and return ``[(persona, intent)]`` plus errors.

    Every returned intent passed ``check_intent`` for the coached team. ``on_tactic(persona, intent)`` is
    awaited as each one lands (at most ``n`` are streamed and returned). A persona that produced nothing
    valid is replaced by playbook recipes, labelled ``"recipe"``; the board dedupes those by tactic id.
    """
    started = time.monotonic()
    team = situation.coaching_team_id
    recipes = _recipes(situation, meta, horizon_s)
    on_board = [{"title": r.title, "mechanism": r.mechanism.value} for r in recipes]
    keys = context_keys(situation, provider_context, baseline)
    allowed = set(keys) | set(getattr(playbook, "PLAYBOOK_KEYS", ()))
    text_numbers = text_number_allowance(meta, situation, horizon_s, baseline)
    offline = not clients.settings.online
    digest = snapshot_digest or situation_digest(situation)
    landed: list[tuple[str, TacticIntent]] = []
    seen: set[str] = set()
    errors: list[str] = []
    claimed: list[str] = []

    def left() -> float:
        return deadline_s - (time.monotonic() - started)

    async def land(persona: str, intent: TacticIntent) -> bool:
        key = intent.model_dump_json()
        if key in seen or len(landed) >= n:
            return False
        seen.add(key)
        landed.append((persona, intent))
        if intent.mechanism.value not in claimed:
            claimed.append(intent.mechanism.value)
        if on_tactic is not None:
            try:
                await on_tactic(persona, intent)
            except Exception:  # noqa: BLE001 - a broken listener must not stop the other personas
                log.warning("on_tactic failed for %s", persona, exc_info=True)
        return True

    def payload_for(persona: str, **extra: Any) -> dict[str, Any]:
        return build_payload(
            situation, provider_context, baseline, prompt, persona, PER_PERSONA, claimed, language, meta=meta,
            horizon_s=horizon_s, snapshot_digest=digest, already_on_board=on_board,
            recipes=recipes if offline else None) | extra

    async def run_persona(index: int, persona: str) -> int:
        await asyncio.sleep(index * STAGGER_S)
        if left() < MIN_CALL_S:
            errors.append(f"{persona}: no time left to ask")
            return 0
        agent = _make_agent(clients, persona)
        payload = payload_for(persona)
        draft, err = await call_agent(agent, payload, TacticDraftSet, timeout=left())
        if draft is None:
            errors.append(err or f"{persona}: no answer")
            return 0
        good, bad = _screen(meta, situation, draft.tactics, horizon_s, allowed, set(), text_numbers)
        count = 0
        for intent in good:  # count what the persona got right, even when another persona already landed it
            await land(persona, intent)
            count += 1
        if bad and left() >= MIN_REPAIR_S:
            note = [f"tactic {i}: {p}" for i, (_, probs) in enumerate(bad, start=1) for p in probs][:12]
            payload = payload_for(persona, problems=note,
                                  previous=[i.model_dump(mode="json") for i, _ in bad])
            draft2, err2 = await call_agent(agent, payload, TacticDraftSet, timeout=left())
            if draft2 is None:
                errors.append(err2 or f"{persona}: no answer to the repair turn")
            else:
                held = {i.mechanism for i in good}
                good2, bad = _screen(meta, situation, draft2.tactics, horizon_s, allowed, held, text_numbers)
                for intent in good2:
                    if count < PER_PERSONA:
                        await land(persona, intent)
                        count += 1
        for intent, probs in bad:
            log.warning("tactician %s dropped %r: %s", persona, intent.title, "; ".join(probs[:3]))
        return count

    names = [p for p in personas if p in PERSONAS]
    counts = await asyncio.gather(*(run_persona(i, p) for i, p in enumerate(names)))

    # Personas that produced nothing: fall back to recipes that are not on the list yet.
    for persona, count in zip(names, counts, strict=True):
        if count or not recipes:
            continue
        picked = pick_recipes(persona, [r for r in recipes if r.model_dump_json() not in seen], claimed)
        for intent in picked:
            await land("recipe", intent)
        errors.append(f"{persona}: no valid tactics" + (", used rule-based recipes instead" if picked else ""))
    return landed, errors


# ------------------------------------------------------------- diverse selection


def _zones(intent: TacticIntent) -> frozenset[str]:
    zones = {d.zone for d in intent.directives if d.zone} | {b.to_zone for b in intent.ball_plan if b.to_zone}
    return frozenset(zones)


def _players(intent: TacticIntent) -> frozenset[str]:
    players = {d.player_id for d in intent.directives}
    players |= {p for b in intent.ball_plan for p in (b.from_player_id, b.to_player_id) if p}
    return frozenset(players)


def _jaccard_distance(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return 1.0 - len(a & b) / len(union) if union else 0.0


def tactic_distance(a: TacticIntent, b: TacticIntent) -> float:
    """0 (same idea) to 1 (nothing in common): mechanism first, then zones used, then players involved."""
    return (0.5 * (a.mechanism != b.mechanism) + 0.3 * _jaccard_distance(_zones(a), _zones(b))
            + 0.2 * _jaccard_distance(_players(a), _players(b)))


def select_diverse(results: list[TacticResult], n: int) -> list[TacticResult]:
    """``n`` results by maximal marginal relevance over utility and tactic distance.

    Rejected and unevaluated results are ignored. The best result is always taken first. If a valid
    maverick/wildcard idea exists it is kept, replacing the weakest other pick. Best utility first.
    """
    pool = [r for r in results if r.status != "rejected" and r.utility is not None]
    if n <= 0 or not pool:
        return []
    if len(pool) <= n:
        return sorted(pool, key=lambda r: -(r.utility or 0.0))
    lo, hi = min(r.utility or 0.0 for r in pool), max(r.utility or 0.0 for r in pool)
    gain = {r.tactic_id: ((r.utility or 0.0) - lo) / (hi - lo) if hi > lo else 1.0 for r in pool}
    chosen = [max(pool, key=lambda r: (r.utility or 0.0, r.tactic_id))]
    rest = [r for r in pool if r is not chosen[0]]
    def marginal(r: TacticResult, others: list[TacticResult]) -> float:
        similarity = max(1.0 - tactic_distance(r.intent, c.intent) for c in others)
        return MMR_LAMBDA * gain[r.tactic_id] - (1.0 - MMR_LAMBDA) * similarity

    while rest and len(chosen) < n:
        best = max(rest, key=lambda r: (marginal(r, chosen), r.tactic_id))
        chosen.append(best)
        rest.remove(best)
    if n > 1 and not any(r.persona in WILDCARDS for r in chosen):
        wild = [r for r in pool if r.persona in WILDCARDS]
        if wild:
            keep = max(wild, key=lambda r: (r.utility or 0.0, r.tactic_id))
            # Make room by dropping the pick that adds least next to the others and the wildcard.
            drop = min(chosen[1:], key=lambda r: (marginal(r, [c for c in chosen if c is not r] + [keep]),
                                                  r.tactic_id))
            chosen[chosen.index(drop)] = keep
    return sorted(chosen, key=lambda r: (-(r.utility or 0.0), r.tactic_id))


# ---------------------------------------------------------------------- verdict


def _row(r: TacticResult) -> dict[str, Any]:
    return {"tactic_id": r.tactic_id, "rank": r.rank, "persona": r.persona, "title": r.intent.title,
            "idea": r.intent.idea, "mechanism": r.intent.mechanism.value,
            "outcome": r.outcome.model_dump() if r.outcome else None, "lift": r.lift, "utility": r.utility,
            "confidence": r.confidence}


def verdict_payload(
    situation: Situation, ranked: list[TacticResult], baseline: OutcomeStats | None, language: str, *,
    meta: MatchMeta | None = None, prompt: str | None = None, snapshot_digest: str | None = None, top: int = 6,
    risk: str | None = None,
) -> dict[str, Any]:
    names = {t: meta.team(t).name if meta else t for t in situation.score}
    scored = [r for r in ranked if r.outcome is not None][:top]
    return {
        "task": "tactics_verdict",
        "language": language,
        "snapshot_digest": snapshot_digest or situation_digest(situation),
        "prompt": (prompt or "").strip()[:400] or None,
        "coaching": {"team_id": situation.coaching_team_id, "name": names.get(situation.coaching_team_id)},
        "horizon_s": baseline.horizon_s if baseline is not None else None,
        "risk": risk,
        "moment": {"minute": situation.minute_label, "phase": situation.phase, "ball_zone": situation.ball_zone,
                   "score": {names.get(t, t): g for t, g in situation.score.items()},
                   "coaching_team_id": situation.coaching_team_id},
        "baseline": baseline.model_dump() if baseline is not None else None,
        "ranked": [_row(r) for r in scored],
        "allowed_metrics": sorted(set(OUTCOME_METRICS) | {m for r in scored for m in r.lift}),
        "names": sorted({p.name for p in situation.players} | set(names.values())
                        | {t for t in situation.score}),
    }


# ----------------------------------------------------------- verdict fact-check

_COUNT_WORDS = (
    "three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen "
    "nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million dozen "
    "tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince veinte treinta cuarenta "
    "cincuenta sesenta setenta ochenta noventa cien ciento mil millon millones docena"
).split()
_COUNT_WORD = re.compile(r"\b(" + "|".join(_COUNT_WORDS) + r")\b", re.IGNORECASE)
_COMMON_WORDS = frozenset((
    "the this that these those then there here they them their it its he his we our and but so or if while because "
    "however overall also still both with without given expect keep when where why what who how on in at for by as "
    "to from after before once since until even only both either neither some any all each every most more less "
    "el la los las un una y o pero si con sin en por para como cuando donde porque aunque tambien "
    "try base based confidence confianza probar basado").split())
_CAPITALISED = re.compile(r"(?<![\w'’-])[A-ZÀ-ÖØ-Þ][\w'’-]*")
_SENTENCE_END = re.compile(r"(?<=[.!?:;])\s+|\n+")


def _forms(v: float, percent: bool) -> set[float]:
    """How a statistic may be written: a share as 0-100 with 0 or 1 decimals or as a fraction, else 2 decimals."""
    out = {round(v, 2), round(v, 1), round(v, 0)} if abs(v) >= 1 else {round(v, 2)}
    if percent:
        out |= {round(v * 100, 0), round(v * 100, 1)}
    return out


def _stat_forms(stats: dict[str, Any], bounds_for: set[str]) -> set[float]:
    """Allowed written forms of an OutcomeStats dump. Interval bounds only for metrics in ``bounds_for``."""
    out: set[float] = set()
    for key, v in stats.items():
        if isinstance(v, dict) and "value" in v:  # Interval
            out |= _forms(v["value"], True)
            if key in bounds_for:
                out |= _forms(v["low"], True) | _forms(v["high"], True)
        elif isinstance(v, dict):  # terminals: counts
            out |= {float(c) for c in v.values()}
        elif isinstance(v, bool) or v is None:
            continue
        elif isinstance(v, (int, float)):
            out |= {float(v)} if float(v).is_integer() else _forms(float(v), False)
    return out


def _allowed_numbers(payload: dict[str, Any], cited: set[str]) -> set[float]:
    """Exactly the written forms of the supplied statistics: a share as value*100 (0 or 1 decimals), a raw
    value to 2 decimals, counts as given, ranks and the moment's own numbers. Interval bounds are allowed
    only for metrics the verdict cites."""
    rows = payload.get("ranked") or []
    out = _stat_forms(payload.get("baseline") or {}, cited)
    for r in rows:
        out |= _stat_forms(r.get("outcome") or {}, cited)
        for lift in (r.get("lift") or {}).values():
            out |= _forms(lift, True)
        if r.get("utility") is not None:
            out.add(round(r["utility"], 2))
        out |= _all_numbers(r.get("title"))
    out |= {float(i) for i in range(1, len(rows) + 1)} | {float(len(rows))}
    out |= _all_numbers(payload.get("moment"))
    if payload.get("horizon_s"):
        out.add(float(payload["horizon_s"]))
    return out


def _vocabulary(payload: dict[str, Any]) -> set[str]:
    words: set[str] = set()
    texts = list(payload.get("names") or []) + [str(payload.get("coaching", {}).get("name") or "")]
    for r in payload.get("ranked") or []:
        texts += [r.get("title", ""), r.get("idea", ""), str(r.get("mechanism", "")).replace("_", " "),
                  str(r.get("persona", ""))]
    texts += [str(k) for k in (payload.get("moment") or {}).get("score", {})]
    texts += [str(payload.get("moment", {}).get(k, "")) for k in ("phase", "ball_zone", "minute")]
    for t in texts:
        words |= {w.lower().strip("'’-") for w in re.findall(r"[\w'’-]+", t)}
        words |= {w.lower().strip("'’-").removesuffix("'s").removesuffix("’s") for w in re.findall(r"[\w'’-]+", t)}
    return words


def unknown_names(text: str, vocab: set[str]) -> list[str]:
    """Capitalised, name-like words that the supplied roster, teams and tactic titles do not contain.

    A word that merely starts a sentence is ordinary capitalisation and is only checked when it is part
    of a multi-word name or a possessive ("Mbappe Jr", "Salah's").
    """
    out: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        lead = len(sentence) - len(sentence.lstrip(" \t\"'“‘(¿¡[-"))
        tokens = list(_CAPITALISED.finditer(sentence))
        for n, m in enumerate(tokens):
            word = m.group(0)
            base = word.lower().removesuffix("'s").removesuffix("’s").strip("'’-")
            if len(base) < 2 or word.isupper() or base in vocab or base in _COMMON_WORDS:
                continue
            if m.start() <= lead:  # starts the sentence
                nxt = tokens[n + 1] if n + 1 < len(tokens) else None
                chained = nxt is not None and sentence[m.end():nxt.start()].strip() == ""
                if not chained and not word.lower().endswith(("'s", "’s")):
                    continue
            out.append(word)
    return sorted(set(out))


def check_verdict(draft: VerdictDraft, payload: dict[str, Any]) -> list[str]:
    """Problems with a verdict: an unknown tactic or metric, a number the statistics do not contain (as
    digits or as a spelled-out count above two), or a name that is not in the roster or tactic titles."""
    problems: list[str] = []
    ids = {r["tactic_id"] for r in payload.get("ranked") or []}
    if draft.recommended_tactic_id is not None and draft.recommended_tactic_id not in ids:
        problems.append(f"recommended_tactic_id {draft.recommended_tactic_id} is not one of the ranked tactics")
    allowed_metrics = set(payload.get("allowed_metrics") or [])
    cited = {m.split(".")[0] for m in draft.cited_metrics}
    unknown = [m for m in draft.cited_metrics if m.split(".")[0] not in allowed_metrics]
    if unknown:
        problems.append(f"cited_metrics not in the statistics: {', '.join(unknown)}")
    text = f"{draft.headline}\n{draft.text}"
    allowed = _allowed_numbers(payload, cited)
    stray = sorted({v for v in numbers_of(text) if not any(abs(v - a) < 1e-6 for a in allowed)})
    if stray:
        problems.append("numbers that are not in the statistics: " + ", ".join(f"{v:g}" for v in stray))
    words = sorted({m.group(0).lower() for m in _COUNT_WORD.finditer(text)})
    if words:
        problems.append("spelled-out counts are not allowed (write the supplied figure): " + ", ".join(words))
    names = unknown_names(text, _vocabulary(payload))
    if names:
        problems.append("names that are not in the roster, teams or tactic titles: " + ", ".join(names))
    return problems


_CONFIDENCE = {
    "es": {"low": "baja", "medium": "media", "high": "alta"},
    "hi": {"low": "कम", "medium": "मध्यम", "high": "उच्च"},
    "en": {"low": "low", "medium": "medium", "high": "high"},
}
_VERDICT_TEXT = {
    "en": ("Try: {title}",
           "{title} ({mechanism}) is the best of the {count} ideas tested: shot chance {ps}% against {bps}% if "
           "nothing changes, {xg} expected goals against {bxg}, and a {cs}% chance of conceding a shot (baseline "
           "{bcs}%). Confidence: {conf}. Based on {k} simulations of {h} s; the simulator is a model, not a "
           "prediction."),
    "es": ("Probar: {title}",
           "{title} ({mechanism}) es la mejor de las {count} ideas probadas: probabilidad de tiro {ps}% frente a "
           "{bps}% si no se cambia nada, {xg} goles esperados frente a {bxg}, y {cs}% de conceder un tiro (base "
           "{bcs}%). Confianza: {conf}. Basado en {k} simulaciones de {h} s; el simulador es un modelo, no una "
           "prediccion."),
    "hi": ("आज़माएँ: {title}",
           "{count} परखे गए विचारों में {title} ({mechanism}) सबसे अच्छा है: शॉट की संभावना {ps}% (कुछ न बदलने पर "
           "{bps}%), अपेक्षित गोल {xg} (आधार {bxg}), और शॉट खाने की संभावना {cs}% (आधार {bcs}%)। भरोसा: {conf}। "
           "{k} सिमुलेशन ({h} सेकंड) पर आधारित; सिमुलेशन एक मॉडल है, भविष्यवाणी नहीं।"),
}
_NO_VERDICT = {
    "en": ("No clear winner", "None of the ideas could be simulated, so there is nothing to recommend yet."),
    "es": ("Sin ganador claro", "Ninguna idea se pudo simular, asi que todavia no hay recomendacion."),
    "hi": ("कोई स्पष्ट विजेता नहीं", "किसी विचार का सिमुलेशन नहीं हो सका, इसलिए अभी कोई सिफ़ारिश नहीं।"),
}


def offline_verdict(payload: dict[str, Any]) -> VerdictDraft:
    """Data-only verdict: the top-ranked tactic and its statistics, nothing else."""
    lang = payload.get("language") if payload.get("language") in _VERDICT_TEXT else "en"
    rows = [r for r in payload.get("ranked") or [] if r.get("outcome")]
    base = payload.get("baseline")
    if not rows or not base:
        head, text = _NO_VERDICT[lang]
        return VerdictDraft(headline=head, text=text, recommended_tactic_id=None, cited_metrics=[])
    best, out = rows[0], rows[0]["outcome"]

    def pct(stats: dict[str, Any], key: str) -> int:
        return round(stats[key]["value"] * 100)

    head, body = _VERDICT_TEXT[lang]
    values = {
        "title": best["title"], "mechanism": best["mechanism"].replace("_", " "), "count": len(rows),
        "ps": pct(out, "p_shot"), "bps": pct(base, "p_shot"), "xg": f"{out['mean_xg']:.2f}",
        "bxg": f"{base['mean_xg']:.2f}", "cs": pct(out, "p_concede_shot"), "bcs": pct(base, "p_concede_shot"),
        "conf": _CONFIDENCE[lang].get(best.get("confidence") or "low", ""), "k": out["rollouts"],
        "h": f"{out['horizon_s']:g}",
    }
    return VerdictDraft(headline=head.format(**values)[:100], text=body.format(**values),
                        recommended_tactic_id=best["tactic_id"],
                        cited_metrics=["p_shot", "mean_xg", "p_concede_shot"])


async def verdict(
    clients: ModelClients,
    situation: Situation,
    ranked: list[TacticResult],
    baseline: OutcomeStats | None,
    language: str = "en",
    *,
    meta: MatchMeta | None = None,
    prompt: str | None = None,
    snapshot_digest: str | None = None,
    timeout_s: float = VERDICT_TIMEOUT_S,
    errors: list[str] | None = None,
    risk: str | None = None,
) -> Verdict:
    """The Gaffer's verdict on the ranked, simulated tactics.

    The strong model writes it at high reasoning. Anything it states must come from the statistics: a
    number, metric or tactic id that is not in them sends the board to the data-only template instead.
    ``errors`` collects why, when given.
    """
    payload = verdict_payload(situation, ranked, baseline, language, meta=meta, prompt=prompt,
                              snapshot_digest=snapshot_digest, risk=risk)

    def fallback(reason: str) -> Verdict:
        log.warning("gaffer's verdict: %s", reason)
        if errors is not None:
            errors.append(f"gaffer: {reason}")
        return Verdict(**offline_verdict(payload).model_dump())

    if not payload["ranked"]:
        return Verdict(**offline_verdict(payload).model_dump())
    opts = clients.options(strong=True)
    agent = Agent(clients.strong, prompts.GAFFER, name="gaffer", description="Explains the ranked tactics",
                  default_options=opts or None)
    draft, err = await call_agent(agent, payload, VerdictDraft, timeout=timeout_s)
    if draft is None:
        return fallback(err or "no answer")
    problems = check_verdict(draft, payload)
    if problems:
        return fallback("; ".join(problems))
    return Verdict(**draft.model_dump())

