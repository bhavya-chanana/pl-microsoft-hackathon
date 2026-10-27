# FotMob teardown (for MatchLens Coach page)

Captured on 2026-10-27 from fotmob.com at 1440×900 desktop and 390×844 mobile, in dark and light colour schemes.

**Pages captured**
- Match: Liverpool 0–3 Manchester City, PL Round 6, 11 Oct (`/matches/manchester-city-vs-liverpool/2f48yd#5795472`).
- Team: Liverpool overview.
- Player: Erling Haaland.

**Method**
- Playwright, using the project's own `node_modules/playwright` and system Chrome. It extracted computed styles, CSS custom properties and SVG attributes from the live DOM.
- The production JS chunks were grepped to recover the logic behind rating badges and the theme tokens.
- The rea MCP tools (`capture_browser_scenario`, `analyze_web_bundle`) confirmed the stack fingerprint.

Screenshots are in `docs/redesign/fotmob/`. Treat them as a private reference. **Don't ship FotMob's brand, fonts (GT Walsheim), assets or exact palette.** Adapt the patterns.

---

## 0. TL;DR: why FotMob feels premium and readable

1. **Near-monochrome chrome; colour is reserved for data.** The page is `#000` with `#1d1d1d` cards and white or `#9f9f9f` text. The only saturated colours are the two team colours, the rating badges (orange, green, blue), the cards and goal icons, and one brand-green accent (the tab underline). Every colour carries meaning.
2. **One card style, used everywhere.** Each card is `#1d1d1d` with a 16px radius, no shadow and no visible border (a 1px transparent border). Cards sit on pure black. Inside a card, sections are split by 1px `#333` hairlines, not nested cards.
3. **Tiny type scale, one font, three weights.** Sizes are 12, 14, 16, 24 and 30px only, in weights 400, 500 and 700. Section titles are just 16px/500, centred. Hierarchy comes from layout and colour (white vs grey), not from big headings.
4. **The "winner gets the pill" stat row.** In a stat comparison row (`home value | label | away value`), the *higher* value gets a pill filled with that team's colour. The other value stays plain text. You can read who won each stat at a glance, with no bars. **Possession is the only stat drawn as a bar.**
5. **Rating badges everywhere.** A small rounded pill shows the 0–10 rating on lineups, substitutes, player tables, team form and season stats. The colour thresholds are consistent across the whole product (section 3).
6. **Symmetry.** Home is on the left and away on the right, everywhere: header, events timeline, lineups, stat rows, momentum (home above the axis, away below). Once you learn it, every module reads instantly.
7. **Few, fast motions.** Tab indicator `left/width 0.2s ease-out`, colour `0.2s`, possession segment `width 0.5s`, stat pill `background-color 0.5s`. Nothing bounces.
8. **Dense, but with generous row height.** Stat rows are 48px tall with 14px text. Lineup player nodes are 40px avatars. Substitute rows are about 54px. It's compact without being cramped.

---

## 1. Stack fingerprint

| Finding | Evidence |
|---|---|
| Next.js (pages router, `_app` chunk) + React | rea `analyze_web_bundle` vendor fingerprints; `/_next/static/chunks/pages/_app-*.js` |
| **Tailwind v4** utilities + **shadcn**-style tokens (`--color-primary`, `--color-muted-foreground`, `--color-ring`, `data-[state=open]:zoom-in-95`, `cmdk`) | `:root` custom properties, utility class names in the stylesheet |
| Emotion CSS-in-JS for legacy components (`css-xxxx-PlayerRatingCSS`, `TabIndicator`, `StatValue`) with a theme object exposed as `--GlobalColorScheme-*` vars | class labels, inline `<style>` vars |
| **Recharts** for the momentum chart (`recharts-area-area`, `recharts-reference-line-line`) | SVG class names |
| Hand-rolled SVG for the shot map (`viewBox="0 0 105 68"`, which is pitch metres) and attacking zones | SVG attrs |
| Lineup pitch is **HTML/CSS absolutely-positioned nodes** on a flat `#2c2c2c` field with CSS-drawn lines, not SVG | DOM probe: no large SVG in the lineup |
| Theme switch: `<html data-color-scheme="dark">` + a `[data-color-scheme="dark"]` token override block | html attrs |
| Fonts: `walsheim` (GT Walsheim, licensed) for 400, 500 and 700; Inter declared as default but unused | `document.fonts` |

This is almost exactly our stack (Next, React, Tailwind v4, shadcn tokens, Recharts), so every pattern below is directly portable.

---

## 2. Design tokens (measured)

### 2.1 Colour: dark (primary)
| Role | FotMob value | Notes / our adaptation |
|---|---|---|
| Page background | `#000000` | Pure black. Cards float on it. |
| Card | `#1d1d1d` | `--color-card` (neroBlack) |
| Card secondary / chip / pitch | `#2b2b2b` / `#2c2c2c` | Lineup field, filter chips, shot-map pitch `#2b2b2b` |
| Card hover, divider, muted | `#333333` | `--color-border`, hover background |
| Lineup team header strip | `#343434` | |
| Strong border / tooltip | `#464646` | |
| Text primary | `#ffffff` | |
| Text secondary | `#9f9f9f` | Minutes, positions, inactive tabs |
| Text tertiary | `#717171` | |
| Brand accent | `#61df6e` (dark), `#00985f` (light) | Tab underline, active dots. **We use our own brand accent here.** |
| Win / draw / lose | `#33c771` / `#8d9499` / `#ff3939` (dark); backgrounds at 15% alpha (`…26`) | Form chips: W/D/L |
| Yellow card | `#ffce2c`-ish fill; chip background `#ffce2c26` | |
| Red | `#dd3636` (light) / `#ff3030` (dark) | |
| Skeleton | base `#1d1d1d`, highlight `#2b2b2b` | |
| Global card shadow token | `0 10px 40px 0 rgba(0,0,0,.2)` | Only on popovers. Cards have none. |

### 2.2 Colour: light
Page `#fafafa` (lotion), card `#ffffff`, border and muted `#f5f5f5`, text `#222`, secondary `#9f9f9f`/`#717171`, hairline `#f0f0f0`. Light mode uses the same layout and only swaps tokens. See `match-facts-viewport-light.png`.

### 2.3 Team colours
- They come per match from the data. This match used Liverpool `#911712` (a darkened red) and City `#76b4e5`.
- FotMob picks a dark-mode-safe variant of each club colour.
- **Text on a team pill is contrast-aware:** `#1d1d1d` on light colours (City sky blue), white on dark (Liverpool red).
- The shot map uses a brighter variant for strokes (`#d9231b`) than for fills or bars (`#911712`).

### 2.4 Typography (measured)
| Use | Size / line-height / weight |
|---|---|
| Score | 30/30, 500 |
| Team names in header | 24/24, 400 |
| Tabs | 16/16, 500; active white, inactive `#9f9f9f` |
| Card titles ("Momentum", "Top stats", "Events") | 16/16, 500, centred |
| Body / stat label / stat value | 14/14, 400 (value in pill 500) |
| Event minute | 14/17.5, 500, `#9f9f9f` |
| Rating badge | 12–15px, 500, letter-spacing 0 |
| Chips / segmented control | 14px, 500 |
| Root | 14px |

Only those sizes are used. The tokens are Tailwind's `text-xs…text-5xl`, but in practice 12, 14, 16, 24 and 30.

### 2.5 Shape, spacing, layout
- **Radius:** cards 16px; chips 16px (32px tall); segmented pills 24px (34px tall, padding 8×20); stat pill 17px (padding 4×8, 24px tall); rating badge 12px (min 28×18, padding 2–4px × 5–6px); possession bar 18px (36px tall); event-minute bubble is a full circle (about 40px).
- **Desktop grid:** a 1280px container holding the main column (960px) and right rail (300px) with a 20px gap. The rail is a stack of small cards (venue, referee, mini-table, insights, "who will win?"). Cards stack with about 16px vertical gap.
- **Header:** fixed top nav, 80px tall, `#1d1d1d` (opaque, *no blur*), search pill with ⌘K hint.
- **Match header card:** back pill + competition + Follow button; a hairline; date and venue meta; a hairline; then a 3-column block (team | score + status | team) with the scorers list under each side and the card icon in the centre column; then the tab row.
- **Tabs:** text buttons, 64px tall, no backgrounds. Indicator: 3px tall bar, radius `3px 3px 0 0`, brand green, sits flush on the card bottom, animated with `left .2s ease-out, width .2s ease-out`. The URL updates with the tab (`#…:tab=lineup`), so tabs are deep-linkable.

### 2.6 Motion (measured)
| Element | Transition |
|---|---|
| Tab indicator | `left 0.2s ease-out, width 0.2s ease-out` |
| Tab text colour | `color 0.2s` |
| Possession segment | `width 0.5s` (plus `gap 0.5s` on the container) |
| Stat value pill | `background-color 0.5s` (animates when switching All, 1st or 2nd half) |
| Popovers | shadcn `data-[state=open]:fade-in-0 zoom-in-95`, 200ms open / 0–300ms close |
| Durations available | 75, 100, 120, 200, 300, 500, 1000ms; default `150ms cubic-bezier(.4,0,.2,1)` |
| Skeletons | `react-loading-skeleton`, which respects `prefers-reduced-motion` |

---

## 3. Rating badge (exact logic from the bundle)

```js
// FotMob PlayerRating colour selection (de-minified)
const r = round(rating, 1);
color = (isPlayerOfTheMatch || r >= 10) ? blue
      : r >= 7 ? green
      : r >= 5 ? orange
      : red;
// star icon appended when isPlayerOfTheMatch
```

| Band | Dark theme | Light theme | Text |
|---|---|---|---|
| Player of the match (any value) or 10 | `#14a0ff` + ★ | `#14a0ff` + ★ | dark: black; light: white |
| ≥ 7.0 | `#33c771` | `#33c771` | |
| 5.0 – 6.9 | `#ff963f` | `#f08022` | |
| < 5.0 | `#ff3939` | `#dd3636` | |

- **Shape:** `min-width 28px; min-height 18px; border-radius 12px; padding 4px 6px` (2px 5px on the pitch); font 12px/14px, weight 500, letter-spacing 0. In tables the radius is 6px and the height 22px.
- **Placement on the pitch:** absolutely positioned at the avatar's top-right, overlapping it by about 30%.
- **Display:** one decimal (7.4). Season averages use two decimals (8.01, 7.42).
- **Team average rating:** the same badge sits in the lineup team-header strip (Liverpool 6.2, City 7.4).
- Note that 8.6 POTM was blue even though 8.5 was green. The blue means "best player of the match", not a score band.

Screenshots: `lineup-pitch-dark.png`, `match-lineup-mobile.png`, `player-overview-1.png` (table use), `team-overview.png` (season XI).

---

## 4. Module teardowns

### 4.1 Momentum (`facts-momentum-topstats-dark.png`)
- It is a Recharts `AreaChart` with `type="basis"`/`monotone`-like smoothing, viewBox 463×248, one series centred on 0. Positive values mean home pressure and negative values mean away pressure.
- **Two-colour fill with one gradient trick:** a `<linearGradient id="splitColor">` with two stops at the *same* offset (`offset = max / (max - min)`). The top half is `home` and the bottom half is `away`, with a hard edge at the zero line. Fill opacity is 0.9, and a stroke uses the same gradient.
- Axis: a dotted baseline (`stroke-width 3; dasharray 0.0001 9.6`, round caps, so the dots are made from dashes) in `#282828`. A vertical dotted reference line marks HT.
- X labels are only `0'`, `HT` and `FT` (bold, 14px). There is no y-axis and no gridlines.
- Event markers are small SVG icons: a goal ball (white with a dark outline) and a red card (red rect), placed above or below the chart at their minute, on the side of the team that produced them.
- It shares the card with Top stats in two columns, with a 1px vertical divider. "All stats →" link in the footer row.

**Recharts recipe (adapt):**
```tsx
const off = max / (max - min);
<defs><linearGradient id="split" x1="0" y1="0" x2="0" y2="1">
  <stop offset={off} stopColor="var(--home)" /><stop offset={off} stopColor="var(--away)" />
</linearGradient></defs>
<Area type="basis" dataKey="v" fill="url(#split)" stroke="url(#split)" fillOpacity={0.9} isAnimationActive={false} />
<ReferenceLine x={45} stroke="var(--hairline)" strokeWidth={3} strokeDasharray="0.0001 9.6" strokeLinecap="round" />
```

### 4.2 Top stats / stat comparison rows (`facts-momentum-topstats-dark.png`, `stats-shotmap-dark.png`)
- **Row:** a 48px `<li>` with a 3-column grid: `value | label | value`. Values are at the edges and the label is centred in white, all 14px.
- **Winner pill:** the larger value gets `background: teamColour; color: contrast; border-radius 17px; padding 4px 8px`. The losing value is plain. Ties get no pill on either side.
- **Possession only:** a full-width split bar, 36px tall with a 18px radius. Two segments carry the team colours, separated by a gap of about 8px, with the % label inside each segment (16px/500).
- **Stats tab groups:** Top stats, Physical performance, Expected goals (xG), Shots, Passes, Duels, Defense, Discipline, Attacking zones. They sit in two columns on desktop, each group with a centred 16px title and a hairline between groups.
- **Period filter:** a segmented pill row (`All | 1st | 2nd`), active white with black text, inactive `#333`. The pills animate their background colour on change.
- The xG group has a "What does Expected Goals (xG) mean? (?)" help link: in-context education, not a tour.

> **This contradicts a "split stat bars" spec.** FotMob does *not* draw a bar per stat. It highlights the winner. Bars appear only for possession, and on the attacking-zones pitch as arrow-shaped gradient bars (36% / 46% etc.).

### 4.3 Shot map (`stats-shotmap-dark.png`)
- One SVG with `viewBox="0 0 105 68"` (real pitch metres). The pitch is a `#2b2b2b` fill with `#454545` line paths, drawn horizontally. The home team shoots left and the away team shoots right, so both teams' shots sit on one pitch.
- Shots are circles with `r≈1.6` and stroke 0.4 in the team colour.
  - **Goals:** filled at 50% opacity, with a ball icon on top.
  - **Non-goals:** outline only.
  - The selected shot gets a white ring.
- **Detail panel above the pitch:** player avatar + name, prev/next arrows with the minute, and a key/value list (Shot type, Situation, Result). There is a mini goal-mouth SVG with the shot placement, plus xG and xGOT numbers.
- **Interaction:** click a dot, or step with ‹ ›, to move through shots chronologically. The player page reuses it ("Season shot map", with an on-target toggle).

### 4.4 Lineups (`lineup-pitch-dark.png`, `match-lineup-mobile*.png`)
- **Desktop:** one horizontal pitch with both XIs, home attacking right from the left half and away mirrored. **Mobile:** a vertical pitch, one team at a time, stacked (home then away).
- **Team header strip** (`#343434`): avg-rating badge, crest, team name, and formation `4-2-3-1` in 16px/500. Away is mirrored on the right.
- **Filter chips row:** Distance, Top speed, Transfer value, Age, Country, Fantasy, Stats ▾. Chips are 32px tall, radius 16, `#2c2c2c` background, 14px/500. Selecting one **replaces the rating badge** on every node with that metric. One pitch shows many lenses.
- **Field:** a flat `#2c2c2c` with low-contrast (`~#333`) CSS lines for the boxes and centre circle. No grass texture, no green.
- **Player node:**
  - A 40px circular photo, then the shirt number and surname in 14px.
  - The rating badge sits at the top-right.
  - Event glyphs go around the avatar:
    - sub-off arrow in a red circle, with the minute above (`66'`);
    - yellow or red card chip at the left;
    - goal ball or assist boot at the bottom-right;
    - captain "C" before the name.
- **Below the pitch:** Coach row (avatar + name per side), then **Substitutes** in two columns. Each row has avatar, rating badge, shirt number, name over position, and on the right any event icons, the minute it came on (green, `66'`) and a green sub-on arrow. Unused subs show no minute. Footer link: "Bench / Injured and suspended players".

### 4.5 Events timeline (`facts-events-dark.png`)
- **Centre spine:** a circular minute bubble, about 40px, `#333` background with 14px/700 white text. Added time shows as a small badge (`+1`).
- Home events sit left of the spine (right-aligned) and away events sit right (left-aligned). Each has a 32px circular icon chip next to the bubble.
- **Substitution:** the player coming on in green over the player going off in red, with a green/red swap-arrow icon.
- **Goal:** "Erling Haaland (0 – **1**)", with the scoring team's new score highlighted in green, plus "assist by …" in grey.
- **Card:** the name over the reason (Foul or Dissent) in grey.
- **Period separators:** a hairline with centred "HT 0 – 0" or "FT 0 – 3" in bold. "+1 minute added" appears as a centred grey line.

### 4.6 Header and Facts page order (`header-dark.png`, `match-facts-full.png`)
1. **Header card:** scorers listed under the score, per side. The red-card player is listed separately with a red rectangle.
2. **Match review:** a news card with thumbnail, title and two-line excerpt.
3. **Momentum and Top stats** in one card, as two columns.
4. **Events** timeline.
5. **Lineup** (a reduced version of the Lineup tab, inline).
6. **Team form:** for each side, the last 5 results as score chips (W green, L red, D grey) with crests.
7. Next match, News, About the match.

**Right rail:** highlights video, venue (capacity, attendance, surface, weather), referee (cards/fouls per match with above/below-average bars), a mini league table with both teams highlighted, insights (bulleted facts with team crests) and a "Who will win?" poll.

### 4.7 Team page (`team-overview*.png`)
- **Header card:** crest, name and country; "Sync to calendar" and "Follow" pills. Underline tabs: Overview, Table, Fixtures, Squad, Player stats, Team stats, Transfers, History, News.
- **Bento-ish first row:** Team form (score chips + crests), Next match, Season stats (best XI on a mini pitch with **season-average rating badges** at two decimals, plus goal and assist counts).
- An AI "Daily summary" card ("Generated with AI" footer).
- Fixture difficulty chips (green, orange, red backgrounds at 15% alpha).

### 4.8 Player page (`player-overview*.png`)
- **Hero:** photo, name, club, and a bio grid (height, age, shirt, value, foot, country).
- A market-value line chart with club badges on the line.
- **Season stat tiles:** a 4×2 grid of big numbers with grey labels; Rating uses the badge.
- **Match stats table:**
  - Columns: date, opponent, result chip (W/L + score), team crest, minutes, position, goals, assists, yellow, red, and rating badge.
  - Filters: "All teams" and "All leagues" dropdowns.
  - Previous and Next pagination.
- **Heatmap:** a green→yellow→red KDE on the dark pitch.
- **Season shot map** with a shot-detail panel.
- Percentile rank bars per category (Shooting, Passing, Possession, Defending).
- Trophies and career lists in the rail.

---

## 5. What the MatchLens Coach page should borrow

Priority order. Each item names the reference screenshot.

1. **Lineup on a pitch with formation and rating badges** (`lineup-pitch-dark.png`)
   - Use a horizontal pitch on desktop and vertical, one team at a time, on mobile.
   - Use a flat dark field (our `--bg-elev-2`, about `oklch(0.23 …)`) with hairline markings. No green grass.
   - Each node is a 40px avatar (or a shirt-number disc if we have no photos), number + surname, and a rating badge at the top-right.
   - Event glyphs: sub minute with a red arrow, card chip, goal or assist glyph.
   - Team strip: average rating, crest, name and formation string.
   - **Add the lens chips** ("Rating | Distance | xT | Passes | Pressures"), which swap the badge metric. This is the most coach-useful idea on FotMob.
2. **Rating badge component** with FotMob-style thresholds: `<5` red, `5–6.9` orange, `≥7` green, POTM blue + ★. Pill: 12px radius, 12px/500 text, contrast-aware text colour. Use it everywhere a player appears: pitch, subs, tables, team form.
3. **Momentum chart:** a Recharts Area with the split-gradient trick (section 4.1), dotted baseline, HT reference line, 0'/HT/FT labels only, and goal or card icons at their minutes. Put it side by side with Top stats.
4. **Stat comparison rows with winner pills** instead of per-stat bars (possession as the only split bar). Add the `All | 1st | 2nd` segmented filter with a 0.5s background-colour transition. Group stats under centred 16px titles: Shots, Passes, Duels, Defense, Discipline, xG.
5. **Shot map:** a 105×68 SVG pitch in real metres; goals filled at 50%, misses outlined, the selected shot ringed in white. Add a detail panel with ‹ › stepping (player, minute, type, situation, xG, xGOT, and a mini goal-mouth). Optionally scale the radius by xG.
6. **Match facts header and events spine:** a score block with scorers under each side; a centre-spine timeline of minute bubbles with home events left and away events right; sub on/off in green/red; HT/FT separators.
7. **Contextual help:** "What does xG mean? (?)" links inside the group they explain. This is better than onboarding modals.
8. **Right rail of small facts cards** (referee, venue, insights) as a 300px column on desktop. It collapses below the main column on mobile.

**Don't borrow:**
- **Pure `#000` behind everything.** It works for FotMob's flat style, but our glass chrome needs a slightly lifted cool-black background (`oklch(0.145…)`) so blur has something to sample.
- **Opaque nav.** Keep our glass top bar, which is the one place we differ on purpose.
- **Ads, banners and polls.**
- **GT Walsheim.** It is licensed. Use Geist as planned. Geist Medium 500 at 14/16 gives a similar calm, geometric feel.

---

## 6. Token mapping (FotMob → MatchLens, adapted, dark)

```css
/* adapt, don't copy: same roles, our hues */
--ml-bg: oklch(0.145 0.006 265);          /* FotMob #000 */
--ml-card: oklch(0.19 0.006 265);         /* FotMob #1d1d1d */
--ml-card-2: oklch(0.23 0.006 265);       /* #2b2b2b: pitch, chips */
--ml-hairline: oklch(1 0 0 / 0.08);       /* #333 dividers */
--ml-text: oklch(0.97 0 0);
--ml-text-2: oklch(0.70 0.005 265);       /* #9f9f9f */
--ml-rating-blue: oklch(0.68 0.17 245);   /* POTM */
--ml-rating-green: oklch(0.74 0.17 150);  /* ≥7 */
--ml-rating-orange: oklch(0.77 0.15 60);  /* 5–6.9 */
--ml-rating-red: oklch(0.65 0.21 25);     /* <5 */
--ml-win-bg: color-mix(in oklch, var(--ml-rating-green) 15%, transparent);
--ml-loss-bg: color-mix(in oklch, var(--ml-rating-red) 15%, transparent);
--radius-card: 16px; --radius-pill: 9999px; --radius-badge: 12px;
--row-stat: 48px; --tab-h: 64px; --tab-indicator: 3px;
--dur-fast: 200ms; --dur-data: 500ms; --ease-ui: cubic-bezier(0,0,.2,1);
```

---

## 7. Screenshot index (`docs/redesign/fotmob/`)
| File | Shows |
|---|---|
| `header-dark.png` | Match header card, score block, scorers, underline tabs |
| `facts-momentum-topstats-dark.png` | Momentum chart + Top stats with winner pills + possession bar |
| `facts-events-dark.png` | Centre-spine events timeline |
| `facts-teamform-dark.png` | Team form chips |
| `lineup-pitch-dark.png` | Horizontal lineup pitch, rating badges, lens chips, coach row, substitutes |
| `stats-shotmap-dark.png` | Period filter, shot map + detail, all stat groups, attacking zones |
| `stats-playerstats-dark.png` | Player stats table |
| `match-*-full.png` | Full-page captures of the Facts, Lineup, Stats, H2H and Table tabs |
| `match-facts-mobile*.png`, `match-lineup-mobile*.png` | 390px mobile: scrollable tabs, vertical pitch |
| `match-facts-viewport-light.png` | Light theme |
| `team-overview*.png` | Team page: tabs, form, next match, season XI |
| `player-overview*.png` | Player page: stat tiles, match table with rating badges, heatmap, shot map |
| `h2h-viewport-dark.png` | Head-to-head tab |
