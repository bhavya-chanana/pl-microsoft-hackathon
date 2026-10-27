# Frontend map (for the designer)

Stack: Next 16 (app router) + React 19, Tailwind v4 (`src/app/globals.css`, no tailwind config), shadcn "base-nova" on
Base UI (`src/components/ui/*`), lucide icons, motion, recharts, @xyflow/react (agent graph), zustand stores,
TanStack Query, next-themes (class `dark`, default dark), sonner toasts. Fonts: Barlow Condensed (display), IBM Plex Sans
(text), Noto Sans Devanagari. All UI copy lives in `src/lib/copy.ts` (en; match content is localised by the backend). Zod API
contract in `src/lib/contract.ts`. Dev server: port 3020 in this worktree.

## Routes (src/app)
| Route | Screen | Main component |
|---|---|---|
| `/` | Match centre (the fan home) | `features/companion/match-centre.tsx` |
| `/multiverse` | Wall of alternative-scenario monitors | `features/multiverse/multiverse.tsx` |
| `/broadcast` | Clean programme output, full-bleed, no header | `features/broadcast/broadcast-monitor.tsx` (`fill`) + `broadcast-controls.tsx` |
| `/coach` | Coach brief: 3 ranked actions per team | `features/coach/coach-room.tsx` |
| `/tactics` | Tactics board: pick a moment, ask for ideas, simulated outcomes | `features/tactics/tactics-board.tsx` |
| `/lab` | Scenario lab: what-if scenarios, dataset download | `features/lab/scenario-lab.tsx` |
| `/agents` | Agent room: pipeline graph + decision log table | `features/agents/agent-room.tsx` |
API proxies: `/api/stream` (SSE live feed), `/api/backend/*`. The live feed is global state (`features/live/live-store.ts`, started once in `providers.tsx`).

## Shell
- `app/layout.tsx`: `<body min-h-dvh>` > Providers > `AppHeader` + page. Single document scroll.
- `components/shell/app-header.tsx`: sticky top, h-14, 7 nav links (icon + label only >= xl; "short" label lg-xl; icons only below), connection pill, theme toggle. Hidden on `/broadcast`. On mobile it wraps to two rows (nav row below logo).
- Nothing else is app-wide chrome. No footer, no command palette, no mobile tab bar.

## Match centre layout (the main screen)
`main` grid: `lg:grid-cols-[1fr_24rem]`, max-w-screen-2xl.
Left column order: heading, `BroadcastMonitor interactive` (16:9 pitch + score bug + momentum + overlays + goal takeover + player sheet),
`MomentumBar`, `MatchPulse` (recharts momentum chart), `MetricTile` (headline metric), `FanPolls`, `ShotMapPanel` (analyst mode only),
then (desktop) Tabs timeline / stats / ratings, `ReplayControls` (demo speed/restart), `PastMatches`.
Right rail `aside`: CatchupDialog ("What did I miss?"), PunditPanel ("Ask the pundit"), RecapDialog, ListenToggle (narration),
`ProfilePanel` ("Your view": mode casual/analyst/player focus, language, club, favourite player, headline metric), then Live insights feed (`InsightFeed` of `InsightCard`s).
Below lg the rail stacks under the left column and becomes tabs (feed/timeline/stats/ratings); the page is ~12,000px tall on mobile.

## Feature components (all under src/features)
- broadcast: `pitch.tsx` (SVG pitch, tilt camera, players), `overlay-layer.tsx` + `use-overlay-director.ts` (timed graphics), `score-bug.tsx`, `goal-takeover.tsx`, `telestrator.tsx`, `use-instant-replay.ts`, `momentum-bar.tsx`.
- companion: `insight-card.tsx` + `fact-check-badge.tsx` (the "verified / match data" trust badges), `match-timeline.tsx`, `match-pulse.tsx`, `catchup-dialog.tsx` (also RecapDialog), `narration.tsx` (TTS).
- stats: `team-stats.tsx` (TeamComparison + PlayerCard), `metric-tile.tsx`, `shot-map.tsx`. fans: `fan-polls.tsx`, `player-ratings.tsx`, `match-card.tsx`. pundit: `pundit-panel.tsx` (chat in a Sheet). player: `player-sheet.tsx`. profile: `profile-panel.tsx`, `profile-store.ts` (persisted). replay: `replay-controls.tsx`, `past-matches.tsx`.
- tactics: `scenario-prompt`, `moment-picker`, `tactic-card`, `ghost-pitch`, `rollout-stage`, `outcome-bar`, `branch-compare`, `agent-trace`, `fixtures/`.
UI primitives present: button, badge, dialog, sheet, tabs, select, slider, toggle(-group), tooltip, scroll-area, separator, skeleton, sonner. No card, command, dropdown-menu, popover, table, input.

## Scroll containers (where they live) and what was wrong
Before: only document scroll should exist, but these nested scrollers existed:
1. Home right rail `aside`: `lg:sticky lg:h-[calc(100dvh-6rem)]` fixed height with 3 content blocks above the feed, so the "Live insights" scroller (`min-h-0 flex-1 overflow-y-auto`) was squeezed to **50px** (measured). FIXED (commit): the rail is `sticky top-20 self-start max-h-[calc(100dvh-6rem)] overflow-y-auto overscroll-contain`, feed flows inside; single scroller.
2. Home timeline tab: `max-h-96 overflow-y-auto` box inside the page (nested scroll, clipped rows). FIXED: removed, page scrolls.
3. `/tactics` input form: `lg:sticky lg:max-h-[calc(100dvh-6rem)] lg:overflow-y-auto` (rail scroller, OK pattern, 938 vs 802px at 1440x900) and `moment-picker` radiogroup `max-h-60 overflow-y-auto` (nested). Result column `xl:sticky xl:top-20`.
4. `/agents` decision table `max-h-[28rem] overflow-auto` (table scroller with sticky thead; horizontally scrolls on mobile; acceptable but a nested scroll).
5. `/multiverse` mobile: horizontal snap carousel (`overflow-x-auto`), grid on lg.
6. `/lab` aside `lg:sticky lg:top-20 lg:self-start`.
7. Sheets/dialogs (player sheet `max-h-70dvh overflow-y-auto`, pundit sheet) are expected.
Heights: `h-dvh` used only for `/broadcast` fill. No 100vh/h-screen, no body overflow locks found. Document horizontal overflow: none at 390 or 1440 on any route.
Sticky offsets are hard-coded `top-20` / `6rem` = header 3.5rem + 1rem; change both if the header changes.

## Current tokens (globals.css)
Colours are OKLCH CSS variables mapped through `@theme inline`: background/foreground/card/raised/popover/primary/secondary/muted/accent/border/input/ring/destructive plus domain tokens: `tally` (on-air red), `verified`, `rewritten`, `blocked`, `data`, `turf`/`turf-stripe`/`chalk` (pitch), `graphic`/`graphic-foreground`/`graphic-warn` (broadcast overlay panels), `home`/`away` team colours (blue / amber), `tier-*`, `telestrator`, `pulse-control`/`pulse-chaos`.
Dark (default): stadium-night slate (bg oklch .165 .018 255, card .205). Light: cool paper. Radius base 0.5rem. Shadows: `--elevation-1/2` (dark = inner hairline). Type scale: `text-score`, `text-display` (clamp), display font uppercase condensed for headings. Motion: `--ease-broadcast`; reduced-motion handled globally. Utility `.graphic-cut` slanted clip edge for on-air graphics.
Look today: flat bordered cards (`rounded-lg border bg-card p-4 shadow-raised`) stacked in columns, small muted captions (text-xs/sm muted-foreground), no glass, no hero, minimal hierarchy: reads like a dashboard.

## Verification tooling
`/private/tmp/.../scratchpad/probe.mjs <before|after>` (Playwright) screenshots all 7 routes at 1440x900 and 390x844 into `docs/redesign/screens/<name>/` and prints nested scrollers and horizontal overflow. The SSE stream means `networkidle` never fires; use `domcontentloaded` + delay.
