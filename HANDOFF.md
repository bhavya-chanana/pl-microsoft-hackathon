# Matchlens: status and handoff (25 Oct 2026)

Working notes. **Not committed.** Do not commit FotMob details or real-league data to the repo, the demo or the video
(BRD §9.3); these notes exist only for local testing.

## 1. Where things stand

| Item | State |
|---|---|
| Simulator calibrated on real statistics (StatsBomb, 100 matches) | Done, committed (`f43a0e3`) |
| Demo re-record | **You:** `npm run record:demo` (the cassette is stale for the coach brief) |
| Coach persona (BRD §6.2) | Done and tested, **not committed** |
| `rea` MCP (reverse-engineer-everything) | Installed in global Claude config (`~/.claude.json`, user scope); available in new sessions |
| FotMob research | Partly done (section 3); the live-update check was paused |
| SoccerTrack v2 video | Blocked: accept the dataset terms on Hugging Face (still returns 403) |
| Voice narration, fan polls and ratings, video auto-eventing | Not started |

Checks at the pause: 62 backend tests pass (the Cosmos round trip is deselected); frontend typecheck and lint pass.

## 2. Coach persona (done, uncommitted)

**What it does.** It gives a ranked brief of at most three items for either side. Each item has:

- evidence;
- a recommendation;
- a trade-off;
- key numbers;
- a confidence of high, medium or low.

It works in English, Hindi and Spanish. The endpoint is `GET /api/coach?team_id=&language=`; the page is `/coach`, the
"Coach" tab.

**How it works.**

1. **Coordinator (code, no model).** In `agents/coach.py`, detectors read the stats engine over the last 15 minutes
   and score urgency. They cover:
   - protect the box;
   - momentum against;
   - blunt attack;
   - bypass the press;
   - card risk;
   - fresh legs;
   - extra man or man down;
   - chase the game or see it out;
   - their danger man;
   - steady.

   The top three are kept, and the confidence comes from the strength of the evidence. Game-state, card and
   substitution advice is skipped at full time.
2. **Coach agent (fast model).** It only words the items. It cannot re-rank them, change the confidence or add
   numbers.
3. **Fact-checker.** It checks the text against the engine. Anything unsupported is blocked, and the data-only
   template text is shown instead. On a live run, a brief claiming "Northbridge on top" was blocked because momentum
   was balanced.
4. Results are cached per run, minute and signal count (`fan_outputs`).

**Files.**

- New: `backend/src/matchlens/agents/coach.py`, `backend/tests/test_coach.py`, `frontend/src/app/coach/`,
  `frontend/src/features/coach/`.
- Modified: `agents/{pipeline,prompts,services,templates}.py`, `api/app.py`, frontend `contract.ts`, `copy.ts`,
  `routes.ts`, `server-config.ts`, `live/queries.ts`, `app-header.tsx`.

## 3. FotMob: what, where from, how

### Where FotMob's data comes from

FotMob aggregates several commercial providers and adds its own models on top. The provider tags were read from
its match API response for a Premier League match on 24 Oct 2026.

| Data | Source | Evidence |
|---|---|---|
| Live match data, xG, shot maps, possession | **Opta (Stats Perform)** | FotMob's May 2026 press release says this data is "powered in real-time by Opta"; players carry an `optaId`; the `superLiveUrl` uses Opta competition, season and match ids |
| Lineups | **Enetpulse** | `content.lineup.source: "enetpulse"`; an older Enetpulse case study describes an XML push feed to FotMob |
| Live text commentary | **GSM (Global Sports Media, now Stats Perform)** | URL `data.fotmob.com/webcl/ltc/gsm/<id>_en.json.gz`; `_gen` languages look machine-translated |
| Match id cross-reference | **Sportradar** | `srmatchid=sr:match:…` inside `superLiveUrl` |
| Player market values | **SciSports** | SciSports partnership announcement |
| Odds and the "Who will win?" poll | **Bet365** (odds) plus FotMob's own templates | `matchOdds?bettingProvider=Bet365_default`; `matchFacts.poll.oddspoll` |
| Player ratings, momentum, match insights | **FotMob's own models** | Ratings use "300+ data points" (formula not published); `momentum.main.debugTitle`; `data.fotmob.com/generated-match-insights/…` |
| Physical metrics (top speed, distance, sprints) | Tracking provider, not confirmed | Per player: `performance.topSpeed`, `totalDistanceCovered`; per team: `physical_metrics_*` |

### How the web app gets it

FotMob runs a Next.js site plus its own JSON API at `https://www.fotmob.com/api/data/…`. Static and generated files
come from `data.fotmob.com` and `pub.fotmob.com`.

| Endpoint | Gives |
|---|---|
| `matches?date=YYYYMMDD&timezone=…` | Every match that day, about 190 leagues, with live status and minute |
| `leagues?id=47` | League overview (47 = Premier League; LaLiga is 87) |
| `tltable?leagueId=47` | Table |
| `matchDetails?matchId=<id>` | **The main one**, about 320 KB; see the next table |
| `ltc?ltcUrl=…/ltc/gsm/<id>_en.json.gz` | Minute-by-minute text commentary |
| `heatmap/match/<id>/heatmaps` | Player heatmaps |
| `matchInsights?matchId=…` | FotMob's generated insights, many languages (Hindi included) |
| `matchOdds`, `vote`, `matchNews`, `tvlisting`, `audio-live-stream` | Odds, poll votes, news, TV, audio commentary |

Observations:

- A same-origin `fetch('/api/data/matchDetails?matchId=…')` from the open FotMob tab returned 200 without adding any
  header. Community libraries say server-side calls need the `x-mas` signing header that the web client generates.
  This is not verified outside the browser.
- **The live update mechanism is not yet confirmed.** No WebSocket or Server-Sent Events connection showed up in
  resource timing on a live MLS match. Polling of `matchDetails` is the likely mechanism. **Next step:** the
  fetch/XHR/WebSocket logger was being attached to live match `5071444` to measure the poll interval.

### What `matchDetails` contains (fields useful to us)

| Path | Contents |
|---|---|
| `general` | Ids, teams, league, kickoff, `started`, `finished`, `coverageLevel` (e.g. `"xG"`) |
| `header.status` | Score string, live time, halves, red cards |
| `content.matchFacts.events.events[]` | Goals (scorer, assister, score, embedded shot), cards, substitutions, added time, halves, with minute |
| `content.shotmap.shots[]` | Every shot: `x`, `y` (appear to be metres on 105 × 68, attacking right), minute, period, `expectedGoals`, `expectedGoalsOnTarget`, on target, blocked (+ block location), goal mouth y/z, `shotType` (foot or head), `situation` (open play, corner, set piece, …), keeper id |
| `content.stats.Periods.{All,FirstHalf,SecondHalf}` | Team stats: possession, xG (open play, set play, non-penalty, on target), shots by type, passes (own and opposition half), long balls, crosses, throws, offsides, tackles, interceptions, blocks, clearances, saves, duels, aerials, dribbles, cards, fouls, **physical** (distance, running, sprinting, sprints) |
| `content.playerStats[playerId]` | Per player: FotMob rating, minutes, goals, assists, xA, touches (and in the box), dribbles, passes into the final third, line-breaking passes, long balls, defensive actions, duels, fouls, top speed, distance, sprints; plus `optaId` |
| `content.lineup` | Formation, starters (shirt number, position, layout x/y), bench, ratings, top speed and distance with ranks |
| `content.momentum.main.data[]` | Momentum per minute (`{minute, value}`, + home / − away) |
| `content.attackingZones` | Left, centre and right attack share, per half |
| `content.matchFacts.playerOfTheMatch`, `topPlayers`, `poll`, `insights`, `infoBox` | Player of the match, polls, generated facts, venue, referee, attendance |

**Not available:** pass-by-pass events with locations, tracking frames (player and ball positions), and per-pass
pressure. FotMob has a timeline, a shot map and totals, not an event stream.

### Terms

FotMob's [terms](https://www.fotmob.com/tos.txt) forbid scraping and bulk automated retrieval without written
consent. You chose to use it anyway, for local testing only. To keep the risk low:

- low volume, one match at a time;
- poll no faster than the app does;
- cache responses locally (gitignored);
- never redistribute, commit, demo or record the data.

## 4. How Matchlens can use FotMob

1. **Live companion test feed (real matches).** Write a `FotMobLiveAdapter` in `ingest/`, local-only, enabled by an
   env flag:
   - poll `matchDetails` every 20 to 30 seconds while `started && !finished`;
   - diff against the last snapshot;
   - emit provider-neutral events.

   | FotMob | Matchlens `EventType` |
   |---|---|
   | `events[type=Goal]` + `shotmapEvent` | `SHOT` (outcome `GOAL`, xg, location, body part) + `GOAL` |
   | `shotmap.shots[]` other | `SHOT` (saved, off target, blocked) |
   | `events[type=Card]` | `CARD` (yellow, red) |
   | `events[type=Substitution]` | `SUBSTITUTION` |
   | `events[type=Half]`, `AddedTime` | period start and end, added time |
   | `lineup` | `MatchMeta` (teams, players, shirt numbers, formation) |

   Totals (possession, passes, tackles and so on) arrive only as running counts. Store them as a separate "provider
   stats" snapshot rather than inventing events. Use them for fact-checker context and as the source for the stats
   panels.
2. **Old matches.** The same adapter in one-shot mode loads a finished `matchDetails` and replays its timeline
   through the engine and agents. That gives recaps, the coach brief and catch-up for any past match.
3. **Ground truth for the video pipeline.** On a real match, compare what our vision pipeline derives (shots,
   distances, top speeds, possession) with FotMob's totals and shot map, which come from Opta and the tracking
   provider. This gives an accuracy report.
4. **Personas and features:**
   - FotMob rating against our own data-driven rating, and the fan average (fan ratings);
   - momentum curve against our momentum;
   - `poll` templates as inspiration for our own fan polls, not copied;
   - player of the match.
5. **What FotMob cannot drive:** pass quality, pressure, passing networks, speeds over time, and the live 2D pitch.
   Those need tracking or video, so they come from the video pipeline (SoccerTrack v2 now, a live feed later).

## 5. What to do next (in order)

1. **You:** run `npm run record:demo`, then ask me to commit the coach work.
2. **You:** accept the SoccerTrack v2 terms at <https://huggingface.co/datasets/atomscott/soccertrack-v2>.
3. **Me, FotMob:**
   - finish the live check (poll interval, whether `x-mas` is needed outside the browser);
   - build the local-only `FotMobLiveAdapter` (live and one-shot), gitignored cache, env flag off by default;
   - write tests against a **synthetic** fixture shaped like `matchDetails`, never a real response.
4. **Me, voice narration:**
   - Azure AI Speech TTS with Entra ID;
   - a browser `speechSynthesis` fallback;
   - Bicep for the Speech resource and its role assignment;
   - a "Listen" toggle (en, hi, es voices).
5. **Me, fan polls and player ratings:**
   - a `fan_votes` Cosmos container (Bicep, test sync);
   - anonymous voter ids;
   - WebSocket tallies;
   - a data-driven rating against the fan average;
   - en, hi and es text.
6. **Me, video auto-eventing:** download the first half of SoccerTrack v2 match 128057 (about 6.2 GB, files listed in
   section 6), then build:
   - detection (RT-DETR, Apache-2.0) and tracking (ByteTrack via `supervision`, MIT);
   - pitch homography and a kit-colour team split;
   - ball possession, which yields passes, shots and speeds;
   - the feed adapter into the engine;
   - a video page synced with the 2D pitch;
   - an evaluation against the SoccerTrack GSR and BAS ground truth.

   It goes in an optional `vision` dependency group and runs on Apple MPS locally.
7. **Me, match archive:** reopen any stored run (`/api/runs`) and show all its stats and outputs.
8. **Me, docs:**
   - BRD: move the §6.2 items into scope and amend §6.3 for SoccerTrack (real footage, CC BY 4.0);
   - README, SCHEMA and DATABASE docs;
   - Bicep.

## 6. Still open, blocked, or decided

**Blocked**
- SoccerTrack download, until the terms are accepted. Planned files for match 128057:
  - `videos/…_panorama_1st_half.mp4`, about 3.4 GB;
  - `gsr/128057_1st.json`, about 2.7 GB;
  - BAS JSON, ball `npz`, and `raw/` homography, keypoints and intrinsics.

**Unverified**
- FotMob live update interval and transport.
- Whether `x-mas` is needed for server-side calls.
- The source of the physical metrics.

**Deployment question**
- The video pipeline needs a GPU when deployed: Container Apps serverless GPU, or a laptop pushes the derived feed.

**Decided**
- FotMob is for local testing only.
- StatsBomb stays the calibration source and is credited ("Data provided by StatsBomb").
- Commit straight to `main`, only when asked.
- Avoid AGPL vision libraries such as ultralytics.

## Sources

- [FotMob × Soccer Aid 2026 (Opta-powered data)](https://www.advanced-television.com/2026/05/14/soccer-aid-for-unicef-selects-fotmob-as-live-score-partner/)
- [Enetpulse case study: FotMob](https://enetpulse.com/client-case-fotmob/)
- [SciSports partnership](https://www.scisports.com/fotmob-partners-with-scisports-to-power-new-player-value-insights/)
- [Player rating systems compared (WhoScored, FotMob, Sofascore)](https://lida.sport-iat.de/dfb/Record/4092713)
- [Opta / Stats Perform](https://en.wikipedia.org/wiki/Opta_Sports)
- [FotMob terms](https://www.fotmob.com/tos.txt)
- [Community `x-mas` note (laliga-api)](https://github.com/jaschrs/laliga-api), [fotmob npm wrapper](https://cdn.jsdelivr.net/npm/fotmob@2.4.1/README.md)
