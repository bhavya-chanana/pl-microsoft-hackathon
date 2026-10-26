# Matchlens database

Everything a match produces is stored in **Azure Cosmos DB for NoSQL (serverless)**: the feed, what the stats
engine detected, what the agents decided and wrote, what went on screen, and every fan catch-up and recap. The
database is the source of truth. The backend keeps a read-through cache in front of it, and a restarted backend
(a deploy, a crash or a scale-to-zero cold start) resumes the live match from the database where it stopped.

Code: [`backend/src/matchlens/store/`](../backend/src/matchlens/store/). Infrastructure:
[`infra/cosmos.bicep`](../infra/cosmos.bicep).

## Model

One replay of a match is a **run** (`run_id`, e.g. `ml-demo-001-20261024175300-7b81`). Every container is
partitioned by `/run_id`, so a run is one logical partition: its writes go in transactional batches and its reads
are single-partition queries. Item fields are the published JSON (see [SCHEMA.md](SCHEMA.md)) at the top level,
plus `id` and `run_id`.

| Container | `id` | One item per | Contents |
|---|---|---|---|
| `runs` | run id, `meta`, `pipeline`, `stats:half_time`, `stats:full_time` | run and its companions (`doc_type`) | Run status, speed, position, score and latest state; match metadata; coordinator and overlay-scheduler state; team and player statistics at half-time and full-time. Partition `_active` holds the pointer to the run the backend is playing. |
| `events` | `event_id` | feed event | Every event as received, corrections included, plus `minute_label` and `pass_quality` |
| `frames` | `elapsed` | tracking frame | Positions and speeds of every player and the ball, 1 Hz |
| `signals` | `signal_id@r<revision>` | key-moment revision | What the stats engine detected, with evidence events and facts; corrections add revisions |
| `insights` | `insight_id` | insight | Latest version: stage, every mode and language variant, club angles, evidence, fact-check report, agents |
| `overlays` | `overlay_id` | overlay | Latest timing, slot, priority and text |
| `decisions` | sequence number | coordinator decision | Signal, revision, plan (`full`, `light`, `data_only`, `retract`, `skip`) and reason |
| `agent_runs` | random | agent workflow or fan request | Plan, outcome (`published`, `superseded`, `timeout`, ...), agents used, fact-check status, fallbacks, **model**, latency |
| `fan_outputs` | hash of window, profile and match position | catch-up or recap | The full result, so the same question is answered once per run |
| `fan_profiles` | Google subject ID | saved mode, language, favorite club/player, pinned metric | One profile per signed-in fan |
| `fan_poll_votes` | match ID | fan subject, choice, update time | One changeable vote per fan per match |
| `fan_player_ratings` | match ID | fan subject, player, 1–5 score, update time | One changeable rating per fan/player/match |

Fields that are never filtered on (large text maps, coordinates, entity arrays) are excluded from the index, which
lowers the write cost; everything else is indexed for ad hoc queries in Data Explorer, for example:

```sql
-- Agent workflows of a run whose text was blocked or never generated, slowest first (agent_runs)
SELECT c.minute_label, c.signal_type, c.outcome, c.fact_check, c.model, c.latency_ms
FROM c WHERE c.run_id = @run AND c.fact_check IN ("blocked", "data_only") ORDER BY c.latency_ms DESC
```

## Write path

```text
feed item ─▶ stats engine ─▶ publish to clients (immediately)
                    └──────▶ RunRecorder ─▶ cache (immediately) ─▶ write-behind queue ─▶ Cosmos DB (batched)
```

- The live feed never waits for the database: score, clock and timeline still reach clients within a second
  (BRD §10.1). Writes are queued and flushed every 250 ms by one background task, grouped per container and run
  into transactional batches of up to 100 items.
- Flushes run in order, so a stored event implies every earlier event of the run is stored. Several writes of the
  same item between flushes (an insight upgraded by the agents, the run's position) collapse into one.
- A failed batch is retried three times with backoff. If the database stays unreachable the match keeps playing and
  `/api/health` reports `dropped` and `last_error`; it never stops the feed.

## Read path and cache

Reads go through an in-process cache: a hit returns at once, a miss reads Cosmos DB and caches the result. The live
run's insights and overlays are always cached in full (they are written through and reloaded on resume). The
backend runs one replica, so the cache never goes stale. `/api/health` shows `cache_entries`, `cache_hits` and
`cache_misses`.

## Resuming after a restart

On startup the backend reads the `_active` pointer. If that run is still live (and was played from the default
storyline), it:

1. rebuilds the stats engine from the stored events and frames, in feed order (no event or frame counted twice);
2. reloads the run's insights and overlays into the cache;
3. restores the coordinator's editorial state, the overlay scheduler and the decision log, and re-queues agent work
   that was in flight (insights still at the data-only stage, within the lag limit);
4. continues the feed from the last stored frame, at the stored speed.

A finished run, or none, starts a new run from kick-off. `POST /api/replay` always starts a new run;
`GET /api/runs` lists stored runs.

## Running it

| Where | How |
|---|---|
| Azure | `azd up` creates the serverless account, database, containers and a role assignment for the backend's managed identity (Cosmos DB Built-in Data Contributor). Key auth is disabled. |
| Local, emulator | `npm run db:emulator` (Docker), then `npm run dev:db`. Data Explorer: <http://localhost:1234>. |
| Local, Azure account | Set `MATCHLENS_COSMOS_ENDPOINT` in `backend/.env` and `az login` with the data-contributor role. |
| No database | Leave `MATCHLENS_COSMOS_ENDPOINT` unset: an in-memory store, nothing survives a restart (logged as a warning). |

Tests use the in-memory store; `npm run test:db` also runs a round trip against the emulator.

If Cosmos DB is unreachable at startup, the backend runs on the in-memory store and `/api/health` shows
`memory (cosmos unavailable: ...)`. A new role assignment can take a few minutes to apply: if the first start after
`azd up` falls back, restart the backend revision.

## Cost

Serverless bills per request unit (RU) and per GB stored; there is no charge while idle. In Central India (October
2026 retail prices) that is USD 0.285 per million RU and USD 0.285 per GB-month. One full match writes about 9,000
items, roughly 85,000 RU, so about USD 0.02 per replay, and about 11 MB of storage. Tracking frames are about 60% of
both.
