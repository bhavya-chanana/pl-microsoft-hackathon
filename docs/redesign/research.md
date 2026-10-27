# MatchLens redesign: external research

Date: 2026-10-27. Stack assumed: Next 16 (app router), React 19, Tailwind v4, shadcn v4 CLI on `@base-ui/react`, `motion` 12, lucide, recharts, @xyflow/react, sonner, next-themes, zustand, TanStack Query.

All registry namespaces below were checked against `https://ui.shadcn.com/r/registries.json` (the built-in shadcn directory) and individual item URLs were probed (HTTP 200) on 2026-10-27.

---

## 0. TL;DR recommendation

**Direction: "Matchday Night", an editorial dark UI in the style of Apple Sports, FotMob and Linear, with glass used only on floating chrome.**

- **Content surfaces are solid.** Use opaque, slightly elevated dark cards with hairline borders. Charts, tables and pitch maps need a stable background to stay readable.
- **Chrome is glass.** The top bar, floating dock or tab bar, command palette, sheets, popovers, tooltips and toasts use frosted glass: `backdrop-filter: blur() saturate()` plus a specular inner highlight. Refraction (an SVG displacement filter) is an optional Chromium-only extra on one or two hero elements at most.
- **Use club colour as the accent.** Home and away team colours tint the match header with a gradient wash, the possession bar and chart series. The neutral UI stays grey.
- **Typography:** Geist Sans for UI, Geist Mono with tabular numerals for stats, and a condensed display face for scorelines (Barlow Condensed or Saira Condensed) to add a sport and broadcast accent.
- **Motion:** spring-based `layoutId` tab indicators, number tickers on stats, a `blur-fade` stagger on page enter, and a sheet for detail views. Respect `prefers-reduced-motion`.

Why not full Liquid Glass everywhere? Glass on top of glass, and glass behind dense data, hurts legibility. Apple's own guidance limits Liquid Glass to the navigation layer that floats above content. The real refraction effect only works in Chromium (details in section 1), so making it the core identity would degrade in Safari and Firefox.

---

## 1. Visual directions compared

| # | Direction | References | Feel | Fit for MatchLens | Key libraries |
|---|---|---|---|---|---|
| A | **Editorial dark + glass chrome** (recommended) | Apple Sports, FotMob, The Athletic, Linear | Premium, calm, content-first, team colour does the talking | Best. Dense data stays readable and still looks like an "app". | shadcn (base) + Animate UI (tabs, sheet, sliding number) + Magic UI (number-ticker, blur-fade, border-beam) + EvilCharts styling ideas |
| B | **Crisp minimal** | Vercel dashboard, Linear settings, Raycast | Engineered, monochrome, hairlines, very fast | Good, but safe. Risks still feeling like an admin dashboard unless typography and team colour are pushed. | shadcn + coss (Base UI) + Geist + motion-primitives |
| C | **Broadcast graphics** | Sky Sports / Monday Night Football touchscreen, Premier League TV graphics, Opta "The Analyst" | Bold condensed type, angled slabs, big numbers, saturated team colours, kinetic reveals | Great for the match hero and "moment" cards, and tiring for whole-app chrome. Use as an accent layer inside A. | React Bits (text animations), Aceternity (spotlight, glowing borders), condensed display fonts |
| D | **Bento overview** | Apple keynote slides, Vercel/Linear marketing, Arc | Mosaic of tiles of different sizes, each with one insight | Good for the match overview and landing ("what can I do here"), not for drill-down screens | Magic UI `bento-grid`, `magic-card`; Aceternity `card-spotlight` |
| E | **Full Liquid Glass** | iOS 26 / macOS Tahoe | Translucent everything, refraction, specular | Risky. Chromium-only refraction, poor contrast over charts, GPU cost. | liquefy-ui (WebGL), liquid-glass-react, glasscn, glass-ui |

**Suggested blend:** A as the system, D for the match overview page, and C for the scoreline hero and key moments (goals, xG swings).

---

## 2. Skills the implementer should load

| Skill | When |
|---|---|
| `frontend-design` | Before any visual work. Sets the aesthetic direction and avoids templated defaults. |
| `shadcn` | Adding registry components, components.json, namespace installs, theming tokens |
| `ui-creation-fundamentals` | Hierarchy, spacing, states, overlays, dark mode |
| `dataviz` | Before touching any recharts colours, KPI tiles or chart layout |
| `vercel-react-best-practices` | RSC/client boundaries, bundle size (motion and registry code can bloat it) |
| `vercel-composition-patterns` | App shell, compound components for cards and panels |
| `web-design-guidelines` | Final audit: a11y, focus, reduced motion, contrast |
| `webapp-testing` | Screenshot verification of scroll and sticky behaviour at mobile and desktop sizes |

---

## 3. Component registries (ranked for a sports analytics app)

Install syntax for all of them is `npx shadcn@latest add @<ns>/<item>`. Namespaces are built into the shadcn CLI directory, so no components.json edit is needed. For one-offs you can also pass the full URL.

| Rank | Registry | Namespace / URL template | Primitive base | Why | Use for |
|---|---|---|---|---|---|
| 1 | **Animate UI** | `@animate-ui` → `https://animate-ui.com/r/{name}.json` | **Has Base UI variants** (`components-base-*`) and Radix variants | Animated versions of exactly the shadcn primitives we already use, on our primitive library | Animated tabs (sliding indicator), sheet, popover, sliding number, highlight |
| 2 | **Magic UI** | `@magicui` → `https://magicui.design/r/{name}` | Primitive-agnostic (motion + Tailwind) | Largest, best maintained, tasteful | `number-ticker`, `dock`, `bento-grid`, `magic-card`, `blur-fade`, `border-beam`, `animated-list` |
| 3 | **motion-primitives** | `@motion-primitives` → `https://motion-primitives.com/c/{name}.json` | Agnostic | Small, high-quality motion building blocks. Repo activity has slowed, but the code is copy-paste so that is fine. | `animated-background` (hover pill behind nav items), `sliding-number`, `morphing-dialog`, `spotlight` |
| 4 | **coss** (Origin UI's successor, by Cal.com) | `@coss` → `https://coss.com/ui/r/{name}.json` | **Base UI** | Polished product-grade primitives on Base UI, which matches our stack | Better-looking inputs, selects, tabs, and table bits if our current ones are plain |
| 5 | **Aceternity UI** | `@aceternity` → `https://ui.aceternity.com/registry/{name}.json` | Agnostic | Flashy. Use sparingly. | `card-spotlight`, `glowing-effect`; maybe a background beam on the empty state |
| 6 | **Kokonut UI** | `@kokonutui` | Agnostic | Nice search and command bars | `action-search-bar` as a reference for the command palette |
| 7 | **React Bits** | `@react-bits` → `https://reactbits.dev/r/{Name}-TS-TW.json` | Agnostic | Text and background effects | `SpotlightCard-TS-TW`, `CountUp`, split-text reveals for scorelines (broadcast accent) |
| 8 | **Cult UI** | `@cult-ui` | Agnostic | A few unusual pieces (dynamic island, expandable cards) | Optional "live match" pill |
| - | **EvilCharts** | `@evilcharts` → `https://evilcharts.com/r/{name}.json` | Recharts | Pre-styled recharts wrappers (gradient areas, glowing lines, loading states) | `recharts-area-chart`, `recharts-bar-chart`, `recharts-radar-chart`, `recharts-tooltip`. Good template for chart theming. |
| - | **data-table-filters** (openstatus) / **tablecn** | `@data-table-filters`, `@tablecn` | TanStack Table (tablecn supports Base UI) | Readable, filterable data tables with a row-detail sheet | Player and event tables |
| - | **Glass registries** | `@liquefy-ui` (WebGL refraction on Base UI; items `glass-dock`, `liquid-tabs`, `liquid-dialog`, `liquid-surface`…), `@glasscn`, `@glass-ui`, `@einui` | Varies | Turnkey glass | Only if the designer picks direction E. liquefy-ui pins `@base-ui/react@1.8.0` and `@liquefy-ui/core@^1.1.0`, so check for version conflicts. |

### Exact install commands (direction A)

```bash
# animated primitives on Base UI
npx shadcn@latest add @animate-ui/components-base-tabs
npx shadcn@latest add @animate-ui/components-base-popover
npx shadcn@latest add @animate-ui/components-animate-tabs      # standalone animated tabs
npx shadcn@latest add @animate-ui/primitives-texts-sliding-number

# Magic UI
npx shadcn@latest add @magicui/number-ticker @magicui/blur-fade @magicui/bento-grid \
  @magicui/magic-card @magicui/border-beam @magicui/dock @magicui/animated-list

# motion-primitives
npx shadcn@latest add @motion-primitives/animated-background @motion-primitives/spotlight

# command palette (shadcn's Command is built on cmdk 1.1.x)
npx shadcn@latest add command dialog

# charts reference (installs into components/evilcharts/…)
npx shadcn@latest add @evilcharts/recharts-area-chart @evilcharts/recharts-tooltip

# fonts
npm i geist            # 1.7.x, full glyphs + font-feature-settings (better than Google Fonts' Geist)

# drawers (bottom sheet on mobile): shadcn `drawer` uses vaul 1.1.x
npx shadcn@latest add drawer sheet
```

Use `npx shadcn@latest view @ns/item` to preview before adding, and `--dry-run`/`--diff` where available.

**Registry pitfalls:**
- Two registries that ship the same file (for example `button.tsx`) overwrite each other in `components/ui`. Review the diff on every add.
- Third-party items may hardcode `@/components/ui/...` imports or Radix imports. Check that they match our aliases and Base UI.
- Many items import `framer-motion`. Rewrite those to `motion/react` so we don't install both.
- Community registries are third-party code. Read each file before committing.

---

## 4. The glass system (copy-paste)

### 4.1 How it works
1. **Frost:** `backdrop-filter: blur(16–24px) saturate(160–180%)`. Saturate is what makes it look like Apple glass rather than grey mush.
2. **Tint:** a low-alpha fill (`rgb(255 255 255 / .06)` on dark, `rgb(255 255 255 / .55)` on light).
3. **Edge:** a 1px hairline border at higher alpha on top than on bottom, done with a gradient border via `mask` or just `border` plus an inset shadow.
4. **Specular highlight:** `inset 0 1px 0 0 rgb(255 255 255 / .12)` plus an optional radial gradient at the top-left.
5. **Depth:** a soft, large, low-alpha drop shadow. On dark, shadows barely show, so lean on the lighter fill and the top highlight.
6. **Noise (optional):** a tiny SVG noise texture at 3–4% opacity kills banding in blurred gradients.
7. **Refraction (optional, Chromium only):** `backdrop-filter: url(#lg-filter)` with an `feDisplacementMap`. Not supported in Safari or Firefox: SVG filters in `backdrop-filter` aren't in the CSS spec, and WebKit bug 313914 is still open. The displacement map must match element size, so a resize means a rebuild. Treat it as progressive enhancement only.

**Support:** unprefixed `backdrop-filter` is in Chrome 76+, Firefox 103+ and Safari 18+. Keep `-webkit-backdrop-filter` for Safari 17 and below. Add a fallback for `prefers-reduced-transparency` and for no support.

### 4.2 Tokens + utilities (Tailwind v4, paste into `globals.css`)

```css
@import "tailwindcss";

/* ---------- design tokens ---------- */
:root {
  /* light */
  --bg: oklch(0.985 0.002 260);
  --bg-elev: oklch(1 0 0);
  --fg: oklch(0.21 0.01 265);
  --fg-muted: oklch(0.50 0.01 265);
  --hairline: oklch(0.21 0.01 265 / 0.08);

  --glass-fill: oklch(1 0 0 / 0.60);
  --glass-fill-strong: oklch(1 0 0 / 0.78);
  --glass-border: oklch(1 0 0 / 0.70);
  --glass-border-bottom: oklch(0.21 0.01 265 / 0.06);
  --glass-highlight: oklch(1 0 0 / 0.90);
  --glass-shadow: 0 1px 1px oklch(0 0 0 / 0.04), 0 8px 24px -6px oklch(0.2 0.02 265 / 0.12),
                  0 24px 48px -12px oklch(0.2 0.02 265 / 0.10);
  --glass-blur: 20px;
  --glass-sat: 180%;

  /* team accents, set per match on a wrapper: style={{'--home': ..., '--away': ...}} */
  --home: oklch(0.62 0.20 25);
  --away: oklch(0.62 0.15 250);
  --accent: oklch(0.72 0.17 155);   /* neutral brand: pitch green */

  --radius-glass: 20px;
  --ease-spring: cubic-bezier(0.32, 0.72, 0, 1);   /* iOS sheet curve */
}

.dark {
  --bg: oklch(0.145 0.006 265);          /* near-black, cool, not pure #000 */
  --bg-elev: oklch(0.19 0.008 265);      /* solid content cards */
  --bg-elev-2: oklch(0.23 0.008 265);
  --fg: oklch(0.97 0.003 265);
  --fg-muted: oklch(0.70 0.01 265);
  --hairline: oklch(1 0 0 / 0.08);

  --glass-fill: oklch(0.22 0.01 265 / 0.55);
  --glass-fill-strong: oklch(0.20 0.01 265 / 0.75);
  --glass-border: oklch(1 0 0 / 0.14);
  --glass-border-bottom: oklch(1 0 0 / 0.04);
  --glass-highlight: oklch(1 0 0 / 0.14);
  --glass-shadow: 0 1px 0 oklch(0 0 0 / 0.4), 0 12px 32px -8px oklch(0 0 0 / 0.55),
                  0 32px 64px -16px oklch(0 0 0 / 0.45);
  --glass-blur: 24px;
  --glass-sat: 160%;
}

@theme inline {
  --color-bg: var(--bg);
  --color-elev: var(--bg-elev);
  --color-fg: var(--fg);
  --color-muted-fg: var(--fg-muted);
  --color-hairline: var(--hairline);
  --color-home: var(--home);
  --color-away: var(--away);
  --color-accent-brand: var(--accent);
  --radius-glass: var(--radius-glass);
  --ease-spring: var(--ease-spring);
  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);
  --font-display: var(--font-display);
}

/* ---------- glass utilities ---------- */
@utility glass {
  background-color: var(--glass-fill);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
  backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
  border: 1px solid var(--glass-border);
  border-bottom-color: var(--glass-border-bottom);
  box-shadow: inset 0 1px 0 0 var(--glass-highlight), var(--glass-shadow);
  border-radius: var(--radius-glass);
  isolation: isolate;
}

/* stronger, for menus/popovers/command palette that sit over busy content */
@utility glass-strong {
  background-color: var(--glass-fill-strong);
  -webkit-backdrop-filter: blur(calc(var(--glass-blur) * 1.5)) saturate(var(--glass-sat));
  backdrop-filter: blur(calc(var(--glass-blur) * 1.5)) saturate(var(--glass-sat));
  border: 1px solid var(--glass-border);
  box-shadow: inset 0 1px 0 0 var(--glass-highlight), var(--glass-shadow);
}

/* specular sheen: add to .glass elements; uses ::before so content is unaffected */
@utility glass-sheen {
  position: relative;
  &::before {
    content: "";
    position: absolute; inset: 0;
    border-radius: inherit;
    pointer-events: none;
    background:
      radial-gradient(120% 60% at 0% 0%, oklch(1 0 0 / 0.18), transparent 50%),
      linear-gradient(180deg, oklch(1 0 0 / 0.06), transparent 40%);
    mix-blend-mode: plus-lighter;
  }
}

/* cursor-follow highlight: set --mx/--my from onPointerMove */
@utility glass-hover {
  transition: background-color .2s var(--ease-spring), box-shadow .2s var(--ease-spring), transform .2s var(--ease-spring);
  &:hover {
    background-image: radial-gradient(240px circle at var(--mx, 50%) var(--my, 0%), oklch(1 0 0 / 0.08), transparent 60%);
  }
}

/* solid content card (NOT glass) */
@utility surface {
  background: var(--bg-elev);
  border: 1px solid var(--hairline);
  border-radius: 16px;
  box-shadow: inset 0 1px 0 0 oklch(1 0 0 / 0.04);
}

/* fallbacks */
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .glass, .glass-strong { background-color: var(--glass-fill-strong); }
}
@media (prefers-reduced-transparency: reduce) {
  .glass, .glass-strong {
    background-color: var(--bg-elev);
    backdrop-filter: none; -webkit-backdrop-filter: none;
  }
}
@media (prefers-contrast: more) {
  .glass, .glass-strong { border-color: var(--fg-muted); }
}

/* tabular numbers everywhere stats appear */
@utility tnum { font-variant-numeric: tabular-nums slashed-zero; font-feature-settings: "tnum", "zero"; }
```

**Ambient background:** glass needs something behind it to look like glass. Give `body` a fixed, very subtle gradient wash using the team colours on match pages:

```css
.match-ambient {
  background:
    radial-gradient(60% 40% at 15% 0%, color-mix(in oklch, var(--home) 22%, transparent), transparent 70%),
    radial-gradient(60% 40% at 85% 0%, color-mix(in oklch, var(--away) 22%, transparent), transparent 70%),
    var(--bg);
  background-attachment: fixed; /* or put it on a position:fixed inset-0 -z-10 div. Safer on iOS. */
}
```

### 4.3 Optional refraction (Chromium-only enhancement)

```html
<svg width="0" height="0" style="position:absolute" aria-hidden>
  <filter id="lg-distort" x="0" y="0" width="100%" height="100%">
    <feTurbulence type="fractalNoise" baseFrequency="0.008 0.008" numOctaves="2" seed="7" result="n"/>
    <feGaussianBlur in="n" stdDeviation="2" result="nb"/>
    <feDisplacementMap in="SourceGraphic" in2="nb" scale="40" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
</svg>
```
```css
@supports (backdrop-filter: url(#x)) { /* note: Safari reports true but doesn't render; gate by UA or skip */ }
.lg-refract { backdrop-filter: url(#lg-distort) blur(4px) saturate(170%); }
```
Gate it in JS (`/Chrome\//.test(navigator.userAgent) && !/Edg|OPR/.test(...)` isn't needed, since all Chromium builds work) and never put it on anything that resizes often. The physically correct version (Snell's-law displacement map by kube.io) produces much better edges but needs a pre-baked map per size. Turnkey options: `liquid-glass-react` 1.1.1 (MIT, about 4k stars; last publish June 2025, so lightly maintained; Safari and Firefox show no displacement) or `@liquefy-ui` (WebGL, works cross-browser, heavier).

---

## 5. App UX patterns

### 5.1 App shell
- **Desktop:** a slim left rail (icons + labels on hover or expand, about 64→240px) **or** a floating glass top bar with a segmented nav. For an analysis tool with a handful of sections (Matches, Match → Overview / Timeline / Players / Tactics / Vision), a **floating glass top bar plus in-page animated tabs** feels most "app" and least "Streamlit". Linear and Vercel both use a left sidebar because they have many sections. We don't.
- **Mobile:** a bottom floating glass tab bar or dock (Apple Sports and FotMob pattern), with the safe area `padding-bottom: env(safe-area-inset-bottom)`.
- **Match header:** a sticky compact scoreline that collapses on scroll (large hero → 56px glass bar). Use `motion`'s `useScroll` + `useTransform`, or CSS `animation-timeline: scroll()` with a fallback.
- **Command palette (⌘K):** shadcn `command` in a `glass-strong` dialog. It should jump to match, player, team or section, and run actions such as "Compare players…", "Show xG timeline" or "Toggle theme". Show a ⌘K hint chip in the top bar. This is the single best fix for "hard to understand what you can do", because it lists capabilities.

### 5.2 Discoverability and onboarding
- **Empty state = capability menu.** The home or no-match screen gets a bento grid of 4–6 "things you can do" tiles, each with a mini preview (sparkline, pitch thumbnail) and a verb ("Explore a match", "Compare two players", "Watch tactical shape"). Show recent matches above the grid.
- **Progressive disclosure:** show summary KPIs first (score, xG, possession, shots), then expandable sections, and open drill-down in a **right sheet** on desktop and a **bottom drawer** on mobile. This keeps context without leaving the page.
- **Inline hints:** small `?` popovers or hover cards on metric labels (what xG means), not a tour.
- **Use modals only for blocking or confirm actions.** Detail views go in sheets. Use toasts (sonner) for async completions.
- **Skeletons** shaped like the final content (not spinners), and keep the layout fixed to avoid CLS.

### 5.3 Scroll bugs: causes and fixes
| Bug | Cause | Fix |
|---|---|---|
| Double scrollbars, wheel "stuck" | Nested `overflow:auto` with `height:100vh` on both shell and page | **One scroll container.** Either let the document scroll (preferred, which keeps sticky, scroll restoration and mobile URL-bar collapse working), or make the shell `h-dvh grid grid-rows-[auto_1fr]` with `overflow:hidden` and exactly one `main` with `overflow-y:auto min-h-0`. |
| Flex child won't scroll | Flex items default to `min-height:auto` | Add `min-h-0` (and `min-w-0` for horizontal) to the flex/grid child that scrolls |
| Mobile content hidden under URL bar | `100vh` = largest viewport | Use `h-dvh` / `min-h-dvh` (Tailwind v4 has `h-dvh`, `h-svh`) |
| `position: sticky` doesn't stick | An ancestor has `overflow:hidden/auto`, or the sticky element is the only child of a short parent | Remove overflow from ancestors, or use `overflow: clip` (doesn't create a scroll container). Sticky only works relative to the nearest scrolling ancestor. |
| Scroll chaining (sheet scroll moves page) | Default overscroll | `overscroll-behavior: contain` on sheets, drawers, palette lists and chart panes |
| Page jumps when a scrollbar appears | Scrollbar takes width | `scrollbar-gutter: stable` on the scroll container |
| Route change keeps old scroll position / jumps | Custom scroll container defeats Next scroll restoration | Prefer document scroll. Otherwise reset `main.scrollTop=0` on pathname change. |
| Anchor or tab jump hidden under sticky header | Header overlaps target | `scroll-margin-top: var(--header-h)` on sections, `scroll-padding-top` on the container |
| Jank with many blurred elements while scrolling | backdrop-filter repaints every frame | Limit glass to fixed chrome (2–4 elements on screen), no glass on list rows. Avoid `background-attachment: fixed` on iOS (use a fixed div instead). |
| Locked body after modal | Scroll-lock not released, or a nested lock | Use Base UI / vaul built-in scroll locking. Don't hand-roll `overflow:hidden` on body. |
| `scroll-behavior: smooth` fights programmatic scroll | Global smooth scroll | Apply it only under `@media (prefers-reduced-motion: no-preference)` and not on `html` if you use the router's scroll |
| Horizontal overflow on mobile | Wide tables or charts | Wrap tables in an `overflow-x-auto` container with a sticky first column, and give ResponsiveContainer a `min-w-0` parent |

### 5.4 Data tables
- Use 13–14px body with tabular numerals, right-aligned numbers, and left-aligned names with a crest or avatar.
- Row height is 40px (compact) or 48px. Use zebra-free hairline separators and a hover row highlight.
- Make the header and first column sticky. Sort affordance on header hover.
- Show inline micro-bars or heat cells for per-90 stats (FotMob and StatsBomb style) rather than raw number walls.
- Use `@tablecn` or `@data-table-filters` for faceted filters and a row-detail sheet.

### 5.5 Reference products: what to steal
- **Apple Sports:** huge scoreline, team-colour gradients, very little chrome, glass tab bar.
- **FotMob:** match header with momentum graph, a tab strip (Facts / Lineup / Stats / Table), and per-stat paired bars with the team colour on each side.
- **The Athletic:** editorial typography, generous whitespace, serif accents for narrative ("Why it matters").
- **Opta Analyst / StatsBomb:** pitch plots with a muted pitch, highlighted events, legends inside the chart, and dark backgrounds.
- **Linear:** ⌘K everywhere, crisp hairlines, subtle gradients, fast motion (150–250ms).
- **Vercel dashboard:** Geist, monochrome plus a single accent, clean empty states.
- **Arc:** playful colour theming, sidebar spaces, delightful micro-motion.

---

## 6. Typography and colour

### Fonts (all available via `next/font` or the `geist` package)
| Role | Pick | Alt |
|---|---|---|
| UI sans | **Geist Sans** (`geist/font/sans`) | Inter (closest to SF Pro on the web) |
| Numbers/mono | **Geist Mono** (`geist/font/mono`) + `tabular-nums` | JetBrains Mono |
| Display (scorelines, big KPIs, section titles) | **Barlow Condensed** 600–700 (`next/font/google`) for the broadcast feel | Saira Condensed, Bebas Neue (caps only), Inter Tight 700 |
| Editorial accent (optional, sparingly) | **Instrument Serif** italic for narrative headings | Newsreader |

```ts
// app/layout.tsx
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Barlow_Condensed } from "next/font/google";
const display = Barlow_Condensed({ subsets: ["latin"], weight: ["600","700"], variable: "--font-display" });
// <html className={`${GeistSans.variable} ${GeistMono.variable} ${display.variable}`}>
```

**Type scale (dark UI, app density):** 12 (labels, caps with +0.04em tracking) · 13 (table) · 14 (body) · 16 (emphasis) · 20 (section) · 28 (page title) · 48–72 (scoreline, display font, `leading-none`, `tracking-tight`). Use weights 400/500/600 only. Avoid 700 in Geist UI text.

### Palette (dark-first)
- Background `oklch(0.145 0.006 265)`, elevated surfaces at 0.19 and 0.23. Never pure black, because glass needs some luminance to show.
- Text 0.97 / muted 0.70 / subtle 0.55.
- One brand accent (pitch green `oklch(0.72 0.17 155)`) for focus rings and primary actions. **Team colours carry the match data.**
- Team colours from the data often clash (red vs red). Pick the away kit colour when ΔE is too small, and normalise lightness to around L 0.62–0.70 in dark mode so both read on the background.
- Semantic: positive `oklch(0.75 0.16 150)`, negative `oklch(0.68 0.19 25)`, warning `oklch(0.80 0.15 85)`.

### Recharts theming
- Map shadcn chart vars: `--chart-1: var(--home)`, `--chart-2: var(--away)`, then `--chart-3..5` as neutral greys or accent for "league average" and so on. Use shadcn's `ChartContainer` (already on recharts) so colours come from CSS vars.
- Axes: no axis lines, `tick={{ fill: 'var(--fg-muted)', fontSize: 11 }}`, `tickLine={false}`, grid `stroke="var(--hairline)" strokeDasharray="0"` with horizontal lines only.
- Areas: a `<linearGradient>` from the series colour at 0.35 to 0. Lines `strokeWidth={2}`, `dot={false}`, `activeDot={{ r: 4, strokeWidth: 0 }}`. EvilCharts is a good reference for glowing and gradient variants.
- Tooltip: a custom `content` rendered with the `glass-strong` class and `tnum` numbers, and `cursor={{ stroke: 'var(--hairline)' }}`.
- Animation: `isAnimationActive` only on first mount (Recharts re-animates on every data change, which looks janky with live updates). Respect reduced motion.
- Set the pitch background to `oklch(0.22 0.03 155)` dark, with line markings at `oklch(1 0 0 / .12)`.

---

## 7. Motion

### Presets (motion/react)
```ts
export const spring = {
  snappy: { type: "spring", stiffness: 500, damping: 40, mass: 0.8 },   // tabs, toggles, pills
  smooth: { type: "spring", stiffness: 260, damping: 30 },               // cards, sheets
  gentle: { type: "spring", bounce: 0.15, visualDuration: 0.45 },        // page/hero
} as const;
export const fadeUp = { initial: { opacity: 0, y: 8, filter: "blur(4px)" },
  animate: { opacity: 1, y: 0, filter: "blur(0px)" }, transition: { duration: 0.35, ease: [0.32,0.72,0,1] } };
```

- **Shared-element tabs:** `<motion.span layoutId="tab-pill" transition={spring.snappy} />` rendered inside the active tab. Wrap each tab group in `<LayoutGroup id="…">` so ids don't collide. Animate UI's tabs and motion-primitives' `animated-background` already do this.
- **Card → detail:** `layoutId={\`match-${id}\`}` on the card and the sheet header, or use motion-primitives `morphing-dialog`.
- **Lists:** stagger with `variants` + `staggerChildren: 0.03`, capped at about 12 items. Never stagger 100 table rows.
- **Numbers:** Magic UI `number-ticker` or Animate UI `sliding-number` for KPIs, triggered `whileInView` once.
- **Hover depth:** `whileHover={{ y: -2 }}` plus a brighter border and a cursor-follow radial highlight (`glass-hover`). Avoid scale > 1.02 on data cards.
- **Reduced motion:** wrap the app in `<MotionConfig reducedMotion="user">`, which turns transform and layout animations into instant changes while keeping opacity. Gate CSS keyframes with `@media (prefers-reduced-motion: no-preference)`.
- **Perf:** animate only `transform`, `opacity` and `filter` (filter blur sparingly). Use `LazyMotion` + `m` components with `domAnimation` to cut bundle size. Don't animate `backdrop-filter` values.

### View Transitions in Next 16
- Still **experimental**: `experimental: { viewTransition: true }` in `next.config.ts`, then `import { ViewTransition } from "react"`. Older examples use `unstable_ViewTransition`.
- It triggers on transitions, `<Suspense>` reveals, `useDeferredValue` and route navigations. It does **not** trigger on plain `setState`. Browsers without support just navigate normally.
- **Recommendation:** use it for route-level cross-fades and for a shared `name` on the match crest and scoreline between the matches list and match page. Keep in-page animation in motion, and don't mix both on the same element. Since it's experimental, keep it behind the flag so it can be switched off.

---

## 8. Pitfalls checklist
1. Glass over charts or tables reduces contrast. Keep content on solid `surface`, and glass only for floating chrome. Check text contrast at 4.5:1 on top of the **worst-case** background behind the glass.
2. Too many `backdrop-filter` layers cost GPU, especially on scroll and on low-end Android. Keep it to about 4 on screen, with no glass on list rows or repeated cards.
3. Glass inside glass: nested backdrop-filters don't sample the parent's blur in Chromium (the inner layer sees an unblurred backdrop). Use `glass-strong` without blur for nested popovers, or a solid fill.
4. `backdrop-filter` on an element creates a containing block for `position: fixed` children. Don't nest fixed overlays inside glass elements. Portal them out (Base UI portals do).
5. Safari: keep the `-webkit-` prefix. SVG refraction doesn't render there.
6. Registry code imports `framer-motion` or Radix: rewrite to `motion/react` and Base UI.
7. Avoid `h-screen` (100vh) on mobile; use `dvh`. Use one scroll container.
8. Don't mix `next-themes` `class` strategy with `prefers-color-scheme`-only tokens. Tokens above key off `.dark`.
9. Fonts: load with `next/font` (no CLS) and set `font-variant-numeric: tabular-nums` on all stat numbers to stop jitter during tickers.
10. Animations on every re-render (Recharts, number tickers re-firing on refetch) make the app feel cheap. Animate on mount or first view only.
11. Motion polish can't fix unclear IA. Fix discoverability with the ⌘K palette, capability bento, clear section tabs and labelled icons first.

---

## 9. Links
- Liquid glass, physics-based CSS/SVG technique: https://kube.io/blog/liquid-glass-css-svg/
- Six ways to build liquid glass: https://dev.to/devyatov/liquid-glass-on-the-web-6-ways-to-build-it-with-css-and-svg-3m07
- WebKit bug, SVG backdrop refraction in Safari: https://bugs.webkit.org/show_bug.cgi?id=313914
- liquid-glass-react: https://github.com/rdev/liquid-glass-react
- liquefy-ui (WebGL glass on Base UI): https://liquefy-ui.com
- shadcn registry directory: https://ui.shadcn.com/docs/directory (JSON: https://ui.shadcn.com/r/registries.json)
- Animate UI: https://animate-ui.com · Magic UI: https://magicui.design · motion-primitives: https://motion-primitives.com · coss: https://coss.com/ui · Aceternity: https://ui.aceternity.com · React Bits: https://reactbits.dev · EvilCharts: https://evilcharts.com · tablecn: https://table-cn.vercel.app
- cmdk: https://github.com/dip/cmdk
- Geist font: https://vercel.com/font · npm `geist`
- Next.js View Transitions guide: https://nextjs.org/docs/app/guides/view-transitions · config: https://nextjs.org/docs/app/api-reference/config/next-config-js/viewTransition
- Motion docs (layout animations, MotionConfig, LazyMotion): https://motion.dev/docs/react
