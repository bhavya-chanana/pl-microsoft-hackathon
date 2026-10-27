# MatchLens design audit (before)

Method: driven with Playwright at 1440x900 and 390x844, no source reading. Screenshots: `docs/redesign/screens/before/` (`desktop-*`, `mobile-*`; `-viewport` and `-full`; plus `home-stats`, `home-missed`).

## (a) Inventory

Top bar: wordmark, 7 equal-weight text links, LIVE/FULL TIME badge, theme toggle. On mobile the links collapse to 7 unlabeled icons (no names).

| Route | What it does |
|---|---|
| `/` Match centre | Scoreboard + 2D pitch with player dots and a "TV camera" toggle; momentum bar; Match pulse chart (per-minute control, event markers); fan polls (who wins / player of the match); tabs Timeline / Stats / Ratings; right rail: What did I miss? (5/15/30 min dialog), Ask the pundit, Match recap, Listen (TTS), "Your view" (Casual/Analyst/Player focus, language, club, favourite player, headline metric), Live insights feed; Demo controls (speed, start minute, restart, pause); Past matches; Recorded matches (video-ids + Play). |
| `/multiverse` | Four simultaneous fan screens (casual Hindi, analyst English, player-focus Spanish, coach) each with pitch, replay badge, 3 insights, mode/language selects. |
| `/broadcast` | Full-bleed pitch with TV-style score bug and lower-third overlays; "Back to Match centre". No nav. |
| `/coach` | Coach's brief: team toggle, top 3 ranked actions (evidence / do this / trade-off / confidence / chips), refresh, "Blocked, data shown" fact-check strip, demo controls. |
| `/tactics` | Tactics board: form (moment, coaching side, free text, quick starts, risk, count) -> "Build the board" -> simulated tactic ideas. |
| `/lab` | Scenario lab: scripted synthetic matches (3-4 cards) with speed and Kick off. |
| `/agents` | Agent room: model name, p95 stats, React Flow pipeline diagram, latest insight, coordinator decisions table, demo controls. |

## (b) Jobs to be done vs discoverability

1. "What's happening / what's the score" -> pitch+score visible, but small and buried in a boxed card. OK-ish.
2. "I missed something, catch me up" -> best feature, but one of three equal grey buttons in a side rail; no hint of what it does.
3. "Explain it to me in my language/level" -> five dropdowns that all default to "None/Neutral". Value never explained.
4. "What should the coach do now" -> `/coach`, but nav label gives no clue; the "Coach" page is mixed with fan features.
5. "What if we change tactics" -> `/tactics`, hidden behind a long form; results are an empty dashed box.
6. "Show the match to others / on a screen" -> Broadcast output: label is jargon; no preview or entry point explaining it.
7. "Try it with a demo" -> Scenario lab and Demo controls are repeated on 3 pages and in the main flow.

Verdict: nothing tells a new user what the app can do. 7 flat tabs, internal vocabulary (Multiverse, Agent room, p95, xG, "Match data", "How this was checked", model names) and demo/dev controls sitting at the same level as the product.

## (c) Problems, ranked

### Critical
- **C1. Scroll jump on mobile.** Repro: 390x844, open `/`, scroll to y=700, tap Stats (or Ratings). Page scroll jumps to y=1691 and lands mid-sidebar ("Your view" dropdowns) with tabs half-visible under the sticky header. The tab content replaces a taller one, and the browser anchors scroll. Same family of problem on desktop: after switching tabs the sticky 57px header overlaps the content start (Match pulse card is clipped by the header glass edge).
- **C2. Nested/clipped scroll region in the right rail (desktop).** Repro: 1440x900, `/`. The sidebar is sticky and the "Live insights" list is an inner `overflow-y:auto` container measured 178px content in a 50px viewport, i.e. you see one clipped row ("1' Match / Match data") cut off by the viewport bottom. Insights, the app's core output, are effectively invisible without finding a second scrollbar.
- **C3. Mobile "Match feed unavailable" overlay** covers the whole pitch and scoreboard with a heavy error wall during a blip (390 run). The error replaces the content instead of showing stale data plus a quiet banner; auto-retry exists but screen shouts.
- **C4. Mobile nav is 7 identical unlabeled icons.** Fails discoverability entirely; icons are not self-evident (grid, clipboard, flask...). No bottom bar, so thumb reach is poor.
- **C5. Contradictory data (from coach review).** Momentum banner "swings to Northbridge" while bar reads "Even"; Yellow card insight says "11 players on pitch". Trust-breaking.

### High
- **H1. No hierarchy on `/`.** Everything is a same-weight dark box: pitch card, momentum, pulse, polls, tabs, demo controls, past matches, recorded matches (raw `video-128057`). 1950px tall on desktop, 2640px on mobile, no clear primary.
- **H2. Pitch is undersized and under-served.** Pitch occupies ~65% of its card with big empty gutters, dots are 14-16px, no names, no ball emphasis, no direction of play. Broadcast overlays (lower third, momentum tag) cover pitch areas and sit half off the pitch edge.
- **H3. Typography/contrast.** Body/supporting copy 11-13px mid-grey on near-black (e.g. "Votes are anonymous...", "Match data", legends). Headings are all the same condensed face at similar size; scoreboard text is small relative to the page. Reads as admin console.
- **H4. Dev/demo controls in the product flow.** Demo controls, Restart match (no confirm, destroys state), Past matches, Recorded matches, Agent room p95/model strings are visible to every user.
- **H5. Streamlit-like composition.** Full-width stacked bordered cards, uniform 1px outlines, equal gutters, left-aligned page title + gray lede on every page, native-looking `<select>`s. No depth, no imagery, no motion.
- **H6. Mobile page is very long** with fan settings between pitch and content; "Your view" block (5 controls) sits above the Insights tabs, pushing real content ~1700px down.

### Medium
- **M1. Tab control looks broken.** Active-tab outline is offset from its pill track (Timeline/Stats/Ratings), focus ring larger than the segment; inconsistent with the segmented controls elsewhere (which are solid white-filled).
- **M2. Primary button styling is solid near-white** for "What did I miss?" and Restart match alike, so the loudest control is also the most destructive.
- **M3. Empty/loading states** are text-only: "The pulse starts once the first minute is played", dashed "Nothing on the board yet", "Pick a favourite player to see their card". No skeletons, no illustration, no next action. Multiverse in a hostile language (Hindi/Spanish) with no explanation of why.
- **M4. Form-heavy Tactics board.** Inner scroll on the form (938/802px) inside the page; free-text + 3 quick starts + 3 segmented controls before any value.
- **M5. Color.** Team colours (blue/amber) good, but pulse strips (teal/pink/grey), green pitch, yellow REPLAY chip, red LIVE and white buttons compete; no single brand accent. Light theme toggle exists but untested; dark is the only polished mode.
- **M6. Next.js dev "N" badge** overlaps bottom-left content in every screenshot (dev-only, but hides the first characters of copy).
- **M7. Coach and Tactics do not recommend responsibly** (coach review): "Recommended" at 0% vs 0%; no timestamps or confidence on numbers; no half-time/full-time report.
- **M8. Broadcast route** has no controls or exit besides a text link, and there is no way to preview it from the app.

### Low
- Page titles repeated as both nav label and H1 with different words ("Broadcast output", "Scenario lab" vs "Lab" on mobile aria).
- LIVE chip, theme toggle top-right are tiny; no match picker/switcher.
- Opaque card borders everywhere; shadows absent.
- No keyboard shortcuts / command palette.

## (d) Direction

### Information architecture
Split by audience and strip dev tooling out of the main path.

- Primary nav (sidebar on desktop >=1024, floating bottom tab bar with labels on mobile): **Live** (hub), **Moments** (feed + recap, "What I missed"), **Stats**, **Coach** (brief + tactics + half-time report), **Explore** (Broadcast, Multiverse, Scenario lab).
- "Behind the scenes" (Agent room, demo controls, past/recorded matches, model info) lives in a Settings/Developer drawer, off by default.
- Persistent compact match header (score, minute, live dot) that collapses on scroll; on mobile it is the sticky mini-scoreboard.
- Fan setup becomes a one-time onboarding sheet (pick club + Casual/Analyst), later a profile popover. Language only if non-English detected.
- "What did I miss?" and "Ask the pundit" become a floating action pill plus Cmd/Ctrl-K command palette ("what can I do" list).
- Home empty state / capability tiles (bento) explaining the 4-5 things you can do.
- Drill-downs (Match data, ratings, player) open in side sheets, not in-page jumps. Fixes C1 by keeping scroll position stable (fixed-height tab panels / `scroll-margin`, `overflow-anchor: none`).
- One page scroll only; no nested scrollers except intentional horizontal carousels.

### Three visual directions
1. **Editorial dark (Apple Sports / FotMob / Linear).** Near-black cool surfaces, solid elevated cards, large scoreline in a condensed display face, one pitch-green brand accent, team colours carry all data, glass used only on nav/sheets/floating bars. Safe, legible, fast, works on iPad at the bench.
2. **Broadcast (Sky Sports Super Sunday).** Chamfered score bug, bold colour blocks, big type, motion wipes. Strong identity, but heavy, hard to keep readable in dense coach views. Use only for the hero and key-moment cards.
3. **Liquid Glass (Apple-style).** Translucent layered panels, specular edges, blur, morphing overlays. Most distinctive, but GPU-costly, contrast risks behind charts/pitch, refraction is Chromium-only. Use as an accent on chrome/overlays rather than the base.

**Pick: 1 as the system, with 2 on the hero scoreboard and goal/card moment cards, and 3 only on floating chrome (top bar, bottom tab bar, command palette, sheets).** This matches the researcher's recommendation: it stops the Streamlit look through hierarchy, scale, depth and motion rather than a gimmick, and is the least risky for readability and mobile performance.

### Visual language notes
- Type: Geist (Sans/Mono, tabular numerals) for UI; Barlow Condensed for scorelines and minute; min body 15px, support 13px, never lighter than 4.5:1.
- Spacing: 8px grid; cards 20-24px radius; fewer outlines, use elevation (layered shadow + 1px inner highlight) instead of borders.
- Motion: spring sheet presentation, shared-element score/pitch hero collapse, number tick-ups, pulse for LIVE; respect reduced motion.
- Pitch is the hero: edge-to-edge, 18px dots with numbers, names on tap/hover, direction arrow, ball glow, layer chips (shape / heatmap / passing / pressing); overlays go below or beside the pitch, never on it.
- States: skeleton shimmer, quiet inline "Reconnecting" pill with stale data, friendly empties with a single CTA.
- Resolve data contradictions (momentum scale, card text) before polishing: trust is part of the design.

Pending reads for the spec: `research.md`, `fan-review.md`, `coach-review.md`, `frontend-map.md`.
