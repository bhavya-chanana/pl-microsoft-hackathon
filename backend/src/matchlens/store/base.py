"""Durable store for everything a match run produces (Azure Cosmos DB for NoSQL).

Every container is partitioned by ``/run_id``: one replay of a match is one
logical partition, so a run's data is written in transactional batches and
read back with single-partition queries.

    runs         run document, match metadata, pipeline state, stats snapshots
    events       every feed event as received (corrections included), plus pass quality
    frames       1 Hz tracking frames
    signals      key moments detected by the stats engine, by revision
    insights     fact-checked insights, every variant and provenance
    overlays     timed broadcast graphics
    decisions    coordinator routing decisions, in order
    agent_runs   one per agent workflow or fan request: plan, agents, model, latency, errors
    fan_outputs  catch-ups and recaps, keyed by window, profile and match position

The live process keeps a read-through cache in front of the store (see
repository.py); the store is the source of truth that a restarted backend
resumes from.

Configuration (environment or backend/.env):
    MATCHLENS_STORE            "auto" (default: cosmos when an endpoint is set), "cosmos" or "memory"
    MATCHLENS_COSMOS_ENDPOINT  https://<account>.documents.azure.com:443/ or http://localhost:8081 (emulator)
    MATCHLENS_COSMOS_DATABASE  database name (default matchlens)
    MATCHLENS_COSMOS_KEY       account key; emulator only (a localhost endpoint uses the emulator's
                               published key). Unset: Entra ID (az login / managed identity)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from dotenv import load_dotenv

log = logging.getLogger(__name__)

PARTITION_KEY = "/run_id"
# The Cosmos DB emulator's well-known key, published by Microsoft; it only ever opens a local emulator.
EMULATOR_KEY = "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    # Large or free-form fields that are never filtered on stay out of the index (cheaper writes).
    unindexed: tuple[str, ...] = ()

    def indexing_policy(self) -> dict[str, Any]:
        excluded = [{"path": f"/{p}/*"} for p in self.unindexed] + [{"path": '/"_etag"/?'}]
        return {"indexingMode": "consistent", "automatic": True,
                "includedPaths": [{"path": "/*"}], "excludedPaths": excluded}


CONTAINERS: dict[str, ContainerSpec] = {s.name: s for s in [
    ContainerSpec("runs", ("meta", "state", "coordinator", "scheduler", "teams", "players")),
    ContainerSpec("events", ("attrs", "start", "end", "pass_quality")),
    ContainerSpec("frames", ("entities",)),
    ContainerSpec("signals", ("facts", "evidence_event_ids")),
    ContainerSpec("insights", ("variants", "club_angles", "fact_check", "key_numbers", "evidence_event_ids")),
    ContainerSpec("overlays", ("localized", "stats")),
    ContainerSpec("decisions"),
    ContainerSpec("agent_runs", ("errors", "actions")),
    ContainerSpec("fan_outputs", ("result",)),
    ContainerSpec("fan_profiles"),
    ContainerSpec("fan_poll_votes"),
    ContainerSpec("fan_player_ratings"),
]}

# Properties Cosmos DB adds to every item.
SYSTEM_FIELDS = {"_rid", "_self", "_etag", "_attachments", "_ts", "_lsn"}
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def to_doc(doc_id: str, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Store a payload as an item: its fields at the top level, plus id and partition key."""
    if "id" in payload or "run_id" in payload:
        raise ValueError("payload must not define id or run_id")
    return {"id": doc_id, "run_id": run_id, **payload}


def from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """The payload back, without the item id, partition key and system properties."""
    return {k: v for k, v in doc.items() if k not in SYSTEM_FIELDS and k not in ("id", "run_id")}


def check_field(name: str) -> str:
    if not _FIELD.match(name):
        raise ValueError(f"invalid field name {name!r}")
    return name


class Store(Protocol):
    """What the rest of Matchlens needs from a document store."""

    label: str

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def upsert_many(self, container: str, run_id: str, docs: list[dict[str, Any]]) -> None:
        """Upsert items that share one partition, atomically where the backend allows."""

    async def read(self, container: str, run_id: str, doc_id: str) -> dict[str, Any] | None: ...

    async def query(self, container: str, run_id: str | None, *, where: dict[str, Any] | None = None,
                    order_by: str | None = None, descending: bool = False,
                    limit: int | None = None) -> list[dict[str, Any]]:
        """Items matching equality filters, optionally across partitions (``run_id=None``)."""


@dataclass
class MemoryStore:
    """Store for tests and machines without Cosmos DB. Nothing survives a restart."""

    label: str = "memory"
    items: dict[str, dict[tuple[str, str], dict[str, Any]]] = field(default_factory=dict)

    async def open(self) -> None:
        for name in CONTAINERS:
            self.items.setdefault(name, {})

    async def close(self) -> None:
        return None

    async def upsert_many(self, container: str, run_id: str, docs: list[dict[str, Any]]) -> None:
        bucket = self.items.setdefault(container, {})
        for doc in docs:
            assert doc["run_id"] == run_id
            # A JSON round trip, as a real store would do, so non-serialisable data fails here too.
            bucket[(run_id, doc["id"])] = json.loads(json.dumps(doc))

    async def read(self, container: str, run_id: str, doc_id: str) -> dict[str, Any] | None:
        doc = self.items.get(container, {}).get((run_id, doc_id))
        return json.loads(json.dumps(doc)) if doc is not None else None

    async def query(self, container: str, run_id: str | None, *, where: dict[str, Any] | None = None,
                    order_by: str | None = None, descending: bool = False,
                    limit: int | None = None) -> list[dict[str, Any]]:
        rows = [d for (rid, _), d in self.items.get(container, {}).items()
                if (run_id is None or rid == run_id)
                and all(d.get(check_field(k)) == v for k, v in (where or {}).items())]
        if order_by:
            rows.sort(key=lambda d: d.get(check_field(order_by)), reverse=descending)
        return json.loads(json.dumps(rows[:limit] if limit else rows))


@dataclass(frozen=True)
class StoreSettings:
    kind: str  # cosmos | memory
    endpoint: str | None = None
    database: str = "matchlens"
    key: str | None = None

    @classmethod
    def from_env(cls) -> StoreSettings:
        load_dotenv()
        endpoint = os.getenv("MATCHLENS_COSMOS_ENDPOINT") or None
        requested = os.getenv("MATCHLENS_STORE", "auto").lower()
        kind = "cosmos" if requested == "cosmos" or (requested == "auto" and endpoint) else "memory"
        if kind == "cosmos" and not endpoint:
            raise RuntimeError("MATCHLENS_STORE=cosmos requires MATCHLENS_COSMOS_ENDPOINT")
        local = bool(endpoint and re.match(r"^https?://(localhost|127\.0\.0\.1)[:/]", endpoint))
        return cls(kind=kind, endpoint=endpoint, database=os.getenv("MATCHLENS_COSMOS_DATABASE", "matchlens"),
                   key=os.getenv("MATCHLENS_COSMOS_KEY") or (EMULATOR_KEY if local else None))


async def open_store(settings: StoreSettings | None = None) -> Store:
    """Open the configured store. If Cosmos DB is unreachable the backend still runs,
    on the memory store, and says so in /api/health (the live feed never depends on it)."""
    settings = settings or StoreSettings.from_env()
    if settings.kind == "memory":
        log.warning("store: memory (set MATCHLENS_COSMOS_ENDPOINT to persist runs in Cosmos DB)")
        store: Store = MemoryStore()
        await store.open()
        return store
    from matchlens.store.cosmos import CosmosStore

    cosmos = CosmosStore(settings)
    try:
        await cosmos.open()
        log.info("store: %s", cosmos.label)
        return cosmos
    except Exception as exc:  # noqa: BLE001 - degrade, never stop the match
        log.error("store: Cosmos DB unavailable (%s); falling back to memory", exc)
        await cosmos.close()
        fallback = MemoryStore(label=f"memory (cosmos unavailable: {type(exc).__name__})")
        await fallback.open()
        return fallback
