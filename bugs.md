# Bugs

Small bugs to fix later. Newest first.

## 1. Live insights are never visible in the Match centre sidebar

**Where:** `frontend/src/features/companion/match-centre.tsx`, the `<aside>` on wide screens.

**What happens:** The right-hand column does not scroll, and the Live insights feed below "Your view" can never be seen.

**Why:** The aside is pinned to the screen height (`lg:sticky lg:top-20 lg:h-[calc(100dvh-6rem)]`). The action card (What did I miss?, Ask the pundit, Match recap, Listen) and the Your view panel together fill all of that height. The feed section is `flex-1 min-h-0`, so it shrinks to zero, and the aside has no overflow of its own to scroll to it.

**Fix:**
- Make the action card and Your view panel smaller: smaller buttons, less padding and gap, maybe two buttons per row, or fold Your view into a collapsible or popover.
- Also give the aside a fallback (`lg:overflow-y-auto`, or let the feed keep a `min-h` such as `min-h-64`) so the feed can always be reached on short screens.

## 2. Listen in the Match centre does not speak

**Where:** `frontend/src/features/companion/narration.tsx` (`ListenToggle`, `NarrationPlayer`).

**What happens:** Turning on Listen gives no audio.

**Things to check:**
- Only insights that arrive *after* Listen is turned on are read. Everything already in the feed is marked as read at that moment, and data cards wait `SETTLE_MS` (6 s) before they are read. Find out whether new insights are actually arriving, or whether the demo replay has finished.
- When the backend reports `speech: "azure"`, it plays `new Audio(/api/backend/speech?...)`. That `play()` happens long after the click, so the browser's autoplay rules may block it. It then falls back to `speechSynthesis`, which may also stay silent. Try unlocking audio on the toggle click: play a silent clip, or `speechSynthesis.speak` an empty utterance.
- Check that `/api/backend/speech` returns audio, not an error that leads to a silent fallback.
- On the `/broadcast` page, the browser voice did speak in testing (2026-10-26), so compare the two set-ups.

## 3. Broadcast controls: a click on the hidden bar does nothing

**Where:** `frontend/src/features/broadcast/broadcast-controls.tsx`.

**What happens:** After 2.5 s of no input the Back and commentary controls fade out and stop taking clicks. Clicking where they were only brings them back, so a second click is needed. This matters most on touch screens.

**Fix:** Keep the controls clickable while faded, or capture the first click and pass it on.
