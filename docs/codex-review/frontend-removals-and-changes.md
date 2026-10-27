# Frontend redesign audit

Compared `afbea45` (the redesign baseline) with `8de3965` (all-features), then reviewed the restored paths and their data flow. This is a functionality audit, not a visual comparison of every styling change.

The gating was an explicit redesign decision: `docs/redesign/design-spec.md:30` specifies that persisted Coach mode hides Explore > Multiverse and Broadcast, polls, and fan voting columns to create a reduced coach-focused navigation. The new request supersedes that Broadcast restriction, so this pass restores both Explore destinations while retaining coach-specific poll and vote presentation.

| Earlier feature or path | Redesign state | Decision in this pass |
| --- | --- | --- |
| Broadcast at `/broadcast` in primary navigation | Moved to `/explore/broadcast`; old URL redirects. Clean output, commentary, link copy and exit remain. Explore hid its tile in coach view; palette omitted it. | Show the Explore tile in every view; add a labelled Live-page link and palette command. Preserve clean output and existing redirect. |
| Multiverse at `/multiverse` | Moved under Explore, old URL redirects; persona and language controls remain. Tile was hidden in coach view. | Make it accessible in every view and add a palette destination. |
| Scenario Lab at `/lab` | Moved under Explore; old URL redirects; scenario creation and launch remain. | Keep tile and add palette destination. |
| Tactics at `/tactics` | Moved to Coach > Tactics; old URL redirects to the tab. The board, prompt and agent trace remain. | Keep consolidated location. |
| Agent room in main navigation | `/agents` remains but is behind Developer tools, which requires `NEXT_PUBLIC_DEV_TOOLS=1`; direct route still exists. | Keep diagnostics out of the main fan journey. |
| Match timeline, team statistics and shot map in Live tabs | Moved to Moments and Stats; timeline, comparisons and shot map remain available. Stats adds sortable players and xG timeline. | Retain the focused destinations and navigation. |
| Player ratings and votes in Live ratings tab | Rating and vote moved to a player sheet opened from Stats > Players; the fan-average column was absent until a vote existed. | Restore visible fan average and direct voting in the Players table, while retaining player sheets. Refresh ratings after a vote. Coach view continues to omit fan voting. |
| Fan polls | Remain on Live, hidden when coach view is on. | Keep persona-specific presentation. |
| Catch-up, recap, pundit and narration | Moved from Live sidebar to global actions, Moments, command palette and stream settings. | Keep these access paths. |
| Replay speed, pause, restart and past matches | Moved to stream settings; restart gains confirmation. An Explore tile was developer-only and incorrectly opened the agent drawer. | Make the replay tile available and open stream settings, which contains playback and past matches. |
| Profile club, style, language, player and metric | Available from profile menu; player and metric are under More and hidden in coach view. Follow from player sheet remains. | Keep context-specific coach view; document the location. |
| Coach ranked recommendations, confidence, evidence, trade-off and key numbers | Remain in Coach > Overview, with evidence and trade-off behind “Why”; new lineup, stats, tactics and report tabs. Refresh button was only visible while already fetching. | Keep progressive disclosure and make refresh available. Show actual fact-check status instead of an unconditional checked label. Update cached content on same-rank refresh; scope cached briefs and queries to the current run so a prior match or team is not briefly presented as the current one. |
| Insight provenance and claim details | Present in a details sheet, but status was not visible on moment cards; corrections were treated as ordinary rows. | Surface provenance on prominent cards and promote corrections to cards and Live highlights. |

Additional data presentation fixes: Coach and its report now use the engine's `half_time` status rather than labelling minutes 45–60 as half time. Live no longer displays a fabricated 0–100 possession split when possession is unavailable. Explore's replay preview no longer invents match scores.

The design still depends on a running match feed and API for live data, votes, replay and coach brief. Recorded video and archive availability depend on backend data. A code audit alone cannot establish end-to-end service behavior or visual quality; those require the running services and browser checks.

The second review pass moves Broadcast controls away from the score on mobile and tablet, with top-right placement on wide screens, presents coach actions before the supporting chart, and places Live actions in the document flow. Coach provenance now explains the actual source. When the backend supplies `verification_scope`, the relevant evidence, coach, catch-up and pundit views expose that scope; earlier responses without the additive field still work.
