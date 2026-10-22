"""Model clients (BRD §8.1, NFR cost and resilience).

* Online: Azure OpenAI models in Microsoft Foundry through Agent Framework
  (Responses API), authenticated with Entra ID (``DefaultAzureCredential``:
  Azure CLI locally, managed identity in Azure), so no API keys are stored.
* Offline: a deterministic client that answers from the data-only templates.
  It keeps the full multi-agent workflow runnable without Azure (tests, local
  development, credit exhaustion) and makes the fallback path explicit.

Configuration (environment or backend/.env):
    AZURE_OPENAI_ENDPOINT        https://<resource>.openai.azure.com/openai/v1/
    FOUNDRY_PROJECT_ENDPOINT     alternative: https://<resource>.services.ai.azure.com/api/projects/<project>
    MATCHLENS_MODEL_FAST         deployment for frequent calls (default gpt-5-mini)
    MATCHLENS_MODEL_STRONG       deployment for recaps (default gpt-5-mini)
    MATCHLENS_REASONING_FAST     reasoning effort for live agents (default low)
    MATCHLENS_REASONING_STRONG   reasoning effort for recaps (default high)
    MATCHLENS_LLM                "auto" (default: online when an endpoint is set), "online" or "offline"
    MATCHLENS_MAX_LLM_CALLS      hard cap on model calls per server process (default 200); beyond it
                                 every agent falls back to data-only text
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message
from dotenv import load_dotenv

from matchlens.agents import templates

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LlmSettings:
    endpoint: str | None
    fast_model: str
    strong_model: str
    mode: str  # online | offline
    kind: str = "openai"  # openai (Azure OpenAI v1 endpoint) | foundry (project endpoint)
    fast_reasoning: str = "low"
    strong_reasoning: str = "high"

    @property
    def online(self) -> bool:
        return self.mode == "online"

    @classmethod
    def from_env(cls) -> LlmSettings:
        load_dotenv()
        openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or None
        project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT") or None
        endpoint = openai_endpoint or project_endpoint
        requested = os.getenv("MATCHLENS_LLM", "auto").lower()
        mode = "online" if (requested == "online" or (requested == "auto" and endpoint)) else "offline"
        if mode == "online" and not endpoint:
            raise RuntimeError("MATCHLENS_LLM=online requires AZURE_OPENAI_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT")
        return cls(
            endpoint=endpoint,
            fast_model=os.getenv("MATCHLENS_MODEL_FAST", "gpt-5-mini"),
            strong_model=os.getenv("MATCHLENS_MODEL_STRONG", "gpt-5-mini"),
            mode=mode,
            kind="openai" if openai_endpoint else "foundry",
            fast_reasoning=os.getenv("MATCHLENS_REASONING_FAST", "low"),
            strong_reasoning=os.getenv("MATCHLENS_REASONING_STRONG", "high"),
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


class CallBudget:
    """Process-wide spend guard: counts model calls and tokens, refuses calls past the cap."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def acquire(self) -> bool:
        if self.calls >= self.max_calls:
            return False
        self.calls += 1
        return True

    def record(self, usage: Any) -> None:
        if isinstance(usage, dict):
            self.input_tokens += int(usage.get("input_token_count") or 0)
            self.output_tokens += int(usage.get("output_token_count") or 0)

    def as_dict(self) -> dict[str, int]:
        return {"calls": self.calls, "max_calls": self.max_calls,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class ModelClients:
    fast: BaseChatClient
    strong: BaseChatClient
    settings: LlmSettings
    budget: CallBudget = field(default_factory=lambda: CallBudget(10**9))

    @property
    def label(self) -> str:
        s = self.settings
        if not s.online:
            return "offline-templates"
        return f"azure:{s.fast_model}({s.fast_reasoning})/{s.strong_model}({s.strong_reasoning})"

    def options(self, strong: bool = False) -> dict[str, Any]:
        """Default run options per tier (reasoning effort only applies online)."""
        if not self.settings.online:
            return {}
        effort = self.settings.strong_reasoning if strong else self.settings.fast_reasoning
        return {"reasoning": {"effort": effort}}


def build_clients(settings: LlmSettings | None = None) -> ModelClients:
    settings = settings or LlmSettings.from_env()
    if not settings.online:
        log.info("LLM mode: offline (data-only templates)")
        offline = OfflineChatClient()
        return ModelClients(fast=offline, strong=offline, settings=settings)

    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    if settings.kind == "openai":
        from agent_framework.openai import OpenAIChatClient

        def make(model: str) -> BaseChatClient:
            return OpenAIChatClient(model=model, base_url=settings.endpoint, credential=credential)
    else:
        from agent_framework.foundry import FoundryChatClient

        def make(model: str) -> BaseChatClient:
            return FoundryChatClient(project_endpoint=settings.endpoint, model=model, credential=credential)

    fast = make(settings.fast_model)
    strong = fast if settings.strong_model == settings.fast_model else make(settings.strong_model)
    log.info("LLM mode: online (%s) %s/%s", settings.kind, settings.fast_model, settings.strong_model)
    budget = CallBudget(int(os.getenv("MATCHLENS_MAX_LLM_CALLS", "200")))
    return ModelClients(fast=fast, strong=strong, settings=settings, budget=budget)
