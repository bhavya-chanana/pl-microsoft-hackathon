"""Model clients (BRD §8.1, NFR cost and resilience).

* Online: Microsoft Foundry project models through Agent Framework's
  ``FoundryChatClient``, authenticated with Entra ID (``DefaultAzureCredential``),
  so no API keys are stored anywhere.
* Offline: a deterministic client that answers from the data-only templates.
  It keeps the full multi-agent workflow runnable without Azure (tests, local
  development, credit exhaustion) and makes the fallback path explicit.

Configuration (environment):
    FOUNDRY_PROJECT_ENDPOINT   https://<resource>.services.ai.azure.com/api/projects/<project>
    MATCHLENS_MODEL_FAST       deployment for frequent calls (default matchlens-fast)
    MATCHLENS_MODEL_STRONG     deployment for recaps and storylines (default matchlens-strong)
    MATCHLENS_LLM              "auto" (default: online when an endpoint is set), "online" or "offline"
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message

from matchlens.agents import templates

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LlmSettings:
    endpoint: str | None
    fast_model: str
    strong_model: str
    mode: str  # online | offline

    @property
    def online(self) -> bool:
        return self.mode == "online"

    @classmethod
    def from_env(cls) -> LlmSettings:
        endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT") or None
        requested = os.getenv("MATCHLENS_LLM", "auto").lower()
        mode = "online" if (requested == "online" or (requested == "auto" and endpoint)) else "offline"
        if mode == "online" and not endpoint:
            raise RuntimeError("MATCHLENS_LLM=online requires FOUNDRY_PROJECT_ENDPOINT")
        return cls(
            endpoint=endpoint,
            fast_model=os.getenv("MATCHLENS_MODEL_FAST", "matchlens-fast"),
            strong_model=os.getenv("MATCHLENS_MODEL_STRONG", "matchlens-strong"),
            mode=mode,
        )


class OfflineChatClient(BaseChatClient):
    """Answers agent requests from deterministic templates.

    Agents send a JSON payload with a ``task`` key; this client routes it to
    the matching template and returns JSON for the requested response format,
    exactly as a model would.
    """

    OTEL_PROVIDER_NAME = "matchlens-offline"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    async def _respond(self, messages: list[Message]) -> ChatResponse:
        payload = json.loads(messages[-1].text)
        task = payload["task"]
        self.calls.append(task)
        draft = templates.TASKS[task](payload)
        return ChatResponse(messages=Message("assistant", [draft.model_dump_json()]), model="offline-templates")

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        if stream:
            raise NotImplementedError("offline client does not stream")
        return self._respond(list(messages))


@dataclass
class ModelClients:
    fast: BaseChatClient
    strong: BaseChatClient
    settings: LlmSettings

    @property
    def label(self) -> str:
        s = self.settings
        return f"foundry:{s.fast_model}/{s.strong_model}" if s.online else "offline-templates"


def build_clients(settings: LlmSettings | None = None) -> ModelClients:
    settings = settings or LlmSettings.from_env()
    if not settings.online:
        log.info("LLM mode: offline (data-only templates)")
        offline = OfflineChatClient()
        return ModelClients(fast=offline, strong=offline, settings=settings)

    from azure.identity.aio import DefaultAzureCredential
    from agent_framework.foundry import FoundryChatClient

    credential = DefaultAzureCredential()
    fast = FoundryChatClient(project_endpoint=settings.endpoint, model=settings.fast_model, credential=credential)
    strong = FoundryChatClient(project_endpoint=settings.endpoint, model=settings.strong_model, credential=credential)
    log.info("LLM mode: online via Foundry (%s, %s)", settings.fast_model, settings.strong_model)
    return ModelClients(fast=fast, strong=strong, settings=settings)
