# Fan personalization: opportunities and delivery order

## Opportunity ranking

| Priority | Opportunity | Why it matters | Status |
| --- | --- | --- | --- |
| P0 | Let a fan choose a club, player, viewing mode, language and headline metric | Establishes the signals the product can personalize against | Existing profile controls and browser persistence are retained |
| P1 | Make favorite-club and favorite-player moments easier to find without losing live context | Turns the existing profile inputs and relevance score into a visibly personal feed | Implemented: keep the latest 50 insights, then rank those by relevance and recency |
| P1 | Give fans a matchday action, not just a read-only feed | Voting and rating create a lightweight reason to return and express an opinion | Implemented as match-scoped, browser-local poll votes and player ratings |
| P2 | Aggregate polls and player ratings across fans | Makes the vote representative and enables public averages or trends | Not implemented: requires a shared backend, fan/session identity, validation and abuse controls |
| P3 | Remember multiple clubs/players and tailor alerts, recaps and match discovery | Extends personalization across matches and beyond the current session | Future opportunity; needs product choices and notification infrastructure |

## Current personalization flow

1. `ProfilePanel` lets the fan set one favorite club, one favorite player, a mode, language and headline metric. The Zustand profile is persisted in the browser.
2. The web client sends those preferences with feed/catch-up/recap requests (`mode`, `language`, `club_id`, `player_id`, `metric`).
3. The backend selects the matching language/mode variant, adds a club-specific angle when available, boosts moments involving the favorite player, and filters lower-priority moments in casual/player-focus modes.
4. The feed keeps the most recent 50 candidate insights, then sorts by relevance and recency. The UI marks favorite-player moments and renders the club angle separately from the verified match narrative.
5. The **Fan zone** adds a match-scoped poll (“which side made the stronger case?”) and a 1–5 player rating. A chosen favorite player is the initial rating target; the fan can choose anyone in either match squad.

## Engagement storage and limits

Poll choice and ratings are saved under `matchlens.engagement.v1.<match_id>` in that browser's `localStorage`. A fan can change their poll vote and rate multiple players. No vote totals or average ratings are shown because this version has no shared voting API; the UI says explicitly that the actions stay on this device. Clearing site data removes them, and they do not follow the fan to another device.

For a production/shared poll, add authenticated or privacy-preserving fan/session identity, server-side match/player validation, one-vote rules, rate limiting, durable match-scoped storage, moderation/retention policy and aggregate endpoints. Only then should the UI display community totals or average player ratings. The local interactions are a functional prototype, not a production voting system.
