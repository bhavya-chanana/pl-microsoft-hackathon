# Google sign-in and fan accounts

Matchlens uses Google Identity Services to authenticate a fan, verifies the Google ID token in FastAPI, then sets a seven-day signed, HttpOnly, SameSite=Lax session cookie. The browser never stores an app token. The backend session secret is server-only.

## Local setup

1. In Google Cloud Console, configure the OAuth consent screen and create an OAuth client of type **Web application**. Add `http://localhost:3000` as an authorized JavaScript origin.
2. Put the same client ID in `frontend/.env.local` as `NEXT_PUBLIC_GOOGLE_CLIENT_ID` and `backend/.env` as `GOOGLE_CLIENT_ID`.
3. Generate a random session secret (for example, `openssl rand -hex 32`) and put it in `backend/.env` as `MATCHLENS_AUTH_SECRET`. Do not put it in a `NEXT_PUBLIC_*` variable or commit either local env file.
4. Restart `npm run dev:offline` (or the normal dev command). Google sign-in appears in the header. Guests can still personalize locally; signed-in users have preferences, poll votes, and player ratings saved server-side.

The client ID is public by design. The server checks the signed token's issuer, signature, expiry, verified email, and audience against that client ID. The app then uses its own HMAC-signed session cookie; it does not persist Google access/refresh tokens.

## Data and deployment

Fan profiles are stored in `fan_profiles`; match-scoped poll votes and player ratings are stored in `fan_poll_votes` and `fan_player_ratings`. A user can update their own vote or rating, with one record per user/match (and per player for ratings). Match totals and averages are computed from those records. Cosmos DB is needed for persistence across backend restarts and instances. The in-memory local store is only a development/demo fallback.

For deployment, use HTTPS, set `MATCHLENS_COOKIE_SECURE=1`, use a unique high-entropy `MATCHLENS_AUTH_SECRET`, add the deployed frontend origin to the Google OAuth client, and configure a durable Cosmos DB account with least-privilege access. Google sign-in will stay disabled until both `GOOGLE_CLIENT_ID` and `MATCHLENS_AUTH_SECRET` are set. Account deletion/export, moderation/anti-abuse controls, rate limiting, and a formal retention policy remain production work.
