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

## Real-footage live match (SportsMOT)

`smot-live` replays one SportsMOT football clip (CC BY-NC 4.0, research use) as a live match on the server clock.

1. Stream one football clip from `MCG-NJU/SportsMOT` into `backend/.data/sportsmot/clip/` (frames, `gt/gt.txt`).
2. `ffmpeg -framerate 25 -i <clip>/img1/%06d.jpg -c:v libx264 -pix_fmt yuv420p frontend/public/video/smot-live.mp4`
3. `uv run --no-project --with numpy --with pillow python scripts/sportsmot_to_live.py <clip dir>` writes
   `backend/.data/sportsmot/live_match.json`: pitch positions estimated from the boxes (homography on the halfway
   line and centre circle, camera pan removed), teams from shirt colour, referees dropped.
4. On startup `fi/live.py` posts those frames through vest ingest second by second, raises a sprint event the first
   time a player passes 25.2 km/h, and restarts the clip 25 s after it has been watched. Reads are capped at the
   server's live edge; the match uses an 8 s delay and 5 s stats snapshots.

Positions and speeds are video estimates, not vest data. SportsMOT has no ball or event labels, so ball-derived stats
(passes, shots, possession) stay at zero for this match. Team names are kit colours; no real club or player names.

## Known gaps

- The server trusts the client's playhead for live matches (it caps at match length, not at the live edge).
- Demo vests run at 1 Hz, not 10 Hz; frames must arrive in time order for snapshots to roll forward.
- No vision LLM is called yet: `vision` observations come from the scripted story.
