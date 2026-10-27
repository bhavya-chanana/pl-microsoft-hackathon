# Backend plan: data and logic fixes from the redesign reviews

Owner: senior engineer (plan and review). Implementer: backend-implementer.
Input: `backend-issues.md`, `coach-review.md`, `coach-review-2.md`, `fan-review-2.md`.
Baseline: `cd backend && uv run pytest -q` → 449 passed, 31 skipped (after merge 8839190).

Rules: stage only `backend/` and `docs/redesign/backend-*.md`, never `git add -A`. One commit per task, prefix by
area (`INTERPRET:`, `AGENTS:`, `TACTICS:`, `RUNTIME:`), trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
Every fix lands with a test that fails before it.

## Findings (confirmed or refuted)

| # | Issue | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | "now have 11 players" under a yellow | Confirmed | `agents/templates.py:55-59` one `card` phrase for every card; `interpret/engine.py:593-596` puts `players_on_pitch` in the facts of every card; `templates.py:226` also shows it as an "On pitch" key number. |
| 2 | Momentum banner picks the wrong team, fires inside "Even" | Confirmed, root cause differs from the report | `interpret/engine.py:674-692`: the team is `home if delta > 0 else away`, i.e. the *direction of change*, not who is on top. "Swings to Northbridge (home) at -8.5" is a move from strongly away towards zero. "68.2 to 0.0" is the 5-minute window emptying (no weighted events after a goal and restart: `h = a = 0` gives index 0), not a real swing. No check that `to_index` is outside the ±10 band the bar calls Even (`frontend/.../momentum-bar.tsx:18`). |
| 3 | Tactics recommends `scored[0]` within noise | Confirmed, in three places | `tactics/board.py:338` (`_fallback_verdict`), `agents/tactician.py:778-792` (`offline_verdict`, the usual path) and the model verdict (`tactician.py:795+`, `check_verdict` accepts any ranked id). `evaluate.rank` already computes interval separation for `confidence` but nothing uses it to gate the recommendation. "Asked for 4, got 2": the board has no field saying how many were asked for; `errors` holds `ideate: TimeoutError` in developer wording only. |
| 4 | "Use the extra player" at full time | Confirmed | `agents/coach.py:404-406`: `LIVE_ONLY = (_game_state, _card_risk, _fresh_legs)`; `_numbers` (extra_man / man_down) is missing. `tests/test_coach.py:42` asserts `man_down` on the *full-time* engine, so the test encodes the bug. Cache: key is `(elapsed minute, signal count, ...)` (`agents/services.py:59-62`); the full-time PERIOD signal changes the count, so a stale cache is unlikely but the key should include `status` explicitly. |
| 5 | Kick-off event after every goal | Confirmed as a presentation bug, not a data bug | A restart kick-off is real football (`simulator/engine.py:968-978`). `interpret/engine.py:413-419` already distinguishes the period start (status pre_match/half_time) but the timeline (`runtime/session.py:28-31,208-209`) and the event stream include every kick-off with no flag. |
| 6 | Unit inside `value` and in `unit` | Partly refuted | Template key numbers never put the unit in `value` (`templates.py:219-233`). The duplicate comes from model-written `TacticalDraft.key_numbers` (`agents/pipeline.py:233-237`); the contract (`agents/contracts.py:96-99`) says "formatted for display", which invites "12.7 m". |
| 7 | Scorer "corrections" | Confirmed as unexplained, intended data | Scripted storyline beat (`simulator/storyline.py:101`, `initially_credited_to`) emits a CORRECTION 40-80 s after the goal (`simulator/engine.py:852-855`). Card text (`templates.py:110-114`) says "Elias Brandvold is now Kenji Arakawa": no "goal", no minute, no reason. Two corrections are emitted (shot and goal). |
| 8 | No minutes or duels per player | Confirmed | `interpret/stats.py:13-14` has `minutes_from`/`minutes_to` but nothing sets them and `as_dict` omits them. No duel concept; the simulator's contest is PRESSURE (presser vs carrier, `simulator/engine.py:392-418`) resolved by TACKLE, FOUL or the carrier keeping the ball. |
| 9 | No per-half possession or field tilt | Confirmed | `interpret/engine.py:627` accumulates `possession_s` for the whole match only; `final_third_passes` exists but no tilt or per-half split. |
| 10 | Pitch-layer feed (shape, heat, pass network, press) | Deferred | New feed; see Deferred. |

## Tasks (priority order)

### P1-a  Card text only talks about numbers when the numbers changed  (INTERPRET: / AGENTS:)
Root cause: one phrase and one fact set for all card types.
Fix:
- `interpret/engine.py` CARD: put `players_on_pitch` in `facts` only when `red` (red or second yellow). Add
  `"player_yellow_cards"` for yellows (1 or 2) so the text has something true to say.
- `agents/templates.py`: `phrase_key` returns `card_red` for `card in ("red", "second_yellow")`, else `card_yellow`.
  `card_yellow` body: "{player} is booked ({minute})." style, all three languages; `card_red` body: "{team} are down
  to {on_pitch} players ({minute})." Keep the `card` entry for old recorded signals (fallback to `card_yellow`).
- `key_numbers`: "On pitch" only appears for red cards (follows from the facts change; assert it).
- Check `agents/pundit.py:196` and prompts do not reintroduce the line (pundit reads team stats, fine).
Tests: yellow card pack → body has no "players"/"on the pitch" and no On pitch key number, in en/hi/es; red and
second-yellow → "down to 10". Engine test: a yellow card signal has no `players_on_pitch` fact.
Contract: none (signal `facts` lose a key on yellows; frontend does not read it: grep clean).

### P1-b  Momentum swing names the side that is now on top, and never inside the Even band  (INTERPRET:)
Root cause: team chosen by `sign(delta)`; no check on `to_index`; decay of an empty window counts as a swing.
Fix in `interpret/engine.py` `_tick`:
- Add `momentum_even_band: float = 10.0` to `interpret/config.py` thresholds (matches the bar).
- Fire only if `abs(delta) >= momentum_swing_delta` **and** `abs(index) >= even_band` **and**
  `sign(index) == sign(delta)`; `towards = home if index > 0 else away`.
- Do not fire when the window holds no weighted events for the side it swings to (`h + a == 0` or the `towards`
  side's weight is 0): that is decay, not a swing.
- Add facts `strength = round(abs(index))` and `previous_leader` (`team_id` or None if `abs(then.index) < band`).
- `templates.py` momentum body: say who is on top and how strongly on the -100..100 scale, e.g. "{team} on top:
  {strength} on a 0-100 scale, since {since}." Every number used must be a fact (so the fact-checker passes).
Tests: synthetic momentum sequences (unit-test the decision as a small pure function, e.g.
`_swing_towards(then, now, h, a, band, delta) -> str | None`): -70 → -8.5 gives None; 68 → 0 gives None;
-40 → +30 gives home; 60 → -20 gives away; 68 → 12 gives None. Replay the demo match and assert every
MOMENTUM_SWING signal has `abs(to_index) >= 10` and `team_id` matches `sign(to_index)`.
Contract: none (fewer, correct signals; two new facts).

### P1-c  Coach brief: no live-only advice after the final whistle  (AGENTS:)
Root cause: `_numbers` not in `LIVE_ONLY`.
Fix: add `_numbers` to `LIVE_ONLY` (coach.py). Add `engine.state()["status"]` to the coach cache key in
`services.py` (`_key(session, "coach", status, team_id, language)`), so a brief can never outlive a status change.
Tests: on the full-time engine, no item is `extra_man`/`man_down`/`chase_game`/`see_it_out`/`card_risk`/`fresh_legs`
for either side. Move the existing `man_down` assertion (`test_coach.py:42`) to a mid-match engine (replay up to
just after the red card). Cache: a brief generated live, then the FT event ingested in the same minute → a
different brief.
Contract: none.

### P1-d  Tactics: no recommendation when the best idea is within noise  (TACTICS:)
Root cause: no "clear winner" rule; all three verdict paths recommend `rows[0]`.
Fix:
- `tactics/evaluate.py`: `clear_gain(stats, baseline) -> bool`: `stats.p_shot.low > baseline.p_shot.high` or
  `stats.p_concede_shot.high < baseline.p_concede_shot.low` (the same rule as the frontend's `withinNoise`,
  `frontend/src/features/tactics/tactics-board.tsx:76-81`, inverted). `clear_winner(ranked, baseline)` returns the
  best-ranked result with a clear gain, or None.
- `verdict_payload`: add `"clear_winner_id"`. `offline_verdict`: if None → a "No clear winner" draft (en/es/hi) that
  names the margin of the top idea using only numbers in the payload, `recommended_tactic_id=None`; else recommend
  that id (not blindly `rows[0]`). `check_verdict`: a `recommended_tactic_id` other than `clear_winner_id`
  (including any id when it is None) is a problem → falls back to the template.
- `board._fallback_verdict`: same rule.
- Prompt (`agents/prompts.py` GAFFER): tell the model to recommend only `clear_winner_id`, or nothing.
Tests: a ranked list whose intervals overlap the baseline → `recommended_tactic_id is None`, headline "No clear
winner", in all three paths (offline, fallback, model draft that recommends rows[0] gets rejected); a clear winner
ranked second by utility is the one recommended. Cassette tests still pass (re-record only if a recorded verdict
now violates the rule; report which).
Contract: none (field already nullable; frontend already handles None).

### P2-a  Restart kick-offs flagged and kept off the timeline  (RUNTIME: / INTERPRET:)
Fix: the interpret engine records period-start kick-off ids (the branch at engine.py:414). `event_payload` adds
`"restart": true|false` to kick-off events; `MatchSession.timeline()` drops restart kick-offs. Event type stays
`kickoff`.
Tests: replay the demo; timeline has exactly 2 kick-offs (one per half); stream payload of a post-goal kick-off has
`restart: true`.
Contract: additive `restart` on kick-off event payloads. Frontend can drop its "first kick-off per half" filter
later (optional, tell implementer).

### P2-b  Key numbers carry the unit once  (AGENTS:)
Fix: `KeyNumber` validator in `agents/contracts.py` strips a trailing unit (case-insensitive, optional space) from
`value` when `unit` is set; field description becomes "The number only, without the unit". Applies to every path
(model, templates, coach).
Tests: `KeyNumber(value="12.7 m", unit="m").value == "12.7"`; `"45%"`/`"%"` → `"45"`; `"3 v 2"`/None unchanged;
a tactical draft with "12.7 m" through the pipeline yields value "12.7".
Contract: none.

### P2-c  Corrections explain themselves  (INTERPRET: / AGENTS:)
Fix: correction signal facts gain `corrected_minute_label` (target event's minute label). Card text: "Goal at
{corrected_minute} re-credited to {value_name}", body "The data feed first credited {previous_name}." (three
languages). Only the GOAL correction produces a card: confirm the coordinator skip at `coordinator.py:87` covers
the shot correction, add a test if not.
Tests: replay demo → exactly one correction insight, text names the goal minute and both players.
Contract: none.

### P2-d  Signal handlers see the match as of the signal (flaky cassette test)  (RUNTIME:)
Found while reviewing P2-a/b: `tests/test_cassette.py::test_live_replay_is_served_entirely_from_the_recording`
fails intermittently under CPU load with a cassette miss `rewrite|shot_speed_badge:ml-demo-001-e01350|r0`.
Pre-existing, not caused by this work: under equal load (14 parallel runs × 3) the merge base 8839190 failed 9/21
and the working tree 7/21; it never fails on an idle machine (20/20 serial passes).
Root cause: `runtime/session.py:127-128` starts each signal handler with `asyncio.create_task`, so
`InsightPipeline.handle` builds its pack (`signal_pack(session.engine, signal)`, `agents/pipeline.py:455`) whenever
the task first runs. Under load the feed loop has already ingested later events (here the GOAL right after the
79' shot), so the pack's score is 2-2 instead of 1-2. The recorded draft says 1-2, the check against the late pack
fails, and the rewrite is not in the recording. In production the same race lets a live insight quote a score or
minute from after its own moment. The tasks are also not referenced, so they can be garbage-collected mid-run
(asyncio docs).
Fix: start handler tasks eagerly (`asyncio.eager_task_factory(loop, coro)`, Python 3.12), so the synchronous
prefix (coordinator decision, pack) runs in feed order before the next event is ingested; keep the tasks in a set
with a done-callback discard.
Tests: a deterministic repro. A handler that records `engine.score()` at its first line, with a feed of a shot then
a goal at the same instant, must see the pre-goal score. Then the cassette live-replay test 10× under
`pytest -p no:randomly` plus parallel load passes.
Contract: none.

### P3-a  Player minutes and duels  (INTERPRET:)
Fix:
- Minutes: accumulate playing seconds per player in `_apply`: on each event while `status == "live"` and in the same
  period, add the clock delta since the previous event to every player with `on_pitch`. Starters are on pitch at
  kick-off; subs from the substitution; off at substitution or red card. Expose `minutes` (int, rounded down) in
  `PlayerStats.as_dict`. Remove the unused `minutes_from`/`minutes_to` or set them.
- Duels: a PRESSURE event opens a contest between `player_id` (presser) and `related_player_id` (carrier). It is
  won by the presser if the next event is a TACKLE by them, by the carrier if the next is a FOUL by the presser or
  anything else. Expose `duels_won`, `duels_total` per player. Document the definition in the docstring.
Tests: a starter who plays the whole demo has 90-ish minutes (between 90 and 100); a sub's minutes equal FT clock
minus sub clock (±1); a red-carded player stops; duels_won <= duels_total and team sums are consistent with
tackles.
Contract: additive `minutes`, `duels_won`, `duels_total` on player stats. Tell implementer (Players table Min and
Duels columns).

### P3-b  Per-half possession and field tilt  (INTERPRET:)
Fix: `TeamStats.possession_s_by_period` and `final_third_passes_by_period` (dicts keyed by period). `_set_possession`
credits the current period. `team_stats` adds `possession_pct_by_half: {"1": x, "2": y}`, `field_tilt_pct`
(team final-third passes / both teams' × 100) and `field_tilt_pct_by_half`; home + away = 100 per half (rounding
like the existing possession fix).
Tests: halves sum to 100 for each half; whole-match possession is the time-weighted mix of the halves; before the
second half `"2"` is absent or None.
Contract: additive team stats fields. Tell implementer (report By half row: possession and territory).

### P3-c  Tactics board says how many ideas it was asked for  (TACTICS:)
Fix: `TacticsBoard.requested: int` (= `request.n`) and `shortfall: str | None`, a plain sentence set at conclusion
when `len(tactics) < requested`, e.g. "2 of 4 ideas ready: the AI ran out of time, so only the built-in recipes were
tested." (from the `ideate:` error kind: timeout / failure / rejected ideas).
Tests: an ideate that times out with n=4 gives `requested == 4` and a shortfall sentence; a full board has None.
Contract: additive. Tell implementer to show `shortfall` under the board.

## Deferred (with reason)
- Pitch-layer feed (shape lines, heat map, pass network, press intensity): a new API surface and design; the data
  exists (tracking frames, pass events) but needs a spec from designer/coach first.
- Duels and minutes for archived real-league matches (`leaguedata`): source data has no duel events.
- Coach half-time variant of the report and a "review" set of FT items: the coach asked for it (coach-review-2
  §Report); needs product wording, not a bug fix. After P1-c the FT brief can be shorter; if it becomes empty it
  falls back to "Keep the shape", which is acceptable for now.
- Coach `_View.minute` uses `elapsed`, which includes first-half added time; minor drift in "minutes left" late on.
  Noted, not user-visible enough to fix now.
- Momentum scale explanation in the UI: frontend copy (bar already shows -100..100 in analyst mode).

## Status
All non-deferred tasks approved. Final full suite: 519 passed, 31 skipped (baseline 449 passed).

| Task | Status | Commit |
|------|--------|--------|
| P1-a | approved | 95120da |
| P1-b | approved | 95120da |
| P1-c | approved | 98fb995 |
| P1-d | approved | 043e352 |
| P2-a | approved | b43eb55 |
| P2-b | approved | 18e0600 |
| P2-c | approved | dd256f2 |
| P2-d | approved | b37b034 |
| P3-a | approved | dd256f2 |
| P3-b | approved | fb9a5a1 |
| P3-c | approved | 1b1a161, 857b9a7 |
