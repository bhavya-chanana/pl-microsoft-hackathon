# Frontend progress (paused)

## Phase 1 (shell + scroll + tokens): mostly built, not reviewed, not committed as final
Done:
- Tokens retuned to spec in globals.css (dark-first, glass utilities, ambient wash, --stack-h/--topbar-h/--hdr-min/--hdr-max), Geist Sans/Mono + Barlow 600/700 + Instrument Serif (--font-story), devIndicators false, redirects in next.config.ts.
- New shell in src/components/shell: AppShell, TopBar (glass, layoutId pill), TabBar (mobile), MatchHeader (scroll-linked hero/collapse), ProfileMenu (popover), CommandPalette (+chip), DevDrawer (demo/matches/agents; Restart behind confirm), ConnectionPill ("Reconnecting…", no error wall).
- Routes: /moments, /stats, /explore, /explore/{multiverse,broadcast,lab}; /coach has Brief/Tactics tabs (?tab=tactics); old routes redirect; /agents kept.
- Live page is a single document scroll (right rail removed, demo controls moved to DevDrawer). Segmented + StablePanel (tab height hold) added.
- tsc and lint clean as of last run. Desktop probe: no nested scrollers (only /agents table), no horizontal overflow.

Not done / unverified:
- `npx next build` not run. Tablet (1024x768) and mobile (390x844) probe not run; mobile scroll-to-700 then tab-switch test (scrollY within 2px) not run; check sticky header never covers content after tab/anchor change.
- Desktop screenshots exist in docs/redesign/screens/after/phase-1/ but not visually reviewed.
- Spec items skipped in P1: shadcn registry installs (@animate-ui, @magicui, @motion-primitives) not added; LazyMotion not set up; the remaining section-6 copy replacements (Match data / How this was checked etc.) not applied; /moments is a simple placeholder.
- Message the designer after phase 1 verification.

## Next steps
1. `npx next build`; run probe2.mjs (scratchpad) for tablet and mobile, fix issues; add scroll-stability test.
2. Commit phase 1, screenshot, message designer, apply numbered fixes.
3. Phases 2-5 per docs/redesign/design-spec.md section 8.

## Designer feedback not yet applied
None received yet.

## Final pass (phase 5 polish)
- One shell width (max-w-7xl) on all pages; Moments gets a right aside; Stats toolbar is one row, Fans column hidden when empty.
- Explore tiles have real mini previews; Multiverse caption sits below the pitch; mobile Live caption is one row and the pill no longer hides content.
- Pitch markers use a roving focus (one tab stop, arrows move, Escape returns to the pitch) instead of 22 tab stops.
- Checked: contrast of subtle text over glass (5.3-6.4:1), reduced motion, reduced transparency, light theme. Screenshots in docs/redesign/screens/after/final/.
