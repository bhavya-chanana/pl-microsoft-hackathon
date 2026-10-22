# Frontend Guidelines

Rules for any agent (or human) building UI in this repo. Follow them in order: stack, design, build, verify.

## 1. Stack (do not substitute without a reason)

| Concern | Choice |
|---|---|
| Framework | Next.js (App Router) + TypeScript (strict) |
| Styling | Tailwind CSS, design tokens as CSS variables |
| Components | shadcn/ui on Radix / Base UI primitives |
| Flourish | Magic UI / Aceternity UI blocks (shadcn-compatible), used sparingly |
| Motion | Motion (Framer Motion); GSAP + ScrollTrigger for scroll sequences |
| 3D / interactive | React Three Fiber + drei (only where it earns its place) |
| Data fetching | TanStack Query; TanStack Table for tables |
| Charts | Recharts / Tremor; D3 or visx for custom |
| Graphs / flows | React Flow (xyflow) |
| Agent / streaming UI | Vercel AI SDK + AI Elements |
| Client state | Zustand (only for state that is genuinely client-global) |
| Icons | lucide-react |

## 2. Skills and tools to load first

- `frontend-design` (anthropics/skills): read before any UI work.
- `shadcn/ui` skill: `npx skills add shadcn/ui`. Reads `components.json`, so use the project's aliases and installed components.
- `vercel-react-best-practices` and `web-design-guidelines` (vercel agent-skills): performance and accessibility audits.
- Playwright MCP or the built-in browser: take screenshots of your own output and fix what looks wrong.
- Vet third-party skills and MCP servers (read the source) before installing.

## 3. Design principles

1. **Pick a point of view before writing code.** State the tone in one line (e.g. "broadcast-sports energy, dark, high contrast") and keep every choice consistent with it.
2. **Avoid generic AI output:** no default Inter-on-white, no purple-gradient hero, no identical card grids, no emoji as icons.
3. **Tokens first.** Define color, spacing, radius, shadow, and type scale as CSS variables in one place. Never hardcode hex values in components.
4. **Typography carries the design.** One display face plus one text face, a clear scale, and tight control of line length (45-75 characters).
5. **Hierarchy over decoration.** One primary action per view. Use size, weight, and space before color.
6. **Spacing on a 4/8 px scale.** No arbitrary values.
7. **Color:** a neutral ramp, one accent, and semantic colors (success/warn/error). Support light and dark from the start via tokens.
8. **Depth:** consistent elevation. Shadows are subtle and share a light source.

## 4. Motion

- Motion explains change (enter, exit, reorder, loading to loaded). It is not decoration.
- Durations 150-300 ms for UI, up to 600 ms for page-level. Use ease-out for entrances.
- Animate `transform` and `opacity` only. Never animate layout properties.
- Respect `prefers-reduced-motion`: provide a reduced or no-motion path.
- One signature moment per page (hero, reveal, or 3D scene), not ten.
- Lazy-load heavy motion/3D and keep it off the critical path.

## 5. Building components

- Start from shadcn: `npx shadcn@latest add <component>`; check what is installed before adding.
- Compose, don't fork: wrap shadcn components rather than editing them in place, unless the design requires it.
- Every component handles **all states**: default, hover, focus-visible, active, disabled, loading, empty, error.
- Server Components by default; add `"use client"` only for interactivity.
- Keep components small and typed; props are explicit, no `any`.
- Colocate by feature (`features/<name>/`), shared primitives in `components/ui/`.
- No magic strings for copy or routes; centralize them.

## 6. Data and agent UI

- Define response types from the backend contract once; validate at the boundary (Zod).
- Never block the page on a slow agent call. Stream, show progress, and keep the UI interactive.
- For multi-agent output, show *what is happening*: agent name, step, status, and sources. Use React Flow or a timeline for live pipelines.
- Show provenance. Fact-checked claims display their verdict and source; unverified content is labelled as such.
- Loading uses skeletons that match the final layout. Errors are specific and offer a retry.
- No API keys or endpoints in client code. Call the backend through server routes.

## 7. Accessibility (non-negotiable)

- Semantic HTML first; ARIA only to fill gaps.
- Full keyboard operation with visible focus rings.
- Contrast: 4.5:1 for body text, 3:1 for large text and UI.
- Every image has `alt`; every input has a label; icon-only buttons have `aria-label`.
- Touch targets at least 44x44 px.
- Do not convey meaning with color alone.

## 8. Responsive

- Mobile-first. Design at 375 px, then scale up through tablet to desktop.
- No horizontal scroll at any width.
- Use fluid type (`clamp()`) and container queries where layout depends on the container.

## 9. Performance budget

- Use `next/image` and `next/font`. Set explicit dimensions to avoid layout shift.
- Code-split heavy libraries (3D, charts, editors) with dynamic imports.
- Target LCP < 2.5 s, CLS < 0.1, INP < 200 ms.
- Check the bundle before adding a dependency; prefer what is already in the stack.

## 10. Verification loop (required before saying "done")

1. `npm run lint` and `npm run typecheck` pass.
2. Run the app and open it in a browser.
3. Take screenshots at **375 px and 1440 px**, in **light and dark**.
4. Exercise the loading, empty, and error states, not just the happy path.
5. Tab through the page with the keyboard.
6. Check the console for errors and warnings.
7. Compare against the stated design intent (section 3.1) and fix mismatches.

Report what you verified and what you did not.

## 11. Don'ts

- Don't add a UI library that duplicates one already in the stack.
- Don't ship placeholder text, lorem ipsum, or stock gradients.
- Don't hardcode colors, sizes, or fonts outside the tokens.
- Don't disable lint or type rules to get something to compile.
- Don't leave `console.log`, dead code, or commented-out blocks.
- Don't claim a UI works without having looked at it.
