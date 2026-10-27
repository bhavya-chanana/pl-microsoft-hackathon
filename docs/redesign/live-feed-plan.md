# Live feed plan: playing or overlaying a real match (e.g. Liverpool v Brighton)

Status: proposal, 28 Oct 2026. Based on the code in `ui-redesign-codex`. `vision/broadcast-calibration` is already
merged here (its tip `7432a15` is the merge base), so all vision code below is in this tree. Line numbers are for
this branch.

## The short answer

1. **The pipeline is already source-agnostic.** Everything downstream of a `FeedAdapter` works on our own Event and
   TrackingFrame schema: the stats engine, signals, agents, overlays and the store. A real match means writing one
   adapter (`ingest/adapters.py:23-28`). What is missing is a way to *start* a live source and *push* data into it
   from the API.
2. **Liverpool v Brighton today is video only.** The broadcast pipeline calibrates the camera (77% of main-camera
   frames) and draws the pitch lines on the clip. It does not yet produce events or tracking from broadcast footage.
   We also cannot legally ingest a Premier League TV broadcast. For a real PL match, the live source has to be a
   licensed event-data feed. Video stays a local research path, using footage we have the rights to.
3. **Endpoints we need:** `POST /api/feeds/live` (start a provider feed), `POST /api/ingest/{run_id}/items` plus a
   WebSocket version (push batches from a provider bridge or a video worker), `POST /api/videos` (an async video job
   that ends as a playable feed), and `POST /api/runs/{run_id}/enrich` (generate missing LLM output in batches).
4. **Async and batched today:** ingest, fan-out, agent work (4 at a time, skipped after 5 match-minutes of lag) and DB
   writes (write-behind batches of up to 100) are all async. Agent calls are *not* batched; there is one workflow per
   signal. Video processing is an offline CLI.
5. **On the fly, today:** catch-up, recap, coach brief and pundit answers are generated when they are not in the DB,
   then stored. Per-moment insights are *not*. A reopened match keeps whatever was stored, so the seeded season
   matches stay data-only for good. The fix: a batch "enrich" job, plus a lazy upgrade when someone opens an insight.

---

## 1. What exists today, per source

| Source | What exists | What is missing |
|---|---|---|
| **Synthetic match** (hero, presets, storylines) | `SyntheticReplayAdapter` + `ReplayControl`/`paced` for speed, pause and start minute (`adapters.py:39-121`). Started by `POST /api/replay` (`app.py:327`) and `POST /api/scenarios/kickoff` (`app.py:502`). Resumed after a backend restart (`manager.py:106-139`). | Nothing for this question. |
| **Recorded feed file** (`data/local/feeds/*.json`) | `GET /api/feeds` and `POST /api/replay/feed` (`app.py:333-347`) load a `SyntheticMatch` JSON and replay it paced through `manager.start_feed` (`manager.py:91-104`). `JsonLinesAdapter` (`adapters.py:139-168`) reads a JSON-lines feed, which is the shape a provider bridge would write. | JSON-lines is not wired to any endpoint. Feed runs are not resumed after a restart (`manager.py:92-93`). The files are local only (`app.py:142-144`). |
| **Stored match** (Cosmos `runs`) | `GET /api/runs` and `POST /api/runs/{id}/open` (`app.py:350-363`). An archived run is hydrated from stored events and frames with a `StoredRunAdapter` that streams nothing (`adapters.py:124-136`, `manager.py:141-167`). | You cannot *replay* a stored run from minute X, only look at its final state. Restart always starts the hero (`frontend/src/features/stream/use-replay.ts:41` sends `seed: 7`). This is being fixed separately with `POST /api/replay/restart`. |
| **Live data feed** | Only the *local-only* FotMob adapter on branch `local/fotmob` (`221e6be`: `local/fotmob.py`, `POST /api/feeds/fotmob`). It polls every 30 s or slower, has no tracking, and is gated by `MATCHLENS_FOTMOB=1`. FotMob's terms forbid scraping, so it can never ship. The API mounts such routers through the `matchlens.local` hook (`app.py:121-126`). | A licensed provider adapter. Push ingest. Resuming a live non-synthetic run after a restart. Corrections that *void* an event: the engine can only reassign `player_id`/`related_player_id`/`team_id` (`interpret/engine.py:358-363`), so a VAR-disallowed goal cannot be removed. |
| **Recorded video, research footage** (SoccerTrack v2, CC BY 4.0) | `python -m matchlens.vision 128057` turns a fixed wide-camera clip into `feeds/video-128057.json` (calibration, RT-DETR detection, tracking, team split, events), which then plays through `/api/replay/feed`. Measured: tracking MOTA 0.86, passes 46% recall / 51% precision (README "Video"). | It is an offline CLI, not a job. Events still use the dataset's player identities and ball track. |
| **Recorded video, broadcast** (`data/local/broadcast/LiverpoolBrighton/`) | `python -m matchlens.vision.broadcast --overlay` (`vision/broadcast.py`) finds main-camera shots (`views.py`) and fits a homography per frame (`camera.py`). On the Liverpool–Brighton clip (16,380 frames, 24 cuts) it calibrated 8,122 of 10,561 main-camera frames and wrote `calibration.json` and `overlay.mp4` (pitch lines drawn on the video). | No detection, tracking or events on broadcast footage. Nothing links video time to match time. Nothing reaches the API. And we have no rights to use the footage beyond local research (see §5). |

**How "overlay" works today:** the Producer agent emits overlay JSON timed in *match* seconds (`start_elapsed`,
`duration_s`), and `GET /api/overlays?active_at=` (`app.py:209-215`) returns what is on screen at a given match time.
The broadcast view draws these over our own 2D pitch. Putting them over a real video only needs one mapping from video
time to match time, i.e. the video timestamp of each half's kick-off. It does not need computer vision. CV is only
needed to draw *on the pitch* (telestrator lines placed through the homography).

## 2. The endpoints we need

All of them end in `manager.start_feed(adapter, control, source)` (`manager.py:91`). The engine, agents, overlays,
store and WebSocket stay as they are.

### 2a. Start a live provider feed

```
POST /api/feeds/live
{ "provider": "sportradar" | "statsperform" | "jsonl", "provider_match_id": "sr:match:123", "options": {...} }
→ 201 { "run_id", "match_id", "source": "live:sportradar", "status": "connecting" }
```

The provider adapter connects (push socket or poll), maps lineups to `MatchMeta`, then yields Events and, where the
contract includes them, TrackingFrames. It uses no `ReplayControl` pacing: a live feed is paced by the real world.
Pause and speed only apply to replays.

### 2b. Push ingest, for a provider bridge or the video worker

```
POST /api/ingest/{run_id}/items          (batch, up to 500 items or 1 MB)
{ "items": [ {"kind": "event", ...Event}, {"kind": "frame", ...TrackingFrame} ] }
→ 202 { "accepted": 480, "duplicates": 20, "last_elapsed": 2712.4 }

WS /api/ingest/{run_id}                  (same items, one per message, for low latency)
```

- Backed by a new `QueueAdapter`: a `FeedAdapter` whose `stream()` reads from a bounded `asyncio.Queue`. That is
  about 30 lines next to `JsonLinesAdapter`.
- **Idempotent for free:** the session already skips events it has (`engine.has_event`, `session.py:102-105`) and
  frames at or before the last stored one. Out-of-order events trigger a rebuild in the engine
  (`engine.py:146-148`), so a bridge can safely retry a batch.
- Authenticate with a per-run ingest token returned by 2a, or with managed identity. This is not a fan endpoint.
- Back-pressure: return `429` when the queue is full rather than buffering without limit.

This is the one seam that serves *every* source: a provider bridge, a replayed JSON-lines file, and the video worker
streaming chunks as it finishes them.

### 2c. Recorded video as an async job

```
POST /api/videos   { "clip": "<blob url or local name>", "kickoffs": {"1": 12.4, "2": 3071.0}, "kind": "fixed" | "broadcast" }
→ 202 { "job_id", "status": "queued" }
GET  /api/videos/{job_id} → { "status": "calibrating" | "detecting" | "events" | "done" | "failed", "progress": 0.42, "run_id"? }
```

A worker runs outside the API process (it is CPU/GPU heavy; the image has no video stack, see DEPLOY.md). Locally
that is a subprocess; in Azure, a Container Apps job. It runs the existing CLI stages in chunks of N seconds and either
writes `feeds/<name>.json` (playable through the existing `/api/replay/feed`) or pushes each chunk through 2b to get
"near-live" behaviour. The `kickoffs` map is stored on the run as `video_sync`, so the frontend can play the video and
ask `/api/overlays?active_at=<video→match time>`.

### 2d. Stored matches: replay and enrich

```
POST /api/replay/restart   { "speed"?, "start_minute"? }            (in progress, task c)
POST /api/runs/{run_id}/enrich  { "what": ["insights","recap","coach"], "languages": ["en"] }
→ 202 { "job_id", "pending": 37 }   progress on the run's WebSocket as normal "insight" messages
```

## 3. Async and batching: where it is and where it is not

| Stage | Today | Code |
|---|---|---|
| Feed ingest | Async generator per adapter. The engine ingests each item synchronously on the event loop, with p95 processing time exposed on `/api/health`. | `session.py:107-151`, `app.py:177` |
| Fan-out to clients | Bounded queue per subscriber (2,000). A slow client loses its oldest message; the feed never blocks. | `session.py:188-204` |
| Signal → agents | Each signal gets an eager task. The data-only insight and overlay are published at once. Agent upgrades are gated by `Semaphore(4)` (`MATCHLENS_AGENT_CONCURRENCY`). A job that waited more than 300 match-seconds is skipped (`skipped_lag`). Workflow timeout is 3×25+5 s. Inside a workflow, Tactical‖Narrator and Producer‖Personaliser run in parallel. | `session.py:172-184`, `pipeline.py:56-59, 444-462, 522-555` |
| Agent calls | **Not batched.** One workflow (about 4–6 model calls) per shown signal. The cassette replays answers for the hero match only. | `pipeline.py:338`, `agents/cassette.py` |
| DB writes | **Batched write-behind.** Flushed every 0.25 s, or at 500 pending. Grouped per (container, run). Chunks of ≤100 ops / 1.5 MB as one Cosmos transactional batch. Repeated writes of the same item collapse. 3 retries, then dropped and counted. | `store/writer.py:24-123`, `store/cosmos.py:59-65` |
| DB reads | Read-through LRU cache (20k). Valid only because there is a single replica (`maxReplicas: 1`, `infra/app.bicep:146`). | `store/repository.py:1-66` |
| Video | **Offline batch CLI.** It uses a process pool for the left-out scoring runs, then writes a file. There is no job API and no streaming. | `vision/broadcast.py`, `vision/__main__.py` |
| Season import | Offline batch. Data-only insights are built from templates, with no LLM. | `store/seed.py:113-115` |

What has to change for a real live match:

- **Restart safety for live feeds.** Only seed-matching synthetic runs resume (`manager.py:109`). A live provider run
  needs its own resume: hydrate from the DB (that code exists), then reconnect the provider from the last
  `event_id`/sequence.
- **One match at a time.** `SessionManager.session` is a single global (`manager.py:38`), `/ws` serves it
  (`app.py:559`), and `InsightPipeline` keeps per-match coordinator and scheduler state that `reset()` wipes
  (`pipeline.py:409-412`). A Saturday 15:00 slate means a manager keyed by `run_id`, `/ws?run_id=`, and one pipeline
  state per session. The single replica and in-process cache also cap scale.
- **Batch the LLM layer where latency allows.** Live, keep one workflow per signal: freshness matters more than cost.
  The *enrich* job (§4) should take many signals per call (for example all data-only insights in a 5-minute window),
  which cuts cost about 5–10×.

## 4. Are missing insights generated on the fly?

| Path | Generated on the fly when not in DB? | Code |
|---|---|---|
| Live signal → insight | Yes. Data-only at once, then the agent upgrade in the background. Stored as it is published. | `pipeline.py:444-462`, `session.py:218-221` |
| Backend restart mid-match (synthetic) | Partly. Jobs still at `stage=data` are re-queued only if their signal is ≤300 match-seconds old. Older ones stay data-only. | `pipeline.py:417-442`, `app.py:73-77` |
| Reopen an archived run | **No.** Insights and overlays are loaded from the DB (`manager.py:176-184`). `restore()` returns 0 for archived or finished runs (`pipeline.py:425-426`). Data-only insights stay data-only, and signals the coordinator skipped never get one. The seeded season runs therefore only ever show template text. | |
| `GET /api/insights/{id}` | No. Memory first, then the DB, else 404. | `app.py:199-206` |
| Catch-up, recap, coach brief | **Yes.** Keyed by (match minute, signal count, parameters). On a miss the agent runs, is fact-checked, falls back to a template, and the result is stored in `fan_outputs`. On an archived run the key is stable, so each answer is generated once and then served from the DB. | `services.py:59-62, 74-175` |
| Ask the Pundit | Yes, the same way (cached per question and profile), with a cap of 40/min. | `pundit.py:495-525`, `app.py:445-461` |
| Offline (`MATCHLENS_LLM=offline`) | For the hero match, recorded cassette answers are served, and a miss raises, so the caller publishes the data-only template. For any other match the cassette is switched off and the offline template clients run instead. Fan services label their output `source: "data"`. | `agents/cassette.py:20-23`, `agents/llm.py:171-186`, `services.py:57` |

**What should happen:**

1. **Do not generate implicitly when a run is opened.** It is expensive and slow, and one viewer opening an old match
   should not spend for everyone. Keep the data-only text visible at once.
2. **Lazy upgrade:** when a client opens an insight whose `stage` is `data` on an archived run, enqueue *that*
   signal's workflow on the same semaphore, return the data version now, and publish the upgrade over the WebSocket.
   The ids and `supersedes_revision` already support in-place upgrades.
3. **Batch enrich** (`POST /api/runs/{id}/enrich`): a background job that takes every data-only FULL/LIGHT signal
   (decisions are stored in `decisions`), runs them in windowed batches, and writes the results back. It is skipped
   for runs that are already enriched (an `enriched_at` flag on the run). Use it for the season archive, and after a
   live match where agents lagged (`agent_runs` with `skipped_lag`/`timeout` lists them).
4. **Pre-generate HT/FT recaps** for every mode and language in the background when `HALF_TIME`/`FULL_TIME` arrives
   (`session.py:133-134` already records stats there), so the most-requested outputs are warm.

## 5. Licensing, kept short

- **TV broadcast:** Premier League pictures are licensed exclusively to broadcasters. Recording, processing and showing
  them, even as an overlay, is not permitted for us. The Liverpool–Brighton clips in `data/local/broadcast/` are
  fine only as private, local R&D on camera calibration. They must never be deployed, demoed or used to publish data.
- **Scraping** (FotMob and similar) is against their terms. That is why the adapter is local-only and never pushed.
- **What would plug in:**
  - **Event data** (passes, shots, cards, subs, lineups, with x/y coordinates), pushed within seconds. Typical sources
    are Opta/Stats Perform, Sportradar, Genius Sports (the league's official data partner; check the current
    rights holder and terms) and StatsBomb/Hudl (mostly post-match). This maps 1:1 to our `Event` and covers almost
    everything we show. It is the realistic first integration.
  - **Tracking data** (all 22 players and the ball at 10–25 Hz), from in-stadium optical systems or broadcast
    tracking (e.g. SkillCorner). It is costly, often delayed or restricted for live use, and maps to our
    `TrackingFrame`. Without it, the live 2D pitch, distance and speed badges, and telestrator carries are empty or
    estimated, as with FotMob.
- **The product shape this implies:** a *second screen*. The fan watches on their licensed TV or stream, and Matchlens
  follows the same match clock from the data feed. "Overlay on the video" is only possible on footage we have the
  rights to (SoccerTrack, amateur or club footage) or through a broadcaster partnership.

## 6. Phased plan

| Phase | Scope | Effort |
|---|---|---|
| 0 | **Done (approved 28 Oct, not committed):** `POST /api/replay/restart` restarts the loaded match: synthetic, recorded, or a stored run replayed from its events and frames (`Recipe` in `runtime/manager.py`). | done |
| 1 | `QueueAdapter`, `POST /api/ingest/{run_id}/items` and WS, `POST /api/feeds/live` with a `jsonl` provider. A small bridge script that replays a JSON-lines file in real time from *another process* proves the live path end to end. Tests for idempotency, out-of-order delivery and back-pressure. | 2–3 days |
| 2 | Enrichment: lazy upgrade on open, `POST /api/runs/{id}/enrich` batch job, HT/FT recap pre-generation, `enriched_at` on runs. | 2–3 days |
| 3 | First licensed event-data adapter (needs a contract and sample feeds): lineup and clock mapping, a `void` correction in the engine (`engine.py:358`) for VAR-disallowed goals, and resume or reconnect for live provider runs after a restart. | 1–1.5 weeks per provider |
| 4 | Several matches at once: manager keyed by run, `/ws?run_id=`, pipeline state per session, cache safe beyond one replica (or sticky sessions). | 1–2 weeks |
| 5 | Video as a job (`POST /api/videos`, worker as a Container Apps job, `video_sync` and a `<video>` player with our overlays) on footage we have rights to. Broadcast detection, tracking and events are research: pass recall is 46% on the *easier* fixed-camera footage. | 2–3 weeks, plus research |

Recommended order: 0 → 1 → 2 now (about a week, no external dependencies). Phase 3 only once a data contract is
signed. Phase 5 stays local research.
