"""Read access for the next10 API: one interface, a Cosmos implementation and an in-memory one for tests."""

from __future__ import annotations

from typing import Any, Protocol

from matchlens.store.next10 import Next10Store

MAX_FRAMES = 3000
MAX_DOCS = 1000


def clean(doc: dict[str, Any]) -> dict[str, Any]:
    """Without Cosmos system fields (``_rid``, ``_etag``, ...)."""
    return {k: v for k, v in doc.items() if not k.startswith("_")}


class Repo(Protocol):
    async def matches(self, limit: int = 200) -> list[dict[str, Any]]: ...
    async def match(self, match_id: str) -> dict[str, Any] | None: ...
    async def score(self, match_id: str) -> dict[str, Any] | None: ...
    async def scores(self) -> list[dict[str, Any]]: ...
    async def predictions(self, match_id: str, kind: str | None = None, team: str | None = None,
                          after: str | None = None, from_s: float | None = None, to_s: float | None = None,
                          last: bool = False) -> list[dict[str, Any]]: ...
    async def frames(self, match_id: str, from_s: float | None = None, to_s: float | None = None,
                     limit: int = MAX_FRAMES, last: bool = False) -> list[dict[str, Any]]: ...
    async def latest_frame_s(self, match_id: str) -> float | None: ...
    async def moments(self, match_id: str, from_s: float | None = None,
                      to_s: float | None = None) -> list[dict[str, Any]]: ...
    async def jobs(self, match_id: str) -> list[dict[str, Any]]: ...


def clock_s(doc: dict[str, Any]) -> float:
    """A prediction's place on the match clock: ``t_s``, or its minute in seconds."""
    t = doc.get("t_s")
    return float(t) if t is not None else float(doc.get("minute") or 0) * 60


def _order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda d: (clock_s(d), d.get("t_issued") or ""))


def _team_ok(doc: dict[str, Any], team: str | None) -> bool:
    return team is None or team in (doc.get("team_side"), doc.get("team"))


class CosmosRepo:
    def __init__(self, store: Next10Store) -> None:
        self.store = store

    async def _q(self, container: str, sql: str, params: dict[str, Any] | None = None,
                 partition: Any = None) -> list[dict[str, Any]]:
        parameters = [{"name": f"@{k}", "value": v} for k, v in (params or {}).items()]
        return [clean(d) for d in await self.store.query(container, sql, parameters, partition=partition)]

    async def matches(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), MAX_DOCS))
        return await self._q("matches", f"SELECT TOP {limit} * FROM c ORDER BY c.kickoff_utc DESC")

    async def match(self, match_id: str) -> dict[str, Any] | None:
        doc = await self.store.read("matches", match_id, match_id)
        return clean(doc) if doc else None

    async def score(self, match_id: str) -> dict[str, Any] | None:
        doc = await self.store.read("predictions", match_id, f"score:{match_id}")
        return clean(doc) if doc else None

    async def scores(self) -> list[dict[str, Any]]:
        return await self._q("predictions", "SELECT * FROM c WHERE c.kind = 'score'")

    async def predictions(self, match_id: str, kind: str | None = None, team: str | None = None,
                          after: str | None = None, from_s: float | None = None, to_s: float | None = None,
                          last: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM c WHERE c.match_id = @m AND c.kind != 'score'"
        params: dict[str, Any] = {"m": match_id}
        if kind:
            sql += " AND c.kind = @k"
            params["k"] = kind
        if team:
            sql += " AND (c.team_side = @t OR c.team = @t)"
            params["t"] = team
        if after:
            sql += " AND c.t_issued > @a"
            params["a"] = after
        for name, op, value in (("f", ">=", from_s), ("u", "<=", to_s)):
            if value is not None:  # the match clock: t_s when the document has one, else minute * 60
                sql += (f" AND ((IS_DEFINED(c.t_s) AND c.t_s {op} @{name}) OR "
                        f"(NOT IS_DEFINED(c.t_s) AND c.minute * 60 {op} @{name}))")
                params[name] = value
        if last and kind == "next10":  # one document a second on a stored match: let the database pick the newest
            sql = sql.replace("SELECT *", "SELECT TOP 1 *") + " ORDER BY c.t_s DESC"
            return await self._q("predictions", sql, params, partition=match_id)
        rows = _order(await self._q("predictions", sql, params, partition=match_id))
        return rows[-1:] if last else rows

    async def frames(self, match_id: str, from_s: float | None = None, to_s: float | None = None,
                     limit: int = MAX_FRAMES, last: bool = False) -> list[dict[str, Any]]:
        sql = f"SELECT TOP {1 if last else max(1, min(int(limit), MAX_FRAMES))} * FROM c WHERE c.match_id = @m"
        params: dict[str, Any] = {"m": match_id}
        if from_s is not None:
            sql += " AND c.t_s >= @a"
            params["a"] = from_s
        if to_s is not None:
            sql += " AND c.t_s <= @b"
            params["b"] = to_s
        return await self._q("frames", sql + (" ORDER BY c.t_s DESC" if last else " ORDER BY c.t_s"), params,
                             partition=match_id)

    async def latest_frame_s(self, match_id: str) -> float | None:
        rows = await self.store.query("frames", "SELECT VALUE MAX(c.t_s) FROM c WHERE c.match_id = @m",
                                      [{"name": "@m", "value": match_id}], partition=match_id)
        return float(rows[0]) if rows and rows[0] is not None else None

    async def moments(self, match_id: str, from_s: float | None = None,
                      to_s: float | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM c WHERE c.match_id = @m"
        params: dict[str, Any] = {"m": match_id}
        if from_s is not None:
            sql += " AND c.t_s >= @a"
            params["a"] = from_s
        if to_s is not None:
            sql += " AND c.t_s <= @b"
            params["b"] = to_s
        rows = await self._q("moments", sql, params, partition=match_id)
        for r in rows:
            r.pop("embedding", None)  # 64 floats the UI never plots
        return sorted(rows, key=lambda d: d.get("t_s") or 0)

    async def jobs(self, match_id: str) -> list[dict[str, Any]]:
        return await self._q("video_jobs", "SELECT * FROM c WHERE c.match_id = @m", {"m": match_id})


class MemoryRepo:
    """Documents held in dicts, same answers as :class:`CosmosRepo`. For tests and offline development."""

    def __init__(self) -> None:
        self.data: dict[str, list[dict[str, Any]]] = {n: [] for n in
                                                        ("matches", "predictions", "frames", "moments", "video_jobs")}

    def put(self, container: str, *docs: dict[str, Any]) -> None:
        rows = self.data[container]
        for doc in docs:
            rows[:] = [r for r in rows if r["id"] != doc["id"]]
            rows.append(dict(doc))

    async def matches(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = sorted(self.data["matches"], key=lambda d: d.get("kickoff_utc") or "", reverse=True)
        return rows[:max(1, min(int(limit), MAX_DOCS))]

    async def match(self, match_id: str) -> dict[str, Any] | None:
        return next((d for d in self.data["matches"] if d["id"] == match_id), None)

    async def score(self, match_id: str) -> dict[str, Any] | None:
        return next((d for d in self.data["predictions"] if d["id"] == f"score:{match_id}"), None)

    async def scores(self) -> list[dict[str, Any]]:
        return [d for d in self.data["predictions"] if d.get("kind") == "score"]

    async def predictions(self, match_id: str, kind: str | None = None, team: str | None = None,
                          after: str | None = None, from_s: float | None = None, to_s: float | None = None,
                          last: bool = False) -> list[dict[str, Any]]:
        rows = _order([d for d in self.data["predictions"]
                       if d["match_id"] == match_id and d.get("kind") != "score" and (kind is None or d.get("kind") == kind)
                       and _team_ok(d, team) and (after is None or (d.get("t_issued") or "") > after)
                       and (from_s is None or clock_s(d) >= from_s) and (to_s is None or clock_s(d) <= to_s)])
        return rows[-1:] if last else rows

    async def frames(self, match_id: str, from_s: float | None = None, to_s: float | None = None,
                     limit: int = MAX_FRAMES, last: bool = False) -> list[dict[str, Any]]:
        rows = sorted((d for d in self.data["frames"] if d["match_id"] == match_id
                       and (from_s is None or d["t_s"] >= from_s) and (to_s is None or d["t_s"] <= to_s)),
                      key=lambda d: d["t_s"])
        return rows[-1:] if last else rows[:max(1, min(int(limit), MAX_FRAMES))]

    async def latest_frame_s(self, match_id: str) -> float | None:
        times = [d["t_s"] for d in self.data["frames"] if d["match_id"] == match_id]
        return max(times) if times else None

    async def moments(self, match_id: str, from_s: float | None = None,
                      to_s: float | None = None) -> list[dict[str, Any]]:
        rows = [{k: v for k, v in d.items() if k != "embedding"} for d in self.data["moments"]
                if d["match_id"] == match_id and (from_s is None or d.get("t_s", 0) >= from_s)
                and (to_s is None or d.get("t_s", 0) <= to_s)]
        return sorted(rows, key=lambda d: d.get("t_s") or 0)

    async def jobs(self, match_id: str) -> list[dict[str, Any]]:
        return [d for d in self.data["video_jobs"] if d.get("match_id") == match_id]
