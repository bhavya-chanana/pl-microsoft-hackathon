# Football Intelligence backend (`/api/fi`)

Sensor-first match data behind the Watch, Stats and player pages. Code: `backend/src/matchlens/fi/`.

## Run

```bash
npm run dev:offline        # backend :8765 + frontend :3000
```

On startup the backend opens `backend/.data/fi.sqlite` and replays the demo match through the ingest functions
(about 2 s). Delete that file to start from scratch. Sign in with any new username: the first sign-in creates
the account (PBKDF2-hashed password, HttpOnly session cookie). Set `MATCHLENS_AUTH_SECRET` in production; locally a
random secret is kept in `backend/.data/session_secret`.

## Flow

```
devices / simulator ─► /ingest/{match}/vest|ball|vision|operator|commentary ─► fusion ─► event ledger (append-only revisions)
                                         └─► vest frames ─► 15 s physical snapshots
browser ─► Next proxy /api/backend/fi/... ─► as-of reads (playhead-filtered)
```

- **Ball sensor** line cross ⇒ goal *provisional*; **video AI** restart ⇒ *confirmed*; VAR observation ⇒ *overturned*.
- **Vests** (1 Hz in the demo) ⇒ distance, high-speed running, sprints, top speed, heart rate, heatmap, line height.
- **Ball + vest fusion** ⇒ passes, touches, duels, recoveries, possession, attacking zones, shots and ball speed.
- **Video AI** ⇒ fouls, cards, offsides, subs, corners, free kicks.
- Commentary is a mock LLM feed keyed by event id; until it is available an event shows as `analysis: pending`.

## Reads (signed-in users)

| Route | Returns |
|---|---|
| `GET /matches?spoilers=` | match list; finished scores only with spoilers on |
| `GET /matches/{id}` | match + roster (no substitution times) |
| `GET /matches/{id}/view?playhead_ms&mode` | events, score, pulse, chips as of the playhead |
| `GET /matches/{id}/stats?playhead_ms&mode&period` | FotMob-style sections, shots, zones, player rows (last 30 s snapshot ≤ playhead) |
| `GET /matches/{id}/players/{pid}?playhead_ms&mode` | player stats, heatmap, moments, fantasy points |

`mode=full_review` is accepted only for finished matches. Nothing returned is later than the playhead.

## Ingest (devices; not exposed through the browser proxy)

`POST /ingest/{match}/vest|ball|vision|operator|commentary|clock-sync`, with an `Idempotency-Key` header
(repeats return `duplicate: true`). Set `MATCHLENS_INGEST_TOKEN` to require `X-Ingest-Token`.
Shapes are in `fi/models.py`; real hardware posts exactly what the simulator posts.

## Known gaps

- The server trusts the client's playhead for live matches (it caps at match length, not at the live edge).
- Demo vests run at 1 Hz, not 10 Hz; frames must arrive in time order for snapshots to roll forward.
- No vision LLM is called yet: `vision` observations come from the scripted story.
