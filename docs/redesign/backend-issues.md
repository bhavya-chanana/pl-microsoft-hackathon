# Backend causes of the coach's "trust problems"

Frontend only renders backend text and numbers here. I did not touch the backend.

1. "Harbour City now have 11 players on the pitch" under a yellow card.
   Cause: `backend/src/matchlens/agents/templates.py:56` (card template) always appends
   "{team} now have {on_pitch} players on the pitch" using `players_on_pitch` for every card type.
   Meaningful only for red cards. Fix: use that sub-line for red cards only; for yellow, say the card count or nothing.

2. Momentum banner says "swings to Northbridge" with value -8.5 while the bar says "Even".
   Cause: templates.py:66 builds the banner from `from_index`/`to_index` and picks the team by the event's data.
   The sign convention is -100 (away) to +100 (home); -8.5 is the away side, so the team pick looks inverted
   (or uses a stale index). The bar (frontend `momentum-bar.tsx`) treats |index| < 10 as "Even", which is correct for -8.5.
   Fix: choose the team from sign(to_index), and skip the banner when |to_index| < 10. Also "68.2 to 0.0" in 4 minutes
   suggests the index is windowed or reset; the unit/scale is never defined for the user.

3. Tactics "best of 2" at 0% vs 0%.
   Cause: `backend/src/matchlens/tactics/board.py` (~line 335-350) always picks `scored[0]` as the recommendation and
   writes "is the best of the N ideas tested" even when intervals overlap the baseline. Frontend now shows
   "No clear winner" when the recommended idea's shot and concede ranges overlap the baseline (committed), but the
   backend should return `recommended_tactic_id=None` in that case. Also: asked for 4 ideas, got 2 because the AI timed out
   and recipes filled in; the UI does not say so reliably.

4. "Use the extra player" at full time (90+5', 3-2).
   Cause: `backend/src/matchlens/agents/coach.py` `find_issues` drops LIVE_ONLY detectors only when
   `engine.state()["status"] == "full_time"`. The extra_man item was still emitted at 90+5', so either the status was
   not yet `full_time` when the brief was generated (stoppage time), or the extra-man detector is not in LIVE_ONLY
   (LIVE_ONLY = _game_state, _card_risk, _fresh_legs). Also the brief is cached with "As of 90+5'" and not regenerated on
   the status change. Fix: add the extra-man detector to LIVE_ONLY and invalidate the cached brief on full time.

## From fan review 2
- Engine emits a `kickoff` event after every goal (restart), with minute labels such as 90+3'. The frontend now keeps only the first kick-off per half; the backend should emit these as `restart` or omit them.
- Insight key_numbers sometimes carry the unit inside `value` ("12.7 m") as well as in `unit`. The frontend now drops the duplicate; the backend should send one or the other.
