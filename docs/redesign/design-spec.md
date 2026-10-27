# MatchLens design spec: "Matchday Night"

Inputs: `design-audit.md`, `research.md`, `fan-review.md`, `coach-review.md`. `frontend-map.md` was not available when this was written; wherever the spec says "existing component", the implementer maps it to the real file. Do not rewrite data logic; this is presentation, layout, IA and copy (plus the small data-integrity fixes in section 8).

Direction (approved): **editorial dark** as the system, **broadcast** style on the scoreline and moment cards only, **glass only on floating chrome** (top bar, bottom tab bar, command palette, sheets/drawers, popovers, toasts, floating action pill). Content surfaces are solid.

---

## 1. IA and routes

Primary nav (5 items, same on all breakpoints): **Live, Moments, Stats, Coach, Explore**. Plus a global match header, a command palette, and a Developer drawer.

| New route | Nav | Contains | Replaces |
|---|---|---|---|
| `/` | Live | Match hub: hero scoreboard, pitch, "story so far", momentum/pulse, floating "Catch me up" + "Ask the pundit", next moments | Match centre (pitch, momentum, pulse, right rail, polls) |
| `/moments` | Moments | Moment cards feed (goals, cards, subs as big cards, minor as quiet rows), filters, share; Catch up/Recap entry | Live insights, Timeline tab, "What did I miss?", Match recap |
| `/stats` | Stats | Segmented: Team stats / Players (ratings) / Shots. Possession, shots, xG race, shot map, ratings table | Stats tab, Ratings tab |
| `/coach` | Coach | Segmented: **Brief** / **Tactics** / **Report** (half-time and full-time). Team switch (colour-coded) | `/coach`, `/tactics`, new report page |
| `/explore` | Explore | Bento of "things to try": Multiverse, Broadcast view, Scenario lab, Recorded matches/Past matches | `/multiverse`, `/broadcast` entry, `/lab`, past/recorded matches |
| `/explore/multiverse` | (Explore) | Four fan screens | `/multiverse` |
| `/explore/broadcast` | (Explore) | Full-bleed broadcast overlay, minimal exit chrome | `/broadcast` |
| `/explore/lab` | (Explore) | Scenario lab | `/lab` |

Developer drawer (opened from the footer item of the profile menu, or `?` palette entry "Developer tools"; right sheet on desktop, drawer on mobile): Agent room (render existing pipeline diagram inside, also keep `/agents` route alive), model and latency info, **Demo controls** (speed, start minute, Pause, Restart behind a confirm dialog), Past matches, Recorded matches. Hidden unless "Show developer tools" is on in Settings (default on in dev builds, off otherwise). Show a small "Demo" pill in the top bar when a replay/scenario drives the feed.

Redirects (keep old URLs working): `/multiverse -> /explore/multiverse`, `/broadcast -> /explore/broadcast`, `/lab -> /explore/lab`, `/tactics -> /coach?tab=tactics`, `/agents` remains (dev). Implementer may keep old route files and just re-export if faster; only the nav and URLs need to match.

Profile popover (avatar chip at top-right): Club, Mode (Casual/Analyst/Player focus), Language, Favourite player, Theme, Coach mode toggle, Developer tools. Replaces the five dropdowns in the sidebar. First visit shows the one-step onboarding sheet (section 4.9).

Coach mode (setting, persisted): hides Explore > Multiverse, Broadcast, polls, fan voting columns; nav becomes Live, Stats, Coach, Explore (reduced). Default off.

## 2. App shell

```
DESKTOP >= 1024                                    MOBILE < 768
+---------------------------------------------+    +-----------------------+
| [logo] Live Moments Stats Coach Explore  ⌘K  [LIVE] [avatar] |   glass top bar 56px   |  mini header 52px (sticky)
+---------------------------------------------+    | NBR 2 - 1 HAR  67' ●  |
| MATCH HEADER (hero, collapses to 56px bar)  |    +-----------------------+
+---------------------------------------------+    |                       |
|                                             |    |   page content        |
|   page content (document scroll)            |    |                       |
|                                             |    |                       |
+---------------------------------------------+    +-----------------------+
                                                   | glass tab bar: 5 items, icon + label, safe-area |
```

- **Top bar (>=768):** fixed, 56px, `glass`. Left: logo wordmark. Centre: 5 nav items with animated pill (`layoutId`, `spring.snappy`) and hover highlight. Right: ⌘K chip ("Search or ask…"), LIVE/FT badge, profile avatar. Between 768 and 1024 the nav labels stay (14px) and the ⌘K chip becomes icon only.
- **Bottom tab bar (<768):** fixed, floating pill 12px above the bottom plus `env(safe-area-inset-bottom)`, `glass`, 5 items with icon (22px) and label (11px, weight 500, never hidden), active item gets a filled pill. Height 64px. Page gets `padding-bottom: calc(64px + 24px + env(safe-area-inset-bottom))`.
- **Match header (every page except `/explore/*` and `/coach?tab=report`?):** no, show on all primary pages. Hero state (on `/` at scrollY 0, desktop 168px, mobile 148px): team crests, names, huge score (display font), minute, LIVE dot, ambient team-colour wash behind. Collapsed state (after 80px scroll): 56px glass bar with `NBR 2 - 1 HAR 67'`. On other pages the header starts collapsed. Driven by `useScroll` + `useTransform` of height/scale (not by layout reflow of the page below: reserve the hero height with a spacer so content does not jump).
- **Single scroll container = the document.** No `overflow:auto` ancestors, no `h-screen`. Use `min-h-dvh`. `html{scrollbar-gutter:stable}`. Sticky/fixed bars are siblings of `<main>`, not ancestors.
- **Scroll fixes (acceptance criteria):**
  1. `main` gets `scroll-padding-top: var(--header-h)` and each anchorable section `scroll-margin-top: calc(var(--header-h) + 12px)`, where `--header-h` = 56px (collapsed header + top bar are one stack: top bar 56 + collapsed match header 48 = 104px; define `--stack-h: 104px` on desktop, mobile 52px).
  2. Tab/segment changes never change scroll position: tab panels render with `min-height` equal to the previous panel's height (measure with ResizeObserver, release after 300ms), `overflow-anchor: none` on the panel, and no `scrollIntoView`. Verify: mobile, scroll to y=700, switch Stats tabs, `scrollY` stays 700 (+-2).
  3. No nested scrollers except: sheets, the command palette, horizontal chip rows and wide tables (each `overscroll-behavior: contain`). The "Live insights" list is a page section, not an inner scroller. The old right-rail sticky + inner scroll is removed.
  4. Desktop 2-column pages use CSS grid with `align-items:start`; any sticky column uses `position:sticky; top: calc(var(--stack-h) + 16px)` and must have `max-height: calc(100dvh - var(--stack-h) - 32px)` only if it contains no list; otherwise no sticky.
  5. Dialogs/sheets use the primitives' built-in scroll lock; no manual `body{overflow:hidden}`.
  6. Remove the Next dev indicator overlap: set `devIndicators: false` in dev.
- **Loading/error philosophy:** never replace content with an error wall. Keep last-known data, show an inline pill in the match header: "Reconnecting…" (amber dot, pulsing) with a "Retry" text button; after 20s: "Offline, showing data from 67'". Initial load: skeletons shaped like the final layout (hero, pitch, three cards). Full-page error only if no data was ever received, and then it is a friendly empty state with "Retry" and "Try demo match".

Breakpoints: mobile <768, tablet 768-1279 (target 1024x768 bench iPad), desktop >=1280 (target 1440). Content max-width 1280, side gutter 16 (mobile), 24 (tablet), 32 (desktop). Touch targets >= 44px on mobile/tablet. Body text never below 14px; captions 13px; only chart tick labels at 12px.

## 3. Design tokens

Use research.md section 4.2 CSS for glass utilities (`glass`, `glass-strong`, `glass-sheen`, `glass-hover`, `surface`, `tnum`, reduced-transparency fallback). Dark is the default theme; light theme remains but is second priority and must pass the same checks. Overrides and additions:

### Colour (dark; oklch, with hex approximations)
| Token | Value | Use | Contrast |
|---|---|---|---|
| `--bg` | `oklch(0.145 0.006 265)` ~#0b0d12 | page | - |
| `--surface` | `oklch(0.19 0.008 265)` ~#14171d | cards | - |
| `--surface-2` | `oklch(0.23 0.008 265)` ~#1b1f27 | raised / inputs / hovers | - |
| `--hairline` | `oklch(1 0 0 / .08)` | borders | - |
| `--fg` | `oklch(0.97 0.003 265)` ~#f3f4f6 | primary text | 16:1 on bg |
| `--fg-muted` | `oklch(0.76 0.01 265)` ~#aeb3bd | secondary text (raised from research 0.70) | 8.6:1 on surface |
| `--fg-subtle` | `oklch(0.66 0.01 265)` ~#8e94a0 | captions, 13px min | 5.6:1 on surface |
| `--accent` | `oklch(0.78 0.17 155)` ~#3ddc84-ish pitch green | primary action, focus ring, LIVE-free accents | 9:1 on bg |
| `--accent-fg` | `oklch(0.2 0.03 155)` | text on accent buttons | 8:1 |
| `--live` | `oklch(0.68 0.22 25)` ~#f04545 | LIVE dot/badge only | - |
| `--positive` / `--negative` / `--warning` | `oklch(0.78 0.16 150)` / `oklch(0.70 0.19 25)` / `oklch(0.82 0.15 85)` | deltas, confidence, cards | >= 4.5 on surface |
| `--home` | from team data, default Northbridge `oklch(0.68 0.17 250)` ~#4c8dff | home | 5.9:1 on surface |
| `--away` | from team data, default Harbour `oklch(0.80 0.15 75)` ~#f5a524 | away | 9:1 on surface |
| `--pitch` | `oklch(0.24 0.04 155)` | pitch fill (muted); lines `oklch(1 0 0 / .18)` | - |

Rules: team colours are set per match on the app root (`--home`, `--away`) and normalised to L 0.66-0.80 in dark; if two team hues are within ~25 degrees, swap away to the nearest contrasting hue. Never use pink/teal for the pulse strip: use `--home` for home control, `--away` for away control, `--fg-subtle` at 30% for balanced, and `--warning` hatched for end-to-end. Text on glass must be `--fg` (>=4.5:1 against the worst-case backdrop: test over the bright pitch green and the home/away wash). Semantics are never colour-only (icon or label too).

### Light theme (same names)
`--bg oklch(0.985 .002 260)`, `--surface #fff`, `--fg oklch(0.21 .01 265)`, `--fg-muted oklch(0.42 .01 265)` (7:1), `--fg-subtle oklch(0.50 .01 265)` (4.8:1), accent `oklch(0.55 0.17 155)`. Team colours darken to L 0.52-0.60.

### Type
Fonts: Geist Sans (UI), Geist Mono (tabular data), **Barlow Condensed 600/700 (display: scoreline, minute, big numbers, moment-card headlines)**. Instrument Serif italic only for the "story so far" line (the single editorial accent).

| Style | Size / line / weight / tracking | Font | Use |
|---|---|---|---|
| `display-xl` | 72/68 (mobile 56/52), 700, -0.01em, caps off | Barlow Cond. | hero score digits |
| `display-l` | 40/40, 700 | Barlow Cond. | moment card headline, KPI numbers |
| `title` | 28/34, 600, -0.02em | Geist | page/section titles |
| `heading` | 20/28, 600 | Geist | card titles |
| `body` | 15/24 (mobile 16/24), 400 | Geist | default |
| `body-strong` | 15/24, 600 | Geist | emphasis |
| `caption` | 13/18, 500, +0.01em | Geist | meta, labels (min size for any UI text) |
| `overline` | 12/16, 600, +0.08em, UPPERCASE | Geist | section kickers only, `--fg-muted` |
| `num` | as context, `tabular-nums slashed-zero` | Geist Mono for tables, Barlow for hero | all numbers |
| `story` | 22/32, italic 400 | Instrument Serif | "Story so far" |

Weights 400/500/600 for Geist; do not use 700 except Barlow.

### Spacing, radii, shadows
- Spacing scale (px): 4, 8, 12, 16, 20, 24, 32, 48, 64. Card padding 20 (mobile 16). Gap between cards 16 (mobile 12). Section gap 32.
- Radii: chip 999, button 12, input 12, card 20 (mobile 16), sheet 28 (top corners on mobile), pitch container 20, dock/tab bar 999 (pill).
- Elevation: `e0` none (flat card, hairline only); `e1` `0 1px 0 oklch(0 0 0/.4), inset 0 1px 0 oklch(1 0 0/.04)` (surface cards); `e2` `0 8px 24px -8px oklch(0 0 0/.5)` (hover cards); `glass` shadow from research for floating chrome. Hover on interactive cards: `translateY(-2px)`, border to `oklch(1 0 0 / .16)`, spotlight radial following the cursor (desktop only).
- Glass elements (max 4 on screen): top bar, tab bar, floating action pill, open sheet/palette/popover. Never on cards, rows, charts. Backdrop for glass is real content scrolling beneath plus the ambient wash: render a `position:fixed; inset:0; z-index:-1` div with the `.match-ambient` gradient (not `background-attachment: fixed`).

### Motion
Use research.md section 7 presets (`snappy`, `smooth`, `gentle`, `fadeUp`), wrapped in `<MotionConfig reducedMotion="user">` and `LazyMotion`.
- Nav pill, segmented controls: `layoutId` with `snappy`.
- Sheets/drawers: `smooth`, backdrop fades 200ms; palette scales 0.97 -> 1 with `gentle`.
- Score change: digits roll (number-ticker / sliding-number, 400ms) plus a 600ms team-colour pulse behind the score; goal triggers the **goal moment**: hero flashes team colour wash, a moment card slides in (`gentle`) at the top of Moments and as a toast (sonner) on other pages.
- Match header collapse: scroll-linked, 0 jank (transform/opacity only).
- Page enter: `fadeUp` on the page header and a 30ms stagger of at most 8 cards. Charts animate on first mount only.
- LIVE dot: 1.6s opacity pulse. Skeleton shimmer 1.4s.
- Durations: micro 150ms, standard 250ms, sheet 350ms. Never animate `backdrop-filter`.

## 4. Screens

All screens: dark, match header on top, content on solid `surface` cards. Text sizes follow section 3.

### 4.1 Live `/`

Desktop 1440 (grid 12 cols, 32 gutter, max 1280):
```
[ HERO HEADER: crest NBR  2 - 1  HAR crest · 67' ● LIVE · ambient wash ]
"Story so far" (serif, 1-2 lines)                                  [Share]
+------------------------------------------+ +---------------------+
| PITCH (hero, aspect 105:68, fills card)  | | Latest moments       |
|  layer chips: Players | Shape | Heat |    | |  goal card (big)     |
|  Pass net | Pressing        [TV cam]      | |  card (big)          |
|  direction arrow, ball glow, 18px dots   | |  3 quiet rows        |
+------------------------------------------+ |  [See all moments]   |
| Momentum bar:  NBR ======|====== HAR       | +---------------------+
+------------------------------------------+ | Fan poll (compact)   |
| MATCH PULSE chart (full width of col)     | +---------------------+
+------------------------------------------+
Floating pill (glass, bottom-center): [Catch me up] [Ask the pundit]
```
- Left column span 8, right column span 4 (no sticky/inner scroll; right column simply follows the page).
- Pitch: container is the full left column width, pitch fills it with 12px padding only. Players 18px circles with shirt number, team colour fill, name label on hover/tap (card with name, role, minute stat). Ball 10px white with glow and 6-point trail. Direction-of-play arrow (home attacking right) in the bottom corner. Overlays (momentum swing, "Samir hits 33 km/h") render **below the pitch** as a caption row with icon, never over it, except goal/card flash, which is a bottom-left chip 40px tall that never covers the penalty areas (position by event side).
- Layer chips switch overlays (Shape = convex hulls/lines, Heat = density, Pass network, Pressing) if data exists; otherwise show only "Players" and hide the others (do not show disabled chips).
- "Story so far": one sentence generated from existing insight/recap data (e.g. "Harbour lead 1-0 after a 23' Achterberg goal; Northbridge have 11 shots to 5 but nothing to show."). Use the latest recap text if available; otherwise hide.
- Momentum: one bar with the label in words ("Northbridge on top", "Even") that always agrees with the bar; scale caption on tap-hint ("How we measure this").
- Match pulse: keep chart (it is the best part), tooltip must not clip (render in portal, `glass-strong`), strips use new colours (section 3), legend inline under the chart at 13px, caption trimmed to one line + info popover.
- Polls: compact card in right column; zero votes shows "Be the first to vote" with the three options as chips; hide results until >=1 vote.

Tablet 1024: single column hero, pitch full width (min height 420px), right column items become a 2-column row beneath the pitch (Latest moments | Poll), pulse full width below. Floating pill bottom-center above nothing (no tab bar; top nav at this size).

Mobile 390:
```
[mini header 52 sticky: NBR 2-1 HAR 67' ●]
[hero 148: crests, score, minute]  (collapses as you scroll)
Story so far (serif 20)
[ PITCH full-bleed 390 wide ]
layer chips (horizontal scroll row)
Momentum bar
[Latest moments: horizontal snap carousel of cards] 
Match pulse (min-w-0, chart height 180)
Poll compact
                      [Catch me up][Ask pundit]  (pill above tab bar)
[tab bar]
```
Order is fixed to keep the first screen = score + pitch + story (target <= 700px to the end of the pitch).

States:
- Loading: hero skeleton (score blocks), pitch skeleton with pitch lines drawn and no dots, three card skeletons.
- Reconnecting: header pill; pitch dims 20% and freezes; no overlay wall.
- No match / pre-kick-off: hero shows "Kick-off 17:30" with a countdown and the capability bento (section 4.5), pitch shows lineups outline.
- Full time: LIVE badge becomes "FT", Coach brief card moves to "Full-time report" CTA in the hero actions.

### 4.2 Moments `/moments`

Desktop: single column max 760px centred, with a right sidebar (span 4) holding filters (All / Goals / Cards / Subs / Shots / Insights; team toggle; "Only my club") as a sticky chip list (no inner scroll). Feed items:
- **Big moment card** (goals, cards, subs, red card): solid surface, 20 radius, left accent in team colour, minute (display-l), icon (ball, yellow/red card rect, swap arrows), headline (heading, plain English), one-line explanation, mini pitch thumbnail showing the event location, actions: **Share** (copies link or creates image via native share; fall back to copy), "Replay on pitch" (scrolls to Live with the pitch seeked). Goals get a 1px team-colour gradient border and a subtle shimmer once on arrival.
- **Quiet row** (everything else): 14px, one line, icon, minute, no card chrome, hairline separator; expand-on-click to show details (the former "Match data" as a "Details" button opening a side sheet in plain language).
- **Catch me up** (replaces "What did I miss?"): a drawer (bottom on mobile, right sheet 440px desktop). Header "Catch me up" with segmented 5 / 15 / 30 min. Body: 3 bullets max, each with minute and plain text, then the big moments as compact cards. Loading: 3 skeleton bullets with text "Pulling together the last 15 minutes" (never blank). Source badge "From match data" as tiny caption with info popover.
- **Ask the pundit**: chat-style drawer: suggested chips ("Who's been best?", "Why are we losing?", "What changes things?"), user bubble right, pundit bubble left with typing dots, answer 3 bullets max.
- Recap: button "Full recap" at the top of Moments opens the same drawer at the top-level (3 paragraphs).
- Empty: "Nothing yet. The first moment lands after kick-off." with the pitch outline illustration.
- Mobile: same list, filters become a horizontal chip row sticky under the mini header (top: 52px).

### 4.3 Stats `/stats`

Segmented control (animated): **Team | Players | Shots**.
- **Team:** hero row of 3 KPI tiles (Possession, Shots, Chances created (xG)) with big numbers (display-l), team colours; below, paired stat bars list: label **above** the bar (13px caption, `--fg-muted`), home value left, away value right, bar two-tone in team colours, leading side's number in team colour and weight 600. Top-4 rows (Possession, Shots, Shots on target, Chances created) are large; the rest in a "More stats" disclosure. Add "Who is winning this?" one-line summary above the bars. Possession is always 100% total. xG race chart (area/step chart, team colours, goal markers) below.
- **Players:** table (sticky first column, 48px rows, tabular numbers, hairline separators): Player, Min, Rating (micro-bar), key stat per role. Sort on header click. Row click opens a player sheet (card: rating, key stats, recent moments). Remove fan vote columns in Coach mode, and in fan mode render votes as a single "Vote" button inside the row sheet, not columns. "Favourite player" card pinned at top when set.
- **Shots:** half-pitch zoomed shot map per team (team toggle, colour-coded), circles sized by chance quality, goals ringed, labels on tap; list beside it (desktop) or below (mobile).
- Mobile: tables become horizontally scrollable with sticky first column inside a `min-w-0 overflow-x-auto` wrapper; KPI tiles are a 3-up snap row.

### 4.4 Coach `/coach`

Header: team switch (segmented, each segment has the team colour dot and crest; selected tints the page ambient and card left borders) + "As of 67'" timestamp + Refresh icon button. Sub-tabs (animated underline): **Brief | Tactics | Report**.
- **Brief:** keep the existing 3-card structure (it is the best-readable part): number, title, confidence chip, Evidence / Do this / Trade-off labelled rows, metric chips. Changes: cache-first (show last brief instantly with "Updated 40s ago" and refresh in the background, skeleton only if none); "What changed since last brief" strip at the top (up to 3 chips); per-card Pin and Dismiss; phase-aware empty state at FT: "Full time. See the full-time report" with CTA; timestamp and confidence on every number chip; "Blocked, data shown" replaced by a quiet "Checked against match data" tick with info popover.
- **Tactics:** goal-first flow. Step 1 chips: "What's the situation?" (Chasing a goal, Protecting a lead, Breaking their press, Custom...) in a 2-col grid of large tap targets; Step 2 (collapsed under "Fine-tune") holds Moment, Risk, Number of ideas. Primary button "Build ideas" (accent) with progress state (indeterminate bar + status text "Testing idea 2 of 4...") and streaming idea cards appearing as scored. Results: idea cards with outcome bars showing range ticks; if differences are within noise show "No clear winner" badge with the margin instead of "Recommended"; label recipe fallbacks "Standard play (AI unavailable)". Layout: form is a top card (not a left column), results below in a grid (2-up desktop, 1-up mobile). No inner scrolling form.
- **Report (half-time/full-time):** new one-page, print-friendly, shareable. Sections in order: scoreline + phase (HT/FT) header; "Top 3 actions" (from brief); xG timeline; shot maps (half-pitch); possession and territory; subs and cards table; key players; "Export" button (browser print to PDF via print stylesheet, plus Copy link). Desktop 2 columns (actions + xG left, shots + tables right), mobile single column. Available whenever there is data; defaults to "Half-time" if minute 45-60, otherwise "Latest".
- Tablet 1024x768 bench: everything >= 14px text, 44px targets, no sticky nav obstruction, pitch >= 420px tall wherever shown.

### 4.5 Explore `/explore`

Bento grid (Magic UI `bento-grid`), tiles with a live mini preview:
```
+---------------------------+ +----------------+
| Multiverse (2x1)          | | Broadcast view |
| 4 tiny fan screens        | | (1x1) overlay  |
+---------------------------+ +----------------+
+----------------+ +--------------------------+
| Scenario lab   | | Replay a past match      |
| (1x1)          | | recorded + past matches  |
+----------------+ +--------------------------+
```
Tile copy: **Multiverse** "See this match told four ways: casual, analyst, player and coach, in other languages." **Broadcast view** "A TV-style overlay you can put on a big screen." **Scenario lab** "Script a famous comeback or a ten-man heist and watch it play." **Replay a match** "Go back to a past match or a recorded video." Hover: spotlight and lift; click opens the page. This bento is also reused as the Live empty state ("What you can do").
- `/explore/multiverse`: 2x2 grid on desktop, 1 column with swipe on mobile; each panel is a smaller solid card; its title is human ("Casual fan, in Hindi"). Mode/language selects become a single "Customise" popover per panel. Empty "Waiting for the first moment" becomes a skeleton and "Starts at kick-off".
- `/explore/broadcast`: full-bleed, pitch 16:9 centred, overlays placed outside the pitch area (lower third below), a glass pill bottom-centre on hover with "Exit" and "Copy overlay link (for OBS)" and a brief first-visit hint "Put this on a big screen. Press F for full screen."
- `/explore/lab`: scenario cards in a grid; each has title, one-line story, scripted final score, sparkline, Speed chips, **Kick off** primary. Dataset download moves to the card overflow menu.

### 4.6 Command palette (⌘K / Ctrl-K, also the ⌘K chip and a search icon on mobile)
`glass-strong` dialog 640px wide (mobile: full-width sheet from the top). Sections: **Go to** (Live, Moments, Stats, Coach, Explore, each tab), **Do** (Catch me up, Ask the pundit, Share this moment, Build tactic ideas, Open full-time report, Toggle Coach mode, Switch theme, Developer tools), **Players** (type to find, opens player sheet), **Teams**. Each row: icon, label, hint text, shortcut at right. Empty search shows a "What can I do?" list with the "Do" section first.

### 4.7 Developer drawer
Right sheet (desktop 520px) or full bottom drawer: Tabs Demo / Agents / Matches. Demo: speed segmented, start-from chips, Pause, Restart (opens confirm dialog "Restart this match? Your catch-up and polls reset."). Agents: render the existing pipeline and stats; model string truncated with copy button. Matches: past matches list with team names, score, status and relative time (e.g. "Today 03:23"), Recorded matches with a human name or "Video 128057" fallback.

### 4.8 Toasts and notifications
sonner, glass, bottom-center above the tab bar (mobile) or top-right (desktop). Goal and red card toasts have team-colour accent and a "View" action. "Notify me about goals and cards for my club" is a toggle in the profile popover (browser Notification permission requested only on toggle).

### 4.9 Onboarding sheet (first visit, skippable)
Centre dialog on desktop, bottom drawer on mobile: "Who are you here to watch?" Two big club tiles (crest, name) plus "Neutral"; then a 2-option segmented "How do you like it? Casual / Analyst" with one example sentence each; "Start watching". Language is offered only if browser language is not English via a chip below. Choices stored in the existing preference state; everything else lives in the profile popover.

## 5. Components and install commands

Install from the repo's frontend dir (Base UI shadcn setup per research.md). Review diffs after each add; rewrite `framer-motion` imports to `motion/react`.

```bash
npx shadcn@latest add command dialog drawer sheet tabs popover tooltip sonner toggle-group segmented-control 2>/dev/null || npx shadcn@latest add command dialog drawer sheet tabs popover tooltip sonner toggle-group
npx shadcn@latest add @animate-ui/components-base-tabs @animate-ui/components-base-popover
npx shadcn@latest add @animate-ui/primitives-texts-sliding-number
npx shadcn@latest add @magicui/number-ticker @magicui/blur-fade @magicui/bento-grid @magicui/magic-card @magicui/border-beam @magicui/dock @magicui/animated-list
npx shadcn@latest add @motion-primitives/animated-background @motion-primitives/spotlight
npx shadcn@latest add @evilcharts/recharts-area-chart @evilcharts/recharts-tooltip   # reference for chart styling
npm i geist
```
Fonts: `geist/font/sans`, `geist/font/mono`, `Barlow_Condensed` (600, 700), `Instrument_Serif` (italic 400) via `next/font`.

Custom components to build (names fixed so reviews can refer to them): `AppShell`, `TopBar`, `TabBar`, `MatchHeader` (hero/collapsed), `ProfileMenu`, `CommandPalette`, `FloatingActions`, `Pitch` (+ `PitchLayers`, `PlayerDot`, `BallTrail`), `MomentCard`, `MomentRow`, `CatchUpDrawer`, `PunditDrawer`, `StatPair`, `KpiTile`, `PulseChart`, `ShotMap`, `PlayersTable`, `PlayerSheet`, `CoachBriefCard`, `TacticIdeaCard`, `ReportPage`, `CapabilityBento`, `Skeletons`, `ConnectionPill`, `DevDrawer`, `OnboardingSheet`.

Delete/avoid: left-rail sticky, inner-scroll insights list, `h-screen`, plain native `<select>`s (use shadcn `select`/popover), solid-white primary buttons for non-primary actions.

Buttons: primary = `--accent` fill (only one per view), secondary = `surface-2` with hairline, ghost = transparent; destructive actions (Restart) are secondary-with-red-text behind a confirm. Focus ring: 2px `--accent` offset 2px. Segmented control: pill track `surface-2`, sliding indicator `surface` with e1; indicator and track share the same radius and no outline offset (fixes the audit's mis-aligned tab outline).

## 6. Copy replacements (plain English)

| Old | New |
|---|---|
| Match centre | Live |
| Multiverse | Multiverse stays only as a tile title with the subtitle "One match, told four ways" (nav label is Explore) |
| Broadcast output | Broadcast view |
| Scenario lab | Scenario lab ("Play a scripted match") |
| Agent room | Developer tools > Agents |
| Tactics board | Tactics (inside Coach) |
| Coach's brief | Coach brief |
| What did I miss? | Catch me up |
| Ask the pundit | Ask the pundit (keep) |
| Match recap | Full recap |
| Live insights | Latest moments |
| Match data / How this was checked / Blocked, data shown | "Details" button; "Checked against match data" with tick; hide model names |
| xG / "xG 1.20" | "Chances created" (with "1.2 goals' worth" in tooltip); chart name "Chance race" |
| Match pulse | Match flow (info popover: "Each column is a minute. Above the line the home side was on top.") |
| Momentum -22.6 | "Harbour on top" (no raw number on fan screens; analyst mode shows "Momentum 23 (scale 0-100)" with definition) |
| Agent p95 / Engine p95 | Developer drawer only: "Typical reply time" / "Engine speed" |
| Demo controls, Replay speed, Start from minute | Developer drawer: "Demo", "Speed", "Start at" |
| Past matches / Recorded matches | "Replay a match" / "Videos turned into data" |
| Your view / Fan mode / Headline metric | Profile: "Your style", "How should we tell it?" / remove Headline metric (analyst only, in profile) |
| Risk appetite | "How bold?" (Safe, Balanced, Bold) |
| Look ahead 10 s / Ideas 3 4 5 | "How far ahead" / "How many ideas" under Fine-tune |
| Match feed unavailable | "Reconnecting to the match..." pill; no wall |
| Pick a favourite player to see their card | "Follow a player to see their card here" + Choose button |
| Recommended (when within noise) | "No clear winner" |

## 7. Fan and coach asks: included vs deferred

Included (phase noted):
- Fan: matchday hub with big score, pitch, story line (P2); no error wall, skeletons, auto-retry (P1); nav cut to 5 with labels, dev stuff hidden (P1); mobile bottom tab bar + sticky mini scoreboard (P1); plain English (P1/P2); moment cards with Share (P3); fix pitch fill, overlays off play, scroll jump (P1/P2); one-step onboarding replacing the dropdowns (P3); floating Catch me up / Pundit pill (P2); stats redesign with labels above bars (P4); pundit as chat (P3).
- Coach: data-integrity fixes at the presentation layer (momentum label agrees with bar, scale defined, no "11 players" sub-line under a card, timestamps and confidence marks) (P2/P4) plus a request to the backend owner for the rest; "no clear winner" and progress state in Tactics (P4); Half-time/Full-time Report page (P4); pitch as hero with names on tap, direction, layers (P2); tablet bench layout at 1024x768 with 14px minimum and 44px targets (all phases); Coach mode toggle and Restart behind confirm (P1 for confirm, P4 for mode); filterable timeline (P3); Brief cache-first and what-changed strip (P4).

Deferred (note in backlog, not in this redesign): real club crests/real team names and fixtures list, push notifications beyond the in-app toggle, pre-match opposition profile, clip tray/video tagging, per-half and last-15 stat splits, heatmap/pass-network/pressing layers when data is not already present in the feed (render the chips only if data exists), "pause on next key event" replay option, replay highlights reel, chat. Backend items to escalate (not UI): momentum scale definition and banner/bar consistency; yellow card sub-line bug; scorer corrections explanation; Tactics zero-baseline recommending; Coach brief phase awareness at FT; recap agent returning an empty body.

## 8. Build order and acceptance

**Phase 1: shell + scroll.** Install fonts/tokens/glass utilities and ambient background; `AppShell`, `TopBar`, `TabBar`, `MatchHeader` (hero + collapse), new routes and redirects, `ConnectionPill` + skeletons (no error walls), `ProfileMenu` shell, `DevDrawer` (move demo controls, past/recorded matches, agents), Restart confirm, `devIndicators:false`, remove right-rail inner scroll, single document scroll. Acceptance: 1440 and 390 screenshots of all routes show the new chrome; at 390 scroll to 700 and switch tabs keeps scrollY (+-2); no element with `overflow-y:auto` outside sheets/palette/tables; no horizontal overflow at 390; sticky header never covers content after tab/anchor jump; Lighthouse a11y contrast pass on text tokens.

**Phase 2: Live.** New hero, `Pitch` rebuild (fills card, overlays off-pitch, names on tap, layer chips), momentum and pulse restyle, story line, latest moments column/carousel, floating actions, number tickers, goal flash.

**Phase 3: Moments + drawers.** `MomentCard`/`MomentRow`, filters, Catch me up and Pundit drawers, Share, command palette, onboarding sheet and profile popover contents, toasts.

**Phase 4: Stats and Coach.** Stats tabs, PlayersTable and PlayerSheet, ShotMap, Coach Brief restyle + cache, Tactics goal-first flow with progress and "no clear winner", Report page with print stylesheet, Coach mode.

**Phase 5: Explore + polish.** Bento, Multiverse restyle, Broadcast chrome, Lab cards, empty states everywhere, light theme pass, motion polish, tablet 1024 review, reduced-motion and reduced-transparency checks, `web-design-guidelines` audit.

Review protocol: implementer saves screenshots to `docs/redesign/screens/after/` as `{desktop|tablet|mobile}-{route}-{viewport|full}.png` after each phase and messages the designer. Designer responds with a numbered fix list; a phase closes only when scroll acceptance, contrast and "does not look like a dashboard" (hierarchy, depth, type scale, motion) are all satisfied.
