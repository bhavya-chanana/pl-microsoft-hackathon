# Data cadence and polling plan

Three live sources: **player vests** (GPS/IMU, all 22 players), **ball sensor** (UWB position + IMU), **live video** (watched by fans and sampled for the vision LLM). Everything runs behind a **90 s viewing buffer**, so the budget for "sensor moment → fact ready" is ~60 s with a 30 s safety margin.

Rates are based on published figures for comparable hardware (Catapult/STATSports vests at 10 Hz GPS; adidas/Kinexon connected ball at ~100 Hz UWB position and 500 Hz IMU). Confirm against the actual devices and change the constants in `frontend/src/features/fi/polling.ts` and the backend config.

## 1. Device → ingest

| Stream | Device rate | Ingest batch | Why |
|---|---|---|---|
| Vest position/speed (22 players) | 10 Hz | push every **1 s** (10 frames × 22) | Enough for speed, distance, sprints, shape; 1 s batches keep request count low (~1 req/s). |
| Vest IMU (accel, impacts) | ~100 Hz on device | device-side summary every **1 s** (peak accel, impact count) | Raw 100 Hz is only needed for collision triggers. |
| Vest heart rate | 1 Hz | in the 1 s batch | HR changes slowly. |
| Ball position (UWB) | ~100 Hz | push every **250 ms** (25 frames) | Passes and shots happen in < 1 s; need sub-second ball path. Store raw, downsample to 25 Hz for analytics. |
| Ball IMU impacts (kicks/touches) | 500 Hz sensing | **push each impact immediately** | A touch is the key fusion signal; don't wait for a batch. |
| Video | continuous HLS, 2–6 s segments | register each segment as it lands | Segments are the unit the vision LLM samples from. |
| Clock sync (sensor UTC ↔ video) | — | re-check every **60 s**, plus at kickoff and each half | Catches drift before it reaches 1 s. |

## 2. Processing cadence (server)

| Job | Cadence | Output |
|---|---|---|
| Sensor event detection (touch, pass, shot, possession, ball out, goal-line cross, sprint) | streaming, **sliding 2 s window evaluated every 500 ms** | `SensorEvent` |
| Fusion → ledger | on every new sensor event or vision observation | `EventRevision` |
| Physical stats snapshot (distance, speed, sprints, HR) | every **15 s** | `StatSnapshot` (physical) |
| Full stats snapshot (passes, shots, possession, xG, heatmaps) | every **30 s**, and immediately after a goal/card | `StatSnapshot` (full) |
| Tactical shape / pressing aggregates | every **60 s** (rolling 5 min) | `TacticalHypothesis` |
| Match Pulse | every **60 s** bucket | pulse points |

## 3. Vision LLM (video)

Models sample frames from clips; they don't watch a stream. Calls take ~5–15 s, which fits inside the buffer.

| Mode | When | Clip | Frames |
|---|---|---|---|
| **Triggered** | ball out of play, stoppage (all player speeds drop), goal-line cross, hard collision (vest impact), whistle-like pause > 3 s | 10 s before → 5 s after the trigger | **2 fps** (~30 frames) |
| **Background sweep** | every **30 s** when nothing has triggered | last 4 s | **1 fps** |
| **Confirmation** | goal / penalty / card pending | 20 s window | 2 fps |

Budget: at most **~6 vision calls per minute per match**. Collapse triggers within 5 s of each other into one call. Vision never confirms a goal without the ball sensor; it decides fouls, cards, offside, VAR, subs, injuries and restart type.

## 4. Commentary LLM (text only)

| Trigger | Latency target | Cadence |
|---|---|---|
| Critical (goal, red card, penalty, VAR overturn) | < 10 s | immediately, bypass cooldown |
| Notable (big chance, save, yellow, sub) | < 20 s | per event, 20 s cooldown per match |
| Tactical / pulse | < 30 s | batch every **60 s** |
| Ask (user question) | < 8 s | on demand |

Generate once per event and persona (4 variants), not per user.

## 5. Client

| Data | Mechanism | Cadence |
|---|---|---|
| Events, insights, score | WebSocket push, held client-side until `available_media_ms <= playhead` | push; fallback poll `/view` every **2 s** |
| Stats / player pages | fetch latest safe snapshot `<= playhead` | every **15 s** while visible |
| Playhead report to server | — | every **1 s** and on seek |
| Match board | REST | every **30 s** |
| Live edge / delay indicator | from `/view` | with each response |

Stats lag the video by 15–45 s by design and never lead it.
