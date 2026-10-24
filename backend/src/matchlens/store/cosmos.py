"""Azure Cosmos DB for NoSQL backend (serverless account in Azure, vNext emulator locally).

In Azure the account has local (key) auth disabled: the backend's managed
identity holds the built-in Data Contributor role, which can read and write
items but not create containers, so infra/cosmos.bicep creates them. With
an account key (the emulator) the database and containers are created here.
"""

from __future__ import annotations

import logging
from typing import Any

from azure.cosmos import PartitionKey
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from matchlens.store.base import CONTAINERS, PARTITION_KEY, StoreSettings, check_field

log = logging.getLogger(__name__)


class CosmosStore:
    def __init__(self, settings: StoreSettings) -> None:
        assert settings.endpoint
        self.settings = settings
        self.label = f"cosmos:{settings.database}" + ("@emulator" if settings.key else "")
        self._client: CosmosClient | None = None
        self._credential: Any = None
        self._containers: dict[str, ContainerProxy] = {}

    async def open(self) -> None:
        s = self.settings
        if s.key:
            credential: Any = s.key
        else:
            from azure.identity.aio import DefaultAzureCredential

            credential = self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._client = CosmosClient(s.endpoint, credential)
        if s.key:
            db = await self._client.create_database_if_not_exists(s.database)
            for spec in CONTAINERS.values():
                self._containers[spec.name] = await db.create_container_if_not_exists(
                    id=spec.name, partition_key=PartitionKey(path=PARTITION_KEY),
                    indexing_policy=spec.indexing_policy())
        else:
            db = self._client.get_database_client(s.database)
            for spec in CONTAINERS.values():
                self._containers[spec.name] = db.get_container_client(spec.name)
            await self._containers["runs"].read()  # fail fast on a wrong endpoint, role or name

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
        if self._credential is not None:
            await self._credential.close()

    async def upsert_many(self, container: str, run_id: str, docs: list[dict[str, Any]]) -> None:
        proxy = self._containers[container]
        if len(docs) == 1:
            await proxy.upsert_item(docs[0])
            return
        # One transactional batch per partition (max 100 operations; the writer chunks for us).
        await proxy.execute_item_batch([("upsert", (doc,)) for doc in docs], partition_key=run_id)

    async def read(self, container: str, run_id: str, doc_id: str) -> dict[str, Any] | None:
        try:
            return await self._containers[container].read_item(doc_id, partition_key=run_id)
        except CosmosResourceNotFoundError:
            return None

    async def query(self, container: str, run_id: str | None, *, where: dict[str, Any] | None = None,
                    order_by: str | None = None, descending: bool = False,
                    limit: int | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        for i, (name, value) in enumerate((where or {}).items()):
            clauses.append(f"c.{check_field(name)} = @p{i}")
            params.append({"name": f"@p{i}", "value": value})
        sql = "SELECT " + (f"TOP {int(limit)} " if limit else "") + "* FROM c"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if order_by:
            sql += f" ORDER BY c.{check_field(order_by)}" + (" DESC" if descending else "")
        kwargs: dict[str, Any] = {"parameters": params}
        if run_id is not None:
            kwargs["partition_key"] = run_id
        return [item async for item in self._containers[container].query_items(sql, **kwargs)]
