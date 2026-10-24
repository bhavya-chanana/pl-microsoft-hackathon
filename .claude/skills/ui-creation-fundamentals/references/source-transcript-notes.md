# Source Transcript Notes

Source video: https://youtu.be/EcbgbKtOELY?si=dowFqYoYC16_9M7r
Fetched transcript: 2026-07-04 via `youtube-transcript-api` using the local Hermes `youtube-content` helper.
Duration in fetched metadata: 9:19.

## Distilled principles

- Good UI uses signifiers: selected states, inactive states, hover states, active nav highlights, button press states, and tooltips.
- Hierarchy comes from contrast: size, position, color, imagery, boldness, and alignment.
- Repeating structured pages can benefit from grids, but whitespace and grouping matter more than rigid 12-column obedience.
- Use a 4-point spacing system for consistency because values can be split in half cleanly.
- UI is mostly text: pick one good sans-serif, use a restrained type scale, and tighten large headings.
- For large headings, transcript guidance was roughly -2% to -3% letter spacing and 110%-120% line height.
- Start color with one primary/brand color, then create tints/shades and semantic colors.
- Use color for purpose: trust/info, danger/error, warning, success, focus, selected, and attention.
- Dark mode requires separate decisions: softer borders, lighter cards than background, and less saturated chips.
- Light-mode shadows should be subtle: lower opacity, more blur, card shadows weaker than popover/modal shadows.
- Icons should usually match the font line-height and align tightly with labels.
- Sidebar links are often ghost buttons until hover/active.
- Buttons need states: default, hover, active/pressed, disabled, and sometimes loading.
- Inputs need focus/error/warning states and useful nearby messages.
- Every interaction needs a response: loading, success, error, pressed, disabled, or microinteraction feedback.
- Microinteractions should confirm action or clarify state, e.g. copy confirmation chip sliding up.
- Text over images needs overlays: linear gradients, optional progressive blur, and contrast testing.

## How the SKILL.md uses this source

The skill converts the transcript into a reusable creation/review workflow. It deliberately avoids copying the transcript verbatim and instead turns each concept into a checkable completion criterion for UI work.
