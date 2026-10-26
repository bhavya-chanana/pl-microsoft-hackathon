"""Recorded demo match (NFR §10.5): agent answers replayed without a model call.

A cassette holds the answer a model gave to each agent request when the hero
match was recorded online (``python -m matchlens.agents.record``). Judges then
get instant, free, repeatable output, and the agent layer still runs for real:
the coordinator, fact-checker, producer caps and personalised feed all work
on the replayed answers exactly as on live ones.

Keys are semantic rather than the raw prompt, so a replay hits even when the
context pack differs slightly (a viewer joining mid-match, a faster replay):

* live insights: task + signal id + revision
* catch-ups and recaps: task + window or phase + mode + language + fan focus
  + a digest of the score and key moments in the window
* Tactics Board persona calls: ``tactics|snapshot|team|persona|n|horizon|prompt hash|language``, and the
  Gaffer's verdict ``tactics_verdict|snapshot|team|horizon|risk|prompt hash|language``. The snapshot digest
  is in the payload and is the same for both sides of one moment, so the coached team is part of the key;
  horizon and risk are included when the payload has them.
  A repair turn shares its persona's key, so the final (repaired) answer is the one stored.

A miss goes to the wrapped client when it is online (and is stored while
recording). Offline, a miss raises, so the caller publishes its data-only
fallback and labels it as such rather than passing templates off as agents.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message

log = logging.getLogger(__name__)

FORMAT = "matchlens-cassette/1"
MAX_REPLAY_DELAY_S = 8.0


class CassetteMiss(LookupError):
    """No recorded answer for this request, and no live model to ask."""


def moments_digest(context: dict[str, Any]) -> str:
    """Short digest of what a catch-up or recap is about: score and key moments."""
    window = context.get("window") or {}
    moments = [[m.get("minute"), m.get("type"), m.get("team_id"), m.get("player_ids"), m.get("facts")]
               for m in context.get("moments") or []]
    basis = [(context.get("match") or {}).get("score"), window.get("score_at_start"), moments]
    return hashlib.sha1(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _focus(profile: dict[str, Any]) -> str:
    return ",".join(filter(None, [
        (profile.get("favourite_club") or {}).get("team_id"),
        (profile.get("favourite_player") or {}).get("player_id"),
        profile.get("headline_metric"),
    ]))


def prompt_hash(prompt: str | None) -> str:
    return hashlib.sha1((prompt or "").strip().encode()).hexdigest()[:10]


def request_key(payload: dict[str, Any], *, focus: bool = True) -> str | None:
    """Cassette key for one agent request, or None if it cannot be recorded."""
    task = payload.get("task")
    if task in ("tactics", "tactics_verdict"):
        digest = payload.get("snapshot_digest")
        team = (payload.get("coaching") or {}).get("team_id")
        if not digest or not team:
            return None
        tail = [prompt_hash(payload.get("prompt")), payload.get("language")]
        horizon = payload.get("horizon_s")
        horizon = f"h{horizon:g}" if isinstance(horizon, (int, float)) else ""
        if task == "tactics":
            persona = payload.get("persona")
            persona = persona.get("name") if isinstance(persona, dict) else persona
            head = [task, digest, team, persona, f"n{payload.get('n', '')}", horizon]
        else:
            head = [task, digest, team, horizon, payload.get("risk") or ""]
        return "|".join(str(p) for p in (*head, *tail))
    context = payload.get("context") or {}
    signal = context.get("signal")
    if signal:
        return f"{task}|{signal['signal_id']}|r{signal.get('revision', 0)}"
    if task in ("catchup", "recap"):
        profile = payload.get("profile") or {}
        scope = payload.get("phase") if task == "recap" else (context.get("window") or {}).get("minutes")
        return "|".join(str(p) for p in (task, scope, profile.get("mode"), payload.get("language"),
                                         _focus(profile) if focus else "", moments_digest(context)))
    return None


@dataclass
class Cassette:
    """Recorded answers for one synthetic match (seed and storyline)."""

    seed: int
    storyline: str
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None
    _unsaved: int = 0

    def matches(self, seed: int | None, storyline: str | None) -> bool:
        return seed == self.seed and (storyline is None or storyline == self.storyline)

    @classmethod
    def load(cls, path: Path) -> Cassette:
        raw = json.loads(path.read_text())
        if raw.get("format") != FORMAT:
            raise ValueError(f"{path} is not a {FORMAT} file")
        return cls(seed=raw["seed"], storyline=raw["storyline"], entries=raw["entries"], path=path)

    def save(self, path: Path | None = None) -> None:
        path = path or self.path
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"format": FORMAT, "seed": self.seed, "storyline": self.storyline,
                "entries": dict(sorted(self.entries.items()))}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1))
        tmp.replace(path)
        self._unsaved = 0

    def put(self, key: str, text: str, latency_ms: float, model: str | None) -> None:
        self.entries[key] = {"text": text, "latency_ms": round(latency_ms), "model": model}
        self._unsaved += 1
        if self.path and self._unsaved >= 20:
            self.save()


class CassetteChatClient(BaseChatClient):
    """Answers from a cassette; misses go to ``inner`` when ``live_misses`` is set.

    ``record`` stores every live answer. ``delay_factor`` replays a share of the
    recorded latency so the demo still shows agents upgrading data-only cards.
    """

    OTEL_PROVIDER_NAME = "matchlens-cassette"

    def __init__(self, inner: BaseChatClient, cassette: Cassette, *, live_misses: bool,
                 record: bool = False, delay_factor: float = 0.0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.inner = inner
        self.cassette = cassette
        self.live_misses = live_misses
        self.record = record
        self.delay_factor = delay_factor
        self.enabled = True  # off while a different match is playing
        self.hits = 0
        self.misses = 0

    async def _respond(self, messages: list[Message], options: Any, kwargs: dict[str, Any]) -> ChatResponse:
        if not self.enabled:
            return await self.inner.get_response(messages, options=options, client_kwargs=kwargs)
        try:
            payload = json.loads(messages[-1].text)
        except (ValueError, TypeError):
            payload = {}
        key = request_key(payload) if isinstance(payload, dict) else None
        entry = self.cassette.entries.get(key) if key else None
        if entry is None and key and not self.live_misses:
            # Offline: an unfocused recording beats no agent text at all.
            entry = self.cassette.entries.get(request_key(payload, focus=False) or "")
        if entry is not None:
            self.hits += 1
            delay = min(entry["latency_ms"] / 1000 * self.delay_factor, MAX_REPLAY_DELAY_S)
            if delay > 0:
                await asyncio.sleep(delay)
            return ChatResponse(messages=Message("assistant", [entry["text"]]),
                                model=f"{entry.get('model') or 'model'} (recorded)")
        self.misses += 1
        if not self.live_misses:
            raise CassetteMiss(key or "unkeyed request")
        started = time.perf_counter()
        response = await self.inner.get_response(messages, options=options, client_kwargs=kwargs)
        if self.record and key and response.text:
            self.cassette.put(key, response.text, (time.perf_counter() - started) * 1000, response.model)
        return response

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        if stream:
            raise NotImplementedError("cassette client does not stream")
        return self._respond(list(messages), options, kwargs)


def cassette_path() -> Path | None:
    """MATCHLENS_CASSETTE: path to a recorded demo match, or unset/"off" to disable."""
    value = os.getenv("MATCHLENS_CASSETTE", "").strip()
    if not value or value.lower() == "off":
        return None
    return Path(value).expanduser()


def delay_factor() -> float:
    return float(os.getenv("MATCHLENS_CASSETTE_DELAY", "0.5"))
