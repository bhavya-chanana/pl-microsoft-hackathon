# MatchLens: Coach's review (opposition analyst / bench view)

Reviewed in the browser only, 1440x900 and 1024x768, across all seven screens. The test match was the synthetic Northbridge v Harbour replay at 15x. Screenshots are in the scratchpad `coach/` folder.

## 1. Workflow it supports
- **Pre-match prep: none.** There is no opponent profile, no shape or lineup view, no set-piece or press patterns and no previous-match comparison. The Tactics board could do prep, but it only works on a "moment" in the current match.
- **Live:** partly. The Match centre pitch, momentum, shot and event feeds exist. The **Coach** page is the best idea in the app: 3 ranked actions, each with evidence, an instruction and a trade-off. It took ~15 s to generate and showed 3 grey skeleton boxes meanwhile. That is too slow for a bench.
- **Half-time:** there is no half-time view. I have to assemble it myself from the Stats tab, Ratings, Timeline and Coach. "What did I miss?" and "Match recap" are fan features. The recap modal still read "The recap agent is writing" with an empty body after 6 s.
- **Post-match:** weak. There are no exports and no clip list. "Past matches" shows a score only. The "Download the dataset" button is buried in the Scenario lab. There is no report, PDF or share.
- The app is pitched at fans (casual, language, club, polls, "Multiverse", "Broadcast output", "Agent room"). Roughly half the navigation is irrelevant to a coach.

## 2. Trustworthiness and legibility
Trust is the biggest problem. I found contradictions within minutes.
- The event feed says "Yellow card for Dominic Achterberg. Harbour City now have 11 players on the pitch". A yellow card does not reduce the player count, so the sub-line is meaningless.
- "Correction at 65': Elias Brandvold is now Kenji Arakawa". A scorer changing after the fact makes me distrust every attribution.
- "Momentum swings to Harbour City: moved from 68.2 to 0.0". A drop from 68 to 0 in 4 minutes is not believable. Momentum is also unitless, with no scale or definition.
- The pitch banner said "Momentum swings to Northbridge FC" with value -8.5, which is a Harbour-side number. The bar underneath said "Even". The banner and bar disagree.
- At 62', Northbridge had 55% possession, 11 shots and 2.00 xG and still trailed 0-2. That is plausible, but nothing explains it. The momentum bar read "Northbridge on top", so the headline says dominance while the score says otherwise. The app never reconciles the two.
- **Tactics board recommends "best of 2 ideas" when every outcome is 0% against a 0% baseline.** The verdict reads "shot chance 0% against 0%", with a "Recommended, medium confidence" badge. That is false precision, and a coach would stop trusting it immediately. It also says "2 of 2 ideas came from built-in recipes because the AI did not answer in time", so the AI part silently failed. I asked for 4 ideas and got 2.
- Coach brief at FULL TIME (90+5', Northbridge 3-2) still tells me to "Use the extra player" and "Protect the box". It does not know the match is over. The brief is stamped "As of 90+5'", which at least shows when it was generated.
- Good: "How this was checked", "Fact-checked" and "Match data" badges are the right instinct. They are tiny, though, and are not shown on the Tactics verdict.
- **Early-phase signals:** the 1' to 10' state shows a 42.9 momentum reading and 0-vote polls. It looks authoritative without enough data.

## 3. Information that is buried or missing
- Formation and shape (shown as shirt-number dots only). No names on the pitch dots. No heatmaps, pass networks, passing lanes, pressing intensity, PPDA, field tilt or xT/OBV. There is no ball-recovery map and no xG timeline, which is the standard post-match chart.
- Substitution context: no list of who is on and off with minutes played and fatigue.
- Subs, cards and goals are mixed into one flat Timeline, which cuts off mid-row at the bottom (clipped "Save" row at 1024 wide). There is no filter by team, event type or player.
- Ratings is a 22-row table with "Fans / You" voting columns that take 40% of the width. Coaches do not want fan votes next to data ratings.
- Stats lacks per-half splits, last-15-minutes trends, duels, aerials and progressive passes. "Corners 5 v 1" and similar bars are decorative; no thresholds flag what matters.
- The Stats tab says "Pick a favourite player to see their card" with the dropdown far away in another panel.
- No video or clip link. Items say "Watch it back" but there is no coach-friendly clip tray or tagging. A recorded-video feature (video-128057) is hidden at the bottom of the page.
- No score/time context in Coach and Tactics headers beyond a small stamp. At 1024 wide the score is tiny.

## 4. Visualizations
- **Pitch map (Match centre):** the idea is right but it is too small and flat. At 1024x768 it shrinks to ~300 px tall. Players are 14 px dots with numbers; there is no team name or direction of play arrow. The ball is a tiny white dot. Overlay captions (momentum, goal) sit on top of the pitch and hide players. At 85' the TV-camera label changes to "REPLAY" with a yellow chip, which is clear.
- **Match pulse (timeline):** this is genuinely good. Momentum area chart, event diamonds and a control/balanced/end-to-end strip are the best part. But it is clipped at the top (the "10': Northbridge on top" tooltip overlaps the axis) and the strip colours are a low-contrast teal/pink. Control versus end-to-end is not legible without reading the legend. The control strip is the part I would put on a half-time slide.
- **Shot map:** works on the Analyst view (markers by type: goal, saved, off target, blocked, plus a "latest shots" list). The Stats tab version is huge, full pitch, all shots piled on one end, circles overlapping so it is unreadable in the box. Use a half pitch, zoom to the box and use team colours with xG labels.
- **Stat bars:** clear, large numbers, good. Labels like "Corners" sit centred over the bar and are low contrast.
- **Tactics replay pitch:** ghost trails, labels like "screen lane / man mark" overlapping at the penalty spot. At 1024 wide, labels collide and are unreadable ("Velasquez", "cover shadow", "man mark" stack on each other).
- **Outcome bars (Tactics):** tick marks and "range 7% to 36%" are the right honest idea. But every row says "0 pts" and the 0.00 xG headline is huge, which hides the real message.
- **Scenario lab:** the mini momentum sparklines are neat, but the lab is a demo tool and not for coaches.

## 5. Speed to an insight
- Fast: score, momentum, latest event are on screen in about 2 s.
- Slow: Coach brief ~15 s (grey boxes), Tactics build ~10-30 s with no progress bar, recap modal very slow.
- To get a half-time picture I clicked through 4 tabs and scrolled ~2,200 px on desktop. Nothing summarises "what changed since last check".
- "What did I miss?" and "Ask the pundit" open the same scrolling feed rather than a clear answer. Pundit gave no visible answer box.

## 6. Readability under time pressure
- The default match runs at 15x. At 15x a goal and the next event flash by; there is a "Live insights" panel at 69', 72', 74', 77', 79' within a minute of wall time. No "Pause on new insight" is offered other than the global Pause.
- Small grey type on dark (10-11 px captions, Control/Balanced/End to end legend, fan-poll blurbs) fails the arm's-length test. Insight cards have a 1-line header and a collapsed "Match data / How this was checked" with tiny text.
- Colours: blue and orange for teams are fine; pink/teal strip and yellow "REPLAY" chip add noise. Red LIVE chip is good.
- The Coach page cards are the standout for readability: big numerals, Evidence / Do this / Trade-off labels. Use that structure elsewhere.

## 7. Bugs (layout, scroll, state)
1. **Match feed went "unavailable" (502) at first load for ~30 s**; the page offered "Try again" but nothing else. The pitch was blank and the fan panel showed empty states. Needs a cached last state and clear stale-data timestamps.
2. Full-page screenshots show the sticky nav appearing mid-page overlapping content (e.g. across the Match pulse chart) while scrolling at 1440. Real users will see the chart cut by the header on scroll.
3. Timeline list at 1024 is clipped mid-row inside a fixed-height scroll box with no scroll affordance; the Live insights column on tablet is empty (a thin strip) while the match runs.
4. At 1024 the nav shortens labels ("Match", "Coach") but the DOM also holds both names. Tablet is OK but the pitch gets small and the right rail pushes it.
5. Fan polls sit between the pulse chart and the stats and take a full row. A "Who wins from here?" poll at 0 votes is a dead block.
6. Demo controls (speed, restart, start minute) are mid-page in production layout and the Restart button is the most prominent in the whole block. One mis-tap by a coach on an iPad resets the match.
7. The Tactics "Build" button label says "Build again" while disabled/loading, with no progress state.
8. Match centre's "Past matches" shows "Northbridge 0–0 Harbour, Stopped · 1'" for old runs. Stale clutter, no names or dates other than a timestamp.
9. Ratings table: 22 rows with "You" dropdowns; at 1024 it overflows into two crowded columns.
10. Coach brief and Tactics header lack a team-colour cue; I had to read the tiny "Coaching Northbridge FC" toggle to know which team I was advising.

## 8. Top 10 asks (priority order)
1. **Match centre / Coach / Tactics:** fix data integrity. Momentum must have a defined scale and consistent banner/bar; cards must not say "11 players" under a yellow; corrections must be explained. Show a timestamp and a "data confidence" marker on every number.
2. **Tactics board:** do not recommend when differences are within noise. Show "no clear winner" and the margin; label fallback recipes clearly; give a progress bar.
3. **New "Half-time" screen (and "Full-time" report):** one page: score, xG timeline, shot map, possession/territory, top 3 coach actions, subs and cards, and a PDF/share export. This is the single most valuable addition.
4. **Match centre:** make the pitch the hero. Larger, with player names on tap, direction of play, ball, and toggles for shape, heatmap, passing network, pressing. Move overlays off the playing area.
5. **Coach:** instant load (cache and refresh in the background), "what changed since last brief", an explicit phase-aware state ("Full time: no actions"), and a pin/dismiss per recommendation.
6. **Match centre / Timeline:** filterable event list (team, goals, cards, subs, shots), with each row jumping the pitch to that moment and opening a clip. Add the xG race chart.
7. **All screens, tablet:** bench-ready layout at 1024x768: minimum 14 px text, 44 px targets, no tiny grey captions, no sticky nav covering content, pitch not shrinking below ~420 px.
8. **Ratings / Stats:** drop fan voting from the coach view; add sortable columns, minutes played, tag the key players and per-half and last-15 splits; zoom the shot map to the half pitch with xG labels.
9. **Navigation:** a "Coach mode" that hides Multiverse, Broadcast, Agent room and fan polls, and puts Demo controls behind a confirm. Add a pre-match opposition profile entry point (shape, set pieces, who to press).
10. **Recap / "What did I miss?" / Pundit:** make them return fast, scannable answers (3 bullets) with a source badge; fix the empty recap modal; add a "pause at next key event" option for fast replay.
