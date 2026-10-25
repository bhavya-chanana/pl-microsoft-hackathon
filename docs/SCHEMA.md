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
| `pass` | `success` / `fail` | `ball_speed_kmh`, `height` (`ground` / `high`), `pressured`, optional `set_piece`, optional `cross` |
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

The backend runs on port 8765. The frontend proxies an allow-list of these under `/api/backend/*` (every `GET` it
uses, plus the replay, archive, vote and rating `POST`s) and the WebSocket as Server-Sent Events under `/api/stream`.

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
| `GET /api/players/{player_id}` | Live statistics plus the player's match `card` (see below) |
| `GET /api/teams/{team_id}` | Live statistics |
| `GET /api/coach?team_id=&language=` | Coach brief for one side: ranked points, each with evidence, trade-off and confidence |
| `GET /api/speech?insight_id=&mode=&language=&club_id=&player_id=` | The insight read aloud (MP3). `503` when Azure AI Speech is not configured: the browser then uses its own voices |
| `GET /api/polls` | Fan polls opened so far, with tallies (see below) |
| `POST /api/polls/{poll_id}/vote` `{voter_id, option_id}` | Vote, or change a vote, while the poll is open. `409` once closed |
| `GET /api/ratings` | Data rating, fan rating and card overall for every player who has played |
| `POST /api/ratings` `{voter_id, player_id, score}` | Rate a player 1 to 10; a fan's later rating replaces their earlier one |
| `GET /api/agents/decisions` | Coordinator routing decisions |
| `POST /api/replay` `{seed, speed, start_minute}` | Start a new replay (a new run in the database) |
| `GET /api/feeds` | Recorded matches on this machine (names), e.g. from video auto-eventing; usually empty |
| `POST /api/replay/feed` `{name, speed, start_minute}` | Play a recorded match as a new run |
| `GET /api/runs?limit=` | Stored runs, newest first: status, position, score, team names, resumes |
| `POST /api/runs/{run_id}/open` | Reopen a stored run: a live one resumes, a finished one opens read-only. Returns `{match_id, run_id, archived}`; `404` if unknown |
| `POST /api/replay/control` `{speed?, paused?}` | Change speed or pause |
| `GET /api/moments/{insight_id}` | Replay of one key moment: `{insight_id, signal_type, minute_label, team_id, player_ids, start_elapsed, end_elapsed, focus_elapsed, frames[], events[], drawings[]}`. Each drawing is `{kind: lane\|carry\|shot\|ring\|zone\|path\|marker, points[{x,y}], at_s, team_id, player_id, label, radius, emphasis, event_id}` in pitch metres, `at_s` seconds after `start_elapsed`. No language model; `404` if the moment is unknown |
| `GET /api/pulse` | Match Pulse: `{home_team_id, away_team_id, now, rows[], pins[], spells[]}`. One row per sampled minute: momentum, `state` (control, balanced or chaos), controlling team, turnovers and fouls a minute, pass share, pressing actions per team and passes |
| `POST /api/ask` `{question, mode, language, club_id?, player_id?}` | Ask the Pundit. Returns `{question, intent, answer, points[], key_numbers[], cited_moments[], minute_label, fact_check, source: agent\|data, agents[], latency_ms}`. Capped service-wide by `MATCHLENS_ASK_PER_MINUTE` (default 40; `429` past it) |
| `GET /api/scenarios` | Scenario Lab presets `{preset_id, seed, storyline}` and the roster a custom storyline may use |
| `POST /api/scenarios/draft` `{prompt, language}` | Write a storyline from a description with the Scenario agent and check it is playable. `503` without live models, `422` `{message, problems[]}` if it cannot be made playable |
| `POST /api/scenarios/kickoff` `{preset_id?, storyline?, seed, speed}` | Play a preset or a checked storyline from kick-off, replacing the current match for every viewer. Returns `{match_id, run_id, title, speed}`; `422` with the problems if the storyline is not playable |
| `GET /api/scenarios/check` | Script vs reality for the running storyline: each beat `delivered`, `missed` or `pending`; `404` if the match was not played from a storyline |
| `GET /api/dataset` | The running match as a zip: `meta.json`, `events.jsonl` (as delivered, corrections included) and `tracking.jsonl.gz` |
| `/mcp` | MCP server (streamable HTTP) with the match-state tools, internal only |

### WebSocket `/ws`

Every message is a JSON object with a `type`:

| `type` | Payload |
|---|---|
| `hello` | Sent on connect and when a new replay starts or a run is opened: `run_id`, `meta`, `state`, `timeline`, `signals`, `insights`, `overlays`, `momentum`, `frame`, `finished`, `archived` (a stored, finished run opened from the archive: nothing more will arrive), `polls` |
| `event` | `event`: an `Event` (with corrections applied) plus `minute_label` and, for passes, `pass_quality` |
| `correction` | `event`: the `correction` event |
| `state` | `state`: match state after each event |
| `frame` | `frame`: a `TrackingFrame` |
| `signal` | `signal`: a detected key moment |
| `insight` | `insight`: an `Insight` (new, upgraded or retracted) |
| `overlay` | `overlay`: an `Overlay` (new, upgraded, trimmed or retracted) |
| `poll` | `poll`: a poll opened, closed or resolved by the match |
| `fan_tally` | `polls` and `ratings`: vote counts and fan rating averages, at most once a second |
| `feed_status` | `status`: `finished`, `error` or `no_match` |
| `ping` | Keep-alive, every second without other traffic |

### Fan polls and ratings

A poll is opened by the match itself (for example "Who wins from here?" after a goal, player of the match at full
time) and closed and resolved by later events:

```json
{"poll_id": "result-1", "kind": "result", "minute_label": "23'", "closed": true, "answer": "NBR", "total_votes": 41,
 "question": {"en": "Who wins from here?", "hi": "...", "es": "¿Quién gana desde aquí?"},
 "context": {"en": "After the goal", "hi": "...", "es": "..."},
 "options": [{"option_id": "NBR", "label": {"en": "Northbridge FC", "hi": "...", "es": "..."}, "votes": 18},
             {"option_id": "draw", "label": {"en": "Draw", "hi": "...", "es": "Empate"}, "votes": 9},
             {"option_id": "HBC", "label": {"en": "Harbour City", "hi": "...", "es": "..."}, "votes": 14}]}
```

`kind` is `result` (who wins from here) or `motm` (best player of a half or the match, options are players). Text
comes in every language. `answer` is the option that turned out right, once the match has decided it.

`voter_id` is a random id the browser keeps; the backend stores only a salted hash of it, never anything that
identifies a person. A rating row is `player_id`, `team_id`, `name`, `short_name`, `position`, `on_pitch`,
`data_rating` (out of 10) with the `parts` that make it up, `overall` (the card's), `fan_rating`, `fan_votes` and
`gap` (fans minus data).

### Match card

Computed from this match's events and tracking only, on a 1 to 99 scale:

```json
{"kind": "outfield", "overall": 89,
 "attributes": [{"key": "PAC", "value": 80}, {"key": "SHO", "value": 99}, {"key": "PAS", "value": 66},
                {"key": "DRI", "value": 68}, {"key": "DEF", "value": 69}, {"key": "PHY", "value": 76}]}
```

Goalkeepers get `DIV`, `HAN`, `KIC`, `REF`, `SPD`, `POS`. An attribute without data (for example `PHY` with no
tracking) is `null`, never zero. The overall is half the weighted attributes for the player's position and half the
data rating, so the two never disagree badly.

### Pass quality

Every pass event on the stream carries `pass_quality` (FR-013): `distance_m`, `progression_m`, `pressured`,
`lane_opponents` (opponents within 2.5 m of the passing lane, from the nearest tracking frame), `lofted`, a
`difficulty` score from 0 to 100 and its `band` (`easy`, `medium`, `hard`, `elite`). The score is the sum of its
`components`: distance (up to 35), progression (up to 20), pressure (15), lane opponents (up to 20) and lofted (10).

### Momentum

`momentum_index` runs from -100 (away on top) to +100 (home on top). It is sampled once per match minute from
weighted attacking events in a rolling five-minute window (FR-015); the weights live in
`backend/src/matchlens/interpret/config.py`.
