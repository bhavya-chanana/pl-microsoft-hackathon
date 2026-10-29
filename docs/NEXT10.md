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
pixels matched to the markings. A Kalman filter and an RTS smoother then run over every segment: they fill frames with
too few lines (flagged `interpolated`) and also replace the measured frames' pan, tilt and focal by the smoothed
value (`SolveOptions.rts="gaps"` keeps measured frames as fitted). `LiveCameraFilter(prior)` is the causal version for
live use. `solve_video(video, ...)` runs the whole chain (view filter, tracker, then `solve_match` for six rounds, each
re-matching paint under the previous cameras). `MatchCalibration.quality_at(t)` gives `calib_status` ("fitted",
"interpolated", "none"), `rmse_px`, `rmse_m`, `n_points` and `inlier_frac` for a video time.
`vision/align.py` maps video time to match time from anchors (kick-offs, goals, cards, or on-screen clock readings via
`Anchor.from_clock`) with a piecewise-linear clock and RANSAC. `vision/venues.json` holds priors for 12 grounds;
`update_venue_prior` merges a new fit into one. The harness is `python -m matchlens.vision.bundle_eval <clip>`
(`--length`, `--k1`, `--holdout block|random`); it writes `bundle_report.json` beside the clip.

The joint solve has no smoothness rows (at its old weights they changed nothing, `joint_smooth=False`); smoothing is
the RTS pass. Video time is the container's presentation timestamp: these recordings are variable frame rate, and
index / average fps is off by up to 4 s over ten minutes.

Synthetic check (known camera, 0.4 px noise, 150 frames): C within 0.5 m, every frame's pitch reprojection under
0.3 m, and `align` recovers a known clock map with one wrong anchor (`tests/test_vision_bundle.py`,
`tests/test_vision_align.py`).

**Liverpool-Brighton clip** (10,561 main-camera frames, paint pixels from every frame; a vertical strip covering 10% of
the image width is held out in each frame, so whole line segments are unseen; the bundle never sees the hand labels;
the per-frame baseline uses the left-out calibrations for the five anchor frames). Pitch model 105 x 68.

| | per-frame chain | shared camera, unsmoothed | shared camera + RTS (default) |
|---|---|---|---|
| (a) calibrated share of main frames | 76.9% (8,122) | | 79.7% fitted; 85% with interpolated |
| (b) held-out line error, median / p90 | 1.04 / 3.17 px, 0.080 / 0.46 m * | 1.44 / 4.11 px, 0.118 / 0.61 m | 1.40 / 4.00 px, 0.113 / 0.59 m |
| (b) fresh 8-parameter refit per frame on the unheld pixels | 1.08 / 3.63 px, 0.103 / 0.59 m | | |
| (b) surveyed landmarks (labels.json), median | 0.27 m, 2.7 px | 0.46 m, 5.0 px | 0.46 m, 5.4 px |
| (c) jitter of projected pitch corners, median / p90 | 13.6 / 103 px | 4.2 / 24 px | 1.8 / 11 px |
| (c) jitter of the image-centre footprint, median | 0.106 m | 0.101 m | 0.060 m |

\* the existing homographies were fitted to every pixel of the frame including the held-out strip, so that row is
flattered; the fresh 8-parameter refit is the fair per-frame comparison. The first round of this evaluation held out
random pixels and gave 1.29 against 1.07 px; neighbouring pixels are correlated, which favours the higher-dof model.

What this says:

- The bundle calibrates a few more frames, and its positions are far smoother: the rigid camera alone cuts jitter 3x
  and the RTS pass halves it again. The smoother does not cost accuracy (1.40 against 1.44 px unsmoothed).
- It does not fit the lines better than eight free parameters per frame: 1.40 against 1.08 px (0.113 against 0.103 m)
  on held-out segments, and 0.46 against 0.27 m on the surveyed landmarks (better on 6 of the 12 labelled frames both
  calibrate, clearly worse on frames 5,460 (2.4 m), 7,800 and 9,359; frame 7,020 is not calibrated by the bundle). Treat bundle positions as good to about half a metre,
  with most of that shared across frames (same C, k1, pitch length), so it does not average out over time.
- The formal standard errors of C (a few cm) are conditional on the markings model being right: moving the pitch
  length from 105 to 101 m moves C by 0.6 m. `centre_sigma` therefore carries a floor of (0.5, 0.5, 0.25) m.
- Pitch length. 105 fits best: landmarks 0.46 m at 105 m against 1.09 m at 101 m (the published Anfield figure) in
  the second round (the latter with k1 held at -0.1), and in the first round's random-pixel runs 0.45 m (105), 0.50 m
  (103) and 1.03 m (101), with held-out coverage 0.69, 0.61 and 0.49. Anfield's prior uses 105 m, `verified: false`.
- Lens term. k1 is free and comes out at +0.42 (pincushion), an unusual sign for a broadcast lens and it touches the
  +0.5 box bound in whole-video solves. Held at 0 or -0.1 the held-out error is 14 to 18% worse (1.60 and 1.65 px) and
  landmarks 0.49 and 0.53 m; C moves under 0.25 m. With k1 = -0.1 and L = 101 m the fit is much worse (landmarks 1.09
  m, coverage 0.48), so there is no degenerate valley in which a barrel lens and 101 m match 105 m. I read k1 as
  absorbing something else (pitch crown, tilt-focal coupling); it matters little in metres. The pitch length has not
  been made a free parameter.
- Interpolated frames (those the smoother fills, within 2 s of a measured one), line error against the frame's own
  paint, median (p90): measured frames 0.09 m; gap 0.4 s 0.08 m (0.49); 0.8 s 0.18 (0.55); 1.2 s 0.14 (0.36); 1.6 s
  0.16 (0.69); 2.0 s 0.23 (0.93).
- Coverage in `solve_video`: the per-frame tracker alone gives homographies for only 1,133 of the 2,222 frames the
  view filter calls main at 5 fps. Six rounds with a 3 s reach lift that to 84.1% fitted (89.1% with interpolated) of
  the frames the chain calls main, against 77.7% for the chain, and 74% of all 2,222 frames the view filter keeps.
  Two rounds with a 1 s reach, the first setting, reached 61% and 66%. The extra frames cost accuracy: median
  reprojection 1.90 px against 1.82. In-sample line error on the whole clip is 1.28 px / 0.105 m against 1.06 px /
  0.083 m for the chain's own homographies.
- Wall time of the whole clip at 5 fps (2,937 samples, 2,222 main): about 13 minutes on a busy machine (load 6 to 20):
  48 s decode and view filter, 554 s tracking (the per-frame tracker and the line extractor, 0.2 s per frame, the hot
  spot), 160 s for the seven solves. Backend-senior measured 7 minutes with the earlier two-round setting.

**Venue.** The clip is at Anfield, not the Amex: the scoreboard reads LIV 0-0 BHA with Liverpool listed first as the
home side, the home stands are red, and the Premier League 2026/27 graphics are Liverpool's. The geometry alone does not
separate the two grounds (no Amex camera has been fitted to compare). The fitted Anfield prior is recorded in
`venues.json`: C = (52.43, 103.61, 16.43) m (about 36 m behind the near touchline, 16 m up), roll -0.008 rad, k1 0.40,
focal 1.4 to 3.4 image widths, pan -0.62 to 0.49 rad, tilt 0.17 to 0.31 rad. `venue_guesses("anfield", frame)` returns
candidate starting homographies from it. Checked on seven labelled frames: the best of three guesses puts the pitch
centre 53 to 138 px (of 960) from the calibrated one and the first guess 81 to 242 px; the guesses score 0.3 to 0.65
on-line against 0.56 to 0.95 for the calibrated cameras. They are starting points for a local search (`camera.fit`),
not calibrations.

**Match clock of the clip.** The broadcast clock was read at every 14th frame (1,170 readings; 1,125 legible, the rest
hidden by graphics) with the macOS Vision text recogniser on the clock crop, run locally. The clip covers match
14:41.4 to 24:28. `align` on 161 readings gives one piece, slope 1.00003, residual rms 0.27 s and max 0.50 s, no
outliers, no edit points; ten injected wrong readings are rejected without moving the map, and 12 anchors reproduce it
to 0.3 s. Against index / average fps the same readings give slope 0.9965 and errors of +-1.8 s that drift with the
frame-rate wander, which is why times are presentation timestamps. The scoreboard goes 0-0, 1-0 between 100 s and 109 s
(clock 16:21 to 16:31, with the scoreboard hidden 103 to 107 s by the goal graphic) and 2-0 between 373 s and 379 s
(20:55 to 21:01); both goals are Liverpool's. Goal-minute anchors (17th and 21st) agree with the clock map within 3 s.
The map is `data/local/broadcast/LiverpoolBrighton/clockmap.json`; its anchors are in `anchors.json`.

Limitations: one clip and one camera position; the clock reader is a one-off tool for this broadcast graphic, not
shipped; the Amex hypothesis and the pitch length are unchecked against a survey; the overlay rule below is
calibrated on this one clip's player and banner and is untested on other broadcasts.

**Overlay frames.** The clip contains frames with the streaming player's controls (title bar, scrub bar, 182.6 to
186.2 s) and a broadcaster's lower-third banner (512.6 to 518.0 s) drawn over the picture; their hard edges read as
pitch lines. `views.look` now flags a frame as `overlay` when the bottom strip (95.5% to 99.5% of the height, read at
960 px wide) is dark (median brightness below 50) and busy (mean absolute brightness step per pixel, horizontal plus
vertical, at least 5): a scrim carrying text and icons. Such a frame is not main, whatever the shot around it votes
(`views.classify`, `bundle._track_video`, `next10.chain.ViewGate`). Over the 2,937 frames sampled at 5 Hz the rule
fires on 52 frames in three runs (80.2 to 81.0 s, a crowd close-up that was already excluded; 182.6 to 186.2 s;
512.6 to 518.0 s) and on no other frame, 47 of which had been main. The channel bumper near 170 s is already excluded
by the grass share (0.03). Re-solving the cached tracking with those 47 frames removed (Anfield prior, 6 rounds,
reach 3 s):

| | before | after |
|---|---|---|
| main frames solved | 2,222 | 2,175 |
| fitted / interpolated / none | 1,556 / 113 / 553 | 1,530 / 140 / 505 |
| fitted share of main frames | 70.0% | 70.3% |
| median reprojection | 1.866 px, 0.312 m | 1.854 px, 0.301 m |
| k1 | 0.488 | 0.493 |

Of the 47 overlay frames 28 had been fitted and 19 had no camera; the 28 fitted ones had rms 2.43 px (p90 2.59)
against 1.87 px (p90 2.54) for all fitted frames, so they fitted worse than typical. The change is
small by design: the frames are 2% of the clip. The 10% hold-out line error of `bundle_eval` was not re-run for this
change (the frames removed are 2% of the paint), so its effect is not measured.
