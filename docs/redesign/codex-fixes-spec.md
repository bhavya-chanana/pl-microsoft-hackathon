# Codex fixes spec: smaller moment cards, score headers that never overflow, sound state

Builds on `design-spec.md`, `coach-v2-spec.md` (addendum 00) and `fotmob-teardown.md`: monochrome UI, colour only for data (team colours as a dot or accent, never as long text), cards with no shadow, 14px minimum body, 13px captions, 44px touch targets.

Observed on the production build (Dovewick Orbit 0-2 Brindle Vale, full time):
- Moment card (`MomentCard`) is 233-367px tall at 390 and 233-273px at 1440 for 2 to 3 short sentences. Cause: 40px minute row, headline, a "From match data" badge line, body, key numbers row, a 44px action row, plus 20px padding and `gap-3`.
- Report header: "Dovewick Orbit" starts left of the card edge at 390 (`/coach?tab=report`). The team name sits in a `flex` span whose inner `truncate` child has no `min-w-0`, so the flex item will not shrink below its text width. The column is `items-end`, so the overflow runs out the left side. Even with that fixed, the two outer columns are only ~100px wide at 390 (card inner 324px minus a 100px score), so names would clip to "Dovewic...". Wrong layout for that width.

---

## 1. Moment card (Latest moment + Highlights)

One compact component for both places. There is no separate "Latest" variant: the Live page "Latest moment" is the same card, with the section title above it carrying the meaning. Goals keep their tinted border; nothing else is promoted.

### Content rules (what shows)
| Field | Rule |
|---|---|
| Minute | `font-display text-2xl leading-7 font-bold tnum` (24px, was 40px) |
| Type icon + label | icon `size-4`; label `text-[13px] font-medium text-muted-foreground`, hidden below `sm` (icon only, `sr-only` text) |
| Headline | `text-base leading-6 font-semibold` (16/24), `line-clamp-2`, `text-balance`, `max-w-[56ch]` |
| Body | `text-sm leading-5 text-muted-foreground` (14/20), `line-clamp-1`, `max-w-[64ch]`. Hidden if it only repeats the headline |
| Key numbers | max 2 at `sm+`, 1 below `sm`; inline in the meta row (below). `dd` `font-display text-base font-semibold tnum`, `dt` `text-[13px] text-muted-foreground`, unit `text-[13px]`. Single line, `whitespace-nowrap` |
| Club angle | not on the card; it lives in the Details sheet only |
| Fact-check | verified: a `BadgeCheck size-4` icon in the meta row with `sr-only` + `title` "Checked against match data". Any other status keeps the text badge in the Details sheet, plus the icon (`Database`) on the card. Remove the badge line from the card body |
| "Your player" | `Star size-4 fill-current` in the meta row; text "Your player" only at `sm+` |
| Share, Replay on pitch | icon buttons, see below. "Details" button is removed: the whole card opens the Details sheet |

Total text budget: headline up to 2 lines plus body 1 line = 3 lines maximum at every width; typical is 2 (1 headline + 1 body) at 768 and above. Truncation is always `line-clamp`, never a mid-word clip.

### Layout
```
[ accent 4px | 36' ◎ Big chance · xG 0.89 · 96 km/h  ✓ ★            [share][replay] ]
[            | Big chance for Brindle Vale                                            ]
[            | Brindle Player 09 had a 0.366 xG chance at 21', but it was saved.      ]
```
- Container: `relative flex flex-col gap-1 overflow-hidden rounded-xl border bg-card py-3 pr-3 pl-4 shadow-none sm:py-3.5 sm:pr-4 sm:pl-5 hover:bg-raised/40`. Left accent `w-1` (was `w-1.5`). Goal: `border-home/50` or `border-away/50` as today.
- Meta row: `flex h-9 items-center gap-2`. Icon buttons sit at the right (`ml-auto flex`), each `size-11 -my-1 text-muted-foreground` ghost (visual row height stays 36px, hit area 44px). `aria-label` "Share" and "Replay on pitch" (keep the existing copy), `title` the same.
- Headline is the stretched button for the Details sheet: `<button class="text-left after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-ring">`. The icon buttons are `relative z-10`.
- Gap between cards in the feed: `gap-2` (was 3 to 4), quiet rows keep their `py-3`.
- Expected heights: 96-104px (1 headline line + 1 body line), 120-128px (2 headline lines). Hard cap 136px. Target cut: roughly 50% from today.

### Widths
- 390: card 358px wide, text column 358 - 12 - 16 = ~330px, headline wraps to at most 2 lines (about 34 characters per line at 16px). Meta row: minute, icon, ONE key number, check, star, two icon buttons (88px). No type label.
- 768: card fills the feed column (~680px), headline fits one line up to ~56ch, body one line to ~64ch; text never exceeds the `ch` caps, so the right 120px stays empty rather than stretching lines.
- 1440: feed column is ~768px wide; same as 768. Never wider than the column; no multi-column card grid.
- Live page right-rail "Latest moment": same card in a 4-column span (~330px): behaves as 390.

### States
- Goal arrival: same `fadeUp` (opacity + y 8, 250ms). No shimmer, no layout jump (`layout="position"` stays).
- Reduced motion: no y offset.
- Hover (desktop only): bg `raised/40`, no lift.

---

## 2. Score headers that never overflow

Rules for every place a team name sits next to a score:
1. Every flex or grid child that contains a name gets `min-w-0`, and its name span gets `min-w-0` too (a `truncate` inside a flex item is not enough on its own).
2. Use full names where there is room, with `line-clamp-2 break-words text-balance`. Use `short_name` only in bars that are one line tall (collapsed header, score bug, chips), with `truncate` and a max width.
3. Never `items-end` on a column whose child can exceed the column (it overflows on the start side, where it is clipped and unscrollable).
4. Names are `text-foreground`; the team colour is the dot only (monochrome rule). Removes the blue/amber long-text contrast issue.
5. Acceptance: with `Wolverhampton Wanderers Football Club` and `Brighton & Hove Albion` as names and five goals for one side, `document.documentElement.scrollWidth <= innerWidth` and no child `getBoundingClientRect().left < card.left` at 320, 360, 390, 768, 1440.

### 2a. Match report header (`features/report/match-report.tsx`)

Below `sm` (640): score on top, two team columns below. From `sm`: classic three columns.

```
390                                      768 / 1440
Latest · 32'                             Latest · 32'                 [Copy link][Export PDF]
[ Copy link ][ Export PDF ]  (2 equal)   Dovewick Orbit ●   0 - 2   ● Brindle Vale
            0 - 2                        Brindle 10 11'              Brindle 02 32'
● Dovewick Orbit     Brindle Vale ●      (scorers one per line)
(scorers, one per line)  (right aligned)
─────────────────────────────────        ──────────────────────────────────────────
Matchlens League · Ground (wraps, centred)
```
- Top row: `flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between`. Phase pill unchanged. Buttons wrapper `grid grid-cols-2 gap-2 sm:flex`; each button `w-full justify-center sm:w-auto` (fixes the left-aligned, wrapped buttons).
- Score grid: `grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-start sm:gap-x-6`.
  - Score `<p>`: `col-span-2 row-start-1 text-center sm:col-span-1 sm:col-start-2` , `font-display text-[56px] leading-[56px] font-bold whitespace-nowrap tabular-nums` (56 at all widths; was 44 at mobile).
  - Home column: `row-start-2 sm:row-start-1 sm:col-start-1 flex min-w-0 flex-col items-start text-left sm:items-end sm:text-right gap-1.5`.
  - Away column: `row-start-2 sm:row-start-1 sm:col-start-3 flex min-w-0 flex-col items-end text-right sm:items-start sm:text-left gap-1.5`.
- Name line: `flex min-w-0 max-w-full items-start gap-2 text-base leading-6 font-semibold sm:text-lg` with `<span aria-hidden class="mt-2 size-2.5 shrink-0 rounded-full bg-home">` and `<span class="min-w-0 line-clamp-2 break-words text-balance">name</span>`. Dot order: home dot `order-first sm:order-last`; away dot `order-last sm:order-first` (the dot sits on the outer edge on mobile, next to the score on `sm+`). Max two name lines.
- Scorers: a list, one scorer per line, not a comma run.
  `<ul class="flex flex-col gap-0.5 text-[13px] leading-5 text-muted-foreground">`, `<li class="break-words">` with player short name, then minutes in `tabular-nums`. Group several goals by one player: "Brindle 10 11', 32'" (join minutes with ", "). Own goals/penalties if the feed marks them: "(og)" / "(pen)" after the minute. No cap (a report prints all goals); two columns share the same row so a 4-goal side simply makes that side taller.
  Keep the existing `sr-only` "Scorers:" prefix as the `aria-label` of the `<ul>`.
- Venue line: `border-t pt-3 text-center text-[13px] leading-5 text-muted-foreground text-balance`; it may wrap to 2 lines at 390.
- Print: same classes; the print width is above `sm`, so it prints in the three-column form.

### 2b. Hero match header (`components/shell/match-header.tsx`, Live hero)
Current mobile cap `max-w-24 truncate` (96px) shows "Dovewic..." for 2-word names.
- Names: replace `truncate` with `line-clamp-2 break-words text-balance text-center leading-4`, keep `max-w-24` below `md`. Two lines of 13px fit "Dovewick / Orbit" and "Brighton & / Hove Albion". Do not raise the cap: 2 x 96 + ~110 score + 32 gaps = 334px is the budget inside the 358px gutter.
- At `md+`: `md:max-w-48 xl:max-w-64 md:text-left` (home `md:text-right`), `md:line-clamp-2`. At 768 worst case: 2 x (44 + 12 + 192) + 160 + 80 = 736, fits the 736 content width exactly; if it does not, drop to `md:max-w-44`.
- Outer flex: add `min-w-0 w-full justify-center`, children `min-w-0`.
- Collapsed bar: already uses `short_name` / 3-letter abbr with `truncate`. Keep; add `max-w-[40vw]` on the text span so a 20-character short name cannot push the clock under the connection pill.

### 2c. Broadcast score bug (`features/broadcast/score-bug.tsx`)
- Team label span: add `max-w-[9ch] truncate @lg:max-w-[14ch]` (uppercase, tracking-wide). Add `max-w-full` to the container and `min-w-0` on each side div. Clock cell `shrink-0`.
- A very long `short_name` truncates with an ellipsis; the scores and clock stay visible.

### 2d. Coach header chips (`coach-page.tsx`)
- Team chip text: `max-w-[10rem] truncate` (it already wraps via `flex-wrap` with the phase pill). Chip keeps `h-11`.

### 2e. Other
- Past matches strings and fan match card use `truncate` inside a `min-w-0 flex-1` parent: keep, verify with the long-name test.
- Multiverse pane title already `min-w-0 truncate`.

---

## 3. Sound control: how it says what is going on

The state machine in `features/companion/voice.ts` (`idle | speaking | blocked | no-voice | failed | unsupported`, plus `languageFallback`) is the right base. The gaps are in the control itself: a speaker icon alone cannot say "off" from "on", the label only lives in `aria-label`/`title`, problems appear in a small `text-xs` note away from the button, and the label changes while `aria-pressed` also changes (double announcement). Spec:

### 3.1 One `SoundButton` everywhere (Broadcast pill, Stream settings, Multiverse panes)
Never icon-only. Visible state text, pill, 44px tall.

| State | Icon | Visible label | Tone | `aria-` |
|---|---|---|---|---|
| Off | `VolumeX` | "Sound off" (below `sm` in the Broadcast pill: "Off") | `text-muted-foreground` | `aria-pressed=false`, name "Commentary" |
| Starting (click to first audio, max 4s) | `Loader2` spinning (static when reduced motion) | "Starting" | muted | `aria-busy` |
| On, waiting for the next moment | `Volume2` | "Sound on" | `text-foreground bg-foreground/10` | `aria-pressed=true` |
| Speaking | 3-bar equaliser (`motion-safe:animate-pulse` bars, 2px wide) | "Speaking" | `text-accent` | live region says "Reading: <headline>" once per clip, `aria-live=polite` |
| Blocked / failed | `VolumeX` + 10px `TriangleAlert` badge at the top-right of the icon | "Tap to enable" | `text-warning`, amber ring | `aria-pressed=true`, description = the problem text below |
| No voice / unsupported | `VolumeX` with a slash overlay | "No voice" / "Unavailable" | muted at 60%, button stays enabled so the explanation can open | `aria-disabled=true` |

- Accessible name is the static word "Commentary" (Stream settings: "Listen"); the pressed state carries on/off. Do not swap `aria-label` and `aria-pressed` together.
- A problem is also never colour only: icon badge plus the words.
- Button anatomy: `inline-flex h-11 items-center gap-2 rounded-full px-3.5 text-sm font-medium ring-1 ring-border`; pressed: `bg-foreground/10`; problem: `ring-warning/60`.

### 3.2 Where the explanation goes
- In the Broadcast glass pill: the message sits as a one-line caption pill directly above the controls (the existing `VoiceNote` slot), `text-[13px] leading-5 max-w-[min(32rem,calc(100vw-2rem))] text-balance text-center`, visible for as long as the problem lasts (not a 6s hint), with the pill kept visible (`show`) while a problem is active so idle fade-out cannot hide it. Warning problems get `TriangleAlert size-4` at the start of the caption.
- In Stream settings: under the label, `text-[13px] leading-5` (was `text-xs`); problem text in `text-warning`, with a "Try again" text button (`h-11 px-0 text-sm underline`) that re-runs `unlockVoice()` from that click.
- In Multiverse: the problem caption sits in the pane under the title, not under the toggle. Only one pane plays at a time; the active pane's button reads "Playing", the others "Listen". Same states as 3.1 for the active pane.
- Copy (replace and shorten the current strings; plain words, say what to do):
  - blocked: "Your browser blocked the sound. Tap the speaker once more, and check this tab is not muted."
  - no-voice: "This device has no speech voice installed. Try Chrome or Safari, or install a voice in system settings."
  - failed: "The voice stopped. Tap to try again."
  - unsupported: "This browser cannot play sound."
  - languageFallback (not a warning, muted): "No voice for Hindi here, so a default voice is reading it."

### 3.3 Behaviour rules
- The switch is off on every load (it is not persisted), so it never claims to be on when the browser would block the first clip. If a future change persists it, the restored state is "Tap to enable" (blocked), not "Sound on".
- On enable the player reads the newest moment at once (already built), so the first click always produces an audible confirmation. If nothing starts within 4s, state becomes blocked with the message above; do not stay on "Starting".
- When Azure clip fails and the browser voice takes over, the state does not change; the Stream settings voice line switches from "Natural voice" to "Your browser's voice" so the change is visible.
- Pundit "Read aloud" uses the same problem text inline under the answer when its utterance errors; button label stays "Read aloud" / "Stop reading".
- Reduced motion: no equaliser animation (static bars), no spinner rotation (static icon plus the word "Starting").

### 3.4 Acceptance
Screenshots at 390 and 1440 of the Broadcast pill in: off, on/idle, speaking (can be forced in dev), blocked (force `useVoice.set("blocked")`), no voice. Every state must be distinguishable with the colour removed (grayscale test), and the pill must stay within `100vw - 1rem` at 360px. Width budget: today's pill is ~318px at 390 with an icon-only sound button; "Off" adds ~35px (353px, fits 390 but not 360). Below `sm` change "Copy overlay link" to "Copy link" (`sm:` shows the long text) so the pill is ~290px.
