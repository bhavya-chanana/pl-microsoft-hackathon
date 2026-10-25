# Matchlens

**Live match intelligence, explained as it happens.**

Matchlens is a multi-agent match-insights engine on Azure. It ingests football events as they happen, works out
what matters and why, checks every claim against the data, and publishes each insight twice: as timed broadcast
overlays for a studio, and as a personalised companion feed for every fan, in their mode, club, player and
language.

Built for the Microsoft Premier League Hackathon (October 2026). Every club, player, venue and competition is
fictional.

> **Demo link:** to be added after deployment (see [docs/DEPLOY.md](docs/DEPLOY.md)).

## What it does

| Stage | What happens | Where |
|---|---|---|
| **Ingest** | A simulator calibrated on real match statistics plays a 90-minute match from a scripted storyline. A pluggable feed adapter streams events and 1 Hz tracking in a provider-neutral schema, handling duplicates, late events and corrections. | `simulator/`, `ingest/` |
| **Interpret** | A deterministic stats engine (no language model) keeps score, clock, possession and player/team stats, rates every pass's difficulty, computes speeds, distance and rolling momentum, and detects key moments. | `interpret/` |
| **Explain** | A coordinator picks the moments worth explaining. Tactical and Narrator agents say why they matter, with evidence. A Fact-Checker verifies every claim against the stats engine and rewrites or blocks anything unsupported. | `agents/`, `factcheck/` |
| **Render** | A Producer agent turns each insight into timed overlay JSON with priority and screen slot; a scheduler caps what is on screen. The broadcast view plays them over a live 2D pitch. | `agents/producer.py`, `frontend/src/features/broadcast/` |
| **Personalise** | A Personaliser agent writes every insight for Casual, Analyst and Player-Focus modes in English, Hindi and Spanish, plus each club's angle. Fans also get "What did I miss?" catch-ups and half-time and full-time recaps. | `agents/services.py`, `frontend/src/features/companion/` |
| **Coach** | Short ranked tactical briefs, each with its evidence, trade-off and confidence, fact-checked like every other insight. | `agents/`, `frontend/src/features/coach/` |
| **Engage** | Fan polls open at key moments and resolve against what happened. Every player gets a rating from the data next to the fans' rating, and a match card (overall and six attributes) computed from this match alone. Insights can be read aloud with Azure AI Speech. | `runtime/fans.py`, `interpret/ratings.py`, `speech.py`, `frontend/src/features/fans/` |
| **Archive** | Every match is stored. Reopen one later: a match cut off part-way resumes, a finished one opens as it ended. | `runtime/manager.py`, `frontend/src/features/replay/` |
| **Video (local)** | A vision pipeline turns match footage into the same events and tracking, scored against hand labels. Developer-machine only; see [Video auto-eventing](#video-auto-eventing). | `vision/` |

## Architecture

```mermaid
flowchart TB
    subgraph Ingest
        SIM["Simulator<br/>storyline + calibrated play"] --> AD["Feed adapter<br/>provider-neutral schema"]
    end
    subgraph Interpret["Interpret (no LLM)"]
        AD --> ENG["Stats engine<br/>state, pass quality, speeds, momentum"]
        ENG --> SIG["Key-moment signals<br/>with evidence events"]
    end
    subgraph Agents["Agent Framework workflow"]
        SIG --> CO["Coordinator<br/>select, route, rate-cap"]
        CO --> TAC["Tactical"]
        CO --> NAR["Narrator"]
        TAC --> FC["Fact-Checker<br/>verify, rewrite, block"]
        NAR --> FC
        FC --> PRO["Producer<br/>overlay JSON"]
        FC --> PER["Personaliser<br/>3 modes × 3 languages"]
        PRO --> PUB["Publish"]
        PER --> PUB
    end
    ENG -. "match-state tools" .-> MCP["MCP server /mcp"]
    ENG -. "claims checked against" .-> FC
    CO -. "data-only card, immediately" .-> PUB
    AOAI[("Azure OpenAI<br/>in Microsoft Foundry")] -.-> Agents
    PUB --> WS["FastAPI<br/>REST + WebSocket"]
    ENG --> WS
    WS <--> DB[("Cosmos DB serverless<br/>runs, feed, insights, agent runs")]
    WS --> NEXT["Next.js server<br/>allow-listed proxy + SSE bridge"]
    NEXT --> BC["Broadcast view"]
    NEXT --> MC["Match Centre<br/>fan companion"]
    NEXT --> AR["Agent Room"]
```

- **The live feed never waits for a model.** Score, clock, timeline and a data-only insight card publish as soon as
  the stats engine sees an event. The agent workflow then upgrades the same insight and overlay in place. A failed
  or slow agent falls back to the data-only text, and an agent failure never stops the feed (BRD §8.3).
- **Only facts from the engine reach the screen.** Agents return structured claims alongside their text. The
  Fact-Checker verifies each claim against the stats engine and flags any number no fact accounts for. It allows one
  rewrite; if problems remain, the agent text is blocked and the verified data-only text is shown. Every insight
  carries its evidence event ids, the agents involved and the fact-check report.
- **Corrections flow everywhere.** When the feed corrects an event, the engine replays the match, updates every
  affected statistic and signal, re-explains changed moments and retracts insights that no longer hold.
- **Nothing lives only in memory.** Every event, frame, signal, insight, overlay, coordinator decision, agent run and
  fan catch-up is written to Cosmos DB (serverless) behind a write-behind queue, and read through an in-process cache.
  A restarted backend resumes the live match from the database where it stopped. See
  [docs/DATABASE.md](docs/DATABASE.md).
- **Cost-aware.** The coordinator rate-caps agent runs; frequent tasks use the fast model and recaps the strong one.
  A recorded demo match replays every agent answer, so judges get instant, repeatable output with no model calls.

Azure hosting (two Container Apps, managed identity, scale to zero) is described in
[docs/DEPLOY.md](docs/DEPLOY.md).

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically) and Node.js 20.9 or later.

```bash
npm install
```

```bash
npm run setup
```

```bash
npm run dev:demo
```

Open <http://localhost:3000>. The backend runs on port 8765 and starts the hero match at real time; use the replay
controls to speed it up (up to 60x), pause it, or restart it from any minute.

| Page | Path | For |
|---|---|---|
| Match Centre | `/` | Fans: broadcast feed, personalised insights, timeline, stats, catch-up and recap |
| Broadcast | `/broadcast` | Studio: full-width pitch with timed overlays |
| Coach | `/coach` | Coaches: ranked tactical briefs with evidence |
| Agent Room | `/agents` | The agent pipeline live: routing decisions, fact-check results, latency |

### Run modes

| Command | Agents answer from | Needs Azure |
|---|---|---|
| `npm run dev:demo` | The recorded demo match (`backend/demo/hero-match.json`), data-only templates for anything not recorded | No |
| `npm run dev:offline` | Data-only templates (the full workflow still runs) | No |
| `npm run dev` | Azure OpenAI, live | Yes |
| `npm run dev:db` | Like `dev:demo`, but stores runs in the local Cosmos DB emulator (start it first with `npm run db:emulator`) | No (Docker) |

Without `MATCHLENS_COSMOS_ENDPOINT` the backend uses an in-memory store and logs a warning; set it to persist runs
(see [docs/DATABASE.md](docs/DATABASE.md)).

For live models, copy `backend/.env.example` to `backend/.env`, set `AZURE_OPENAI_ENDPOINT` to your Azure OpenAI
(Foundry) endpoint with a `gpt-5-mini` deployment, and run `az login`. Authentication uses Entra ID, so there are no
API keys. Other settings (`MATCHLENS_SPEED`, `MATCHLENS_SEED`, model names, reasoning effort, timeouts) are listed
in [`backend/.env.example`](backend/.env.example).

Voice narration uses Azure AI Speech when `MATCHLENS_SPEECH_REGION` and `MATCHLENS_SPEECH_RESOURCE_ID` are set (also
Entra ID, no keys); without them the browser's own voices read the insights.

### Tests and checks

```bash
npm test
```

```bash
npm run lint
```

The backend tests cover the simulator and its calibration, the feed adapters, the stats engine (duplicates,
out-of-order events, corrections), the fact-checker (wrong claims rewritten, unfixable ones blocked), the agent
workflow end to end, the recorded demo, and that every published overlay and insight validates against the
documented schema.

## Video auto-eventing

`backend/src/matchlens/vision/` turns a match video into Matchlens events and tracking: it maps the camera's pixels to
pitch metres, detects and tracks players (RT-DETR on tiles of the frame, then a ByteTrack-style tracker), splits them
into teams by kit colour, and reads passes, carries, tackles, interceptions, shots and restarts from who has the
ball. The result plays like any other match from the Past matches card.

It is built and measured on [SoccerTrack v2](https://github.com/AtomScott/SoccerTrack-v2) research footage
(CC BY 4.0, [dataset](https://huggingface.co/datasets/atomscott/soccertrack-v2)). That footage, its labels and everything derived from it stay in `backend/data/local/` (gitignored): none
of it is in this repository, the deployed demo or the demo video, and a match built from it is shown as the fictional
Westmoor v Eastbrook (BRD §6.3).

Measured on a 4-minute clip of match 128057 (from the first-half kick-off), against the dataset's hand labels:

| What | Result |
|---|---|
| Pitch calibration | median 0.9 m error (leave-one-out over 65 surveyed points) |
| Team split | every player on the right team; both keepers found |
| Player detection and tracking (first 20 s) | 96% recall, 90% precision, MOTA 0.86 |
| Ball detection (first 20 s) | within 3 m on 40% of in-play frames |
| Passes | 46% recall, 51% precision (±1 s, same team) |
| Carries | 23% recall, 50% precision |
| Shot, throw-ins, free kick | all found |

The events above still use the dataset's player identities and ball track. Joining the detector's broken tracks
into whole players and driving events from the detected ball are the next steps (BRD §6.2).

```bash
cd backend && uv sync --group vision
```

```bash
cd backend && uv run --group vision python -m matchlens.vision 128057 --detect 20
```

The first run with `--detect` downloads the RT-DETR weights (`PekingU/rtdetr_r50vd`, Apache-2.0, about 172 MB).

## Data and schemas

- **[docs/DATABASE.md](docs/DATABASE.md):** the Cosmos DB containers, write and read paths, cache, resuming after a
  restart, and cost.
- **[docs/SCHEMA.md](docs/SCHEMA.md):** the event, tracking, overlay and insight contracts, the REST API and the
  WebSocket stream. Machine-readable JSON Schemas are in [docs/schema/](docs/schema/).
- **[dataset/](dataset/):** the open synthetic dataset: five full matches (the hero demo match and four unscripted
  ones) with events, 1 Hz tracking, metadata and the simulator's calibration against real match statistics.
  Regenerate it with `npm run export:dataset`.
- **Calibration:** the simulator is tuned against aggregate statistics of 100 real top-flight matches (passing
  lengths and completion, pressure, shot locations, xG, set pieces, fouls and cards). Only the averages are kept;
  no real names or match data are used. Real-data calibration:
  [StatsBomb Open Data](https://github.com/statsbomb/open-data), data provided by StatsBomb.

## Repository layout

```text
backend/                 Python 3.12, FastAPI, Microsoft Agent Framework
  src/matchlens/
    domain/              provider-neutral schema (events, tracking, match metadata)
    simulator/           match simulator, storylines, fictional roster, calibration
    ingest/              feed adapters (synthetic replay, JSON lines)
    interpret/           stats engine, pass quality, momentum, key-moment signals
    agents/              coordinator, agent workflow, prompts, fallbacks, recorded demo
    factcheck/           claim verification
    tools/, mcp_server/  match-state tools and the MCP server
    runtime/             live match session, pub/sub, run start and resume
    store/               Cosmos DB store, write-behind queue, read-through cache
    vision/              video auto-eventing: calibration, detection, tracking, teams, events
    dataset/             open dataset export and JSON Schema generation
    api/                 REST and WebSocket API
  tests/
  demo/hero-match.json   recorded agent answers for the hero match
frontend/                Next.js, TypeScript, Tailwind, shadcn/ui, TanStack Query, Zustand, React Flow
dataset/                 open synthetic dataset
docs/                    BRD, schema reference, deployment guide
infra/                   Bicep for Azure Container Apps and Cosmos DB (azd up)
```

## Technology

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, WebSockets, Pydantic |
| Agents | Microsoft Agent Framework workflows |
| Models | Azure OpenAI in Microsoft Foundry (`gpt-5-mini`), Entra ID authentication |
| Tools | MCP server for match state |
| Database | Azure Cosmos DB for NoSQL (serverless), managed identity |
| Frontend | Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui, Motion, Recharts, React Flow |
| Voice | Azure AI Speech, browser speech synthesis as fallback |
| Vision (local only) | PyTorch, RT-DETR (Transformers), OpenCV, SciPy |
| Hosting | Azure Container Apps, Azure Container Registry, Cosmos DB, managed identity, Log Analytics |
| Infrastructure as code | Bicep with the Azure Developer CLI |
