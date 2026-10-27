# MatchLens: Coach's review of the redesign (round 2)

I drove http://localhost:3020 at 1440x900 and 1024x768, using Playwright with `domcontentloaded`. I covered Live, Moments, Stats (Team, Players, Shots), the player sheet, Coach (Brief, Tactics, Report with a PDF export), Coach view, Explore, the Scenario lab and the dev drawer. I did not open `backend-issues.md`; I judged only what the UI shows. Screenshots are in the scratchpad `coach/` folder (`n*_*.png`, `rpt.png`, and others).

Caveat: the backend was sitting at full time (3-2) for most of my review. Restarting from the dev drawer did nothing (see "New bugs"). I got a live state by kicking off a Scenario lab script, which only reached about minute 15. So my half-time and late-live checks are limited to full time and minutes 5 to 15.

## 1. What is fixed, against my original top 10

| # | Original ask | Status |
|---|---|---|
| 1 | Data integrity (yellow card saying "11 players", scorer correction, momentum scale) | Partly fixed. The momentum banner no longer fights the bar, and the Live page now shows "Momentum: Northbridge on top" once. The unit bug "12.7 mm" is now "12.7 m". The moments feed has been rewritten ("Moberg sent off for a second yellow", "Harbour City are down to 10 players"). **Still wrong in the feed:** "Yellow card for Dominic Achterberg: Harbour City now have 11 players on the pitch (62')", "Yellow card for Jakub Wilczek: now have 10 players", and "Correction at 65': Elias Brandvold is now Kenji Arakawa". These are shown raw with no explanation. |
| 2 | Tactics: no "recommended" when the options are within noise | Fixed in the verdict box ("No clear winner. Every idea lands inside the baseline's range"). It is honest and I like it. Still contradictory, see below. |
| 3 | Half-time / Full-time report with export | Done (Coach > Report): xG-style chance chart, possession, key players, shot map, subs and cards, Copy link, Export PDF. Good. Several gaps below. |
| 4 | Hero pitch with layers | Partly. The Live pitch is bigger, has a direction-of-play note, pass arrows, a name chip on the ball carrier and a cleaner look. **No layers** (no heatmap, shape, pass network, pressing). Dots are numbers only. |
| 5 | Coach Brief fast and phase-aware | Mostly fixed. At full time it says "the match is over, so there is nothing to change on the bench" and points to the report. Live it loaded in 9 s (was 15 s) and shows one honest card (Low confidence, with pin and dismiss, and "Checked against match data"). Evidence now names its minute ("56% last 15 min, 7'"). |
| 6 | Filterable timeline, xG race, clips | Partly. Moments has All / Goals / Cards / Shots / Insights and team filters, plus Highlights / Timeline. The step chart "Chances created over time" with goal dots is the xG race I asked for. No clip link or video. |
| 7 | Bench-ready iPad layout | Better. No horizontal overflow on any page at 1024. Text is larger, targets are bigger, and the nav is a calm top bar. See "still wrong". |
| 8 | Coach-focused Ratings and Stats | Partly. Fan voting is gone from the table. But the table is thin (see below). |
| 9 | Coach mode navigation | Not delivered. The "Coach view" toggle exists on the Coach page. I turned it on and saw no visible change on Coach, Live, Stats or Moments. Fan features (Fan polls, Explore, Multiverse) are still in the product. |
| 10 | Fast recap / pundit | Partly. "Catch me up" and "Ask the pundit" now sit under the score and over the pitch. I did not get a recap to render in this run, and a replay speed control is hidden in a drawer. |

## 2. What is better (keep these)
- The visual design is a real step up: calm dark theme, a sticky score strip with the clock on every page, big numerals, clear blue/orange team colours.
- **Live:** one italic sentence summarising the match ("Northbridge FC beat Harbour City 3-2"), then pitch, momentum bar, Match flow chart, polls, then "Latest moments" cards with Share / Replay on pitch / Details. Pass arrows on the pitch are useful.
- **Stats > Team:** plain-English headline ("Northbridge FC are on top: more chances created, shots, possession"), then 3 tiles, then bars, then the cumulative chances chart. The one-line headline is the first thing I want at half-time.
- **Tactics:** it has simplified entry (four situation cards instead of free text and sliders). "How this was made" and the "(AI unavailable)" label are honest. The "No clear winner" verdict is the right call.
- **Players table:** sortable by rating, colour-coded by team, team filter. The sheet shows 4 headline numbers, "Recent moments" and a match-only disclaimer.
- Print/PDF: white background with chart colours kept; a good start.

## 3. What is still wrong (priority)

### Trust
1. **Tactics shows two different verdicts on one screen.** The "How this was made" strip still reads "Try: High press trap on the back pass" while the right-hand verdict says "No clear winner". The idea card 1 is highlighted with a green outline and a "Medium confidence" badge. Everything is 0% to 17% with "0 pts", and the hero number is a giant "0.00" xG. A coach will read that as "the tool says nothing".
2. The Tactics board was available and built a "Counter-press" idea at full time (90+5') with Harbour in possession. There is no "match is over" guard here, whereas the Brief has one.
3. Raw feed lines remain illogical (see row 1 above). The UI should hide a "players on pitch" stat unless the count actually changed. The correction line should say who corrected what and why, or be hidden.
4. **Two rating scales for the same player.** The table says Brandvold 9.5; the sheet says "Match card 89, Elite" with PAC 80 / SHO 99 / PAS 66 (FIFA-card style), while the shots stat reads "11 shots, 2 on target". SHO 99 on 2 of 11 on target is not credible. The small print says "Not a video game rating", but that is exactly what it looks like. A coach will dismiss the whole sheet.
5. "Chances created 4.02" replaces xG. Coaches say xG; the label is a rename of the same number and also shows decimals ("4.02 chances created") that make no sense as counts.

### Missing or buried (still)
- No formation or shape, no names on dots (a name appears only for the ball carrier), no heatmap or pass network, no pressing numbers. This was my top missing item and is unchanged.
- **Players table has only Player, Pos and Rating.** No minutes, goals, shots, xG, passes, duels, distance or speed in the table (they are only in the sheet, and only a handful). Cannot sort by anything but rating. At 1440 the table is a 1,200 px wide list with about 900 px of empty space on the right.
- **Report:** "Top 3 actions" is empty at full time and there is no half-time variant (I could not test the 45' state). "Possession and territory" shows only possession (no territory/field tilt). "Key players" show only 3 names with a number and no why. "Substitutions and cards" mixes yellows and subs in a plain list (and 12 items collapsed to 6). No score at half-time, no per-half split, no goalscorers listed.
- **Shot map** is still unreadable in the box: the 4 to 5 overlapping markers are bundled into numbered bubbles ("3", "4", "5") with no zoom or tap-to-list. The note "both teams attacking the same goal" is confusing; I would rather see each team on its own half.
- No video/clips. "Replay on pitch" is the only replay.
- Coach view toggle gives no visible effect, so I cannot tell what it is for.
- Explore still carries Multiverse, Broadcast, Scenario lab and Replay a match, i.e. fan and demo features in the main nav.

## 4. New bugs and rough edges
1. **Restart match does nothing.** Dev tools > Restart match closes the drawer, shows no toast and no confirm, and the page stays at 3-2 FT (tested at 15 s, three times). Either the action fails silently or the restart takes far longer than the UI admits. Either way, there is no feedback. At least now it is red, behind a drawer, and not one tap from the main screen.
2. **Player sheet header collides with the nav.** At 1024 the sheet opens over the page and the page title ("Stats") shows through under the top nav, and the nav stays above the scrim. The Rate-this-player dropdown is still there ("You") plus "Follow this player" (fan features).
3. The Live header and sticky strip duplicate the score. The big header says "3 - 2 FT" and the sticky strip right underneath also says "Northbridge 3 - 2 Harbour 90+5' FT", and the page announces both. On tablet this wastes about 120 px.
4. **Live page on iPad (1024x768):** the pitch fills the first screen, so momentum, Match flow and latest moments are all below the fold. The floating "Catch me up / Ask the pundit" pill overlaps the pitch and the momentum label on the long screenshot (it sits at the bottom of the viewport in real use, covering content when you scroll).
5. **PDF print:** page 1 of the exported PDF is blank except for the header ("Coach", team toggle, and a stray "Coach view" label). The report body starts on page 2. The print stylesheet should drop the controls and the page-1 gap.
6. The Brief shows one card at minute 7 with "Low confidence". Fine, but the page is two-thirds empty. Pinning or dismissing has no visible undo.
7. Live clock: in the Scenario lab run the clock moved from 5' to 15' in about 40 s of wall time, which is not a real-time clock. The speed setting is not visible anywhere except the dev drawer, so I cannot tell what pace I am watching.
8. The Fan polls ("Player of the match? 0 votes", "Who wins from here? 0%") still sit on Live and compete with the match for space.
9. Moments page is 6,600 px long at 1440. The filter chips are not sticky, so after a scroll I cannot change filter.
10. Small stat-bar labels ("On target", "Passes") are still about 12 px grey text at 1024.

## 5. Visualisations
- **Match flow** (momentum area with event diamonds): best chart, now clearer (event markers sit in a row above the area, a legend under it). Still has too many marks to read at 1024.
- **Chances over time** (step chart with goal dots): good. Add the half-time line label and the scoreline at each goal.
- **Possession bar:** fine.
- **Shot map:** see above; needs work.
- **Pitch:** pass arrows good; no layers.
- **Tactics replay pitch:** labels still overlap at the penalty spot ("screen lane", "man mark", "cover shadow" stacked).
- **Outcome bars:** honest ranges but all values read zero; hide them when the sample is that thin.

## 6. Speed to insight
- Live: score, state sentence, momentum in 2 s. Good.
- Brief: 9 s (down from 15 s) with a spinner, not a skeleton; still too slow for a half-time huddle.
- Tactics: 25 s to build. No progress detail beyond the strip.
- Half-time view: I could not test 45'. A dedicated "Half-time" report that auto-opens at the break is still the key missing piece.

## 7. Remaining top asks (priority order)
1. **Coach > Tactics:** one verdict only. Remove the stale "Try: ..." line, drop the "Recommended" outline and "Medium confidence" badge when the verdict is "No clear winner", lead with the margin or the sample size, and block building at full time.
2. **Live / Moments feed:** remove or explain illogical event text (yellow card with "N players on the pitch", "Correction at 65'"). One source of truth for the score and the clock (header duplicates).
3. **Stats > Players:** a real table. Columns for minutes, goals, shots, xG, passes, key passes, duels, distance and speed; sort by any column; sticky header; drop the empty right-hand half. Replace the FIFA-style card with those same numbers in the sheet. Remove "Rate this player" from the coach view.
4. **Coach > Report:** make it the half-time and full-time pack. Add scorers, per-half splits, field tilt or territory, a short "why" under each key player, and a print stylesheet that starts on page 1 without controls. Test the 45' state, which I could not reach.
5. **Live pitch:** add layers (shape/lines, heatmap, passing network, press intensity) with names on tap, and cap its height at about 55% of the iPad viewport so momentum and the latest moments are visible without scrolling.
6. **Shot map (Stats, Report):** one half pitch per team, xG labels on tap, and a way to list overlapping shots instead of "3 / 4 / 5" bubbles.
7. **Coach view toggle:** make it do something real: hide fan polls, Explore, Rate player and "Ask the pundit", pin the Brief and the clock, and default to the Coach tab.
8. **Dev drawer:** Restart match needs a confirm, a progress state and a visible result; move the pace/speed to a small indicator next to the clock.
9. **Label xG as xG** (with a one-line tooltip) instead of "Chances created", and keep decimals to one place.
10. **Clips:** a "Watch it back" link or clip tray for goals, big chances and red cards, even if only a stub for the recorded video.
