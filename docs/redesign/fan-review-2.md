# MatchLens: fan review 2 (redesigned build, localhost:3020)

Tested at 1440x900 and 390x844: onboarding, Live, Moments (Highlights and Timeline), Stats (Team, Players, Shots), Coach (Brief, Tactics, Report), Explore (Multiverse), Cmd-K, Catch me up, Ask the pundit, Share, profile menu. The match was already at full time (3-2) every time I loaded, so I never saw a match in progress; live-feel is unverified.

## Verdict
Night and day. It now looks and reads like a football app (FotMob/Apple Sports territory), not a dev console. Score is big, the pitch is the hero, moments are cards, and I can actually find things. A handful of bugs and one broken mobile section still stop it feeling finished.

## What got fixed (vs my original top 10)
1. Matchday hub, no error wall: FIXED. Big score + FT badge, italic one-line story ("Northbridge FC beat Harbour City 3-2."), pitch, "Latest moments". No errors in console on any page.
2. Nav cut to fan tabs: FIXED. Live / Moments / Stats / Coach / Explore, labelled bottom tab bar on mobile. Agent room and demo controls are gone from the main flow (Explore holds Multiverse, Broadcast, Scenario lab, Replay a match).
3. Plain English: MOSTLY. No more "Match data", "p95", "How this was checked". "Momentum -22.6" gone. xG is still shown on goal cards but "Chances created" is the headline stat. Good.
4. Goal/moment cards with Share: FIXED and delightful. Goal card has big minute, colour edge, Share ("Copied to clipboard" toast), "Replay on pitch", Details.
5. Sticky mini scoreboard + labelled tab bar: FIXED. Compact "Northbridge 3 - 2 Harbour 90+5' FT" pill follows me on scroll.
6. Layout: MOSTLY. Pitch now fills its card, no overlays covering play, no sticky-header overlap or scroll jump. Desktop is tidy.
7. Onboarding: FIXED in concept. "Who are you here to watch?" Northbridge / Harbour / Neutral + Casual/Analyst + "Start watching". Lovely.
8. Stats: FIXED. Verdict sentence ("Northbridge FC are on top: more chances created, shots, possession."), three big hero numbers, clear bars with labels above, "Chances created over time" line chart with goal dots, Players ratings table, Shots map.
9. Catch me up / Ask the pundit: FIXED. Floating pill. Catch me up: sheet with 5/15/30 min, "Then 2-2, Now 3-2", bullet list. Pundit: side panel, quick answer with "Checked against match data", Read aloud, rating/goals/assists chips. Great.
10. Matchday hooks: NOT DONE (no notifications, other fixtures, crests are just letter circles N/H).

## What is still wrong
- Mobile Live "Latest moments" is broken: a sliver of a goal card ("9", "GC", "sc") pokes in from the right edge as a clipped horizontal carousel next to "Full-time Final score:..." which is truncated with an ellipsis. Looks like a bug, hides the key goal. It also leaves a big empty gap below.
- Floating "Catch me up / Ask the pundit" pill overlaps the momentum bar on desktop (covers the middle of "Momentum: Northbridge on top") and sits on top of the "Match flow" card on mobile.
- Profile / "Your view" dropdown is transparent: "Latest moments" and the goal card text bleed through behind the Favourite player / Headline metric fields. Hard to read. It still has the old "Favourite player: None" / "Headline metric: None" dropdowns and a "Developer tools" link.
- On mobile the profile button did not open for me at first tap (my automated tap hit nothing; the avatar sits next to the search icon and both are small circles overlapping the header). Treat as a possible tap-target issue.
- Cmd-K: typing "goals" gives "Nothing found. Try a page name." A command palette that says "Search or ask..." should find moments, players and answer questions. It only knows page names.
- Onboarding: both Northbridge FC (focus ring) and Neutral (checkmark) look selected at once, so I could not tell what I picked. Tapping a club did not clearly change the highlight.
- Polls: "Who wins from here? ... Final result 0% / 0% / 0%, 0 votes, Closed" and "Player of the match? 0 votes" are dead; a closed poll with 0% across the board is useless noise on the Live page. The poll is below the fold, easy to miss.
- Coach at full time shows "nothing to change on the bench" and an empty Brief: dead end. The Report tab (Copy link, Export PDF, chart, key players) is good, so Brief should just open on Report after FT. Coach view toggle on mobile is unlabelled.
- Explore is clear, but the preview tiles are placeholders (identical "3-2" mini pitches), and "Replay a match" lists "Northbridge v Harbour 3-2" next to "Kestrel Park Comeback 3-2", which is confusing with the main match.
- Header on mobile: "Northbridge 3 - 2 Harbour" wraps to two lines and the Harbour crest sits far from its name; the sticky header takes ~50px on a small screen.
- Pitch dots are still just numbers, one orange dot has a "Benali" tooltip permanently stuck on at full time (stale hover label).

## New bugs
1. Timeline tab (Moments): "Kick-off" is listed at 90+3' (should be 1'), and it sits between Save and Goal events. Looks like the minute is wrong or an event is mis-sorted.
2. Unit typo: "Shot distance 12.7 m m" / "12.7 mm" on the 84' Big chance card (renders as a stray "m m").
3. Stats Team tab shows Possession, Shots and Chances created twice, once as hero tiles and again as bars directly underneath. Redundant.
4. Moments page "Catch me up | Full recap" buttons do not exist on desktop Moments; only mobile has them (inconsistent).
5. Duplicate "90+5' Full-time" entry shows both as "Full-time" at top of Live and as the first card on Moments; fine, but "Latest moments" on Live repeats the same two items as Moments, leaving the right column dense and the page 1,700px tall.

## Delightful now
- The onboarding sheet, the big score header, the chip-style filters on Moments (All / Goals / Cards / Shots / Insights), the highlights vs timeline toggle.
- The Match flow chart with tappable event dots (goals, cards, subs) and "How to read match flow".
- Share toast, Read aloud, Catch me up with Then / Now score.
- Stats verdict line and the Chances-over-time chart: I would actually screenshot this.
- Multiverse now reads naturally ("One match, four fans") with the Hindi version live.

## Would I come back on matchday?
Closer. Still missing: notifications, my club's other fixtures, crests, a "next kick-off" state when nothing is live, and a way to see the live feed in action (I only ever got a finished game).

## Remaining top asks (priority order)
1. Fix mobile Live "Latest moments": make it a vertical list of full-width cards (no clipped carousel, no truncated "Final score:...").
2. Fix the floating Catch me up / Ask the pundit pill so it never overlaps content (dock it to the tab bar or tuck it beside the momentum bar) and add safe bottom padding.
3. Make the profile menu opaque, drop "Developer tools" and the None/None dropdowns (or put them under "Advanced").
4. Fix data bugs: "Kick-off" at 90+3' in Timeline, "12.7 m m" unit, remove the duplicate stats blocks, clear the stale "Benali" pitch label.
5. Make Cmd-K actually search moments, players and stats, and accept questions (hand off to the pundit).
6. Fix onboarding selection state so exactly one club is clearly selected (and confirm it sticks).
7. After full time: land Coach on Report, hide closed 0% polls, and show a "match winner / player of the match" summary hero on Live.
8. Real crests and team names/colours in the header, plus a compact one-line header on mobile that does not wrap.
9. Add share beyond clipboard (native share sheet on mobile, a generated goal image card) for goals and the Catch-me-up summary.
10. Matchday hooks: notifications for goals/cards, other fixtures strip, "next match" state, and a clear loop for watching a live replay at speed.
