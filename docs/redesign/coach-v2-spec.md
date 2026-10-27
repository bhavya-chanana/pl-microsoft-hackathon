# Coach v2 spec (FotMob-inspired), plus "polish everywhere"

Status: written from the current app, the coach's answers (coach-review-2.md plus a direct reply) and well-known FotMob patterns. The researcher's teardown (`fotmob-teardown.md`) had not landed when this was written; anything it contradicts overrides this file, and I will patch the "FotMob cues" notes when it arrives. The existing design system (tokens, glass chrome, type, motion) in `design-spec.md` stays; this file only changes the Coach area and adds a polish list.

## 0. What is wrong with Coach today (from screenshots)
- The Coach page at full time is a single card and ~450px of nothing (`final/dark-desktop-coach-viewport.png`). Brief, Tactics and Report are three separate pages with three different layouts and no shared context.
- It looks like forms and cards, not like football: there is no lineup, no formation, no rating badges, no momentum or comparison view on the Coach page itself.
- Tactics is an 1100px-tall wall (form + status strip + pitch + outcome bars + idea list). Hard to scan on an iPad.
- The coach wants: the three ranked actions, xG/score/territory, and the lineup with ratings and bookings, each reachable in one tap, each about one screen at 1024x768.

## 1. Structure: five segments under a pinned scoreboard

Route stays `/coach`; segment is `?tab=`. The segmented control becomes a FotMob-style **underline tab strip** (not a pill group): text 15px/500, the active tab has a 2px team-accent underline (the selected team's colour) sliding with `layoutId`, hairline under the strip, horizontally scrollable on narrow screens. Order and default:

1. **Overview** (default): the 90-second half-time view.
2. **Lineup**: formation pitch with rating badges, both teams, bench.
3. **Stats**: comparison bars with a period filter.
4. **Tactics**: ideas lab (restructured).
5. **Report**: printable report (kept, restyled).

The old "Brief" content moves into Overview (top actions) and the old Brief page ceases to exist; `?tab=brief` redirects to `?tab=overview`.

### Pinned header (all segments)
Sticky, glass, 2 rows at 1024 and one row below 600px:
- Row 1: team switch (two chips with crest + short name + dot in team colour, selected chip filled with 12% team colour and a 1px team border), phase pill ("Half-time" / "Live 67'" / "Full time"), Coach view toggle moves to the profile menu (it is a global setting, not a page control), refresh icon only shows while loading or on error.
- Row 2: the tab strip.
The global match header already shows score and clock; do not repeat them here. Page title "Coach" is dropped (the nav shows it); saves ~60px.

Selecting a team recolours the accent for the whole page (`--accent-team`), the underline, key numbers and chart emphasis, and dims the other team's marks to 55% where both are plotted.

## 2. Overview (default): "what changed, what to do"

Goal: answer the coach's half-time trio on one screen at 1024x768 with no scrolling for the top band.

Layout at 1440 (grid 12, gap 16/24):
```
[ Phase strip: "Half-time · 45+1'  ·  Northbridge 0-2 Harbour"  | Updated 20s ago ⟳ ]
+----------------------------------------+ +-------------------------+
| TOP 3 ACTIONS (span 7)                 | | THE GAME SO FAR (span 5)|
|  1 [conf chip] Stop their danger man   | | xG race mini chart      |
|    Evidence · Do this · Trade-off      | |  NBR 1.54 ─── HAR 1.83  |
|    [Pin] [Dismiss]                     | | Momentum strip (pulse)  |
|  2 ...                                 | | Territory bar 52 / 48   |
|  3 ...                                 | | Possession bar          |
+----------------------------------------+ +-------------------------+
+--------------------------------------------------------------------+
| WATCH LIST: players under 6.0 / on a yellow / carrying injury risk |
|  [badge 5.4 Valaderes ▲Y] [badge 5.9 Marchetti Y] [...]  → Lineup  |
+--------------------------------------------------------------------+
| WHAT CHANGED SINCE LAST CHECK (chips, max 3)                       |
+--------------------------------------------------------------------+
```
- **Top 3 actions**: keep Evidence / Do this / Trade-off. Card: 20px radius, left bar in team colour, big number (display, 40px) top-left, title (heading 20/600), confidence chip right ("High / Medium / Low confidence" with a dot), then three labelled rows (label overline 12px muted; text 16px). Metric chips at the bottom (14px, `xG 1.20`, `Shots 4`) with an info tooltip. Pin/Dismiss are icon buttons (44px targets). Max 3 cards; on 1024 the three cards are stacked in the left column and each is ~170px tall when collapsed: show Title + "Do this" by default, "Evidence" and "Trade-off" behind a chevron (state remembered per card). Cut: "How this was made" strip, a second verdict, and any "Blocked/Checked" text; a single quiet "From match data" tick lives inside the info popover.
- **The game so far**: a card with
  - xG race step chart (reuse the report chart), with goal dots, half-time tick, team colours, current totals as end labels, labelled "xG" (not "chances created") in Coach and Analyst; the first-half filter is the default at half-time, otherwise full match.
  - Momentum strip under it (the Match flow / pulse), 56px high.
  - Two bars: Territory and Possession, with values in the team colour at 28px.
  - Skeleton while loading; if the sample is thin (<10 shots or <15 min), show "Early days: too little data to read xG" instead of a 0.00 hero number.
- **Watch list**: a horizontal row (wrap on mobile) of player chips, each = rating badge (see 3.3) + surname + booking icon. Rule: rating < 6.0, or carrying a yellow, or substituted. Tapping opens the player sheet; "See lineup" link goes to the Lineup tab with the team preselected.
- **What changed**: up to three chips ("Harbour now have 3 shots on target (+2)", "Northbridge pressing up 18%") with timestamps. Hide if no changes.
- **Phase-aware states**: pre-match = "Kick-off 17:30" and lineups only; live = as above; half-time = as above with first-half filter; full time = Top 3 replaced by "Full time. Open the report" (a big button) and the game-so-far card enlarged to full width, plus a "Match rating" summary row (top 3 players by rating, as badges).

At 1024x768: single column of [phase strip], [Top 3, collapsed cards ~ 3x170 = 510px], then the game-so-far card sits beside nothing (it drops below): so reorder at <1100px as [Phase strip], [Game so far compact: chart 140px + two bars in one row, ~220px], [Top 3 cards], [Watch list]. This keeps the xG line and both bars above the first card and the first card's "Do this" above the fold at 768px height (total above-fold ≈ 56 nav + 48 header + 56 tabs + 220 + 170 ≈ 550px).

## 3. Lineup (FotMob "Lineup" pitch)

### 3.1 Layout
```
[ Team switch: Northbridge | Harbour ]   formation "4-3-3"   [Starters | Bench]

+------------------------------+  +------------------------------+
| PITCH (vertical, 3:4)        |  | List (desktop only, span 4)  |
|  GK                          |  |  Rating-sorted players       |
|  LB  CB  CB  RB              |  |  (same data as pitch)        |
|  CM  CM  CM                  |  +------------------------------+
|  LW  ST  RW                  |  | BENCH row, subs on/off       |
+------------------------------+  +------------------------------+
```
- The pitch is **vertical**, a single team at a time (FotMob shows both on one tall pitch; at 1024x768 landscape a single team is far more readable). Pitch ratio 3:4, centred, height `min(640px, 70vh)` on desktop, full-width on mobile. Both teams are available via the team switch; on ≥1280px an optional "Both" mode shows two vertical pitches side by side (home attacking up on the left, away attacking up on the right).
- Players positioned from the formation string (`formation: "4-3-3"` in the feed) and each player's `position` (GK, LB, CB, ...). Build a coordinate map: x from the role (left to right), y from the line (GK bottom, defenders, midfielders, forwards); if a position cannot be placed, fall back to even spacing within the line so nothing overlaps. Starting XI comes from `starting_xi`; subs from `bench` and the substitution events; players who left show with a "↓ minute" mark and are drawn dimmed.
- Pitch styling: the app's muted pitch (L .24 chroma .04) with chalk lines at 14%, grass stripes at 3%, subtle vignette; no glass on the pitch.

### 3.2 Player marker
44px (touch target) circle:
- Photo is not available: show a 40px circle filled with the team colour at 22% over a 1.5px team-colour ring, the shirt number in Barlow 18/700, centred.
- **Rating badge** (FotMob signature) overlapping top-right, 22px rounded rect, colour-coded: ≥8.0 `--rating-great` (blue-green), 7.0-7.9 `--rating-good` (green), 6.0-6.9 `--rating-ok` (amber), <6.0 `--rating-poor` (red-orange); number in Geist Mono 12/600 with 1 decimal; text colour chosen for 4.5:1 on each fill. Rating tokens (dark): great `oklch(0.72 0.15 200)`, good `oklch(0.75 0.16 150)`, ok `oklch(0.80 0.15 85)`, poor `oklch(0.68 0.19 28)`; text on them `oklch(0.18 0.02 265)`.
- **Event icons** (14px) bottom-left, up to 3 stacked: ball (goal, with count), boot/arrow (assist), yellow card, red card, sub off (red arrow with minute), sub on (green arrow with minute). Captain "C" not available: omit.
- Name label below: surname, 13px/500, `--fg`, max 12 chars with ellipsis, backed by a 60% pitch-colour pill so it reads on the stripes.
- Highlight rules (coach's ask): under 6.0 rating gets a 2px red ring pulse once; a player on a yellow gets a yellow corner flag; man of the match gets a small star on the badge. Highlights are shown in the legend.
- Tap/click opens the player sheet (the existing sheet; in Coach view it shows the plain stat grid, recent moments, minutes if available). Hover shows a tooltip with position, minutes on pitch if available, key stat of the role. Keyboard: roving tabindex, arrow keys move by position, Enter opens, Escape returns.

### 3.3 List and bench
- Desktop: right column list of the same players sorted by rating, row 48px: badge, surname, position, icons, minutes (if the feed has them; if not, hide the column, do not invent).
- Below the pitch: **Bench** as a horizontal row of markers (36px) with "On at 58'" captions for subs used and "Unused" in muted text.
- Substitutions block: chronological list "58' Whitcombe ↔ Valaderes" with the two ratings.
- Fatigue: only if the feed provides distance/sprints. The player sheet already has distance (km) and top speed; show a thin bar under the marker only when `distance` exists, scaled against the team's max, labelled "Distance" in the legend. Otherwise omit.

### 3.4 Empty/loading
Skeleton pitch with lines, 11 ghost circles; pre-match: lineups shown without ratings (badges hidden); no data: "Lineups arrive about an hour before kick-off."

## 4. Stats (comparison bars)

FotMob "Top stats" pattern, tuned for the coach:
- Top: period filter as a small segmented control `1st half | 2nd half | Full` (default = current half at HT, else Full). Disabled segments show a tooltip "Not played yet".
- Eight rows max (coach's list), in this order: xG, Shots (on target in the row detail), Possession, Passes (accuracy), Final-third entries or field tilt (if available, else Corners), Pressures or PPDA (if available, else Fouls), Duels won (hide if absent), Corners. Rows that the feed cannot supply are omitted rather than zero-filled; Backend gaps go in `backend-issues.md`.
- Row anatomy (64px high at 1440, 72px on touch): the label centred above (13px/500 muted), values at each end in Geist Mono 20/600; the bar is a **split bar** of height 8px, the leading side's value in the team colour, the trailing side's in `--fg-muted`; row hover shows the exact numbers and period split in a popover; bar values animate on mount (number ticker 400ms).
- Above the rows: a 2-up summary tile: "Who is on top?" in plain English (one sentence) and a mini shot-map thumbnail that links to the Report's full shot map.
- Below: small "Shot map" card (half-pitch, existing) and a "Players" table link (to Stats > Players).

## 5. Tactics (restructured for a bench)

Keep the engine and data; change the presentation to a three-step flow with a persistent right panel so it fits 1024x768:

1. **Pick the situation** (top, one row): four large tiles in a 2x2 (Chasing a goal, Protecting a lead, Breaking their press, Custom), each 72px tall, with a 20px icon and one-line caption. A **Fine-tune** disclosure holds moment, risk and idea count. The primary **Build ideas** button (accent, 48px) sits at the right of the tile row on ≥1100px, below on smaller screens. Disabled at full time with the inline message "The match is over. Pick a moment to review." (the moment picker stays enabled).
2. **While building**: the Build button becomes a progress bar (indeterminate bar plus "Testing idea 2 of 4…" text), ideas appear one by one in the list, each with a skeleton until scored. No status strip that duplicates the verdict.
3. **Results** (two columns at ≥1100px, tabs on smaller): left = the pitch with the selected idea playing, with Typical / Best case / Worst case / Compare chips and the playback controls (scrubber 48px high) ; right = **one verdict card** at the top, then the ideas list.
   - The verdict card has exactly one state: "Try: <idea>" with margin and confidence, or "No clear winner" with the margin line ("Best idea is 8 points from the baseline on shot chance, over 24 simulations"). Never both. No "Recommended" badge on ties. No green card highlight on ties.
   - Idea card: number, name, a 2-line plain description, a row of three metric chips (Shot %, xG, Loses ball %) each coloured relative to baseline with an arrow and sign (not zeros: hide metrics with no signal), "Show on pitch" and "Compare" buttons (44px). "Who does what" is behind a chevron.
   - "What usually happens" (the outcome bars): reduce to three bars (Shot, Reach the box, Lose the ball), each with the baseline tick and a plain sentence under the group ("About 1 in 12 end in a shot, a little above doing nothing"). Hide when all values are within noise; show instead the "No clear winner" text.
4. Remove: the "How this was made" block (move the one-line "Built-in ideas (AI was slow)" note into the verdict card's info popover) and the 0 pts rows.

## 6. Report (restyle only)
Keep the structure from `design-spec.md` 4.4 and what exists; changes:
- Header card gets a FotMob-style match strip: crests, score (display 56), scorers beneath each team (icon + minute), phase pill.
- Sections use the same card rhythm as Overview: xG race (large), Stats bars (the 8-row set, period filter inherits), Lineup mini (two small formation pitches with badges, no labels except badges), Key players (badges), Subs and cards (timeline).
- Print stylesheet: light theme, avoids breaks inside cards, hides nav and controls, first page begins with the header.
- Share: Copy link and Export PDF stay; add "Share image" (a 1080x1350 PNG card with the scoreline, xG, top actions, only when the Web Share API is available).

## 7. Player rating system (shared component)
`RatingBadge` (sizes 18/22/28), with the colour tokens in 3.2, used in Lineup, Overview watch list, Stats > Players (replace the thin rating bar with the badge plus a 2px underline in the same colour), the player sheet header, moment cards for player events, and the Live pitch hover card. Tooltip: "Rating from this match only. 6.0 is average." Add "Man of the match" star on the top rated player after full time.

## 8. Tokens and motion additions
- `--accent-team` (set from the selected team), `--rating-great|good|ok|poor` (above), `--pitch-vertical-stripe` (same as horizontal turf), `--tab-underline: 2px`.
- Motion: tab underline slides (`spring.snappy`); lineup markers stagger in by line from GK to ST (30ms steps, max 11), badge numbers count up once; the team switch cross-fades the pitch (200ms) and swaps markers with `layoutId` per `player_id` so shared players do not "jump" between pages (not applicable across teams, so cross-fade only). Reduced motion: no stagger, instant.
- Touch: all controls on Coach ≥44px; no text below 13px except chart ticks (12px).
- Performance: only chrome uses glass. The pinned Coach header uses `glass` while stuck; the pitch and cards are solid.

## 9. Responsive behaviour
- ≥1280: two-column Overview; Lineup shows pitch + list; Stats single column 760px with the bars; Tactics two columns.
- 1024x768 (bench iPad): everything described above; each segment's first screen shows its key content without scrolling (Overview: xG line + bars + first action; Lineup: whole vertical pitch at ≈ 600px tall; Stats: first 4 rows + filter; Tactics: tiles + Build; Report: header + first two cards).
- 390: segments scroll horizontally; Lineup pitch full-bleed with 36px markers; stats rows 72px; Tactics results become a tab pair (Pitch / Ideas).

## 10. Coach vs Fan modes
Coach view (global setting): the Coach tab strip appears in the primary nav's position 4 as today; Explore hides fan-only tiles; xG wording; rating badges include the number only (no fan votes). Fan view: Coach remains available but the Overview uses plain wording ("Chances created") and hides the Watch list's booking flags only if the profile style is Casual.

## 11. Component list
New: `CoachTabs` (underline strip), `CoachHeader` (team switch + phase pill), `ActionCard`, `GameSoFarCard`, `WatchList`, `LineupPitch` (+ `PlayerMarker`, `RatingBadge`, `BenchRow`, `SubList`), `StatBars` (+ `PeriodFilter`), `VerdictCard`, `IdeaCard`, `SituationTiles`, `BuildProgress`, `ReportHeader`, `ShareImage`.
Reuse: `PulseChart`, `ShotMap`, player sheet, `Segmented`, skeletons, empty-state pitch outline.
No new installs required; if a table or tooltip primitive is missing add `npx shadcn@latest add tooltip popover`.

## 12. Data availability (checked against the contract)
- Available: `formation`, `starting_xi`, `bench`, player `position`/`shirt`, `data_rating`, `on_pitch`, events (goals, assists, cards, substitutions), per-player passes/shots/key passes/distance/top speed, momentum/pulse, possession, shots, xG, corners, fouls.
- Not available (omit or request): minutes played, duels, PPDA/pressures, field tilt/final-third entries, per-player sprint counts. Log requests in `backend-issues.md`; until then, rows are omitted, never zero-filled.

## 13. Polish everywhere (FotMob-inspired)

### Live
- Add a **mini lineup/event strip** under the hero (goals with scorers and minutes on each side, as FotMob's match header does): "Achterberg 23', Quilmes 39'" under Harbour; cards as small icons.
- **Top-stats tiles** under the pitch (3 split bars: Possession, Shots, xG) instead of the long cards, linking to Stats.
- Tabs inside Live ("Match / Lineup / Stats" are distinct pages already; keep Live focused): add a thin **"Live ticker"** line on the hero ("Corner for Harbour · 44'") that cross-fades.
- Rating badges on pitch hover cards.

### Stats
- Switch the Stats > Team page to the same `StatBars` component with the period filter.
- Players table: replace the rating bar with `RatingBadge`; add "Top performers" as a three-card row (best rating per team + man of the match) above the table; sort chips as in FotMob ("Rating, Goals, Assists, Shots, Passes").
- Shots tab: add a player filter (chips for the top 5 shooters) and the xG per shot as a label.

### Moments
- Group by period with sticky period headers ("First half", "Second half") and a thin vertical timeline with minute dots; goal rows get a large scoreline chip ("0-1") like FotMob's key-events list; cards and subs use the same icons as the lineup.
- Filters stay one row; add "Mine" (my club) when a club is chosen.

### Global
- Page transitions: 200ms cross-fade; skeleton rows with the exact heights of final content.
- Number polish: use tabular figures everywhere, score and clock in Barlow; consistent 1-decimal ratings and 2-decimal xG.
- Empty states: reuse the pitch-outline empty state with one CTA.
- Pull-to-refresh on mobile Live/Moments (re-subscribe SSE) with a haptic-style bounce.
- Settings discoverability: see the Stream settings note in `design-spec.md` follow-up (top bar and ⌘K).

## 14. Build order for implementer
1. Shared pieces: `RatingBadge`, rating tokens, `CoachTabs` underline strip, `CoachHeader` (team switch, phase pill), remove Coach toggle from the page (profile menu only).
2. Overview (`ActionCard`, `GameSoFarCard`, `WatchList`, phase states) and delete the old Brief page; redirect `?tab=brief`.
3. Lineup (`LineupPitch`, markers, bench, subs, team switch, keyboard).
4. Stats (`StatBars`, period filter) and reuse it in Stats > Team.
5. Tactics restructure (situation tiles, progress, single verdict, 3 bars).
6. Report restyle (header strip, lineup mini, print stylesheet, share image optional).
7. Polish list items for Live, Stats, Moments.

Screenshots to `docs/redesign/screens/after/round-3/` at 1440, 1024x768 and 390: Coach Overview (live, half-time, full time), Lineup (both teams), Stats, Tactics (empty, building, results with a clear winner and with a tie), Report, plus Live, Stats > Players and Moments after polish. Acceptance: each Coach segment's key content fits in the first 768px at 1024 wide; no 0.00/0 pts hero numbers on thin samples; one verdict only; every control ≥44px; text ≥13px; rating badges pass 4.5:1.
