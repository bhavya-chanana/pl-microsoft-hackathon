# Next 10 seconds: the real-match companion

Matchlens predicts the next 10 seconds of **open play** (no set pieces) for real matches, live and on any stored
match, from the players' and the ball's x/y coordinates. It covers 12 clubs: Liverpool, Manchester City, Brighton,
Aston Villa, Arsenal, Barcelona, Real Madrid, Atlético Madrid, Bayern Munich, RB Leipzig, Paris Saint-Germain and
Porto. Around the prediction it answers questions about these clubs over the last 10 seasons (domestic league, UCL,
UEL), and after a match it plays out the moments that mattered: goals conceded and chances that could have been
scored.

Status: being built on `feat/next10-real`. This page is the design; each section says what exists.

## Ground rules

- **The deployed app is not touched.** It shares the Azure Cosmos DB account with this work. Everything here
  writes to a **separate database, `matchlens_next10`**, declared in its own module
  ([`infra/next10-cosmos.bicep`](../infra/next10-cosmos.bicep)), never in `infra/cosmos.bicep` or `CONTAINERS` in
  `store/base.py`. Every new feature is off unless its env flag is set. Nothing here runs `azd up`.
- **Docker first, Azure second.** Data is loaded and checked in the local Cosmos emulator (a kept, reused
  container). The Azure seeder refuses to run unless that exact bundle passed its emulator check
  (`store/next10.py`).
- **Video comes from the owner.** Recordings are dropped in a watched folder, or a live input (OBS, a capture device
  or a camera) is pointed at the match being watched. Nothing here records or decrypts DRM-protected streams.
- **Fetchers are polite.** They cache, pace and stop on 403/429; no IP rotation or bot-detection evasion.
- Raw data, footage and derived feeds stay under `backend/data/local/` (gitignored). Test fixtures stay fictional.

## Why video

A 10-second horizon needs positions with a few seconds' latency. Event feeds (FotMob and similar) arrive 20-30 s
late and have no positions, so they cannot drive it. They are the **data clock**: authoritative events, lineups,
shot maps and labels. Broadcast video is the **video clock**: fast, approximate x/y. An aligner maps one onto the
other using kickoffs, goals and cards.

```
VIDEO CLOCK (x/y)                                         DATA CLOCK (labels)
watched folder | live input (OBS / capture / camera)        FotMob live poll (>= 30 s), post-match details
  -> main-camera gate (vision/views.py)                       -> FotMob adapter -> Events, lineups, shot map
  -> whole-match calibration + stadium prior                  |
  -> detect players + ball -> track in pitch coordinates      |
  -> team, jersey number (vision model, async)                |
  -> 5 Hz frames, observed mask, off-screen players imputed   |
       '------ aligner: video time -> match clock; jersey -> lineup player ------'
                                   |
               Next10 model: every second live, any moment on a stored match
                                   |
   Cosmos matchlens_next10: matches, events, frames, predictions, moments, video_jobs, kg_nodes, kg_edges
                                   |
     live overlay | play it out | historian Q&A (graph RAG) | verification board
```

## Research basis

**TacticAI** (Wang et al., *Nature Communications* 2024, arXiv 2310.10553). 7,176 Liverpool corners (2020-21,
25 fps tracking, private). A fully connected graph of the 22 players (position, velocity, height, weight,
possession; no ball node; teammate/opponent edges), GATv2 with D2 symmetry through group convolution, receiver
prediction (top-3 0.782) and shot prediction decomposed as P(shot) = sum_i P(shot | receiver i) P(receiver i)
(F1 0.71 against 0.52 without it), and a cVAE that repositions one team. Its stated limits: it needs high-quality
tracking, has no aleatoric uncertainty, moves one team, and covers corners only. `tacticsml/` already covers open
play, a ball node, five frames of history, ensemble uncertainty and both teams
([TACTICSML.md](TACTICSML.md)). From TacticAI we take the receiver-conditioned shot decomposition, group
convolution as an ablation against our frame averaging, and latent-space retrieval.

**Broadcast to pitch coordinates** (SoccerNet Game State Reconstruction). The 2025 winner (GS-HOTA 63.9, baseline
29.0) used a YOLO-X detector, Deep-EIoU tracking with OSNet re-identification and split-and-merge of tracklets,
multi-frame keypoint calibration, a vision-language model for jersey numbers and colour clustering for teams. The
2024 winner (63.8) ran in real time with a camera-parameter regressor, 74-keypoint refinement, temporal smoothing
and tracking in pitch coordinates. GSR does not localise the ball; ball models come from the TrackNet/WASB line.
Calibration from points and lines: PnLCalib.

**Trajectories.** TranSPORTmer (ACCV 2024) forecasts, imputes unseen players and infers the ball in one
set-attention model, which is what partial-view broadcast tracking needs. CausalTraj (2025) argues for joint
metrics (minJADE, minJFDE). JointDiff (ICLR 2026), TacticGen and GenTac (2026) sample joint futures with diffusion.

Licences: detectors are Apache 2.0 (RT-DETR in use, RF-DETR N/S/M/L as the upgrade); no AGPL code. Third-party
model code is checked before use.

## Pipeline

**P0, events (exists on this branch).** FotMob adapter (`local/fotmob.py`, `MATCHLENS_FOTMOB=1`). Shadow
predictions (`predict/shadow.py`): a discrete-time hazard model gives each team's chance of a shot and of a goal in
the next 60 s and 5 minutes, written with the time it was issued before the outcome is known, then scored after the
match against the base rate (Brier, log loss).

**P1, data.** Warehouse (`leaguedata/`) extended to 2016-17 to 2025-26, Liga Portugal and the UEL, plus FotMob match
details for every fixture of the 12 clubs (shot maps with x/y and xG, incidents, momentum, average positions, lineups).
StatsBomb open data where it overlaps. A `ProviderEventSource` interface for a licensed event or tracking feed.

**P2, video to x/y.** A watched inbox (`backend/data/local/video/inbox/`) and a live input. **Calibration over the
whole match:** the main broadcast camera sits at one gantry, so one camera centre, roll and lens distortion are solved
for every main-camera frame of a match, leaving three values per frame (pan, tilt, focal length) instead of eight, with a
robust reprojection loss and temporal smoothing. Each match refines a **stadium prior** (`vision/venues.json`) for
the 12 home grounds, which live mode starts from. Then detection, a ball model, tracking in pitch coordinates,
jersey numbers matched to the lineup, and an imputer for players off screen.

**P3, the Next10 model.** `tacticsml` extended: team-style conditioning (FiLM from a team-season embedding seeded with
playbook features), view-mask augmentation so full-tracking data trains a broadcast model, open play only, and new
heads: `ball_zone10` (where the ball is in 10 s, 16 x 12 grid), `poss10`, `action_next`, and multi-horizon
trajectories (a fast mixture model live, diffusion for play-it-out). Scored per club and on held-out time against
the base rate, the pitch-control + xT baseline and constant velocity.

**P4, history.** A typed knowledge graph built from the curated tables (clubs, players, managers, matches, seasons,
competitions, venues, patterns, moments) with vector search, and a historian agent that answers with SQL, graph and
vector tools, every number traced to a tool result.

**P5, product.** Live overlay (where the ball goes next, shot and turnover chances, likely receivers), play-it-out
for goals conceded and big chances (what the model expected, what happened, alternative futures, drag-to-edit what
if), and a verification board scoring every prediction against what happened.

## Running it

```bash
npm run db:emulator          # starts the kept emulator container, or creates it once with a data volume
npm run shadow:watch -- --date 2026-10-28    # shadow predictions for today's matches of the 12 clubs
npm run shadow:score         # score finished matches
```

`MATCHLENS_FOTMOB=1` is required for anything that calls FotMob.
