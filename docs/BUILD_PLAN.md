# Matchlens: build plan for the winning demo

Six features that turn a strong engine into a product judges remember. Each section says what it is, why it
wins, what to build on each side, what "done" means, the cut line if time runs short, and the effort.

**Effort** is focused hours for one developer working with Claude. **MVP** is the smallest version that still
lands in the demo video; **Full** is the polished version.

## Summary

| # | Feature | Brief stage it proves | MVP | Full | Depends on |
|---|---|---|---|---|---|
| 1 | Broadcast-grade redesign | (d) Render | 8 h | 14 h | — |
| 2 | Multiverse: one match, four broadcasts | (e) Personalise | 6 h | 10 h | 1 (visual tokens) |
| 3 | AI telestrator and moment replay | (c) Explain + (d) Render | 9 h | 14 h | 1 (pitch layers) |
| 4 | Match Pulse | (iv) Control vs chaos, rhythm | 6 h | 10 h | 1 |
| 5 | Voice and Ask the Pundit | (iii) Narrative, (vi) Multi-language | 10 h | 16 h | Speech backend (in progress) |
| 6 | Scenario Lab | (v) Data innovation, (a) Ingest | 7 h | 11 h | — |
| — | Demo cassette re-record, demo script, video | All | 4 h | 6 h | 1–6 |
| | **Total** | | **50 h** | **81 h** | |

**Suggested tracks** (they touch separate files, so they can run in parallel):

- **Frontend track:** 1, then 2, then the Pulse UI (4) and the telestrator UI (3).
- **Backend track:** telestrator drawings and the moment API (3), the Pulse API (4), the pundit agent (5) and
  Scenario Lab (6).
- **Last day:** re-record the cassette with every new agent output, rehearse the demo script, and record the video.

## Status: MVPs built

All six MVPs are built on the `claude/winning-mvps` branch. Voice narration (part of item 5) had already landed
on main, so item 5 here is the pundit only.

| # | Built | Where |
|---|---|---|
| 1 | Ball trail; tilted "TV camera" pitch; a goal takeover in the scoring side's colour; a score bug whose score rolls over on a goal and widens the possession marker; telestrator and pulse colour tokens for both themes | `broadcast/pitch.tsx`, `goal-takeover.tsx`, `score-bug.tsx`, `globals.css` |
| 2 | `/multiverse`: four screens with their own profile (casual fan in Hindi, analyst in English, player focus in Spanish, coach). Each screen has its own monitor, a top-three feed or bench brief, and persona and language pickers. One screen can be heard at a time, a top-priority moment flashes every screen, and the screens swipe on a phone | `features/multiverse/`, `profile-store.ts` (scoped profiles) |
| 3 | `GET /api/moments/{id}`: replay frames and telestrator strokes (lanes, carries, the shot with its speed, rings, pressing zones, run paths) worked out from the engine with no model. The monitor plays them back with a Replay bug, a caption and Back to live. A goal call rolls into its own replay, and any insight card, pulse pin or cited moment can start one | `interpret/moments.py`, `use-instant-replay.ts`, `telestrator.tsx` |
| 4 | `GET /api/pulse`: a row per minute (momentum, control/balanced/chaos, pressing, turnovers), pins and spells. The Match Pulse strip shows them under the monitor; a pin starts a replay and the hover readout gives the minute | `interpret/pulse.py`, `companion/match-pulse.tsx` |
| 5 | `POST /api/ask`: a keyword router in three languages, a grounded data pack, the Pundit agent and the Fact-Checker. Off-topic questions never reach a model, and a data answer is the fallback. The sheet has suggestion chips, key numbers, cited moments that start a replay, and read-aloud | `agents/pundit.py`, `features/pundit/` |
| 6 | `/lab`: four checked presets (comeback, ten-man heist, late late show, seven-goal thriller), each with a dominance ribbon and its beats. A described match is drafted by the Scenario agent with a repair loop (live models only). Kickoff takes a speed, a script-vs-reality card tracks each beat, and the running match downloads as a dataset | `simulator/scenarios.py`, `agents/scenario.py`, `features/lab/` |

Not built from the Full column: telestrator drawing by hand, a replay scrubber, a shareable Multiverse URL, and
pundit answers spoken in the Azure voice (they use the browser's voice). The demo cassette has not been
re-recorded, so in demo mode the pundit gives data answers and free-text scenarios return 503.

## What the plan builds on

These already exist, so the estimates assume them:

- Every insight already carries `variants[mode][language]` and `club_angles`. The Multiverse view needs **no new
  backend work**.
- `MatchState` has `momentum_index`, `rhythm` and `controlling_team`. The engine emits control, chaos and pressure
  spell signals.
- The MCP tools in `tools/match_tools.py` (`get_match_state`, `get_events`, `get_player_stats`,
  `get_team_stats`, `get_momentum`, `get_recent_signals`, `verify`) are exactly the tools the pundit agent needs.
- `Storyline` (`simulator/storyline.py`) is already designed to be LLM-produced JSON, and
  `SessionManager.start_synthetic` already takes a storyline.
- Azure AI Speech text-to-speech (`speech.py`, `/api/speech`, `infra/speech.bicep`) is **in progress in the working
  tree** and is not committed yet. Plan 5 covers only the parts that come after it.

Constraints that still apply (the synthetic-only rule was lifted on 28 October 2026, BRD §9.3): the feed never waits for a model; every
number on screen comes from the engine, and the Fact-Checker checks every model sentence.

---

## 1. Broadcast-grade redesign

**What.** The app should look like a match-night broadcast, not an admin panel. It gets a dark stadium theme by
default, a pitch with depth and motion, and TV-grade score bug and lower-thirds.

**Why it wins.** The judges' first 5 seconds decide whether they read "product" or "prototype". Today every page is
white cards with dots on green.

**Frontend** (`globals.css`, `features/broadcast/*`, `components/shell/*`)

- **Design tokens.** Dark broadcast theme as default (deep navy and charcoal, floodlit-green pitch, one electric
  accent per club). Light theme stays. Use a condensed display face for scores and headlines (e.g. Barlow
  Condensed or Oswald) and a tabular numeral face for stats.
- **Pitch** (`pitch.tsx`), as layered SVG groups so later features can add layers:
  - mown grass stripes, a floodlight vignette and softer chalk;
  - **3D tilt**: a CSS `perspective` + `rotateX(~35°)` on the pitch container, with a broadcast-camera toggle
    (top-down ↔ tilted). Check that tap-to-pick still works when tilted. If the radius maths uses
    `getScreenCTM`, fall back to `elementFromPoint` hit-testing while tilted;
  - **ball trail**: the last 6–8 ball positions as a fading polyline. Frames are 1 Hz, so interpolate with a
    Catmull-Rom curve to keep it smooth;
  - **player dots**: kit colour, shirt number and a glow ring for the favourite player. The player in possession
    gets a pulse;
  - **pass lanes**: the last pass draws with an animated `stroke-dashoffset`. Pass difficulty sets the colour
    (green easy → amber → red hard);
  - **pressure zones**: radial blobs where pressure events clustered in the last 3 minutes, with team colour at low
    opacity.
- **Score bug and lower-thirds** (`score-bug.tsx`, `overlay-layer.tsx`): broadcast-style slab shapes, club-colour
  edges, `motion` slide/wipe in and out, a timer that ticks, and a goal takeover (full-width banner + score flip).
- **Match Centre layout:** pitch hero full-bleed at the top, then the feed. Move profile settings into a slide-out
  "Your broadcast" sheet with big visual mode tiles instead of dropdowns.

**Done when**

- Screenshots at 375 px and 1440 px in both themes read as a TV product (`frontend/scripts/shoot.mjs`).
- There is no horizontal overflow, and frames tween smoothly at 15×.
- A goal triggers the takeover animation.

**Cut line:** drop the 3D tilt and pressure zones (−5 h).

**Effort:** MVP 8 h · Full 14 h.

## 2. Multiverse: one match, four broadcasts

**What.** A `/multiverse` page with four synced panes showing the same live moment as four products:

1. **Casual fan** · Hindi · Northbridge
2. **Analyst** · English · neutral
3. **Player Cam** on the star striker · Spanish
4. **Coach tablet** · Harbour City

**Why it wins.** It proves stage (e) in one glance: "two genuinely different experiences", times four. It is the
opening and closing shot of the demo video.

**Frontend**

- **Scoped profiles.** `useProfile` is a single global persisted store today. Add a `ProfileScope` context that
  provides a non-persisted zustand store per pane (`createStore`). `useProfile`/`useFanProfile` read the nearest
  scope and fall back to the persisted store. That way the existing feed, insight card, overlay layer, metric tile
  and player sheet work in a pane unchanged.
- **Pane** = a compact pitch (shared frame data, its own focus player) + an overlay layer (its own language) + the
  top 3 insights in its own mode. Pane 4 embeds the coach brief.
- **Sync moment.** When a priority-1 signal fires (goal, red card), all four panes flash together, then each shows
  its own take. A "same moment, four stories" label ties them together.
- **Pane picker.** Click a pane's header to change its mode, language, club or player live. Make one pane the
  "audio" pane (used by plan 5).
- **Layout.** 2×2 on desktop. On mobile, swipeable panes with dots.
- **Performance.** One WebSocket and one frame store shared by all panes, memoised pitch layers, and four feed
  queries (one per profile).

**Done when**

- During a goal, all four panes update within the same second.
- The panes show different languages and wording, and a different focus.
- There are no duplicate WebSocket connections.

**Cut line:** fixed presets with no per-pane picker (−3 h).

**Effort:** MVP 6 h · Full 10 h.

## 3. AI telestrator and moment replay

**What.** Each explained moment comes with **drawings on the pitch**: the pass chain that built the goal, a ring
around the scorer, the gap that opened, or the zone where the press won the ball. The "Replay" button on any insight
rewinds the pitch about 12 seconds and plays the moment back with the drawings appearing in time.

**Why it wins.** It is the clearest picture of "explain why it matters" a judge can see, and it makes the pipeline
tangible. The drawings are built from evidence events, so they are as trustworthy as the fact-checked text.

**Backend**

- **Contract.** Add `drawings: list[Drawing]` to `Overlay` and `Insight`. A `Drawing` is a discriminated union:
  `arrow` (from → to, style: pass, carry or run), `ring` (player id or point), `zone` (polygon or circle, team),
  `lane` (ordered points), and `label` (point, short text). Each drawing has `at_s` (offset into the replay) and
  `evidence_event_id`. Update `docs/SCHEMA.md`, the JSON schemas and the dataset export.
- **Generator** (`interpret/drawings.py`, **code, not a model**). For each signal, build drawings from its
  evidence events and frames:

  | Signal | Drawings |
  |---|---|
  | goal / big chance | pass chain as lanes, carry arrows, shot arrow, ring on the shooter |
  | pressure spell | zone over the pressure cluster, rings on the top pressers |
  | chaos spell | possession-change markers joined in sequence |
  | control spell | possession lanes and a passing-triangle highlight |
  | speed badge | run arrow from the tracking path, label with km/h |
  | hard pass | lane with its difficulty rating |

  The Producer agent keeps choosing *whether* and *how long*. It never invents coordinates.
- **Moment API.** `GET /api/moment/{insight_id}` returns frames from about [t−12 s, t+3 s], the evidence events and
  the drawings. The session keeps a ring buffer of the last 15 minutes of frames, plus a repository read for older
  ones.
- **Tests.**
  - Every drawing references real event ids.
  - Every coordinate is inside the 105 × 68 pitch.
  - Overlays with drawings validate against the schema.

**Frontend**

- A `TelestratorLayer` on the pitch draws the primitives with chalk-style strokes (SVG `pathLength` animation).
- A replay mode on the pitch pauses the live frames, plays the moment's frames, shows a "REPLAY" bug, then returns
  to live.
- Insight cards and overlays get a "Replay" button, also used by the pundit answers (plan 5) and the Pulse pins
  (plan 4).

**Done when**

- For the hero match's goals, red card and press spell, the replay shows the right players and passes.
- The drawings line up with the dots.
- Live play resumes cleanly after a replay.

**Cut line:** draw on the live pitch only, with no frame replay (−4 h).

**Effort:** MVP 9 h · Full 14 h.

## 4. Match Pulse

**What.** A heartbeat-style strip for the whole match:

- momentum as a diverging area (home above, away below);
- tempo as line thickness;
- **chaos** as a jagged, warmer band and **control** as a smooth, cooler band;
- key moments pinned on top (⚽ 🟥 🔁 ⚡).

Hovering a point says what the game felt like then ("Harbour City in control: 71% possession, 4 passes per
sequence"). Clicking a pin replays the moment (plan 3).

**Why it wins.** Brief item (iv) asks for "control vs chaos, tactical pressure changes and game rhythm". The engine
already computes these, but nothing shows them. The Pulse is a single memorable image that also works as a studio
graphic.

**Backend**

- `GET /api/pulse?bucket_s=30` returns one row per bucket from the engine: `momentum`, `tempo` (events per
  minute), `chaos` (possession changes and duels per minute), `pressure` (pressure events per team),
  `possession_share`, `pass_accuracy` and `rhythm` label. It also returns the pins (signals with priority ≤ 2).
- Push the newest bucket over the WebSocket when it closes, so the strip grows live.
- **Tests:** the buckets add up to the engine totals, and a correction replay rewrites the affected buckets.

**Frontend**

- A full-width strip under the pitch on the Match Centre and on the broadcast page as a studio graphic. Make it SVG
  with a `motion` path morph when a bucket is added.
- Analyst mode shows the layer legend and per-bucket numbers. Casual mode shows one plain-language caption ("The
  game has gone wild in the last 10 minutes").
- The tooltip text comes from the bucket data through templates, not a model, so it is instant and always true.

**Done when**

- The hero match shows Harbour City's first-half control, the chaos after the red card, and Northbridge's late
  surge as visibly different shapes.
- Clicking a pin opens the replay.

**Cut line:** momentum and pins only, without the chaos/control band (−3 h).

**Effort:** MVP 6 h · Full 10 h.

## 5. Voice and Ask the Pundit

**What.**

- **Listen:** the feed is read aloud in the fan's language, with a delivery that suits their mode. Casual fans get
  an excited style for goals; analysts get a measured one.
- **Ask the Pundit:** a chat where anyone can ask "Why did Northbridge come back?" or "Who's been the best player
  since half-time?". The answers are grounded in the match tools, fact-checked and badged, and each comes with a
  "Replay" for the moment it cites.

**Why it wins.** It turns the engine into a conversation and works in any language. It also shows the MCP and
agent stack doing real work, which matters to Microsoft judges.

**Backend**

- **Speech** (after the in-progress `speech.py` lands):
  - per-mode SSML style (`mstts:express-as` where the voice supports it), picked by signal priority;
  - pick the voice per language;
  - cache audio per insight, mode and language.
- **Pundit agent** (`agents/pundit.py`, `POST /api/ask`):
  - the fast model with the match tools as Agent Framework tools, answering in the profile's language;
  - structured output (`answer`, `claims[]`, `cited_signal_ids[]`) goes through the existing Fact-Checker. If it
    is unsupported, it gets one rewrite, then a "data-only" fallback built from the tool results;
  - scope guard: football and this match only. Rate limit per client;
  - stream tokens over Server-Sent Events through the Next.js bridge.
- **Demo mode:** record 6–8 suggested questions into the cassette (different minutes and languages). Free-typed
  questions in offline mode answer from a template over `get_match_state` and `get_recent_signals`.
- **Tests:**
  - an answer with a wrong number gets rewritten or blocked;
  - an off-topic question is refused;
  - the cassette replays the suggested questions.

**Frontend**

- **Listen toggle** in the header and in the Multiverse audio pane. It keeps a queue of priority ≤ 2 insights and
  never overlaps clips. It ducks for a goal takeover and falls back to the browser's `speechSynthesis` when
  `/api/health` reports no speech.
- **Pundit panel:** a slide-over chat with suggested-question chips, streaming text, a fact-check badge per answer,
  evidence chips that open the telestrator replay, and an optional "read it to me".

**Done when**

- Suggested questions answer in under 3 s in demo mode, and with live models when Azure is configured.
- A planted wrong claim shows as rewritten.
- Hindi and Spanish answers read aloud with the right voice.

**Cut line:** Listen with no SSML styles, and the pundit without streaming (−5 h).

**Effort:** MVP 10 h · Full 16 h (Listen 4 h, pundit 12 h).

## 6. Scenario Lab

**What.** A judge types a match script, for example *"Underdogs Harbour City go a man down at 30', equalise from a
set piece and win it in stoppage time"*. Or they pick a preset. Matchlens:

1. writes a storyline;
2. shows it as a beat timeline before kick-off;
3. plays the whole match through ingest, engine, agents, overlays and personalisation;
4. offers the generated dataset (events, tracking, metadata) as a download.

**Why it wins.** It makes "data innovation" (v) interactive: the judge creates the synthetic data and watches the
whole pipeline react. It also shows the simulator is general, not one hard-coded match.

**Backend**

- `POST /api/scenario` takes `{prompt, language}` and calls the strong model with structured output to produce a
  `Storyline`. The prompt includes the roster (ids, names, positions) and the beat and phase rules.
  - Validate the result with pydantic and allow one repair turn with the validation errors.
  - Map names to ids, reject impossible scripts (a sent-off player scoring, a goal before kick-off), and return
    the storyline for preview.
- `POST /api/scenario/{id}/kickoff` → `manager.start_synthetic(storyline=…)`, at the speed the judge picked.
- **"Script vs reality" check** at full time: every beat was delivered (goals, cards, subs) and the phases match
  the momentum. It shows as a ✓ list, which proves the simulator honoured the script.
- `GET /api/runs/{run_id}/dataset.zip` exports events.jsonl, tracking.jsonl.gz and meta.json using the existing
  `dataset/export.py`.
- **Demo mode:** 3–4 preset scenarios with recorded cassettes (the cassette is keyed by seed and storyline), so
  presets run without Azure. Free-typed prompts need live models.
- **Tests:**
  - a generated storyline validates;
  - an invalid one is repaired or rejected;
  - the simulator delivers every beat for each preset.

**Frontend**

- A `/lab` page with a prompt box, preset chips, a storyline preview (phases as a dominance ribbon, beats as icons
  on a 0–95' line), "Kick off" with a speed choice, then a redirect to the Match Centre.
- At full time, show the "Script vs reality" card and the dataset download.

**Risk.** There is one live session, so starting a scenario replaces the match for every viewer. That is fine for
the demo, but say so on the Lab page.

**Done when**

- Each preset plays and reaches full time with every beat delivered.
- A typed prompt yields a valid storyline in live mode.
- The dataset download opens and matches the schema.

**Cut line:** presets only, no free-text prompt (−3 h).

**Effort:** MVP 7 h · Full 11 h.

## Last mile: demo, cassette and video (4–6 h)

1. **Re-record** `backend/demo/hero-match.json` with live models after 3, 5 and 6 land (pundit questions, preset
   scenarios). Commit the cassette so the demo needs no Azure.
2. **Agent Room additions:**
   - the Azure services in use (Foundry, Speech, Cosmos DB, Container Apps);
   - p95 latency from event to data card and from event to agent upgrade (fill in the "–");
   - model calls and cost per match.
3. **Demo script** (3 minutes):

   | Time | Shot |
   |---|---|
   | 0:00 | Multiverse cold open: a goal lands four ways |
   | 0:20 | Broadcast view with the takeover and telestrator replay |
   | 0:50 | Match Pulse tells the story of the match |
   | 1:20 | Ask the Pundit in Hindi, answered aloud and fact-checked |
   | 1:50 | Agent Room: the pipeline and a blocked claim |
   | 2:20 | Scenario Lab: a judge's own match kicks off |
   | 2:45 | Close on the Multiverse |

4. **Final checks:** run `npm test` and `npm run lint`, take screenshots at both widths in both themes, and deploy
   to Container Apps with the demo link in the README.

## Open decisions

- **Deadline and team size.** These decide MVP vs Full per feature. With one developer and about 3 days, ship every
  MVP (≈50 h). With 2–3 people, run the tracks above and take the Full versions of 1, 2 and 3.
- **Auto-eventing from video** (brief item ii) stays on the roadmap slide unless someone has spare time after the
  MVPs.
