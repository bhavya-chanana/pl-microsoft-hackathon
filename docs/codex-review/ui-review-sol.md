# Independent UI review — Sol

Reviewed 27 October 2026 against the current worktree at `/private/tmp/matchlens-ui-redesign-codex`. This is a runtime review plus a targeted audit of the uncommitted frontend changes, from fan, coach and enthusiast perspectives. No application code was changed by this reviewer.

## Runtime and evidence boundary

- Frontend `http://127.0.0.1:3030`, backend `http://127.0.0.1:8770`; separate headless Chromium contexts, not the parent's browser.
- Live pass: paused synthetic/cassette demo, Northbridge 0–2 Harbour at 62′. FT pass: Northbridge 3–2 Harbour at 90+5′. Parent controlled global replay; this reviewer did not restart, seek or change its speed.
- Live, Explore, Broadcast, Coach, Moments, Stats, profile, voting, catch-up and evidence checked at 1440×1000 and/or 390×844. FT Coach/Report/Tactics also checked at 1024×768. No document-width overflow on sampled primary pages at those sizes; no page errors in the completed desktop pass.
- One demo fan vote was submitted for Quilmes: 8, and the visible fan average became 8.0. Browser preferences were changed locally for coach-view checks.
- Initial unstyled output came from a development-server/Tailwind setup problem. It disappeared after the parent's server repair and is excluded from product findings.
- Screenshots are temporary review evidence under `/private/tmp/matchlens-ui-review/`; none were added to tracked source. Screenshot filenames below refer to that directory.

## Prioritized findings

### P1 — Broadcast controls obscure the score on a phone

**Observed:** At 390×844, the active Exit / Copy overlay link / sound pill covers nearly the whole scorebug. Each touch wakes this pill; it therefore hides the core match context precisely while the viewer is interacting. The idle view restores the score, and keyboard Tab correctly reveals/focuses Exit, so the problem is placement rather than missing controls.

**Evidence:** `sol-mobile-broadcast-390.png`, `sol-mobile-broadcast-idle.png`, `sol-mobile-broadcast-keyboard.png`. `frontend/src/features/broadcast/broadcast-controls.tsx:77` anchors the pill at top-centre at every width, while `broadcast-monitor.tsx` places the score top-left.

**Fix:** Use a phone-specific control position below the scorebug or along the bottom. Keep the full score and clock readable while controls and first-visit hint are visible. Recheck portrait and keyboard focus.

### P2 — Coach hides its primary action below the entire chart on narrow screens

**Observed:** On a phone, Overview initially shows only “The game so far”, xG, momentum, territory and possession. The first recommendation is below the first viewport. At FT the same ordering hides “See the full-time report” beneath the chart, despite that being the primary next step. Desktop shows the actions and evidence side by side and is much faster to use.

**Evidence:** `sol-mobile-coach-390.png`, `sol-mobile-coach-full.png`, `sol-ft-coach-390.png`. `frontend/src/features/coach/overview.tsx:336` gives actions `order-2`; line 339 gives GameSoFar `order-1` below 1100px.

**Fix:** Put recommendations, or the FT report CTA, first below 1100px; keep the supporting chart next. The coach should read the first action and its minute without scrolling. This also makes the existing FT guard useful immediately.

### P2 — Coach's provenance popover contradicts its data-only badge

**Observed:** The demo brief correctly displays “From match data”. Source inspection shows its popover always uses “Coordinator ranks from the stats engine; the Coach agent words it; the Fact-Checker verifies it.” That describes an agent pathway even when `source === "data"`. The badge was improved in this pass; its explanation needs the same conditional treatment.

**Evidence:** `sol-desktop-coach-full.png`; `frontend/src/features/coach/overview.tsx:357` selects the honest badge, but line 359 always renders `copy.coach.pipeline`; `frontend/src/lib/copy.ts:38` supplies the unconditional agent claim. The current runtime brief source was `data`. This is a source-confirmed copy mismatch; the popover itself was not separately captured.

**Fix:** Use `copy.factCheckHelp` for the displayed status or source-specific pipeline copy. A direct-data path should say that no language model generated it. Retain the agent explanation for an actual agent result.

### P2 — Highlights places the break after the second-half restart

**Observed:** In the descending Highlights feed, 45+2′ “Half-time” appears above 46′ “Kick-off”. Reading from old to new therefore puts the restart before the break. The Timeline view correctly places 46′ Kick-off above Half time 45+2′; this is specific to insight ordering.

**Evidence:** Runtime text captured on both desktop and mobile at 62′. `backend/src/matchlens/agents/services.py:225` sorts personalized insights only by descending `elapsed`. First-half added time exceeds the second-half starting clock. `frontend/src/features/companion/match-timeline.tsx` instead reverses the delivered event sequence and displays the right order.

**Fix:** Sort insight feed entries using period and event/feed sequence, preserving proper order across added time and period boundaries. Do not change the correctly ordered Timeline as a workaround. The API feed currently does not expose a period/sequence field to the frontend.

### P2 — Mobile Live's action pill covers the headline stat values

**Observed:** On the first 390×844 Live screen, the Catch me up / Ask the pundit pill lies over the Possession/Shots/xG numbers. The values can be revealed by scrolling, but the first screen presents labels with obscured values. Bottom padding makes the end of the page reachable; it cannot prevent fixed chrome covering content throughout scrolling.

**Evidence:** `sol-mobile-live-390.png`. `frontend/src/features/live/floating-actions.tsx:67` fixes the bar above the bottom tabs; `match-centre.tsx:32` reserves padding only at the end of the document. The source comment “Never floats over content” is not true of this runtime layout.

**Fix:** Give the action dock an opaque/reserved viewport region, or use inline actions before the pitch and a compact action after scrolling. Recheck initial Live, the stats strip and Match flow while scrolling, rather than only page-end reachability.

## Verified improvements and retained flows

- **Broadcast discovery and continuity:** The labelled Live button, Explore tile and palette result are present. Live → Broadcast → Exit to Explore retains 62′ and 0–2. Copy overlay link returned `http://127.0.0.1:3030/explore/broadcast`. Direct `/broadcast` redirects to that path. Exit works, sound starts off, and Tab focuses Exit after the controls idle. Broadcast remains available with Coach view enabled. Exit intentionally lands on Explore, so a user entering from Live needs an additional navigation step to return there.
- **Fan:** Mobile cards are full width; the historical clipped carousel was not reproduced. Catch-up returned 47′–62′, then/now 0–2, relevant bullets and “From match data”. Profile becomes opaque once its opening animation settles. The restored replay tile opens Stream settings with actual speed, pause/resume, restart and past-match controls. No restart was performed.
- **Trust:** A 62′ card says “Checked against match data”; Details repeats that status and lists supported card, score, event and momentum claims plus participating agents. Raw 55′/52′ cards say “From match data”. A user-requested moment replay returns to Live with an explicit REPLAY label and Back to live control. No video playback or video event extraction was verified.
- **Enthusiast:** Players has sortable match statistics, fan average and direct vote controls. One vote immediately refreshed the average. On phones the table scrolls horizontally with an explicit swipe instruction and a sticky player name. Coach view removes the voting columns and poll section. Full-time Report shows corrected scorer Arakawa at 64′, scorers, per-half data, key-player reasons, shot map and lineups. Rendering and page widths were checked; PDF export was not tested in this pass.
- **Coach:** Current brief shows three ranked actions with confidence and Why/evidence controls, a current-minute label, refresh and a watch list. At FT it replaces live recommendations with a report CTA. Tactics disables building from the finished live state, so the historical ability to issue new in-match instructions at FT was not reproduced.

## Secondary polish and limits

- FT Tactics says “Pick a moment to review” while the moment picker is inside Fine-tune, and the empty board still says to build ideas. Link the instruction to the picker or expand it at FT. This is discoverability/copy, not a claim that the capability is absent.
- The evidence sheet's supported momentum claim is rendered merely as “Momentum”; `insight-card.tsx:35` expects `team_id`. A plain-language subject/direction would make the proof understandable. Status agreement was verified, not the truth of every underlying fact-check; the backend review owns that validation.
- Highlights promotes corrections to full cards in the changed source, and the FT report uses the corrected scorer. The correction transition from old attribution to new attribution was not watched live in this pass. Do not present that transition as runtime-verified here.
- Reconnection, unavailable backend, first-half/half-time phase transition, generated Tactics outcomes, multilingual speech, external OBS capture and PDF export were not exercised. Source changes scope Coach brief queries/cache to run/team/language and replace minute-range half-time inference with the engine status; these are appropriate fixes, but all state transitions require a separate run.

## Perspective verdict

**Fan:** The score, story and pitch now form a coherent starting point, and Broadcast is discoverable. Fix the mobile control collisions before demoing a phone.

**Coach:** The available recommendations and FT report are useful. Put the action first at narrow widths and make data-only provenance honest throughout its explanation.

**Enthusiast:** Statistics, votes, moment details and reports support real exploration. Fix period-boundary ordering and retain the distinction between generated language and direct match data.

The changes restore concrete capabilities without a new navigation system. The remaining findings are layout, ordering and provenance issues; no evidence from this pass supports calling Broadcast removed or the core mobile navigation broken.

## Final production verification

The integrator repeated checks against a successful production build after the implementation passes. At 390, 1024 and 1440 pixels, Broadcast score/control bounding boxes do not overlap, the page has no horizontal overflow and the browser reports no page errors. Controls stay at the bottom through tablet widths and move to the top-right on wide desktop screens. The mobile full-time Coach report action appears before the chart; Live actions are in the document flow. Source-aware provenance replaces the unconditional coach-agent description, and evidence views support the report's optional verification scope.

The final API check has second-half kickoff at feed index 25 and half-time at index 26, so the equal-elapsed boundary order is correct. A full warm-up to the hero's 3–2 full-time state produces zero newly scheduled early-stage graphics. Historical insights remain available. These checks resolve the five actionable first-pass findings and the tablet collision discovered on the second pass.

Final production screenshots and machine-readable bounds are in ignored `logs/final-review/` (`broadcast-390.png`, `broadcast-1024.png`, `broadcast-1440.png`, `coach-390.png`, and `production-ui.json`). Earlier screenshots and findings remain above as the review record, not a claim that those defects remain present. Online Speech, external OBS capture and PDF export remain outside the checks performed.
