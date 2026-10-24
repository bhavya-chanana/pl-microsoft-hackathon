---
name: ui-creation-fundamentals
description: "Use when creating, improving, or reviewing UI screens/components from product requirements. Applies transcript-derived UI fundamentals: signifiers, hierarchy, spacing, typography, color, dark mode, shadows, icons, states, microinteractions, and overlays."
license: MIT
---

# UI Creation Fundamentals

## Overview

Use this skill to turn a product requirement, rough wireframe, or ugly functional UI into a deliberate interface. The core principle: **good UI communicates behavior without needing instructions**. Visual choices should create signifiers, hierarchy, feedback, and confidence — not decoration for its own sake.

This workflow is distilled from the YouTube transcript at `https://youtu.be/EcbgbKtOELY?si=dowFqYoYC16_9M7r`. Treat the video as design-principle source material, not as a rigid style guide.

## When to Use

Use this skill when:

- Designing a new screen, landing page, dashboard, card, sidebar, form, or component set.
- Improving a UI that works functionally but looks flat, spreadsheet-like, cramped, or unclear.
- Creating frontend code, HTML/CSS, React/Tailwind components, or a high-fidelity mockup.
- Reviewing UI for hierarchy, spacing, color, states, dark mode, or interaction feedback.
- Writing prompts for another design/build agent to generate UI.

Do **not** use this skill for pure backend work, brand strategy, illustration, logo design, or deep user-research planning. It can support those workflows, but it is not a substitute for them.

## Operating Principle

A UI element should answer these questions visually:

1. **What is important?** Use size, position, color, imagery, and contrast.
2. **What belongs together?** Use containers, spacing, alignment, and grouping.
3. **What can I do?** Use buttons, hover states, active states, affordances, and tooltips.
4. **What just happened?** Use loading, success, error, pressed, disabled, and microinteraction feedback.
5. **Can I read it quickly?** Use whitespace, typography, contrast, and overlays.

If the interface needs paragraphs of explanation to be usable, add clearer visual signifiers before adding copy.

## Workflow

### 1. Extract the UI job

Before designing, write a compact UI brief:

- Primary user task.
- Primary action/CTA.
- Secondary action, if any.
- 3-5 pieces of information the user must scan first.
- Target surface: landing page, dashboard, mobile app, card list, form, modal, etc.
- Density target: spacious marketing UI vs dense operational UI.

Completion criterion: you can rank the screen's content from most important to least important and name the one action the user should notice first.

### 2. Add signifiers before polish

Create visual cues that make behavior obvious:

- **Containers** show related groups.
- **Selected containers/highlights** show active tabs, filters, or nav items.
- **Muted/gray text** shows inactive or unavailable options.
- **Hover, pressed, active, focus, disabled, and loading states** show what an element affords.
- **Tooltips or helper text** explain non-obvious actions, but only after visual affordance is handled.

Completion criterion: a user can tell what is selected, what is clickable, what is inactive, and what belongs together without reading instructions.

### 3. Build hierarchy with contrast

Avoid spreadsheet UI: equal weight makes everything look equally unimportant. Use contrast deliberately:

- Put the most important content near the top or strongest scan path.
- Make primary information larger, bolder, and/or more colorful.
- Push supporting information smaller, lower, or lower-contrast.
- Use images when they genuinely improve scanning or recognition.
- Use alignment and icons to imply relationships instead of verbose labels.
- Use color sparingly to direct attention to key values, prices, status, or calls to action.

Rule of thumb: **hierarchy is the difference between things** — large vs small, colorful vs neutral, top vs bottom, bold vs regular.

Completion criterion: at a glance, the eye lands first on the intended primary content and second on the intended action or supporting detail.

### 4. Use grids as guidelines, not handcuffs

Do not overfit to a 12-column grid or exact 8px gaps when the design does not need it. Grids are most useful for highly structured, repeating content:

- Galleries.
- Blogs.
- Product grids.
- Dashboards.
- Card lists.

Responsive guideline:

- Desktop: often 12 columns.
- Tablet: often 8 columns.
- Mobile: often 4 columns.

For custom landing pages, hero sections, and expressive layouts, prioritize visual balance and whitespace over strict column alignment.

Completion criterion: repeating content has predictable responsive behavior, while custom sections still feel balanced and breathable.

### 5. Use whitespace and grouping

Whitespace is usually more important than complex layout rules.

Practical defaults:

- Use a **4-point spacing system**: 4, 8, 12, 16, 24, 32, 48, 64, 96.
- Start with around **32px between major items** in a simple landing section.
- Reduce spacing for items that belong together, such as eyebrow + headline or headline + subtext.
- Increase spacing between separate conceptual groups.
- Let content breathe; cramped UI usually reads as lower quality.

Completion criterion: related items feel grouped, separate sections feel distinct, and the screen does not look cramped at the target viewport.

### 6. Keep typography boring and sharp

Most UI is text. Do not waste time font shopping.

Defaults:

- Use one strong sans-serif family unless there is a real brand reason not to.
- For landing pages/websites, use no more than about **six font sizes**.
- For dashboards and dense products, keep the type scale tighter; text rarely needs to exceed **24px** inside dense app UI.
- For large display headings, tighten letter spacing by roughly **-2% to -3%** and use **110%-120% line height**.
- Body copy should be comfortably readable; do not apply heading-tight line height to paragraphs.

Completion criterion: headings feel intentional, body copy is readable, and font variation is not doing work that hierarchy should do.

### 7. Create a purposeful color system

Start with one primary color, usually the brand color. Build from there:

- Lighten the primary color for subtle backgrounds, tints, badges, and selected states.
- Darken the primary color for text, outlines, or emphasis.
- Use semantic colors intentionally:
  - Blue: trust, links, primary actions, information.
  - Red: danger, destructive action, urgency, error.
  - Yellow/amber: warning, caution, pending state.
  - Green: success, completion, positive status.
- Let color appear where the UI needs meaning: announcement bars, focus states, chips, errors, success messages.

Do not use color just to make the screen busier.

Completion criterion: every non-neutral color has a job: brand, state, attention, status, or meaning.

### 8. Handle dark mode explicitly

Dark mode is not light mode with inverted colors.

Rules:

- Reduce bright border contrast; light borders on dark cards often look harsh.
- Create depth by making cards/surfaces slightly lighter than the page background.
- Avoid highly saturated bright chips; lower saturation/brightness for chip backgrounds and keep text readable.
- Deep purples, reds, greens, and blues can work; do not default only to navy and gray.
- Test contrast for text and interactive states.

Completion criterion: cards are distinguishable from the background, borders are not glowing, chips are readable, and the UI still has hierarchy without light-mode shadows.

### 9. Use shadows for depth, not decoration

In light mode, shadows can separate layers. Most bad shadows are too strong.

Defaults:

- Lower opacity.
- Increase blur.
- Use subtler shadows for cards.
- Use stronger shadows for floating content above other content: popovers, dropdowns, modals, command palettes.
- Use inner/outer shadows only when building tactile controls, and keep them restrained.

Rule: if the shadow is the first thing you notice, it is too strong.

Completion criterion: shadows clarify layer order without becoming the visual subject.

### 10. Design icons and buttons as a system

Icons and buttons should align with text, not fight it.

Rules:

- Match icon size to the text line-height when possible. Example: 24px line height -> 24px icon.
- Tighten icon/text spacing after matching size.
- Treat sidebar links as ghost buttons: no background by default, background on hover/active.
- For CTA pairs, make primary and secondary actions visually distinct but aligned.
- Button padding guideline: horizontal padding should usually be about **2x vertical padding**.
- Buttons may include icons, but the icon must clarify the action, not decorate it.

Completion criterion: buttons look clickable, icons align optically with labels, and primary/secondary actions are visually ranked.

### 11. Every interaction needs a response

When a user does anything, the interface should respond.

Minimum component states:

- Buttons: default, hover, active/pressed, disabled, and often loading.
- Inputs: default, focus, error, disabled, and sometimes warning/success.
- Navigation: default, hover, active/current.
- Async actions: loading, success, error, empty state.
- Destructive actions: confirmation or clear irreversible-state warning.

Feedback examples:

- Loading spinner or skeleton while data is fetching.
- Success toast/message when action completes.
- Error message near the field/action that caused it.
- Press animation or visual depression for tactile buttons.

Completion criterion: no meaningful user action feels ignored.

### 12. Add microinteractions only after core states work

Microinteractions are enhanced feedback, not a substitute for basic states.

Good uses:

- A copied chip/toast sliding up after pressing a copy button.
- A subtle checkmark after save succeeds.
- A list item animating into place after creation.
- A swipe/scroll animation that clarifies spatial relationship.

Keep motion short, purposeful, and interruptible. Avoid playful motion in serious workflows unless it reduces confusion or increases confidence.

Completion criterion: the microinteraction confirms a user action or state change; it is not random animation.

### 13. Make image overlays readable

If text sits on an image, do not rely on luck.

Options:

- Use a linear gradient that preserves the image where possible and converges into a readable text background.
- Use a full overlay only when preserving image detail is less important.
- For a more modern look, combine gradient with progressive blur.
- Test the overlay on bright, dark, and busy image regions.

Completion criterion: text remains readable without destroying the image's purpose.

## Implementation Checklist

When building UI in code, create explicit tokens/classes for:

- Type scale and line heights.
- Spacing scale.
- Color tokens: background, surface, border, text, muted text, primary, semantic states.
- Radius tokens.
- Shadow tokens.
- Component states: hover, active, focus, disabled, loading, error.
- Responsive breakpoints.

If using Tailwind, avoid one-off arbitrary values unless the design genuinely needs them. If using CSS modules/plain CSS, group tokens near the top of the file or in a design-token file.

## Review Pass

Run this review after the first draft:

1. **Squint test:** Does the primary hierarchy still read when details blur?
2. **State test:** Can every button/input/nav item show default, hover/focus, active, disabled, and error/loading where relevant?
3. **Spacing test:** Are related items closer than unrelated items?
4. **Color test:** Does every accent color have meaning?
5. **Dark/light test:** If dark mode exists, surfaces, borders, chips, and text contrast were designed separately.
6. **Responsive test:** Repeating content adapts predictably on desktop/tablet/mobile.
7. **Accessibility test:** Text contrast, focus indicators, keyboard navigation, and reduced-motion behavior are acceptable.

## Common Pitfalls

1. **Starting with decoration.** Fix hierarchy, grouping, and states before gradients, shadows, and animation.
2. **Equal-weight content.** If every label/value/card has the same size and contrast, the UI becomes a spreadsheet.
3. **Overusing grids.** Grids help responsive repeating content; they should not make custom sections stiff.
4. **Font shopping.** One good sans-serif and a sharp type scale beat endless font comparisons.
5. **Color without purpose.** Random accents make the UI noisy and weaken semantic colors.
6. **Dark mode via inversion.** Dark mode needs separate surface, border, chip, and depth decisions.
7. **Missing states.** A component without hover/focus/active/disabled/error/loading behavior is unfinished.
8. **Obvious shadows.** Strong shadows make the design look amateur; depth should be felt before noticed.
9. **Unreadable overlays.** Text over images needs gradients/blur and real contrast testing.
10. **Motion as garnish.** Microinteractions should confirm actions or clarify state changes.

## Verification Checklist

- [ ] The UI brief names the primary task and primary CTA.
- [ ] The visual hierarchy makes the most important content obvious first.
- [ ] Related elements are grouped with containers, spacing, or alignment.
- [ ] The spacing system uses mostly 4-point multiples.
- [ ] Typography uses a small, intentional scale and one primary sans-serif family.
- [ ] Large headings use tighter tracking and line height where appropriate.
- [ ] Accent colors have brand or semantic meaning.
- [ ] Dark mode, if present, was designed rather than inverted.
- [ ] Shadows communicate layering without calling attention to themselves.
- [ ] Icons align with text and clarify actions.
- [ ] Buttons/inputs/nav elements include required states.
- [ ] Async and destructive flows include visible feedback.
- [ ] Microinteractions confirm actions or clarify state changes.
- [ ] Image overlays preserve readability.
- [ ] The implementation has been run or previewed at target breakpoints before final delivery.
