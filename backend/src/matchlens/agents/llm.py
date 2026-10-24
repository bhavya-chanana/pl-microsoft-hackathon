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
    MATCHLENS_CASSETTE           recorded demo match to replay agent answers from (see cassette.py)
    MATCHLENS_CASSETTE_DELAY     share of the recorded latency to replay (default 0.5)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message
from dotenv import load_dotenv

from matchlens.agents import templates
from matchlens.agents.cassette import Cassette, CassetteChatClient, cassette_path, delay_factor

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


@dataclass
class ModelClients:
    fast: BaseChatClient
    strong: BaseChatClient
    settings: LlmSettings
    cassette: Cassette | None = None

    @property
    def label(self) -> str:
        s = self.settings
        if not s.online:
            return "recorded-demo" if self.cassette else "offline-templates"
        live = f"azure:{s.fast_model}({s.fast_reasoning})/{s.strong_model}({s.strong_reasoning})"
        return f"recorded-demo+{live}" if self.cassette else live

    def use_cassette(self, seed: int | None, storyline: str | None) -> None:
        """Replay recorded answers only for the match they were recorded on."""
        if self.cassette is None:
            return
        on = self.cassette.matches(seed, storyline)
        for client in {id(c): c for c in (self.fast, self.strong)}.values():
            if isinstance(client, CassetteChatClient):
                client.enabled = on
        log.info("recorded demo %s for seed %s", "on" if on else "off", seed)

    def options(self, strong: bool = False) -> dict[str, Any]:
        """Default run options per tier (reasoning effort only applies online)."""
        if not self.settings.online:
            return {}
        effort = self.settings.strong_reasoning if strong else self.settings.fast_reasoning
        return {"reasoning": {"effort": effort}}


def build_clients(settings: LlmSettings | None = None) -> ModelClients:
    clients = _live_clients(settings or LlmSettings.from_env())
    path = cassette_path()
    if path is None:
        return clients
    if not path.exists():
        log.warning("MATCHLENS_CASSETTE=%s not found; running without the recorded demo", path)
        return clients
    cassette = Cassette.load(path)
    online = clients.settings.online

    def wrap(client: BaseChatClient) -> BaseChatClient:
        return CassetteChatClient(client, cassette, live_misses=online, delay_factor=delay_factor())

    fast = wrap(clients.fast)
    strong = fast if clients.strong is clients.fast else wrap(clients.strong)
    log.info("recorded demo: %d answers from %s (misses go %s)", len(cassette.entries), path,
             "to the live model" if online else "to data-only fallbacks")
    return ModelClients(fast=fast, strong=strong, settings=clients.settings, cassette=cassette)


def _live_clients(settings: LlmSettings) -> ModelClients:
    if not settings.online:
        log.info("LLM mode: offline (data-only templates)")
        offline = OfflineChatClient()
        return ModelClients(fast=offline, strong=offline, settings=settings)

    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    if settings.kind == "openai":
        from agent_framework.openai import OpenAIChatClient

        # Agent Framework also reads AZURE_OPENAI_ENDPOINT itself, so pass the same value as
        # azure_endpoint (resource root) rather than a conflicting base_url.
        root = settings.endpoint.split("/openai/")[0].rstrip("/") + "/"

        def make(model: str) -> BaseChatClient:
            return OpenAIChatClient(model=model, azure_endpoint=root, credential=credential)
    else:
        from agent_framework.foundry import FoundryChatClient

        def make(model: str) -> BaseChatClient:
            return FoundryChatClient(project_endpoint=settings.endpoint, model=model, credential=credential)

    fast = make(settings.fast_model)
    strong = fast if settings.strong_model == settings.fast_model else make(settings.strong_model)
    log.info("LLM mode: online (%s) %s/%s", settings.kind, settings.fast_model, settings.strong_model)
    return ModelClients(fast=fast, strong=strong, settings=settings)
