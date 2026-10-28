"""Store for the next-10-seconds companion: its own Cosmos database, never the deployed app's.

The deployed app shares the Azure Cosmos DB account and owns the ``matchlens``
database (``store/base.py``, ``infra/cosmos.bicep``). Nothing here reads or
writes it: this module refuses that database name, uses its own endpoint and
database settings, and its Azure containers are declared separately in
``infra/next10-cosmos.bicep``.

Docker first, Azure second. A bundle (a folder of ``<container>.jsonl`` files)
is seeded into the kept local emulator (``npm run db:next10``) and verified
there; the check is recorded by the bundle's hash. Seeding Azure is refused
unless that exact bundle passed its emulator check.

    matches      one per real match: teams, competition, kickoff, venue, sources      /match_id
    events       provider-neutral events (FotMob and video), corrections included     /match_id
    frames       5 Hz tracking frames derived from video, with observed masks         /match_id
    predictions  every prediction as issued (before its outcome), then its score      /match_id
    moments      key moments with the critic's embedding (vector search)              /match_id
    video_jobs   one per processed video: source, chunks done, calibration summary    /job_id
    kg_nodes     knowledge-graph nodes (club, player, match, season, ...)             /type
    kg_edges     knowledge-graph edges, stored with their source node                 /src

Configuration (environment):
    MATCHLENS_NEXT10_ENDPOINT  default http://localhost:8091 (the kept emulator)
    MATCHLENS_NEXT10_DATABASE  default matchlens_next10
    MATCHLENS_NEXT10_KEY       emulator only; a localhost endpoint uses the emulator's published key.
                               Unset on Azure: Entra ID (az login / managed identity)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from matchlens.store.base import EMULATOR_KEY, check_field

log = logging.getLogger(__name__)

APP_DATABASE = "matchlens"  # the deployed app's database: never touched from here
DEFAULT_ENDPOINT = "http://localhost:8091"
DEFAULT_DATABASE = "matchlens_next10"
CHECKS_DIR = Path(__file__).resolve().parents[3] / "data" / "local" / "next10" / "checks"
BATCH = 100  # Cosmos transactional batch limit


@dataclass(frozen=True)
class Next10Container:
    name: str
    partition: str  # a top-level field name; the partition key path is /<partition>
    unindexed: tuple[str, ...] = ()
    vector: tuple[str, int] | None = None  # (field, dimensions), cosine, DiskANN index

    def indexing_policy(self) -> dict[str, Any]:
        excluded = [{"path": f"/{p}/*"} for p in self.unindexed] + [{"path": '/"_etag"/?'}]
        policy: dict[str, Any] = {"indexingMode": "consistent", "automatic": True,
                                  "includedPaths": [{"path": "/*"}], "excludedPaths": excluded}
        if self.vector:
            policy["excludedPaths"].append({"path": f"/{self.vector[0]}/*"})
            policy["vectorIndexes"] = [{"path": f"/{self.vector[0]}", "type": "diskANN"}]
        return policy

    def vector_policy(self) -> dict[str, Any] | None:
        if not self.vector:
            return None
        path, dims = self.vector
        return {"vectorEmbeddings": [{"path": f"/{path}", "dataType": "float32", "distanceFunction": "cosine",
                                      "dimensions": dims}]}


# Keep in sync with infra/next10-cosmos.bicep; tests/test_next10_store.py checks it.
CONTAINERS: dict[str, Next10Container] = {c.name: c for c in [
    Next10Container("matches", "match_id", ("sources", "lineups")),
    Next10Container("events", "match_id", ("attrs", "start", "end")),
    Next10Container("frames", "match_id", ("entities",)),
    Next10Container("predictions", "match_id", ("features", "probs")),
    Next10Container("moments", "match_id", ("graph",), vector=("embedding", 64)),
    Next10Container("video_jobs", "job_id", ("chunks", "calibration")),
    Next10Container("kg_nodes", "type", ("props",), vector=("text_embedding", 1536)),
    Next10Container("kg_edges", "src", ("props",)),
]}


@dataclass(frozen=True)
class Next10Settings:
    endpoint: str = DEFAULT_ENDPOINT
    database: str = DEFAULT_DATABASE
    key: str | None = None

    @classmethod
    def from_env(cls) -> Next10Settings:
        endpoint = os.getenv("MATCHLENS_NEXT10_ENDPOINT", DEFAULT_ENDPOINT)
        key = os.getenv("MATCHLENS_NEXT10_KEY") or (EMULATOR_KEY if is_emulator(endpoint) else None)
        return cls(endpoint=endpoint, database=os.getenv("MATCHLENS_NEXT10_DATABASE", DEFAULT_DATABASE), key=key)

    def __post_init__(self) -> None:
        if self.database.strip().lower() == APP_DATABASE:
            raise ValueError(f"'{APP_DATABASE}' is the deployed app's database; next10 data never goes there")

    @property
    def emulator(self) -> bool:
        return is_emulator(self.endpoint)


def is_emulator(endpoint: str) -> bool:
    return (urlparse(endpoint).hostname or "") in {"localhost", "127.0.0.1", "::1"}


class Next10Store:
    """Async access to the next10 database. On the emulator it creates the database and containers;
    on Azure the data-plane role cannot, so they must already exist (infra/next10-cosmos.bicep)."""

    def __init__(self, settings: Next10Settings) -> None:
        self.settings = settings
        self._client: Any = None
        self._credential: Any = None
        self._containers: dict[str, Any] = {}

    async def open(self) -> Next10Store:
        from azure.cosmos import PartitionKey
        from azure.cosmos.aio import CosmosClient

        s = self.settings
        if s.key:
            credential: Any = s.key
        else:
            from azure.identity.aio import DefaultAzureCredential

            credential = self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._client = CosmosClient(s.endpoint, credential)
        if s.emulator:
            db = await self._client.create_database_if_not_exists(s.database)
            for c in CONTAINERS.values():
                kwargs: dict[str, Any] = {"indexing_policy": c.indexing_policy()}
                if c.vector_policy():
                    kwargs["vector_embedding_policy"] = c.vector_policy()
                self._containers[c.name] = await db.create_container_if_not_exists(
                    id=c.name, partition_key=PartitionKey(path=f"/{c.partition}"), **kwargs)
        else:
            db = self._client.get_database_client(s.database)
            for c in CONTAINERS.values():
                self._containers[c.name] = db.get_container_client(c.name)
                await self._containers[c.name].read()  # fail fast: the bicep module was not deployed
        return self

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
        if self._credential is not None:
            await self._credential.close()

    async def __aenter__(self) -> Next10Store:
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def upsert(self, container: str, docs: list[dict[str, Any]]) -> int:
        """Upsert documents in transactional batches per partition. Returns how many were written."""
        spec = CONTAINERS[container]
        proxy = self._containers[container]
        by_partition: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for doc in docs:
            by_partition[partition_value(spec, doc)].append(doc)
        for pk, group in by_partition.items():
            for i in range(0, len(group), BATCH):
                chunk = group[i:i + BATCH]
                if len(chunk) == 1:
                    await proxy.upsert_item(chunk[0])
                else:
                    await proxy.execute_item_batch([("upsert", (d,)) for d in chunk], partition_key=pk)
        return len(docs)

    async def read(self, container: str, partition: Any, doc_id: str) -> dict[str, Any] | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return await self._containers[container].read_item(doc_id, partition_key=partition)
        except CosmosResourceNotFoundError:
            return None

    async def query(self, container: str, sql: str, parameters: list[dict[str, Any]] | None = None,
                    partition: Any = None) -> list[Any]:
        kwargs: dict[str, Any] = {"parameters": parameters or []}
        if partition is not None:
            kwargs["partition_key"] = partition
        return [item async for item in self._containers[container].query_items(sql, **kwargs)]

    async def count(self, container: str, partition: Any) -> int:
        spec = CONTAINERS[container]
        rows = await self.query(container, f"SELECT VALUE COUNT(1) FROM c WHERE c.{check_field(spec.partition)} = @p",
                                [{"name": "@p", "value": partition}], partition=partition)
        return int(rows[0]) if rows else 0


def partition_value(spec: Next10Container, doc: dict[str, Any]) -> Any:
    if "id" not in doc:
        raise ValueError(f"{spec.name}: a document has no id")
    if doc.get(spec.partition) in (None, ""):
        raise ValueError(f"{spec.name}/{doc['id']}: missing partition field '{spec.partition}'")
    return doc[spec.partition]


# ------------------------------------------------------------------ bundles and the emulator-first gate


def load_bundle(folder: Path) -> dict[str, list[dict[str, Any]]]:
    """``<container>.jsonl`` files; every document needs an id and its container's partition field."""
    bundle: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(folder.glob("*.jsonl")):
        if path.stem not in CONTAINERS:
            raise ValueError(f"{path.name}: unknown container (expected one of {sorted(CONTAINERS)})")
        docs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for doc in docs:
            partition_value(CONTAINERS[path.stem], doc)
        bundle[path.stem] = docs
    if not bundle:
        raise ValueError(f"{folder}: no <container>.jsonl files")
    return bundle


def bundle_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(folder.glob("*.jsonl")):
        h.update(path.name.encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def expected_counts(bundle: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for name, docs in bundle.items():
        spec = CONTAINERS[name]
        per: dict[str, int] = defaultdict(int)
        for doc in {d["id"] + "\x00" + str(partition_value(spec, d)): d for d in docs}.values():
            per[str(partition_value(spec, doc))] += 1
        out[name] = dict(per)
    return out


async def verify(store: Next10Store, bundle: dict[str, list[dict[str, Any]]], sample: int = 20) -> list[str]:
    """Problems found (empty when the store holds the bundle): every partition has at least the bundle's
    documents, and a random sample reads back equal."""
    problems: list[str] = []
    for name, per in expected_counts(bundle).items():
        for pk, n in per.items():
            got = await store.count(name, pk)
            if got < n:
                problems.append(f"{name}[{pk}]: {got} documents, expected at least {n}")
    rng = random.Random(0)
    for name, docs in bundle.items():
        spec = CONTAINERS[name]
        for doc in rng.sample(docs, min(sample, len(docs))):
            stored = await store.read(name, partition_value(spec, doc), doc["id"])
            if stored is None:
                problems.append(f"{name}/{doc['id']}: missing")
            elif {k: v for k, v in stored.items() if not k.startswith("_")} != doc:
                problems.append(f"{name}/{doc['id']}: stored copy differs")
    return problems


def check_path(digest: str, checks_dir: Path = CHECKS_DIR) -> Path:
    return checks_dir / f"{digest}.json"


def record_check(digest: str, settings: Next10Settings, counts: dict[str, dict[str, int]],
                 checks_dir: Path = CHECKS_DIR) -> Path:
    path = check_path(digest, checks_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bundle": digest, "endpoint": settings.endpoint, "database": settings.database,
                                "counts": counts, "passed_at": datetime.now(UTC).isoformat()}, indent=2))
    return path


def require_emulator_check(digest: str, counts: dict[str, dict[str, int]], checks_dir: Path = CHECKS_DIR) -> None:
    """Azure seeding gate: the same bundle must have passed its emulator check."""
    path = check_path(digest, checks_dir)
    if not path.exists():
        raise PermissionError(f"bundle {digest[:12]} has not been seeded and verified in the emulator; "
                              "run `seed --target emulator` first")
    record = json.loads(path.read_text())
    if record.get("counts") != counts or not is_emulator(record.get("endpoint", "")):
        raise PermissionError(f"the emulator check for {digest[:12]} does not match this bundle; re-run it")


async def seed(folder: Path, settings: Next10Settings, *, checks_dir: Path = CHECKS_DIR,
               confirm_azure: bool = False) -> dict[str, Any]:
    bundle = load_bundle(folder)
    digest, counts = bundle_hash(folder), expected_counts(bundle)
    if not settings.emulator:
        require_emulator_check(digest, counts, checks_dir)
        if not confirm_azure:
            raise PermissionError("seeding Azure needs --yes (the emulator check passed)")
    async with Next10Store(settings) as store:
        written = {name: await store.upsert(name, docs) for name, docs in bundle.items()}
        problems = await verify(store, bundle)
    if problems:
        raise RuntimeError("verification failed: " + "; ".join(problems[:10]))
    if settings.emulator:
        record_check(digest, settings, counts, checks_dir)
    return {"bundle": digest, "target": "emulator" if settings.emulator else "azure",
            "database": settings.database, "written": written}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m matchlens.store.next10")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="seed a bundle: the emulator first, then Azure")
    s.add_argument("--bundle", type=Path, required=True)
    s.add_argument("--target", choices=["emulator", "azure"], default="emulator")
    s.add_argument("--yes", action="store_true", help="confirm writing to Azure")
    sub.add_parser("init", help="create the next10 database and containers in the emulator")
    args = parser.parse_args()
    settings = Next10Settings.from_env()
    if args.cmd == "init":
        if not settings.emulator:
            parser.error("init only creates containers in the emulator; Azure uses infra/next10-cosmos.bicep")

        async def _init() -> None:
            async with Next10Store(settings):
                pass
        asyncio.run(_init())
        print(f"ready: {settings.database} @ {settings.endpoint} ({', '.join(CONTAINERS)})")
        return
    if (args.target == "azure") == settings.emulator:
        parser.error(f"--target {args.target} does not match MATCHLENS_NEXT10_ENDPOINT={settings.endpoint}")
    print(json.dumps(asyncio.run(seed(args.bundle, settings, confirm_azure=args.yes)), indent=2))


if __name__ == "__main__":
    main()
