# MatchLens: fan review (role-play: passionate non-technical PL supporter)

Tested at 1440x900 and 390x844, dark theme, replay at 1x and 60x, every nav page.

## First impression (5 seconds)
Desktop, first load: a dark page with "MATCH FEED UNAVAILABLE / The match engine is not responding". That is a dead app. (The backend was warming up; 11 x 502 in console. A fan would have left.) On the second load it worked: a clean title "Northbridge FC v Harbour City", a scoreboard strip, a 2D pitch with dots. Looks like a dev dashboard or a FIFA-26 debug view, not FotMob. Nobody I know has heard of either team; "Matchlens Invitational League" tells me it is fake. I want my real team.

## Did I understand what it does?
Partly. "Live insights", "What did I miss?", "Ask the pundit" are great hooks. But the 8-item top nav (Match centre, Multiverse, Broadcast output, Coach, Tactics board, Scenario lab, Agent room) is a menu for the builders, not for fans. I have no idea what "Multiverse" or "Agent room" are for until I click.

## Confusing / jargon
- "Multiverse", "Broadcast output", "Scenario lab", "Agent room", "Agent p95", "Engine p95 0.3 ms", "Coordinator decisions: Narrator only / Data card / All agents", "recorded-demo+azure:gpt-5-mini(low)/gpt-5-mini(high)" (truncated with ... on mobile). Pure engineering.
- "Match data" badge on every card, "How this was checked", "Fact-checked", "Blocked, data shown". I do not care how it was checked; show a tick or nothing.
- xG, "pass difficulty 76.4/100", "Momentum -68.4", "Turnovers/min 2.8", "Pass share 60.8%" with no explanation. Momentum -22.6 for Harbour: what does minus mean?
- "Match pulse" chart: caption is a paragraph of instructions; the chart itself is a thin line and tiny coloured blocks. Unreadable.
- "Demo controls", "Replay speed 60x", "Start from minute", "Past matches", "Recorded matches: video-128057 Play". Raw file name shown to users. "Showing" label is cryptic.
- "Your view": Fan mode Casual/Analyst/Player focus, "Headline metric: None", "Favourite player: None". Five settings in a column before I see any value.
- Tactics board: "Risk appetite", "Look ahead 10 s", "Ideas 3 4 5", a blank text box. Needs a goal-first flow.
- Coach page: three empty grey boxes with "Reading the match for the bench" for a long time. Looks broken.

## Boring
- The Stats tab: rows of numbers, bars with tiny labels centred over thin lines. Possession 57/43 is fine but everything else (corners 0, fouls) is flat. No form guide, no lineups, no table position.
- Timeline: plain list "Shot Elias Brandvold, off target (xG 0.19)". No icons that read as goal/card/sub at a glance, no minute scrubber.
- Agent room: it is a system diagram (React Flow). Cool for a demo, zero fan value. Should be hidden behind "How it works".
- Insights feed cards are all the same weight: a goal looks the same as "End-to-end chaos".

## Delightful
- The pitch with moving dots, ball trail and a TV-style overlay ("Samir Haddou hits 33.2 km/h", "Elite pass from Yusuf Demirkan"). This is the shareable bit.
- "What did I miss?" and "Ask the pundit" buttons. Catch-up is a killer feature when I am late to the pub.
- Multiverse: same match told in Hindi, Spanish, casual vs analyst vs coach. Genuinely clever, very shareable ("my nan gets it in Hindi").
- Fan polls ("Who wins from here?"), listen/read-aloud toggle, light/dark switch, momentum bar.
- Yellow-card and goal moments get coloured left edges on mobile: good start.

## Would share with mates
Goal moments as a story card/clip ("GOAL! Dominic Achterberg scores for Harbour City 23'"), the multi-language view, the poll, and the Scenario lab ("The Kestrel Park Comeback", "The Ten-Man Heist" are fun). There is no share button anywhere.

## What brings me back on matchday
Notifications for goals/cards, my club pinned automatically, a live score on top that stays visible, a one-line "story so far", a fixtures list / other games, and chat or poll results. Today there is one fake match and no reason to return.

## Readability problems
- Muted grey small text on near-black (captions under Match pulse, "Votes are anonymous", stat labels at ~11px) is hard to read.
- Stat labels sit over the bars, tiny and low contrast.
- Overlay cards on the pitch (top-right "Elite pass", bottom-left "Samir Haddou hits...") cover players and the goal area; the overlay hides ball action.
- The pitch does not fill its container on desktop; big empty gap to the right, overlays hang off the pitch edge.
- Mobile nav is 7 identical icons with no labels; I cannot tell Multiverse from Tactics.
- Mobile match page is ~2,500px of stacked cards; the scoreboard scrolls away (the tiny header shows no score).

## Bugs and layout issues
1. First desktop load: "Match feed unavailable" plus "Insights could not be loaded" with 502s; no skeleton, no auto-recover visible. 
2. Desktop: after clicking a tab or button the page scrolls so the sticky header overlaps content (the top of the poll card is cut under the nav). Clicking Stats/Timeline jumps the page.
3. Desktop layout: left column has big empty areas (poll card, Demo controls, Past matches sit in narrow boxes with blank space beside them); right sidebar starts lower than the pitch so the page feels unbalanced.
4. "Past matches" list showed "Northbridge 0-0 Harbour In play 1'" while the match was at 17'+ (stale at 60x), and the Next.js dev "N" badge overlaps the text.
5. Mobile: duplicated "Match pulse" and tabs; page height 2471px; Demo controls and Past matches inside the main flow are in the way.
6. Multiverse "Player focus: Quilmes" panel says "Waiting for the first moment." and Coach panel is an empty box at 14'. Empty states with no explanation.
7. Coach page and Tactics: blank placeholders; Coach never filled during my test.
8. Possession: totals added up to 100 (57/43) so that earlier fix holds.
9. Broadcast output page: scoreboard and overlay only, no controls; I did not know I was meant to put this on a TV/OBS.

## Top 10 asks (priority order)
1. Make the first screen a real matchday hub: big live score, the pitch, and one "story so far" line. Never an error wall; show skeletons and auto-retry.
2. Cut the nav to 3-4 fan tabs (Live, Stats, Tactics/Coach, Fan modes). Move Agent room, Broadcast, Scenario lab, Demo controls under "Behind the scenes" or a settings sheet.
3. Plain English everywhere: replace xG, Momentum numbers, p95, "Match data", "How this was checked" with fan wording and a tooltip. Hide model names.
4. Make goals/cards/subs big, visual moment cards with a share button; make minor insights quiet.
5. Sticky mini scoreboard on mobile with the score and minute always visible; labelled bottom tab bar.
6. Fix layout: pitch fills its card, overlays do not cover play, sidebar aligned, no scroll jump on tab press.
7. Onboarding in one step: "Pick your club and mode" (Casual/Analyst), not five dropdowns of None.
8. Stats redesign: larger numbers, labels above bars, my team's colour highlighted, a short "who is winning this" summary.
9. Make "What did I miss?" and "Ask the pundit" front and centre (floating button); show the pundit answer as a chat bubble.
10. Matchday hooks: notifications, other fixtures, poll result display, real team names and badges (or at least crests), a replay highlights reel.
