# Matchlens data contracts

This page documents everything Matchlens takes in and puts out: the provider-neutral **event** and **tracking**
schema a feed adapter must produce (BRD §9, FR-003 to FR-005), and the **overlay** and **insight** JSON a rendering
partner or client consumes (FR-023, FR-027, FR-044).

The machine-readable JSON Schemas (draft 2020-12) in [`docs/schema/`](schema/) are generated from the Pydantic
models the code validates against, and a test fails if they drift:

| Schema | Model | Used for |
|---|---|---|
| [`event.schema.json`](schema/event.schema.json) | `domain.models.Event` | Feed input, open dataset |
| [`tracking-frame.schema.json`](schema/tracking-frame.schema.json) | `domain.models.TrackingFrame` | Feed input, open dataset |
| [`match-meta.schema.json`](schema/match-meta.schema.json) | `domain.models.MatchMeta` | Teams, lineups, venue, kick-off |
| [`storyline.schema.json`](schema/storyline.schema.json) | `simulator.storyline.Storyline` | Scripted match narratives |
| [`overlay.schema.json`](schema/overlay.schema.json) | `agents.contracts.Overlay` | Broadcast graphics output |
| [`insight.schema.json`](schema/insight.schema.json) | `agents.contracts.Insight` | Fan companion output, provenance |

Regenerate after changing a model:

```bash
cd backend && uv run python -m matchlens.dataset.export
```

## Conventions

- **Pitch:** metres on a 105 x 68 pitch. `x` runs from the home goal (`x = 0`) to the away goal (`x = 105`), `y` from
  the bottom touchline (`y = 0`) to the top (`y = 68`). The home team attacks towards `x = 105` in both halves.
- **Time:** two clocks travel with every record.
  - `clock` is the broadcast clock in seconds. Period 2 starts at `2700`, so first-half stoppage time is
    `clock > 2700` in period 1 and is shown as `45+N'`.
  - `elapsed` is monotonic seconds since kick-off, including stoppage time but excluding the half-time interval.
    Ordering, replay timing, windows and overlay timecodes all use `elapsed`.
- **Speeds:** tracking speeds are m/s; event attributes ending in `_kmh` are km/h.
- **Identifiers:** `match_id`, `team_id` and `player_id` are stable strings (e.g. `ml-demo-001`, `NBR`, `NBR-09`).
  Event ids are `<match_id>-e<sequence>`.
- **Unavailable values** are `null`, never `0` (FR-045).
- **Fictional identity:** every club, player, venue and competition is invented (BRD §9.3).

## Event

One match event, delivered one at a time in match-clock order (FR-001).

| Field | Type | Notes |
|---|---|---|
| `event_id` | string | Unique. Re-delivering an id is a duplicate and is ignored. |
| `match_id` | string | |
| `sequence` | integer | Monotonic in generation order. An event arriving with a lower sequence than one already seen is out of order and triggers a rebuild. |
| `period` | 1 or 2 | |
| `clock` | number | Broadcast clock, seconds (see above). |
| `elapsed` | number | Monotonic seconds since kick-off. |
| `type` | enum | See the table below. |
| `team_id` | string or null | Team performing the action. |
| `player_id` | string or null | Player performing the action. |
| `related_player_id` | string or null | Receiver, tackled or fouled player, assister, keeper beaten, player coming on. |
| `start` | `{x, y}` or null | Where the action started. |
| `end` | `{x, y}` or null | Where it ended (passes, carries, shots). |
| `outcome` | enum | `success`, `fail`, `goal`, `saved`, `off_target`, `blocked`, `yellow`, `second_yellow`, `red`, `none`. |
| `attrs` | object | Type-specific attributes (string, number or boolean values). |
| `corrects_event_id` | string or null | Set on `correction` events only. |

Mapping to the BRD §9.1 names: `match_clock` is `clock`, `start_xy` is `start`, `end_xy` is `end`.

### Event types and attributes

| `type` | `outcome` | `attrs` |
|---|---|---|
| `kickoff` | `none` | |
| `pass` | `success` / `fail` | `ball_speed_kmh`, `height` (`ground` / `high`), `pressured`, optional `set_piece` |
| `carry` | `success` | `speed_kmh` |
| `pressure` | `none` | `distance_m` (presser to carrier) |
| `tackle` | `success` | |
| `interception` | `success` | optional `clearance` |
| `possession_change` | `none` | `reason` |
| `shot` | `goal` / `saved` / `off_target` / `blocked` | `xg`, `shot_speed_kmh`, `body_part`, `big_chance`, `distance_m`, optional `set_piece` |
| `save` | `success` | `xg_faced`, `shot_event_id` |
| `goal` | `goal` | `xg`, `shot_speed_kmh`, `shot_event_id`, `home_score`, `away_score` |
| `foul` | `none` | |
| `card` | `yellow` / `second_yellow` / `red` | |
| `free_kick`, `corner`, `throw_in`, `goal_kick` | `none` | |
| `substitution` | `none` | `player_id` goes off, `related_player_id` comes on |
| `half_time`, `full_time` | `none` | `home_score`, `away_score` |
| `correction` | `none` | `field`, `previous`, `value` |

### Corrections

A correction amends an earlier event without deleting it (FR-006):

```json
{
  "event_id": "ml-demo-001-e01120", "match_id": "ml-demo-001", "sequence": 1120, "period": 2,
  "clock": 3867.6, "elapsed": 3929.3, "type": "correction", "team_id": "NBR", "player_id": "NBR-09",
  "related_player_id": null, "start": null, "end": null, "outcome": "none",
  "attrs": {"field": "player_id", "previous": "NBR-10", "value": "NBR-09"},
  "corrects_event_id": "ml-demo-001-e01118"
}
```

In the hero match the 64th-minute goal is first credited to `NBR-10` and corrected to `NBR-09` about a minute
later; the shot and the goal event each get a correction.

`field` is one of `player_id`, `related_player_id` or `team_id`. On receipt the stats engine replays the match with
the corrected value, so every dependent statistic, signal, insight, overlay and summary is updated; insights whose
signal no longer exists (for example a milestone credited to the wrong scorer) are withdrawn with
`stage: "retracted"`.

## Tracking frame

Positions of every player on the pitch and the ball, at 1 Hz (FR-010, BRD §9.2).

```json
{
  "match_id": "ml-demo-001", "clock": 100.0, "elapsed": 100.0, "period": 1,
  "entities": [
    {"entity_id": "ball", "x": 48.62, "y": 35.79, "speed": 8.8},
    {"entity_id": "NBR-01", "x": 6.01, "y": 34.05, "speed": 0.35}
  ]
}
```

Each entry of `entities` is one BRD §9.2 tracking sample: `timestamp` is the frame's `elapsed`, `xy` is `(x, y)`,
`speed` is m/s. Players who are sent off or substituted leave the frame. Distance covered, sprints and top speed
are computed from consecutive frames.

## Match metadata

`MatchMeta` carries `match_id`, `competition`, `venue`, `kickoff_utc`, the `home` and `away` teams (`team_id`, names,
`colour`, `formation`, `starting_xi` in formation-slot order, `bench`) and a `players` map keyed by `player_id` (name,
shirt, position, formation role, nationality, `top_speed` in m/s, career appearances and goals for milestones).

## Overlay

A timed broadcast graphic (FR-023 to FR-027). The Producer agent designs it; code clamps it to the on-screen caps
before publication.

The 64th-minute goal in the hero match after the scorer correction (offline templates, `localized` cut to English):

```json
{
  "overlay_id": "goal:ml-demo-001-e01118",
  "insight_id": "goal:ml-demo-001-e01118",
  "revision": 1,
  "kind": "lower_third",
  "title": "GOAL! Kenji Arakawa scores for N",
  "subtitle": "Northbridge FC 1-2 Harbour City at 64'. Chance quality 0.517",
  "stats": [
    {"label": "xG", "value": "0.517", "unit": null},
    {"label": "Shot speed", "value": "102.1", "unit": "km/h"}
  ],
  "team_id": "NBR",
  "focus_player_id": "NBR-09",
  "accent_colour": "#1F6FEB",
  "start_elapsed": 3991.1,
  "duration_s": 8.0,
  "priority": 1,
  "slot": "lower",
  "localized": {
    "en": {"headline": "GOAL! Kenji Arakawa scores for Northbridge FC", "body": "Northbridge FC 1-2 Harbour City at 64'."}
  },
  "stage": "agent"
}
```

| Field | Meaning |
|---|---|
| `overlay_id` | Stable id. A later message with the same id **replaces** the earlier one (agent upgrade, correction, trimmed duration). |
| `insight_id` | The insight this graphic shows; its evidence and fact-check live there. |
| `revision` | Incremented when a correction changes the underlying moment. |
| `kind` | `lower_third`, `stat_card`, `player_card`, `momentum_bar`, `shot_card`, `correction_banner`, `milestone_banner`. |
| `title`, `subtitle` | Display text, at most 32 and 60 characters. |
| `stats` | Up to three `{label, value, unit}` numbers, already formatted. Values come from the stats engine. |
| `team_id`, `focus_player_id` | Anchor: the team and/or player the graphic is about. Renderers that draw on the pitch can place a player-anchored graphic at that player's position from the latest tracking frame. |
| `accent_colour` | The anchored team's colour, hex. |
| `start_elapsed` | Timecode **in**, match `elapsed` seconds. |
| `duration_s` | Timecode **out** is `start_elapsed + duration_s`. Between 4 and 12 seconds. |
| `priority` | 1 (highest) to 5. |
| `slot` | Screen region: `lower` (lower third), `upper_right`, `banner` (top). |
| `localized` | Casual-mode text per language (`en`, `hi`, `es`). |
| `stage` | `data` (template text, published immediately), `agent` (upgraded by the agents), `retracted` (withdraw it now). |

**Kinds and slots:** `lower_third`, `shot_card` and `player_card` use `lower`; `momentum_bar` and `stat_card` use
`upper_right`; `milestone_banner` and `correction_banner` use `banner`.

**On-screen cap (FR-026):** at most one graphic per slot at a time, so at most three on screen. A new graphic waits up
to 10 seconds for a busy slot, pre-empts a lower-priority graphic (which is re-published with a shorter
`duration_s`), or is dropped. Priority 5 moments (minor badges) stay in the companion feed and are not put on screen.

**Life cycle:** a data-only overlay is published as soon as the stats engine detects the moment (within one
second). When the agents finish, the same `overlay_id` is re-published with `stage: "agent"` and richer text,
keeping its slot and start time if it is still on screen.

## Insight

The fact-checked explanation behind every overlay and feed card, with full provenance (FR-019 to FR-022, FR-044).

| Field | Meaning |
|---|---|
| `insight_id`, `signal_id` | Same value: one insight per detected key moment. |
| `signal_type` | `goal`, `big_chance`, `big_save`, `card`, `substitution`, `momentum_swing`, `pressure_spell`, `chaos_spell`, `control_spell`, `milestone`, `speed_badge`, `distance_badge`, `shot_speed_badge`, `hard_pass`, `correction`, `period`. |
| `revision`, `supersedes_revision` | Correction history. |
| `stage` | `data`, `agent` or `retracted`, as for overlays. |
| `elapsed`, `minute_label`, `period` | When the moment happened. |
| `team_id`, `player_ids`, `priority` | Who it is about, and how important it is (1 to 5). |
| `headline`, `explanation`, `why_it_matters`, `narrative`, `storyline` | Tactical and Narrator agent text (null when not generated). |
| `key_numbers` | The numbers that prove the point. |
| `evidence_event_ids` | Source events in the feed. |
| `variants` | `mode -> language -> {headline, body}` for `casual`, `analyst`, `player_focus` in `en`, `hi`, `es`. |
| `club_angles` | `team_id -> language -> text`: the moment from each club's point of view. |
| `fact_check` | `status` (`verified`, `rewritten`, `blocked`, `data_only`), per-claim `results` with verdict and actual value, `unaccounted_numbers`, and `actions` taken (including fallbacks). |
| `agents` | Agents whose output was used. |
| `latency_ms` | Signal to published agent insight. |

## Live API

The backend runs on port 8765. The frontend proxies a read-only allow-list of these under `/api/backend/*` and the
WebSocket as Server-Sent Events under `/api/stream`.

| Method and path | Returns |
|---|---|
| `GET /api/health` | Status, LLM mode, p95 stats-engine and agent latency, current `run_id`, store and cache counters |
| `GET /api/match` | Snapshot for a late joiner (same as the WebSocket `hello`) |
| `GET /api/state` | Score, clock, period, possession, momentum index, rhythm |
| `GET /api/timeline?team_id=&player_id=&type=` | Key events, filterable |
| `GET /api/signals` | Every detected key moment |
| `GET /api/insights?mode=&language=&club_id=&player_id=&metric=` | Personalised feed |
| `GET /api/insights/{insight_id}` | Full insight with every variant and the fact-check report |
| `GET /api/overlays?active_at=` | Overlay JSON, optionally only those on screen at an `elapsed` time |
| `GET /api/catchup?minutes=5\|15\|30&mode=&language=&club_id=&player_id=` | "What did I miss?" |
| `GET /api/recap?mode=&language=&club_id=&player_id=` | Half-time, full-time or live recap |
| `GET /api/players/{player_id}`, `GET /api/teams/{team_id}` | Live statistics |
| `GET /api/agents/decisions` | Coordinator routing decisions |
| `POST /api/replay` `{seed, speed, start_minute}` | Start a new replay (a new run in the database) |
| `GET /api/runs?limit=` | Stored runs, newest first: status, position, score, resumes |
| `POST /api/replay/control` `{speed?, paused?}` | Change speed or pause |
| `/mcp` | MCP server (streamable HTTP) with the match-state tools, internal only |

### WebSocket `/ws`

Every message is a JSON object with a `type`:

| `type` | Payload |
|---|---|
| `hello` | Sent on connect and when a new replay starts: `run_id`, `meta`, `state`, `timeline`, `signals`, `insights`, `overlays`, `momentum`, `frame`, `finished` |
| `event` | `event`: an `Event` (with corrections applied) plus `minute_label` and, for passes, `pass_quality` |
| `correction` | `event`: the `correction` event |
| `state` | `state`: match state after each event |
| `frame` | `frame`: a `TrackingFrame` |
| `signal` | `signal`: a detected key moment |
| `insight` | `insight`: an `Insight` (new, upgraded or retracted) |
| `overlay` | `overlay`: an `Overlay` (new, upgraded, trimmed or retracted) |
| `feed_status` | `status`: `finished`, `error` or `no_match` |
| `ping` | Keep-alive, every second without other traffic |

### Pass quality

Every pass event on the stream carries `pass_quality` (FR-013): `distance_m`, `progression_m`, `pressured`,
`lane_opponents` (opponents within 2.5 m of the passing lane, from the nearest tracking frame), `lofted`, a
`difficulty` score from 0 to 100 and its `band` (`easy`, `medium`, `hard`, `elite`). The score is the sum of its
`components`: distance (up to 35), progression (up to 20), pressure (15), lane opponents (up to 20) and lofted (10).

### Momentum

`momentum_index` runs from -100 (away on top) to +100 (home on top). It is sampled once per match minute from
weighted attacking events in a rolling five-minute window (FR-015); the weights live in
`backend/src/matchlens/interpret/config.py`.
