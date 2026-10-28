# Matchlens — Business Requirements Document

**Document status:** Draft for review
**Version:** 1.1 (scope update, 25 October 2026)
**Date:** 18 October 2026
**Delivery type:** Hackathon prototype (Microsoft Premier League Hackathon, submission 27 October 2026)

## 1. Purpose

Matchlens is an AI match-insights engine on Azure. It ingests synthetic football events as they happen, works out what matters and why, checks every claim against the data, and publishes each insight in two forms: timed broadcast overlays for studio and streaming production, and a personalised companion experience for each viewer.

## 2. Problem

1. **Stats without meaning.** Viewers see "62% possession" or "4 shots" but not why it matters. The story behind the scoreline (who controls the game, when pressure shifted, why a goal happened) depends on a pundit noticing it.
2. **One experience for everyone.** A casual fan, a data-hungry analyst, a fan of one club, a fan following one player, and a viewer in another language all receive the same graphics and commentary.
3. **Studios cannot do this by hand at scale.** Explained, on-screen insight for every moment, audience, market and language, in real time and with recaps, is not feasible with human teams alone.

Any solution must be fast enough for live play and trustworthy: it must not invent events, statistics or explanations.

## 3. Product vision

Every match is explained, and every viewer gets their own version. The studio receives broadcast-ready graphics without extra staff, and each fan sees the game in their own mode, club, player and language, with every insight backed by evidence.

A viewer using Matchlens should be able to answer:

1. What is happening now, and why does it matter?
2. Who is in control, and when did that change?
3. What did I miss?
4. How is my club or my player doing?

## 4. Business objectives

- Turn raw match events into explained insight automatically and in real time.
- Deliver genuinely different experiences to different audiences from one intelligence pipeline.
- Give studios machine-readable, timed overlay output that a rendering partner could consume.
- Support global audiences through multi-language output.
- Demonstrate trustworthy AI: every claim traceable to source events, unsupported claims blocked.
- Demonstrate purposeful multi-agent orchestration on Microsoft Foundry and Microsoft Agent Framework.

## 5. Target users

| User | Need |
|---|---|
| Studio / broadcast producer | Timely, accurate on-screen graphics with in/out timing, without manual tagging |
| Casual fan | An accessible match story in plain language, without advanced statistics |
| Data-oriented analyst | Pass quality, speeds, momentum, shot map, and the evidence behind each pattern |
| Club-focused fan | The match told from their club's perspective |
| Player-focused fan | The whole match followed through one chosen player |
| International viewer | All of the above in their preferred language |
| Late-arriving viewer | A fast, accurate catch-up |

## 6. Scope

### 6.1 In scope

- One synthetic match at a time, replayable at 1x to 20x speed.
- Synthetic match simulator with LLM-scripted storylines, using fictional clubs, players and venue.
- Pluggable feed adapter with a documented, provider-neutral event schema.
- Deterministic stats engine for live match state and metrics.
- Multi-agent insight pipeline with a coordinator and a fact-checking agent.
- Broadcast view: 2D pitch feed with timed overlays.
- Fan companion view: personalised by mode, favourite club, favourite player and language.
- Filterable match timeline, "What did I miss?" catch-ups, and automatic full-time recaps.
- Deployment on Azure with a public demo link available to judges until 10 November 2026.

Added in version 1.1, after the first build was complete:

- **Coach persona:** short ranked tactical briefs with evidence, trade-off and confidence, following the coordinator-and-specialist pattern already used in Matchlens.
- Fan polls and player ratings, with match cards rated from the match's own data.
- Voice narration through Azure AI Speech, with the browser's own voices as a fallback.
- Video auto-eventing: player and ball tracking from match footage turned into events, positions and speeds, scored against hand labels (see 6.3 for the footage used).
- A match archive: every stored match can be reopened afterwards.

### 6.2 Future scope

- Video auto-eventing end to end from the detector: joining broken player tracks into whole players and finding the ball without labels.
- Licensed live data provider plugged into the feed adapter.
- Multiple simultaneous matches.

### 6.3 Out of scope

- Club crests, kits and other trademarks as artwork. (Real match data, footage-derived tracking, and real club, player and competition names are in scope since the post-hackathon amendment in §9.3.)
- Real video clipping and video-rights management.
- User accounts, sign-in and identity merging.
- Predictive tracking simulation (5–10-second futures).
- Push, email or SMS notifications.
- Betting or real-money prediction features.
- Open chat or community publishing.

## 7. Functional requirements

### 7.1 Ingest

**FR-001:** The system must ingest match events as a live stream, one event at a time, in match-clock order.

**FR-002:** Supported event types must include at minimum: kickoff, pass, carry, shot, save, tackle, interception, pressure, possession change, foul, card, substitution, corner, free kick, goal, half-time and full-time.

**FR-003:** Each event must carry stable match, team and player identifiers, a match timestamp, pitch coordinates (x/y start and, where applicable, end), and an outcome.

**FR-004:** The system must also ingest match metadata: teams, lineups, formations, venue and kickoff time.

**FR-005:** Events must enter through a pluggable feed adapter that converts provider data into the common internal schema, so that a licensed live provider can replace the synthetic feed without changes downstream.

**FR-006:** Duplicate, delayed, out-of-order and corrected events must be handled safely. A correction must update every dependent statistic, insight, overlay and summary.

### 7.2 Synthetic data

**FR-007:** A simulator must generate a full 90-minute match with football-realistic event distributions and pitch positions.

**FR-008:** Simulator output must be calibrated against publicly known match averages (for example passes per match, pass completion, shots per match, conversion rate, sprint speeds), and the calibration must be documented.

**FR-009:** An LLM must be able to script a storyline for a match (for example a comeback, a red card, a player's breakout performance) that the simulator then plays out.

**FR-010:** The simulator must produce player tracking samples (position and speed) sufficient to compute distance covered, sprint speed and ball/shot speed.

**FR-011:** Generated matches must be exportable and published as an open synthetic dataset with its schema.

### 7.3 Interpret

**FR-012:** A deterministic stats engine must maintain live match state: score, clock, period, possession, and team and player statistics.

**FR-013:** Pass quality must be computed for every pass: distance, accuracy (completed or not), and a difficulty rating based on distance, direction, pressure on the passer and density of opponents near the passing lane.

**FR-014:** Speed metrics must be computed: ball speed, shot speed, player sprint speed and distance covered.

**FR-015:** Momentum must be computed as a rolling five-minute attacking-pressure score per team. Event weights must be configurable, with goals, shots on target and major chances weighted above other attacking events.

**FR-016:** The engine must detect key moments: goals, big chances, saves, cards, momentum swings, pressure spells, tempo changes and player milestones.

**FR-017:** Speed and distance indicators must trigger when predefined, configurable thresholds are reached (for example a sprint above a set km/h).

**FR-018:** Per-event processing in the stats engine must not depend on a language model.

### 7.4 Explain

**FR-019:** For each key moment selected for explanation, the system must state why it matters, not only that it happened. Explanations must cover concepts such as control vs. chaos, tactical pressure changes and game rhythm.

**FR-020:** Every explanation must cite the observable evidence behind it, including the events and time window (for example "three turnovers in 90 seconds under a high press").

**FR-021:** Every explanation must carry a confidence level.

**FR-022:** When the data is insufficient for an explanation, the system must show the available facts without adding a speculative narrative.

### 7.5 Render

**FR-023:** Every insight intended for screen must be published as machine-readable overlay JSON containing at minimum: overlay ID, type, match timecode in and out, priority, anchor (player, team or pitch position), display text and data payload.

**FR-024:** The broadcast view must render overlays in sync with the match on a 2D pitch feed.

**FR-025:** Overlay types must include at minimum: player name tag, speed/distance badge, pass difficulty, shot speed, momentum swing, key-moment explanation card and score bug.

**FR-026:** The number of overlays on screen must be capped, and overlays must be prioritised so that the screen is not cluttered during event-heavy periods.

**FR-027:** The overlay JSON schema must be documented so that a rendering partner could consume it directly.

### 7.6 Personalise

**FR-028:** The fan companion view must support the following modes, each a genuinely different experience rather than a rewording:

| Mode | Experience |
|---|---|
| Casual | Short story beats in plain language, minimal numbers |
| Analyst | Pass quality, speeds, momentum graph, shot map, evidence detail |
| Player-Focus | The match followed through one chosen player: actions, stats, milestones, tags |

**FR-029:** A viewer must be able to select a favourite club. Narrative and emphasis must reflect that club's perspective.

**FR-030:** A viewer must be able to select a favourite player, with that player's events and milestones prioritised.

**FR-031:** A viewer must be able to choose a single headline metric to track throughout the match (for example pass accuracy, distance or momentum).

**FR-032:** A viewer must be able to change mode, club, player, metric or language at any time without losing match state.

**FR-033:** Personalisation preferences must be stored for the anonymous session only. No sign-in is required.

### 7.7 Narrative and recap

**FR-034:** The system must generate live narrative beats after key moments and at defined intervals.

**FR-035:** The system must surface notable milestones and player-performance context.

**FR-036:** A viewer must be able to request a "What did I miss?" summary for the previous 5, 15 or 30 minutes, including the current score, major events and standout players.

**FR-037:** The system must generate an automatic half-time and full-time recap for each mode and language.

**FR-038:** The match timeline must list key events in order and be filterable by team, player and event type. Selecting an event must open its insight card, statistics and explanation.

### 7.8 Multi-language

**FR-039:** All narrative, explanation, catch-up and recap output must be available in at least English, Hindi and Spanish.

**FR-040:** Football terminology must read naturally in each language.

**FR-041:** All submission materials must be in English.

### 7.9 Trust and fact-checking

**FR-042:** Generated content must use only events and statistics received or computed by the system. Language models must not invent figures, events or players.

**FR-043:** A fact-checking agent must verify every generated claim against the stats engine before publication and must block or downgrade any unsupported claim.

**FR-044:** Every published insight must be traceable to its source events, originating agent, fact-check result and model used.

**FR-045:** An unavailable statistic must be shown as unavailable, never as zero.

### 7.10 Added in version 1.1

**FR-046:** A coach view must give short ranked tactical briefs, each with its evidence, trade-off and confidence, checked by the fact-checking agent like every other insight.

**FR-047:** The match must open fan polls at key moments (for example "Who wins from here?" after a goal and player of the match at full time). Votes are anonymous, can be changed until the poll closes, and each poll resolves against what happened.

**FR-048:** Each player must have a rating from the match data, shown next to the fans' rating, and a match card with an overall score and six attributes computed from this match's events and tracking only.

**FR-049:** Insights must be available read aloud in the viewer's language through Azure AI Speech, falling back to the browser's voices when the service is not configured.

**FR-050:** A vision pipeline must turn match footage into the common event schema and tracking samples: pitch calibration, player tracking, team assignment by kit colour, and events from ball possession. Its accuracy must be measured against hand-labelled events and boxes and reported as measured.

**FR-051:** Any stored match must be reopenable. A match stopped part-way resumes where it stopped; a finished match opens read-only with its score, timeline, insights and polls as they were.

## 8. Agent design

Matchlens uses Microsoft Agent Framework for orchestration, with agents hosted on Microsoft Foundry. Match state is exposed to all agents through an MCP server.

| Agent | Role | Constraint |
|---|---|---|
| Coordinator | Selects which moments deserve insight, routes work to specialists, deduplicates and ranks output, caps publication rate | Only the coordinator publishes |
| Tactical | Explains why a moment matters: control vs. chaos, pressure shifts, rhythm | Must cite evidence and confidence |
| Narrator | Writes live beats, milestones, catch-ups and recaps | Must use only supplied facts |
| Personaliser | Reshapes each insight by mode, club, player, metric and language | Must not change facts |
| Producer | Converts insights into timed overlay JSON with priority and timing | Must respect on-screen caps |
| Fact-Checker | Verifies every claim against match state; blocks or downgrades unsupported claims | Can veto publication |

### 8.1 Flow

```text
Simulator → Feed adapter → Stats engine (every event, deterministic)
                                   ↓ key moment detected
                              Coordinator
                    ↙            ↓            ↘
              Tactical       Narrator       Producer
                    ↘            ↓            ↙
                             Fact-Checker
                                   ↓ approved
                              Personaliser
                          ↙                 ↘
                Broadcast overlays      Fan companion views
```

### 8.2 MCP tools

The match-state MCP server must expose at minimum:

- `get_match_state` — score, clock, period, possession
- `get_events` — events in a time window, filterable by team, player and type
- `get_player_stats` — live statistics for one player
- `get_team_stats` — live statistics for one team
- `get_momentum` — momentum series for a time window
- `verify_claim` — check a stated figure or event against match state

### 8.3 Resilience

- If an agent fails or times out, the coordinator must retry once and otherwise publish a data-only insight card in place of the generated content.
- Failure of the agent layer must not stop the stats engine, score, clock or timeline.

## 9. Data requirements

### 9.1 Event schema

Each event must include: `event_id`, `match_id`, `sequence`, `period`, `match_clock`, `type`, `team_id`, `player_id`, optional `related_player_id`, `start_xy`, optional `end_xy`, `outcome`, type-specific attributes, and optional `corrects_event_id`.

### 9.2 Tracking samples

Periodic samples per player and for the ball: `timestamp`, `entity_id`, `xy`, `speed`.

### 9.3 Identity and real data

The hackathon build used fictional clubs, players, venues and competitions only. **Post-hackathon amendment (28 October 2026):** that rule is lifted product-wide. Matchlens now works on real matches: real club, player, manager and competition names, real match and event data, and tracking derived from broadcast video, live or recorded (see `docs/NEXT10.md`).

- No crests, kits or other trademarks as artwork.
- Raw downloads, footage and anything derived from them stay under `backend/data/local/` (gitignored) and are never committed. Each source's terms are recorded in `docs/LEAGUEDATA.md` and `docs/TACTICSML.md`.
- Test fixtures keep fictional names, so tests never depend on real data.
- The fictional synthetic match stays as the offline demo source.
- Deploying real data publicly needs the owner's sign-off on source terms.

## 10. Non-functional requirements

### 10.1 Performance

- Stats engine processing must complete within 100 ms per event.
- Score, clock, timeline and data overlays must appear within 1 second of event ingestion.
- Agent-generated explanations and narratives must appear within 10 seconds of the key moment.
- "What did I miss?" summaries must return within 10 seconds.
- The interface must remain responsive during event spikes, and must reconnect automatically after a temporary network interruption.

### 10.2 Accuracy and trust

- Feed and stats-engine data always take precedence over generated content.
- Generated explanations must avoid unsupported certainty.
- Fact-check pass rate and groundedness must be measured with Foundry evaluations on a fixed test set of matches.

### 10.3 Accessibility and devices

- The fan companion view is mobile-first and also supports desktop and tablet.
- Core functions must support keyboard navigation, screen readers and sufficient colour contrast.
- Information shown through colour must also have a text or symbol indicator.

### 10.4 Observability

- Agent runs must be traced in Foundry, including handoffs, tool calls, fact-check results and latency.
- Logs must separate feed delay from processing delay.

### 10.5 Cost and hosting

- Total Azure spend must stay within the available USD 200 credit through the end of judging (10 November 2026).
- Services must scale to zero when idle. Smaller models must be used for frequent tasks and a larger model only for recaps.
- A pre-generated, cached demo match must be available so that judges can test without triggering heavy model usage.

### 10.6 Engineering quality

- Public GitHub repository with README, architecture diagram, setup instructions and schema documentation.
- Automated tests for the stats engine, feed adapter and fact-checker.
- Infrastructure defined as code.
- Secrets held in Azure Key Vault or managed identity, never in the repository.

## 11. Technology

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, WebSockets |
| Frontend | React (Next.js) |
| Vision | PyTorch, RT-DETR, OpenCV (local development only) |
| Agents | Microsoft Agent Framework, Microsoft Foundry Agent Service |
| Models | Azure OpenAI via Foundry |
| Tools | MCP server for match state |
| Hosting | Azure Container Apps |
| Observability | Foundry tracing and evaluations, Azure Monitor |
| Development | GitHub, GitHub Copilot |

## 12. Hackathon alignment

| Brief requirement | Matchlens feature |
|---|---|
| Ingest | Simulator, feed adapter, event stream |
| Interpret | Stats engine, pass quality, speeds, momentum |
| Explain | Tactical agent with evidence and confidence |
| Render | Producer agent, overlay JSON, broadcast view |
| Personalise | Modes, club, player, metric, language |
| Player identification | Name tags, speed and distance threshold badges |
| Pass quality | Distance, accuracy, difficulty rating |
| Speed metrics | Ball speed, shot speed |
| Narrative generation | Narrator agent, milestones, catch-ups, recaps |
| Explainability | Control vs. chaos, pressure shifts, rhythm, with evidence |
| Data innovation | Calibrated simulator, LLM storylines, open dataset |
| Multi-language | English, Hindi, Spanish |

Target prizes: Grand Prize, Best Multi-Agent System, Best Use of Microsoft Foundry.

## 13. Success measures

- All five pipeline stages demonstrated end-to-end on a live replayed match.
- Zero unsupported claims published across the agreed test matches.
- Latency targets in section 10.1 met during the demo.
- At least three modes and three languages producing distinct outputs for the same moment.
- Demo link available and within budget until 10 November 2026.

## 14. Acceptance criteria

The prototype is ready for submission when:

- A synthetic match can be played from kickoff to full time without page refreshes.
- Events appear once, in the correct order, and a correction updates every affected element.
- Pass quality, speeds and momentum are computed and match the underlying events.
- Key moments produce explanations with evidence and confidence.
- The fact-checker blocks a deliberately unsupported claim in the test set.
- Overlay JSON validates against the documented schema and renders in sync on the broadcast view.
- The same moment produces visibly different output in Casual, Analyst and Player-Focus modes and in all three languages.
- "What did I miss?" works for 5, 15 and 30 minutes, and the full-time recap generates automatically.
- (Hackathon criterion, retired by the §9.3 amendment) No real club, player or competition names appear anywhere.
- The deployed demo, public repository, pitch and a demo video under two minutes are submitted by 27 October 2026, 11:59 pm Pacific Time.

## 15. Open decisions

1. Fictional club, player, venue and competition names for the demo match.
2. Storyline for the hero demo match.
3. Visual identity and branding.
4. Exact Azure OpenAI models per agent, within budget.
5. ~~Whether fan polls return from future scope if time allows.~~ Resolved in version 1.1: they did.
