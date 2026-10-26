# Persona UX → Data Needs → Source Mapping (v2.0 draft)

Sources: **V** = player vests (GPS/IMU, ~10 Hz, HR via pod/strap), **B** = ball sensor (UWB position ~100 Hz + IMU ~500 Hz, Kinexon/adidas-style), **L** = LLM on live video frames (sampled clips), **F** = fusion of V+B (deterministic code), **O** = operator/mock fallback.

## 1. Sensor facts (researched)
| Source | What it likely gives | Implication |
|---|---|---|
| Ball (adidas/Kinexon "connected ball") | UWB **true position** ~100 Hz, ±~10 cm; IMU 500 Hz → kick/touch timing, impact force, spin; needs ~24 pitch antennas | Ball xy is real. Touch = IMU spike + nearest vest. Passes/shots/goal-line cross are computable. |
| Vests (Catapult / STATSports class) | GPS 10 Hz (some 18 Hz), accelerometer/gyro ~100 Hz, HR if strap/ECG paired | Position, speed, accel, distance, sprints, load are real. 10 Hz GPS ≈ 1–2 m error → fine for shape/runs, not for "who touched" alone (ball IMU+UWB fixes that). |
| LLM video | Not true streaming: frames sampled from N-second clips → image+text model; ~10 s+ latency per call | Fine inside a 90 s buffer. Use for what sensors can't see; trigger by sensor events + slow background sweep. |

## 2. Persona Watch / Stats experience

| | Diehard | Casual | Tactical | Fantasy |
|---|---|---|---|---|
| **Full-screen popups** | club goals/concessions, big chances, saves, cards, subs | goals, big saves, cards, near misses (plain words) | turnovers→chances, press won/broken, shape change, overloads | owned-player goals/assists/saves/cards, points delta, clean-sheet status |
| **Score bug chips** | shots, big chances, momentum | score, possession | press intensity, line height, xG | live points, minutes |
| **Scroll feed cards** | "why we conceded", club consequence | short recap, explained terms | evidence-cited tactical cards with time ranges | player contribution cards, provisional points |
| **Stats first tab** | team overview | match essentials | passing/progression/pressing | my squad |
| **Player page emphasis** | impact, duels, key moments | goals, rating-ish summary | progressive passes, positions, heatmap | points breakdown, minutes, shots, physical |

All personas see everything; only order/wording changes.

## 3. Per-player metrics and how we get them

| Metric | Source | How |
|---|---|---|
| Minutes, on/off pitch | V (+L for sub confirmation) | vest enters/leaves pitch polygon |
| Distance, high-speed running, sprints, top speed, accel/decel | V | integrate 10 Hz positions/speeds; thresholds (e.g. >25 km/h sprint) |
| Heart rate, load, fatigue trend | V (if HR strap) | raw HR → zones; mark `unavailable` otherwise |
| Heatmap, average position | V | grid aggregation |
| Touches | F | ball IMU impact + nearest vest within ~1.5 m |
| Passes attempted/completed, accuracy, progressive passes | F | touch by A → next touch by teammate B; progressive if gains ≥10 m toward goal |
| Pass network | F | A→B counts |
| Shots, on target, shot speed | F (+L confirms) | kick toward goal mouth with ball speed > threshold; on target from trajectory/goal-plane cross |
| Goals | B + L | ball crosses goal-line inside posts (B) + celebration/restart (L) → confirmed |
| Assists | F | last teammate touch before scorer (provisional) |
| xG | model on F data | shot distance/angle/defenders between (from V) — label `model` |
| Dribbles, duels, tackles, interceptions | F | possession change between opposing players in contact radius |
| Recoveries / high regains | F | possession won; "high" if in opponent half |
| Pressing (PPDA, pressures) | F | defender speed toward ball carrier within ~5 m |
| Saves (GK) | F + L | shot on target stopped by GK touch |
| Fouls, cards, offside, VAR, injuries | **L** (O override) | vision observations on triggered clips |
| Set-piece type (corner/FK/penalty/throw) | B + L | ball out of play location (B) + restart type (L) |
| Team shape, line height, width, compactness | V | geometry of 10 outfield positions per team |
| Momentum / Match Pulse | F | rolling possession in final third + shots + pressure |
| Fantasy points | derived | apply ruleset to above events |

## 4. LLM video strategy
- **Event-triggered**: on ball-out, stoppage (all speeds drop), goal-line cross, sharp collision (vest impact), unusual pause → send 8–15 s clip frames (~2–4 fps) + sensor context (who's near ball, shirt numbers).
  Ask: foul? card (colour, player)? offside flag? VAR? sub? injury? restart type?
- **Background sweep**: low fps every ~30–60 s for missed context (subs boards, crowd, weather).
- **Commentary**: text-only LLM over structured events (cheap); vision not needed for narration.
- Output strictly as `VisionObservation` JSON; never sets score alone.

## 5. Gaps / fallbacks
- No ball sensor in a given match → ball position estimated from player possession (degraded) → mark lower confidence.
- No HR → physiological metrics `unavailable`.
- Referee decisions only from L → provisional until confirmed by restart pattern or operator.
