# Tactics model card (`backend/src/matchlens/tacticsml/`)

The learned model behind the Tactics Board. It scores a frozen moment of play (who gets the ball next, will
the team shoot, score, concede, lose the ball) and proposes small repositionings of the players. This card
says what the code does, what it was trained on, how it is evaluated and where it should not be trusted.

> **Numbers in section 6 come from one recorded run** (2026-10-26) and nothing else. Re-run
> `npm run tacticsml:eval` after retraining and replace them. A run on simulator data checks the pipeline; it
> says nothing about real football.

## 1. What it is

A two-part model over a 25-node graph of the moment (22 players, the ball, two goal anchors):

* a **critic**: a GATv2 graph network, made D2 invariant by frame averaging, with five heads: next receiver
  (11 possession-team slots plus "ball lost"), and `shot10`, `goal10`, `concede10`, `turn5`. A seed ensemble of
  critics gives the answer (mean logit) and the uncertainty (spread between members);
* a **proposer**: a conditional VAE that suggests per-player displacements for one team or both, given a target
  (`shot`, `retain`, `press`, `receiver`) and a set of locked players, on top of a frozen critic.

At serve time both run in numpy from exported weights (`infer_numpy.py`, `learned.py`); torch is only needed to
train, export and evaluate. `registry.get_tactical_model()` returns the learned model when exported weights
pass their hash check and the pitch-control + xT `BaselineModel` otherwise, so the product works without
weights.

### Compared with TacticAI

TacticAI (Wang et al., arXiv 2310.10553) is the design we started from: GATv2 on a player graph, D2 symmetry,
receiver and shot prediction, and a generative model for repositioning. The comparison below is how the
project plan describes it; check the paper before quoting it.

| | TacticAI | This model |
|---|---|---|
| Situations | corner kicks only | open play and set pieces (corner, free kick, throw-in, goal kick flagged where the source knows) |
| Input | one frame | five frames, 1 s apart, with velocities (a single frame for sources that only have one) |
| Ball | not a node | ball is a node (position and velocity) |
| Who is moved | one team | possession team, defence or both, with players lockable |
| Uncertainty | none | ensemble spread (member standard deviation) |
| Player quality | none | the graph accepts per-player profile features (see limitations: no current shard fills them) |
| Language | none | not part of this model: LLM personas propose ideas upstream (`agents/tactician.py`), the simulator and this critic check them |
| Retrieval | similar corners | cosine retrieval of similar real moments over the shards (`retrieval.py`) |
| Data | 7,176 Liverpool corners, private | open or user-downloaded data (section 2) |

## 2. Data sources and licences

Shards are built by `python -m matchlens.tacticsml.train shards --sources ...` into
`backend/data/local/tacticsml/shards/<source>/<match_id>.npz` (gitignored). Raw downloads go under
`backend/data/local/tacticsml/raw/` (gitignored). Nothing here is redistributed.

| Source | What it gives | Licence and terms | Notes |
|---|---|---|---|
| PFF FC World Cup 2022 (`pff`) | 64 matches, 30 Hz tracking plus events | Gated. The user requests it from PFF, accepts PFF's non-commercial research terms and unpacks it to `raw/pff` | Knockout matches (week >= 4) are the held-out test set. The parser was written against the published schema without the data to hand, so the first real run needs its label rates checked |
| DFL / IDSSE (`sportec`) | 7 Bundesliga / 2. Bundesliga matches, 25 Hz tracking plus events | CC BY 4.0, attribution required (Bassek et al., Sci Data 12, 195, 2025) | Two matches (`J03WMX`, `J03WR9`) are the test set. No carry events |
| SkillCorner open data (`skillcorner`) | 20 A-League matches (all 20 on disk since round 2), broadcast tracking at 10 fps plus dynamic events, with a per-player `is_detected` flag and the camera footprint polygon | MIT, copyright SkillCorner | Broadcast tracking: players off camera are extrapolated. Its footprints and detection flags are what the video-like view mask is fitted to (section 9.8) |
| StatsBomb 360 (`statsbomb360`) | Event data with one freeze frame of the visible players per event | StatsBomb open-data user agreement: research and non-commercial use, attribution to StatsBomb | Single frame, anonymous players, no velocities, so **no receiver labels**; Euro 2024 is the test set. Corner, free-kick, throw-in and goal-kick flags come from the pass type |
| Metrica sample (`metrica`) | 3 anonymised games, 25 Hz, events (games 1 and 2 CSV; game 3 EPTS tracking plus JSON events, added in round 2) | **CC BY-NC 4.0, attribution to Metrica Sports. NON-COMMERCIAL: Metrica data, and weights trained on it, must not ship in a commercial build without a separate licence from Metrica Sports.** | Parity / smoke source. Game 3 falls in the hash `test` split and is not part of any headline test set; games 1 and 2 are in the Next10 training set, so a commercial build must retrain without Metrica, and without StatsBomb 360, which is also non-commercial (`fit10 --sources sportec skillcorner`) |
| Synthetic (`synthetic`) | Simulator matches (`simulator/engine.py`), 1 Hz tracking | Generated by this repository | Pipeline and smoke source. Its labels come from the simulator's own events |

The xT grid used by the baseline is fitted from StatsBomb open event data (`train/xt_fit.py`).

**Splits.** Per match, 80 / 10 / 10 % train / val / test by a hash of `source:match_id`. Held-out test sets
override the hash: Euro 2024 (StatsBomb 360), the two IDSSE matches above and the PFF knockout games. Sources
with no held-out rule split by hash only, so their test split is a random 10 % of matches, not an unseen league.

**Labels** (`train/labels.py`, one definition for every source). Receiver: the player who receives the
possession team's next pass or keeps the ball on a carry; "lost" when the pass fails or the ball changes team
first; unknown otherwise. `shot10`, `goal10`, `concede10`: the possession team shoots, scores or concedes in the
next 10 s. `turn5`: possession is lost within 5 s. Labels are 0 / 1, and -1 where the window runs past the data
and nothing positive was seen; -1 labels are ignored in training and evaluation.

## 3. Architecture

**Features** (`features.py`). Coordinates are metres, centred on the pitch, mirrored so the possession team
attacks +x. Nodes: 0-10 possession team, 11-21 opponent (goalkeeper first, then by id, "pad" for missing
players), 22 ball, 23 own goal, 24 target goal. Per node: positions and velocities over `T = 5` steps (position
scaled by 50, last velocity by 8), a velocity-known flag, and `F = 7 + 2P` features: ball / goal flags,
goalkeeper, carrier, possession team, observed, and `P` optional profile values with presence masks (normalised
inside the model from training statistics). Edges (5): same team, opponent, involves ball, involves goal,
distance / 10, plus the relative position of the two nodes.

**Critic** (`train/model.py`, default `hidden 96, heads 4, layers 4, ffn 144, emb 64`; a few hundred thousand
parameters per member). Dense GATv2 attention over all 25 nodes with edge features, post-LayerNorm residual
blocks, attention pooling plus the carrier's state into a 64-d embedding, then the heads. The receiver head
scores each possession-team slot from its node state and the carrier, and shares a softmax with the "lost"
logit. The forward pass is specified op for op in the module docstring and reproduced in numpy by
`infer_numpy.py`.

**Frame averaging.** The pass is run on the four D2 reflections of the input (identity, mirror x, mirror y,
both) and the logits are averaged. Because the set of reflected inputs is the same whichever one is given, the
output is invariant to D2 by construction, and the evaluation measures it rather than assuming it. Training
uses one random reflection per sample as augmentation.

**Ensemble.** One critic per seed (default 0, 1, 2), mean of logits. The spread between members' probabilities
is the uncertainty shown to users. Whether that spread is informative is measured (section 5).

**cVAE** (`train/cvae.py`). Posterior `q(z_i | graph, target displacement, condition)` with an 8-d latent per
node; decoder `p(dpos | graph, z, condition)` on top of the frozen critic's node states, averaged over D2 with
the same latent. Conditions: target one-hot, team mode (attack / defence / both), receiver node, locked mask.
Displacements are passed through a smooth norm clamp with radius `min(top_speed * 3 s, max_move)`, and nodes
that are locked, off the moved team, the ball, the goals or padding get exactly zero. Training loss:
reconstruction of the real position ~3 s later (where the source has it, otherwise a denoising fallback),
KL, and a small term (0.05) asking the frozen critic to like the moved scene for the requested target.

**Retrieval** (`retrieval.py`). Cosine index over shard moments, using the critic's pooled embedding or a
handcrafted D2-invariant descriptor; a missing index returns no results.

## 4. Training and export

```bash
cd backend
uv run --group tactics-ml python -m matchlens.tacticsml.train shards --sources synthetic statsbomb360 --limit 20
npm run tacticsml:train -- --sources synthetic statsbomb360 --epochs 30 --seeds 0 1 2 --cvae   # gat_seed{S}.pt, ensemble.json, cvae.pt
npm run tacticsml:export                                  # numpy weights + manifest + retrieval index
npm run tacticsml:eval                                    # report.json + report.md
```

`npm run` scripts run from the repository root. `train` options: `--sources`, `--epochs`, `--seeds`, `--batch`,
`--lr`, `--device auto|mps|cuda|cpu`, `--cvae`, `--cvae-epochs`, `--max-steps`, `--ckpt-dir`. The critic uses
AdamW (lr 1e-3, weight decay 1e-2), warmup then cosine decay, gradient clipping at 1.0, and early stopping on
frame-averaged validation loss. The loss is cross-entropy for the receiver plus binary cross-entropy for each
outcome head, weighted (receiver 1, shot10 1, goal10 0.5, concede10 0.5, turn5 1), ignoring -1 labels; the
outcome biases start at the training base rates. Checkpoints go to `backend/data/local/tacticsml/ckpt/`, exported
weights to `backend/data/local/tacticsml/weights/` (with sha256 hashes that `weights_available()` checks).

`evaluate` options: `--ckpt-dir`, `--shards-root`, `--sources`, `--out-dir`, `--device`, `--max-samples`
(per source), `--baseline-max` (samples per source for the baseline comparison, default 1500), `--no-baseline`,
`--no-cvae`, `--cvae-samples`, `--cvae-k`, `--d2-samples`, `--seed`.

## 5. Evaluation protocol (`train/evaluate.py`)

Everything is computed on the `test` split, per source and per situation (`all`, and `open_play`, `corner`,
`free_kick`, `throw_in`, `goal_kick` where the shards carry the `set_piece` flag). Sources whose shards have no
flag (the tracking sources) only get `all`.

* **Receiver:** top-1 and top-3 accuracy among the 11 possession-team slots, on samples whose receiver is a
  known player. Ties count against the model. The chance level for the same samples is reported. "Ball lost"
  samples are scored separately as a binary head (AUC, Brier, ECE).
* **Outcome heads:** AUC, Brier, the Brier of always predicting the base rate (and the skill against it), ECE with
  15 equal-width bins, and the 15-row reliability table, on samples with a known label.
* **Ensemble spread vs error:** per head, Spearman and Pearson correlation between the across-member standard
  deviation and the absolute error (receiver: mean spread over slots against `1 - p(true receiver)`), and the error
  in each spread quartile. Needs at least two members. Error is partly a function of the probability itself, so a
  positive correlation is necessary for useful uncertainty but not proof of it; read the quartile table too.
* **D2 invariance:** on 200 test samples, every output is compared between the input and its three non-identity
  reflections. Reported for the ensemble and the members in float32 on CPU, and for member 0 in float64.
* **Learned vs baseline:** `BaselineModel` run on an evenly spaced sub-sample of each source (converted from the
  shard back to a frame; no history), next to the learned ensemble on the same samples. The baseline never
  proposes the carrier as a receiver, so the receiver comparison drops samples where the receiver is the carrier
  and removes the carrier from the learned candidates too. The baseline has no "lost" output.
* **cVAE:** (1) displacement error against the real future (needs `target_valid` samples) for the posterior mean,
  the prior mean-of-k and best-of-k, against standing still, in RMS metres per movable player; (2) for each
  target and for attack-only and both-team modes, the share of sampled proposals that raise the critic's target
  probability (shot up; turnover down for `retain`, up for `press`; the requested receiver's probability up), next
  to a control with the same displacement magnitudes in random directions; (3) a check that every proposal respects
  `min(top_speed * 3 s, max_move)` with and without the 8 m cap, and that locked and non-movable nodes stay still.
  The "raise" check uses the same family of critic that guided training, so it is optimistic; the simulator
  rollouts of the Tactics Board are the independent check.

Targets from the plan, flagged `PASS` / `FAIL` per scope (each source, and pooled real sources when there are
two or more). A target with no data or fewer than 30 labelled samples is `NOT_EVALUATED`, never a pass.

| Target | Threshold |
|---|---|
| Open-play receiver top-3 | >= 0.85 |
| Corner receiver top-3 | >= 0.70 (TacticAI reports receiver top-3 accuracy of 0.782 on 7,176 Liverpool corners with private tracking, arXiv 2310.10553; our corner samples are far fewer, so compare with care) |
| `shot10` AUC | >= 0.80 |
| `shot10` ECE (15 bins) | <= 0.03 |
| D2 equivariance error (max absolute change of any probability) | < 1e-5 |

## 6. Results

Numbers come only from a real run. Copy them from `report.json`; leave a row empty if it was not evaluated.
Record the checkpoints (`meta.members`), the shards (`meta.sources`) and the date next to the table.

Run: 2026-10-26. Three critics (`gat_seed0..2.pt`, 6 epochs each, about 8 minutes per seed on Apple MPS) and the
cVAE (4 epochs), trained on the train splits of synthetic (20 matches), StatsBomb 360 (47), Sportec/IDSSE (7),
SkillCorner (8) and Metrica (2). Scored on the held-out test splits below. Metrica is CC BY-NC, so these
checkpoints are for non-commercial use.

| Source | Situation | n | Receiver top-1 | Receiver top-3 | shot10 AUC | shot10 Brier | shot10 ECE | goal10 AUC | concede10 AUC | turn5 AUC | turn5 ECE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SkillCorner | all | 2,962 | 0.535 | 0.847 | 0.889 | 0.0518 | 0.0142 | 0.764 | (too rare) | 0.771 | 0.0299 |
| Sportec/IDSSE | all | 7,390 | 0.361 | 0.706 | 0.876 | 0.0385 | 0.0102 | 0.850 | 0.864 | 0.792 | 0.0482 |
| StatsBomb 360 | all | 22,173 | n/a | n/a | 0.847 | 0.0394 | 0.0046 | 0.833 | 0.949 | 0.752 | 0.0064 |
| StatsBomb 360 | corner | 56 | n/a | n/a | 0.509 | 0.2321 | 0.0821 | 0.782 | n/a | 0.461 | 0.0154 |
| Real, pooled | all | 32,525 | 0.421 | 0.754 | 0.860 | 0.0403 | 0.0044 | 0.822 | 0.801 | 0.762 | 0.0112 |
| Synthetic (pipeline check) | all | 18,407 | 0.339 | 0.665 | 0.880 | 0.0471 | 0.0070 | 0.951 | 0.727 | 0.727 | 0.0106 |

Chance receiver top-3 is about 0.28. StatsBomb 360 has no receiver labels (anonymous freeze frames), so the
pooled receiver figures come from SkillCorner and Sportec only. `concede10` positives are so rare (0.03 to 0.1%)
that its AUC is noisy.

| Check | Value |
|---|---|
| D2 equivariance error (ensemble, float32) | 1.2e-7 (float64 member: 2.2e-16) |
| Spread-error Spearman, `shot10` | 0.85 to 0.89 on every source: higher spread, larger error |
| Learned vs baseline, receiver top-3 (same sub-sample) | SkillCorner 0.853 vs 0.751, Sportec 0.781 vs 0.689 |
| Learned vs baseline, `shot10` AUC (same sub-sample) | SkillCorner 0.889 vs 0.839, Sportec 0.883 vs 0.819, StatsBomb 360 0.814 vs 0.803 |
| cVAE RMSE, no move / posterior / best-of-8 (m) | 7.29 / 6.10 / 6.05 |
| cVAE share of proposals raising the target (model / random control), `attack/shot` | 0.58 / 0.46 |
| cVAE speed clamp and lock check | respected |

Plan targets, honestly:

* **Open-play receiver top-3 >= 0.85: not met.** SkillCorner reaches 0.847, Sportec 0.706. No source flags open
  play, so all moments count, set pieces included.
* **Shot within 10 s, AUC >= 0.80 and ECE <= 0.03: met** on every real source (pooled 0.860 / 0.0044).
* **D2 equivariance < 1e-5: met.**
* **Corner receiver top-3 >= 0.70: not evaluated.** No real source has labelled corner receivers. TacticAI
  reports 0.782 on 7,176 Liverpool corners with private tracking, so there is no like-for-like comparison yet.
  The 56 StatsBomb 360 corners show the critic is weak on set pieces (shot10 AUC 0.51).

## 7. Limitations

* **Labels are what happened, not what was best.** The receiver is the next pass target; the outcomes are
  rates over 10 s or 5 s windows. A high shot probability describes frames that looked like frames before shots.
* **Proposals are correlational.** The cVAE learns where players tended to go, with a critic nudging it. It does
  not know why a team did something and cannot show that a move causes an outcome. The Tactics Board checks ideas
  with the simulator, which is a model of football too.
* **Sources differ a lot.** StatsBomb 360 has one frame of the visible players, no velocities and no receiver
  labels; the broadcast tracking of SkillCorner is noisy; PFF needs a manual download and its parser has not been
  run against the data; the synthetic source follows the simulator's own rules. A model trained on a mixture can
  look good on a source it shares assumptions with. Compare per source.
* **Few corners.** Corner and other set-piece flags exist only for the synthetic and StatsBomb sources. Corner
  metrics will rest on small counts, which the report shows in `n` and refuses to score below 30.
* **Player-quality features are supported, not populated.** The graph and `learned.py` accept profile values
  per player, but none of the current source builders fills them, so shards carry `profile_keys = []` until they do.
* **Uncertainty is ensemble spread** from a few seeds. It is not a calibrated interval, and a small ensemble can
  agree with itself and be wrong.
* **No post-hoc calibration.** Calibration is whatever training gives; the report measures it.
* **One league per source.** Results do not carry over to leagues, tracking vendors or eras not in the data.
* **Real names.** Real club and player names and data are allowed product-wide (BRD section 9.3 amendment);
  the licences in section 2 apply to anything derived from those datasets.

## 8. Intended use

Decision support inside the Matchlens Tactics Board: ranking ideas, showing which players matter for a
moment, and finding similar real moments. Not for betting, for judging or ranking individual players, for
automatic decisions about people, or as evidence about real matches without the checks above. Outputs should
carry their uncertainty and the model version (`ScoreResult.model_version`).

## 9. Next10 (rounds 1 and 2): what happens in the next 10 seconds

Next10 extends the critic of section 3 with new heads, team conditioning and view-mask training, adds a joint trajectory
forecaster, and ships a numpy inference path for the live service. The deployed Tactics Board path (`learned.py`,
`registry.get_tactical_model()`, `infer_numpy.py`, `weights/`, `ckpt/`) is untouched; every Next10 artefact lives under
`backend/data/local/tacticsml/next10/`. Old checkpoints still load into the new class (`load_critic`, `strict=False`, FiLM off).

### 9.1 Heads and losses (`train/heads.py`, `train/next10.py`)

The backbone is the section 3 GATv2 critic (25 nodes, D2 frame averaging). Let `z` be the critic embedding, `h_i` the node states,
`c` the carrier vector, `q_i` the receiver softmax over the 11 possession-team slots plus "lost" (index 11).

* **ball_zone10.** The ball position at t+10 s on a 16 x 12 grid over the 105 x 68 m pitch (cells 6.56 x 5.67 m, canonical frame).
  Per-cell logits come from a query MLP on `[z, h_ball, cell features]` (cell centre, cell minus ball, distance, distance to goal), so
  the grid head shares weights across cells. Target: a Gaussian bump with sigma = 1 cell around the true cell, normalised,
  `t_c = exp(-d_c^2 / 2) / sum_c' exp(-d_c'^2 / 2)`. Loss: soft cross-entropy `-sum_c t_c log softmax(l)_c`. The head is D2 *equivariant*:
  `p(g x) = flip_g p(x)` (columns for the x mirror, rows for the y mirror); averaging the four reflections in logit space makes it exact.
* **poss10.** `P(possession team has the ball at t+10 s)`, binary cross-entropy, logit bias initialised at the base rate.
* **action_next.** Softmax over {pass, carry, shot, other} for the next on-ball action; cross-entropy; labels only where the source has
  carry events (SkillCorner among the tracking sources), -1 elsewhere and then ignored.
* **Receiver-conditioned shot (TacticAI-style marginalisation).** Direct head `l_d` (the existing shot10 logit). Per outcome
  `i in {1..11, lost}` a residual `delta_i` from `[h_i, c, z]` gives `P(shot | i) = sigma(l_d + delta_i)`, with slot 2 and the lost branch
  zero-initialised (the decomposition starts equal to the direct head). Then

      P(shot) = sum_i P(shot | recv = i) P(recv = i)        (i over the 11 slots and the ball-lost outcome)

  Training uses both a teacher-forced conditional term (BCE of `P(shot | i*)` with the observed receiver `i*`) and the marginal BCE of the sum.
  The served shot probability is `blend` = the mean of the direct and the decomposed value (`manifest.serve.shot`; chosen from validation).
* **Total.** `masked_losses` of section 4 (receiver, shot10, goal10, concede10, turn5) plus `w_zone L_zone + w_poss L_poss + w_act L_act +
  w_cond L_cond + w_marg L_marg` (`heads.NEXT10_WEIGHTS`). Labels of -1 are ignored in every term.

### 9.2 Open-play filter, FiLM, view-mask augmentation

* **Open play** (`data10._open_play`): no set-piece flag on the sample and at least 10 s since the last dead ball. Where the feed has no
  ball state (Metrica and SkillCorner through kloppy) a dead ball is inferred (`infer_alive`): ball off the pitch, or ball nearly
  still (< 0.5 m/s) for 1.5 s. All headline targets are scored on open play; the report also shows "all".
* **FiLM on the team (`Next10GAT._post_layer`).** For possession team `a` and opponent `b`,
  `c = [E[a] + S(style_a * mask_a), E[b] + S(style_b * mask_b)]` and in every GAT layer
  `h <- (h (1 + gamma_l) + beta_l) * node_mask`, `(gamma_l, beta_l) = W_l c`, `W_l` bias-free and zero-initialised. `style` is a 14-value
  z-score vector from the league-data playbooks (`leaguedata/kb/playbooks.json`, latest season of the club) where the club is there; otherwise the
  learned embedding alone. Id 0 is "unknown team": `E[0] = 0`, `style[0] = 0`, so `c = 0` and FiLM is exactly the identity. Training
  replaces each id by 0 with probability 0.3 so that path is trained and an unseen club gets the plain critic. Honest scope: this is
  team-name + latest-season style, not a true per-season conditioning; only clubs seen at least 1000 training samples get an id.
* **View-mask augmentation (`train/augment.py`).** A broadcast camera is a cone: a random camera 8-25 m outside a touchline near the ball,
  looking within a few metres of it, with a 35-65 degree field of view. Players outside the cone get `observed = 0`, their position is
  displaced by static noise of sigma in [0.5, 1] m (ball: 2x) plus per-step jitter, velocities follow, edge features are recomputed; goal
  anchors stay known; at least 4 players remain observed. Applied to 50% of training samples in the critic runs (`Fit10Config.view_mask_p`; the evaluation masks 100% of the inputs).
  **Round 2 adds `mode = "footprint"` (`augment.video_config`), fitted to real broadcast tracking** (section 9.8): the visible region is a real SkillCorner
  camera footprint (`image_corners_projection`, 40,000 polygons stored relative to the ball in `next10/footprints.npz`, picked among those recorded with the ball
  near where the sample's ball is and mirrored into its quadrant), scaled by 1.3 about the ball so that the seen-player count matches; a player inside is missed
  with probability 0.03; an unseen player is **removed** (node mask 0, features and positions zero, an unseen receiver becomes an unknown label) with probability 0.9, as the video
  pipeline does, and imputed as in round 1 otherwise; the carrier is never removed; seen players and the ball get independent position noise
  (sigma ~ U(0.08, 0.2) m per step, the measured 5 Hz jitter of video tracks); with probability 0.06 the ball sits at the centroid of the seen players (the pipeline's
  behaviour when the ball is lost). The round-1 behaviour is still `mode = "cone"` and is the default.

### 9.3 Trajectory forecaster (`train/trajectory.py`)

A mixture-density set-attention model. Tokens are (node, time) pairs over the 5 s history; axial blocks alternate agent attention
(25 nodes, additive bias from an MLP of the relative position) and time attention. The last-time tokens are copied for K = 6 modes, a learned
mode embedding is added and two agent-attention blocks make each mode one joint scenario for the 22 players and the ball. The head emits, for
horizons h = 1..10 s,

    mu_{k,i,h}    = pos_i + v_i h + (1 + h) r_{k,i,h}                 (constant velocity plus a learned residual)
    sigma_{k,i,h} = (0.3 + softplus(s_{k,i,h})) (1 + 0.3 h)
    pi_k          = softmax_k( mean over real nodes of Linear(token_{k,i}) )

Loss (winner-take-all / hard EM): with `cost_k` the mean Euclidean error of mode k, `k* = argmin cost_k`,
`L = 0.3 (cost_{k*} + 0.05 mean_k cost_k) + CE(pi, k*) + 0.1 NLL(mu_{k*}.detach(), sigma_{k*})`. D2: the network runs on the four reflections,
`mu` is mapped back and averaged mode-wise, so `mu(g x) = g mu(x)` and `pi(g x) = pi(x)` exactly.

### 9.4 Metrics and baselines (`train/evaluate10.py`)

* Binary heads: AUC, Brier (and the base-rate Brier `p(1-p)`), ECE with 15 bins, reliability bins.
* ball_zone10: **headline = probability mass within 10 m of the true ball position at t+10 s**, `sum_c p_c 1[|centre_c - ball_10| <= 10 m]`
  (decision of round 2: a cell-accuracy target needs the ball to land in one of five cells, which a 30 to 40 m move in 10 s makes the wrong
  question; the mass is a proper summary of a density). It is compared with the best baseline's mass (a model-minus-baseline gain > 0 passes);
  top-1/3/5 cell accuracy, hard-cell NLL, ranked probability score along each axis,
  `RPS = 1/(R-1) mean_n sum_{r<R} (F_r - O_r)^2` with `F` the predicted and `O` the observed cumulative distribution over the R ordered bins
  (R = 16 for x, 12 for y), and the expected distance (m) between the predicted distribution and the true ball position stay as secondary
  columns. Baselines: uniform; the static prior (train-split histogram); persistence (Gaussian around the current ball position, sigma tuned on train).
* poss10: Brier against the train base rate. Receiver: top-1/3 against chance; lost-outcome AUC. Shot: against the pitch-control + xT
  `BaselineModel`. Ensemble spread against error (Spearman).
* Trajectories: minADE / minFDE (best of K = 6 per player and for the ball), minJADE / minJFDE (one mode for all players, averaged over players),
  the most probable mode, constant velocity, and standing still. **Headline (round 2): minFDE@10s relative to standing still** (the last position),
  because constant velocity is a straw man at 10 s (players turn: 23 m against 15 m for standing still); the comparison with constant velocity and the
  like-for-like single guess (most probable mode against standing still) are secondary rows.
* D2 check: largest output change over the four reflections, in float32 and float64.
* Uncertainty (round 2): 95% percentile intervals from a **cluster bootstrap by match** (matches resampled with replacement, 1,000 draws, no
  interval below 3 matches) for AUC, ECE, Brier skill, poss10 Brier skill, zone mass and the trajectory ratios (pooled FDE sums per match); and the
  **seed spread**: every ensemble member scored alone on the same samples (mean, sd, min, max).
* Ablations on the same samples: FiLM on / off / teams set to unknown; view-mask augmentation off / round-1 cone / video footprint replay, on clean,
  cone-masked and video-masked test inputs; direct vs receiver-decomposed shot. Targets are marked PASS / FAIL, or NOT_EVALUATED below 30 labelled samples.

Targets (round 2 wording): open-play shot10 AUC >= 0.85; shot10 ECE <= 0.03; ball_zone10 mass within 10 m above the best baseline (round 1 asked
for top-5 cell accuracy >= 0.6, which failed at 0.11 and is now an INFO row); minFDE@10s at least 25% better than standing still (round 1: than
constant velocity; both are in the report); D2 error < 1e-5. (The last is checked in float64 for positions in metres: a float32 value of 50 m has a 4e-6 spacing, so 1e-5 is not
meaningful there; the float32 numbers are in the report.)

### 9.5 Generative "play it out" (`train/diffusion.py`)

A conditional DDPM over the 10 s future of 23 agents,
`x_0 = (fut - pos_T) / 20 m`, cosine schedule with S = 100 steps, epsilon-prediction loss `E ||eps - eps_theta(x_s, s, c)||^2`, ancestral sampler,
an axial-attention denoiser, and D2 frame averaging of `eps_theta` (the law of the samples is then D2 equivariant). Unit tests cover the
schedule, the loss and its masking, equivariance, and that 150 steps on one scene bring the samples closer to the truth than the untrained model.
Round 1 shipped the skeleton only; round 2 trained it once on the real tracking (first result in section 9.8, "Diffusion against the MDN"). It is a design with a first, unconverged result, not a replacement for the K = 6 forecaster. Guidance and DDIM sampling are still not done.

### 9.6 Data, commands and files

Training data: the tracking sources need future frames, so `sportec`, `skillcorner` and `metrica` shards were rebuilt into
`next10/shards/` (new keys: `fut_pos` f16 (n, 10, 25, 2) canonical absolute future positions, `fut_valid`, `since_dead`, `poss10`, `action_next`,
`poss_team`, `opp_team`, `period`). The StatsBomb 360 shards of section 2 are reused unchanged (`--legacy-shards`): they have no future
frames, so they train only the old heads. Raw files are read from `MATCHLENS_TACTICSML_RAW`; downloads now need `MATCHLENS_TACTICSML_DOWNLOAD=1`.

    cd backend
    uv run --group tactics-ml python -m matchlens.tacticsml.train shards --sources sportec skillcorner metrica --root data/local/tacticsml/next10/shards
    uv run --group tactics-ml python -m matchlens.tacticsml.train.fit10 --legacy-shards <statsbomb360 shards> --seeds 0 1 [--no-film] [--no-view-mask --tag _noaug]
    uv run --group tactics-ml python -m matchlens.tacticsml.train.trajectory --epochs 14
    uv run --group tactics-ml python -m matchlens.tacticsml.train.evaluate10 --legacy-shards <statsbomb360 shards>   # -> report_next10.md/json
    uv run --group tactics-ml python -m matchlens.tacticsml.train.export10 --shot-mode blend                           # -> next10/weights/
    # serving: from matchlens.tacticsml.infer_next10 import infer_next10   (numpy only; MATCHLENS_NEXT10_WEIGHTS overrides the directory)

Tests: `backend/tests/test_next10_model.py` (D2 invariance and equivariance, view-mask correctness, FiLM unknown-team identity and zero init,
old-checkpoint loading, loss sanity, numpy-vs-torch parity, export / serve round trip, tamper refusal, DDPM).

### 9.7 Results of the round-1 run (kept as it was; round 2 is section 9.8)

Two critic seeds (8 epochs, about 12.7k steps each) and one forecaster seed (14 epochs), trained on Apple MPS in under 30 minutes each; evaluated on the held-out test matches (38,920 samples, 1 Hz and overlapping, so the effective sample size is far smaller). StatsBomb 360 contributes no future labels. Differences of a few thousandths in AUC between ablations are within what one more seed would change; no confidence intervals were computed.

| target | scope | value | status |
|---|---|---|---|
| open-play shot10 AUC >= 0.85 | pooled/open_play | 0.865 | **PASS** |
| open-play shot10 ECE <= 0.03 | pooled/open_play | 0.003 | **PASS** |
| open-play shot10 AUC >= 0.85 | sportec/open_play | 0.888 | **PASS** |
| open-play shot10 ECE <= 0.03 | sportec/open_play | 0.005 | **PASS** |
| open-play shot10 AUC >= 0.85 | skillcorner/open_play | 0.893 | **PASS** |
| open-play shot10 ECE <= 0.03 | skillcorner/open_play | 0.017 | **PASS** |
| open-play shot10 AUC >= 0.85 | statsbomb360/open_play | 0.847 | **FAIL** |
| open-play shot10 ECE <= 0.03 | statsbomb360/open_play | 0.005 | **PASS** |
| ball_zone10 top-5 cell accuracy >= 0.6 | pooled/open_play | 0.110 | **FAIL** |
| ball_zone10 top-5 cell accuracy >= 0.6 | sportec/open_play | 0.121 | **FAIL** |
| ball_zone10 top-5 cell accuracy >= 0.6 | skillcorner/open_play | 0.080 | **FAIL** |
| player minFDE@10s >= 25% better than constant velocity (K=6 best-of vs 1 CV) | sportec/open_play | 0.739 | **PASS** |
| player minFDE@10s >= 25% better than constant velocity (K=6 best-of vs 1 CV) | skillcorner/open_play | 0.747 | **PASS** |
| player minFDE@10s >= 25% better than constant velocity (K=6 best-of vs 1 CV) | pooled/open_play | 0.741 | **PASS** |
| D2 error < 1e-5 (float32, critic probabilities) | test open play | 0.000000 | **PASS** |
| D2 error < 1e-5 (float64, critic probabilities) | test open play | 0.000000 | **PASS** |
| D2 error < 1e-5 (float64, trajectory mu in m / pi / log sigma) | test open play | 0.000000 | **PASS** |

| open play | n | shot10 AUC | Brier (base rate) | goal10 AUC | turn5 AUC | poss10 Brier (base) | receiver top-1 / top-3 | zone top-1 / 3 / 5 |
|---|---|---|---|---|---|---|---|---|
| sportec | 8,760 | 0.888 | 0.0376 (0.0441) | 0.862 | 0.813 | 0.1593 (0.1831) | 0.342 / 0.683 | 0.034 / 0.081 / 0.121 |
| skillcorner | 3,625 | 0.893 | 0.0534 (0.0692) | 0.790 | 0.746 | 0.1972 (0.2032) | 0.535 / 0.825 | 0.019 / 0.048 / 0.080 |
| statsbomb360 | 21,746 | 0.847 | 0.0388 (0.0440) | 0.836 | 0.751 | n/a (n/a) | n/a / n/a | n/a / n/a / n/a |
| pooled | 34,131 | 0.865 | 0.0401 (0.0468) | 0.820 | 0.768 | 0.1704 (0.1893) | 0.410 / 0.733 | 0.029 / 0.071 / 0.110 |

Shot10 against the pitch-control + xT baseline on an even subsample of 1,498 open-play samples: AUC 0.872 vs 0.837, Brier 0.0374 vs 0.0426 (base rate 0.0433).

ball_zone10, pooled open play (n = 12,160): model top-5 0.110, NLL 4.875, RPS x/y 0.1236 / 0.1715; best baseline (persistence) top-5 0.077, NLL 5.103; uniform top-5 0.016, NLL 5.257.

Trajectories, pooled open play (SportEC + SkillCorner, n = 6,080): player minADE 3.96 m, minFDE 5.99 m (constant velocity 23.15 m, standing still 15.14 m); most-probable-mode FDE 13.38 m; joint minJFDE 7.59 m vs constant velocity 23.14 m; ball minFDE 12.62 m vs 31.99 m. With view-masked inputs minFDE is 6.02 m.

Ablations (pooled / sportec / skillcorner shot10 AUC on up to 6,000 open-play samples per source):

| variant | input | pooled | sportec | skillcorner |
|---|---|---|---|---|
| main ensemble (2 seeds) | clean | 0.876 | 0.886 | 0.893 |
| main ensemble | view-masked | 0.876 | 0.885 | 0.894 |
| teams set to unknown | clean | 0.878 | 0.888 | 0.898 |
| FiLM on, seed 0 | clean | 0.878 | 0.887 | 0.898 |
| FiLM off | clean | 0.877 | 0.889 | 0.895 |
| view-mask aug on, seed 0 | clean | 0.878 | 0.887 | 0.898 |
| view-mask aug on, seed 0 | view-masked | 0.878 | 0.885 | 0.898 |
| view-mask aug off | clean | 0.876 | 0.886 | 0.892 |
| view-mask aug off | view-masked | 0.872 | 0.881 | 0.887 |

Receiver decomposition: direct head AUC 0.865, decomposed 0.865, served blend 0.865 (pooled open play).

D2 (200 test samples, four reflections): critic probabilities change by at most 1.19e-07 in float32 and 3.33e-16 in float64; trajectory outputs 7.63e-06 (float32, metres) and 1.42e-14 (float64).

How to read these results.

* **Passed:** pooled and SportEC / SkillCorner open-play shot10 AUC and ECE, trajectory minFDE against constant velocity, D2. **Failed:** StatsBomb 360 shot10 AUC (0.847, just under 0.85) and ball_zone10 top-5 (0.110 against 0.6). The 0.6 target is probably not reachable: the ball moves about 30 to 40 m in 10 s and five cells cover about 190 m2 of a 7,140 m2 pitch. The head does beat every baseline (uniform 0.016, static prior 0.060, persistence 0.077 top-5; lower NLL and RPS), so it learned something, but it is a smooth density, not a cell classifier.
* **ECE is easy at a 4 to 7% base rate.** Brier against the base rate and AUC are the more informative numbers; shot10 Brier improves on the base rate by 14% pooled.
* **The 25% trajectory target is weak.** Constant velocity is worse than standing still at 10 s (23.2 m against 15.1 m). The best-of-6 per player (6.0 m) is generous, and the most probable mode (13.4 m) is only 12% better than standing still: the mode probabilities carry little information (the mode cross-entropy ended at 1.58 against ln 6 = 1.79). The joint minJFDE (7.6 m) is the fairer multi-modal number. Use the forecaster as a set of plausible scenarios, not as a point prediction.
* **No ablation shows a measurable gain.** FiLM on / off and teams unknown differ by at most 0.002 pooled AUC; only 11.6% of test samples have a known possession team (the other clubs are not in the training vocabulary or the playbook KB), so FiLM is barely exercised and nothing here says whether it would help with more clubs. View-mask augmentation helps on view-masked inputs by 0.006 AUC pooled (0.878 against 0.872) and costs nothing on clean input, which is the intended direction but small and from one seed. The receiver decomposition is neutral (0.865 direct and decomposed), as designed: it adds per-receiver explanations (what the live UI draws) without hurting the probability.
* **Masking the test input changes almost nothing for any model** (0.876 to 0.876 for the main ensemble). The headline shot10 signal sits in the ball and carrier, which the camera cone always keeps, and the masking only degrades far players. This is not a test of real broadcast detection noise.

Limitations of this round. The training data is small and in effect three sources: SportEC (3 test matches, no validation match), SkillCorner (8 of 20 matches were on disk, the rest were not downloaded) and Metrica (two matches, no test split; CC BY-NC); StatsBomb 360 has no future frames, so it trains the old heads only. Samples are 1 Hz and overlapping, so test errors have wide intervals, and no seed-to-seed or bootstrap interval was computed. `action_next` exists for SkillCorner only (accuracy 0.695 against 0.600 for the majority class, NLL 0.717). poss10 beats the base rate on SportEC and SkillCorner (Brier 0.159 and 0.197 against 0.183 and 0.204). The FiLM team vocabulary has 15 clubs. The open-play filter uses an inferred dead ball for Metrica and SkillCorner. The live service will feed a camera-limited, 5 Hz, video-derived view, which none of these data sources reproduces; the view-mask augmentation is a geometric stand-in for it. During the round an unintended 124 MB download of the Metrica raw files happened (the raw path was not exported in that shell); it was deleted, and downloads are now refused unless `MATCHLENS_TACTICSML_DOWNLOAD=1`.

### 9.8 Round 2: domain gap to video, more data, intervals, and a retrain

Every number below comes from `backend/data/local/tacticsml/next10/{r2/report_next10.md, r2_pre/report_next10.md, domain_gap.json, ddpm_report.json}` (this run).

**More data (one approved download).** The owner approved one download of the remaining SkillCorner and Metrica open tracking, through the cached, paced
fetchers with `MATCHLENS_TACTICSML_DOWNLOAD=1` (no 403/429 was met). Twelve SkillCorner matches (1996435 ... 2017461; 1,156.7 MB) and Metrica `Sample_Game_3`
(55.2 MB; EPTS tracking and JSON events, read through kloppy by the new `metrica.iter_match_epts`) were fetched: 1.21 GB in all. Shards before / after: metrica
2 / 3, skillcorner 8 / 20, sportec 7 / 7 (17 / 30 shards); samples 72,342 / 110,553 (SkillCorner 36,667 / 70,079: train 27,738 / 55,343, val 4,349 / 7,093, test
4,580 / 7,643). The hash split is unchanged for existing matches. The new SkillCorner test match is 2007721 (so SkillCorner has two test matches, not one), and
Metrica game 3 is a `test` match that is not in any headline test set. **Licence:** Metrica data is CC BY-NC 4.0, non-commercial (section 2); the weights trained here include games 1 and 2 and so must not ship in a commercial build.

**What the video looks like (domain gap).** `train/domain_gap.py` measures the frames written by the video pipeline (`frames_*.ndjson`, 5 Hz, calibrated frames only) against the
training sources.

| quantity | video, test:lb (per-frame calibration) | video, 6.6-minute clip | SkillCorner broadcast tracking | SportEC / Metrica |
|---|---|---|---|---|
| players seen per frame (mean; p10 / p50 / p90) | 14.1; 6 / 15 / 18 | 13.1; 6 / 14 / 19 | 14.4 detected in the footprint; 10 / 15 / 19 | 22 |
| ball seen | 93% | 82% | 94% detected | 100% |
| detection inside the footprint | | | 97.3% | |
| median / p90 of the second difference \|x(t+1) - 2x(t) + x(t-1)\| at 5 Hz | 0.40 / 1.11 m | 0.34 / 1.09 m | 0.042 / 0.112 m (extrapolated players 0.022 m) | 0.036 / 0.108 m (SportEC), 0.041 / 0.106 m (Metrica) |
| speed p50 / p99; share of steps above 12 m/s | 2.7 / 10.1 m/s; 0.3% | 2.4 / 14.0 m/s; 2.2% | 2.0 / 6.8 m/s; 0.002% | 1.5 / 6.2 m/s; 0.005% |
| track run length (samples), p50 / p90 | 12.5 / 40 | 8 / 33 (1,005 tracks) | 38 / 124 | whole period |

* **Seen count matches.** Partial view per se is not the gap: the video sees as many players as SkillCorner's own camera footprint (a polygon that
  covers 40% of the pitch on average, 2,878 m2) and the round-1 cone already produced 15.0 seen players. What was different is that unseen players are *absent* in the
  video pipeline, not imputed with 0.5 to 1 m noise.
* **Jitter is the real difference.** Video tracks are ten times noisier at 5 Hz: a second difference of 0.34 to 0.40 m is white position noise of about
  0.14 m (median of the norm = 2.88 sigma), against 0.04 m in every research source. The model reads positions 1 s apart, so 0.14 m moves a velocity by about 0.2 m/s: mild.
  Track fragmentation and ID switches are visible in the clip (median run 1.6 s, 2.2% of steps above 12 m/s). The calibration error of
  about 0.5 m that is correlated across frames (phd-student's measurement, plus about 0.2 m on interpolated frames) is not in the jitter and is not modelled by the
  augmentation; a shift common to all players is nearly invisible to a model of relative geometry, but it does move players relative to the goals and touchlines.
* **Team labels were the biggest problem in the first frames file** (test:lb: 'home' absent in 131 of 257 frames, 15 to 19 'away' per frame including staff outside the pitch,
  so the model saw about 11 players of one team). It was reported to the backend; the later frames files have both teams (home 1,653 / away 1,958 detections in test:lb). Predictions on the earlier file are not evidence of anything.
* **Predictions on video.** With the v2 weights (`predict.Predictor`): test:lb shot10 mean 0.133 (39 predictions) against 0.052 on open-play test samples, possession 0.727 against 0.735, zone entropy 4.66 against 4.88 nats
  (a sharper density); the clip: shot10 0.096, possession 0.711, entropy 4.67 (247 predictions). The 60 s clip is one passage of play, so the gap in shot10 is not
  evidence of miscalibration either way. **Bundle against per-frame calibration on test:lb** (39 matched seconds): shot10 differs by 0.024 median (mean 0.052, p90 0.134), possession by 0.016
  median, the expected ball position moves 4.0 m median (p90 19.6 m), and the possession team differs in 26% of the seconds. The two runs also differ in tracker ids and team clustering, so
  this is an upper bound on the effect of the calibration alone; most of it is the nearest-player possession rule flipping.

**Augmentation fitted to this** (`augment.video_config`, section 9.2). Seen-player counts on open-play test samples (mean; p10 / p50 / p90): SkillCorner detected in footprint 14.4; 10 / 15 / 19; round-1 cone 15.0; 9 / 16 / 20;
footprint replay (polygon scale 1.3; the grid search prefers 1.35, a 0.3-player difference) 14.1; 7 / 15 / 20; video test:lb 14.1; 6 / 15 / 18. Critics were retrained with it (3 seeds, 12 epochs, 60% of the samples masked,
`fit10 --view-mode video`) and so was the forecaster (`trajectory --view-mode video`) on the enlarged data, with ablations trained on the same data: no mask and the round-1 cone.

| shot10 AUC, up to 3,000 open-play test samples per source (pooled) | clean input | round-1 cone mask | video mask |
|---|---|---|---|
| no mask augmentation (seed 0) | 0.877 | 0.871 | 0.859 |
| round-1 cone augmentation (seed 0) | 0.875 | 0.875 | 0.857 |
| video footprint augmentation (seed 0 / 3-seed ensemble) | 0.876 / 0.876 | | 0.870 / 0.872 |

Video-masked input costs the un-augmented models 0.018 AUC (0.877 to 0.859), 0.041 in poss10 AUC (0.689 to 0.648) and 0.096 in receiver top-3 (0.780 to 0.684); the cone augmentation does not help against it (0.857). The video augmentation recovers 60 to 70% of the shot10 loss
(0.870 for one seed, 0.872 for the ensemble), costs nothing on clean input and also moves the video-masked poss10 AUC to 0.684 and receiver top-3 to 0.708 (clean 0.773 to 0.780): the receiver is often outside the view and that loss is
inherent. Forecaster minFDE at 10 s on video-masked input: 6.18 m with the retrained model against 6.60 m for the round-1 model (clean 5.85 m, standing still 15.95 m). One seed per ablation row: differences under about 0.003 AUC are noise (seed sd 0.0004 to 0.004).

**Intervals and seeds** (test split, 3-seed ensemble, open play; 95% percentile interval by cluster bootstrap over test matches, 1,000 draws, no interval below 3 matches):

| scope (matches) | shot10 AUC | shot10 ECE | shot10 Brier skill | poss10 Brier skill | zone mass within 10 m (model) | gain over the best baseline | minFDE vs standing still |
|---|---|---|---|---|---|---|---|
| pooled (16 incl. 11 StatsBomb 360) | 0.865 [0.845, 0.883] | 0.0028 [0.0025, 0.0087] | 0.143 [0.111, 0.174] | 0.102 [0.049, 0.152] | 0.066 [0.064, 0.068] | +0.019 [0.018, 0.021] | 0.615 [0.594, 0.634] (5 matches) |
| SportEC (3) | 0.889 [0.842, 0.919] | 0.0044 [0.0030, 0.0152] | 0.146 [0.103, 0.184] | 0.132 [0.061, 0.178] | 0.066 [0.064, 0.068] | +0.019 [0.018, 0.020] | 0.595 [0.584, 0.601] |
| SkillCorner (2) | 0.877 (no interval) | 0.0125 | | | 0.066 | +0.019 | 0.642 |

Seed spread of single members (3 seeds): pooled shot10 AUC 0.8635 sd 0.0004, SportEC 0.8867 sd 0.0017, pooled zone mass 0.0661 sd 0.0001, pooled poss10 AUC 0.693 sd 0.003. Two things to read carefully:
the intervals are by match and SportEC has three test matches, so its interval is wide (0.84 to 0.92); the pooled interval is dominated by StatsBomb 360 (11 matches), which has no future labels;
SkillCorner still has two test matches, too few for an interval. The StatsBomb 360 shot10 AUC (0.849) is below 0.85 inside its interval.

**Did the headline change?** No. Round-1 models on the enlarged test set (`r2_pre`): pooled shot10 AUC 0.864, zone gain +0.016, minFDE vs still 0.609. Retrained models: 0.865, +0.019, 0.615: inside every interval. The SkillCorner AUC in the
round-1 table (0.893) was one match; with the second test match it is 0.880 for the round-1 models and 0.877 for the retrained ones. Doubling the SkillCorner training data did not move the numbers, which
says the limit is the label noise and the test size, not the amount of training data.

**Targets in the new wording** (r2 report): shot10 AUC and ECE PASS pooled, SportEC, SkillCorner; StatsBomb 360 AUC 0.849 FAIL (as in round 1); ball_zone10 mass within 10 m beats the best baseline by +0.019 (PASS; top-5 cell accuracy 0.115, INFO);
minFDE@10s vs standing still 0.615 pooled (PASS against 0.25; most probable mode against standing still only 0.124); D2 error < 1e-5 PASS.

**Diffusion against the MDN (first result, not converged).** `train/ddpm_fit.py`: 359k parameters, 20,860 steps, 25 minutes on MPS, trained once on the same train split and the video-footprint augmentation, evaluated on 200 open-play test samples with 20 samples per scene (no D2 averaging at sampling time). Metrics are the forecaster's own (`trajectory_metrics`, samples taken as modes); the MDN row is the retrained K = 6 forecaster on the same 200 samples. Metres.

| input | model | minADE | minFDE | minJADE | minJFDE | ball minFDE |
|---|---|---|---|---|---|---|
| clean | DDPM best of 20 | 4.03 | 5.09 | 6.04 | 9.16 | 10.24 |
| clean | DDPM best of 6 | 5.61 | 8.70 | 7.08 | 11.49 | 19.12 |
| clean | MDN K = 6 | 3.95 | 5.92 | 4.75 | 7.53 | 12.26 |
| video-masked | DDPM best of 20 | 4.32 | 5.30 | 6.54 | 9.50 | 10.76 |
| video-masked | DDPM best of 6 | 6.07 | 9.27 | 7.61 | 12.00 | 19.24 |
| video-masked | MDN K = 6 | 4.21 | 6.08 | 5.08 | 7.80 | 12.61 |

Standing still has FDE 15.68 m (clean) and 16.26 m (video-masked). At equal budget (6 samples) the MDN is clearly better on every metric. With 20 samples the DDPM wins the per-player minFDE (5.09 against 5.92 m) and the ball minFDE, but its joint minJFDE (9.16 against 7.53 m) is worse: best-of-20 per player rewards diversity, while the joint metric asks one scene to be right for all 23 agents. Plausibility is fine: 0.014% (clean) and 0.012% (video-masked) of player steps exceed 12 m/s; the spread of samples at 10 s is 15.0 m and 11.6 m. One training run, one seed, 200 test samples from a few matches, so the 20-sample per-player gap is within what a longer run could reverse; the joint and equal-budget gaps are large enough that more training alone is unlikely to close them. Not served; the product uses the MDN.

**Serving.** New weights `next10/weights_v2` (version `next10-v2+702177b2`, three critics and the forecaster; `export10 --version next10-v2`); the round-1 `weights/` (`next10-v1+b712214a`) is untouched. Parity with torch (96 open-play test samples): critic logits within 4e-6, forecaster `mu` within 2e-5 m (float32 metres), 41 ms for one frame with trajectories. `infer_next10` now also returns the 64-float critic embedding (`embedding`, mean over members of the D2-averaged
critic embedding; `with_embedding=False` omits it); no other output changed.

**Limitations of round 2.** The footprint bank comes from one league's broadcast (A-League) and one camera style; the scale 1.3 is a fit to counts, not a geometry. Independent per-step noise does not represent the correlated calibration error. Track fragmentation
and ID switches in the history frames are not simulated (the node is removed for all five history steps or none). Video-masked evaluation uses these same simulated masks, so it measures the model on a simulation of the video, not on video: there is no labelled video here, and the
shot10 or zone numbers on the 60 s clip and the 6.6 min clip are descriptive only. The team vocabulary is still 15 to 20 clubs, so FiLM is unused in most of the live calls.
