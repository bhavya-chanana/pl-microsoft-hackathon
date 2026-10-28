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
npm run db:next10            # starts the kept next10 emulator container (port 8091), or creates it once with a volume
npm run next10:init          # creates matchlens_next10 and its containers in the emulator
npm run shadow:fit           # fits the hazard model (add --warehouse DIR for a separately built warehouse)
npm run shadow:fixtures -- --date 2026-10-28  # today's matches of the 12 clubs
npm run shadow:watch -- --date 2026-10-28     # predicts every minute, scores each match at full time
npm run shadow:score -- --match fotmob:6275371  # re-scores a finished match from its stored predictions
```

The shadow model trains on a warehouse built for it alone (`backend/data/local/next10/warehouse`, raw files linked
from `leaguedata/raw`), so rebuilding it never replaces the tables the app's playbooks and knowledge base read.

`MATCHLENS_FOTMOB=1` is required for anything that calls FotMob.

## Whole-match calibration (results)

`vision/bundle.py` solves one camera for a whole match: a shared gantry position C (metres, pitch coordinates with
the origin at a corner and height up), roll and lens k1 (optionally k2), the principal point at the image centre,
and per frame only pan, tilt and focal length. It is a robust (soft-L1) sparse least-squares problem over painted-line
pixels matched to the markings, with a smoothness term on the per-frame channels. A Kalman filter and an RTS smoother
fill frames with too few lines, flagged `interpolated`; `LiveCameraFilter(prior)` is the causal version for live use.
`solve_video(video, ...)` runs the whole chain (view filter, tracker, two rounds of `solve_match`).
`vision/align.py` maps video time to match time from a few anchors (kick-offs, goals, cards) with a piecewise-linear
clock and RANSAC, robust to one wrong anchor and handling half boundaries. `vision/venues.json` holds priors for 12
grounds; `update_venue_prior` merges a new fit into one. The harness is `python -m matchlens.vision.bundle_eval
<clip>`; it writes `bundle_report.json` beside the clip.

Synthetic check (known camera, 0.4 px noise, 150 frames): C within 0.5 m, every frame's pitch reprojection under
0.3 m, and `align` recovers a known clock map with one wrong anchor (tests in `tests/test_vision_bundle.py` and
`tests/test_vision_align.py`).

**Liverpool-Brighton clip** (10,561 main-camera frames, paint pixels from every frame, 10% held out per frame, the
bundle never sees the hand labels; the per-frame baseline uses the left-out calibrations for the five anchor frames):

| | per-frame (existing) | whole-match bundle |
|---|---|---|
| (a) calibrated share of main frames | 76.9% (8,122) | 80.0% fitted (8,444); 85.2% with interpolated |
| (b) held-out line error, median / p90 | 1.07 / 3.24 px, 0.083 / 0.46 m | 1.29 / 3.73 px, 0.099 / 0.55 m |
| (b) same, a fresh 8-parameter refit per frame | 0.91 / 2.75 px, 0.068 / 0.43 m | |
| (b) surveyed landmarks (labels.json), median | 0.27 m, 2.7 px | 0.45 m, 5.4 px |
| (c) jitter of projected pitch corners (second difference), median / p90 | 13.7 / 104 px | 1.2 / 6.3 px |
| (c) jitter of the image-centre footprint, median / p90 | 0.106 / 0.35 m | 0.041 / 0.16 m |

What this says:

- The bundle calibrates more frames (+3 points fitted; the extra 5% interpolated frames are not verified) and is far
  smoother, which is what the downstream tracking and the Next10 model need.
- It does not fit the lines better. On held-out line pixels it is about 20% worse than the existing per-frame
  homographies and worse than a fresh 8-parameter refit; on the 90 surveyed landmark points it is worse in the
  median (0.45 against 0.27 m), better on 6 of the 12 labelled frames that both calibrate and clearly worse on frames 5,460 (2.2 m), 7,800
  and 12,479. A rigid camera cannot absorb pitch or lens deviations the way eight free parameters can. Treat bundle
  positions as good to about half a metre, per-frame ones as noisier frame to frame but locally tighter.
- The lens term is weakly identified: k1 came out between 0.14 and 0.50 depending on the pitch-length assumption and
  on how much video was used, while C stayed within about 1 m. Do not read k1 as a physical lens property.
- Pitch length: markings models of 105, 103 and 101 m (the published Anfield figure) were fitted. 105 gave the lowest
  held-out error and landmark error (0.45 / 0.50 / 1.03 m) so Anfield's prior uses 105 m, marked `verified: false`.
- An early run of this evaluation was invalid because seeking in this file is not frame-exact; paint is now decoded
  sequentially from the first frame.

**Venue.** The clip is at Anfield, not the Amex: the scoreboard reads LIV 0-0 BHA with Liverpool listed first as the
home side, the home stands are red, and the Premier League 2026/27 graphics are Liverpool's. The geometry alone does not
separate the two grounds (no Amex camera has been fitted to compare). The fitted Anfield prior is recorded in
`venues.json`: C = (52.43, 103.61, 16.43) m (about 36 m behind the near touchline, 16 m up), roll -0.008 rad, k1 0.40,
focal 1.4 to 3.4 image widths, pan -0.62 to 0.49 rad, tilt 0.17 to 0.31 rad. `venue_guesses("anfield", frame)` returns
candidate starting homographies from it; on a labelled frame the best candidate lands within roughly 200 px of the far corners of the
calibrated pitch, close enough to start `camera.fit`.

**Runtime.** The whole clip (two rounds) takes about 3 to 4 minutes on one core once paint is extracted. A 60 s trial
of `solve_video` at 5 fps takes 63 s and fits 270 of 279 frames (9 interpolated); in-sample line error is 1.26 px
against 1.03 px for the per-frame chain.

Limitations: one clip and one camera position; interpolated frames and the Amex hypothesis are unchecked; the pitch
length is inferred from the fit, not surveyed.
