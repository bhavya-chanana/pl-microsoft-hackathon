# API audit

Run against a private backend on port 8771 (`MATCHLENS_LLM=offline`, hero cassette, Cosmos emulator database
`matchlens_audit` seeded with the 12 season archives). Every route was called with a realistic request and with
bad input, in three states: **live** (hero match at minute ~10), **finished** (hero at full time) and
**archived** (season run DWO v BVD reopened from the store). About 75 calls per state. The route table is the
FastAPI OpenAPI schema (39 HTTP operations) plus `WS /ws` and the mounted `/mcp`.

All paths below are under `/api` (the frontend reaches them at `/api/backend/...`).

## Summary

| | |
|---|---|
| HTTP operations | 39 (+ `WS /ws`, `/mcp`) |
| Bugs found | 3 (all fixed, see end) |
| 5xx seen before fixes | 1: `GET /timeline?type=bogus` -> 500 |
| Slowest call | `GET /dataset` 0.5 s, 1.2 MB (a zip of the whole match) |
| Largest payloads | `GET /match` 370 KB (archived), rollouts 70 KB, overlays 52 KB, signals/pulse/timeline ~28 KB |
| Everything else | under 40 ms; most under 2 ms. `POST /ask`, `/catchup`, `/recap`, `/coach` 4-10 ms (offline templates) |

## The four requests the user flagged

| Request | On 8770 (loaded run: archived DWO v BVD) | Why |
|---|---|---|
| `GET /teams/NBR` | 404 `unknown team`, 0.9 ms, 25 B | **Expected.** `NBR`/`HBC` are the hero match's teams; the loaded match is DWO v BVD. The frontend still holds `clubId=NBR` from the hero match (profile store) and an old match meta. |
| `GET /teams/HBC` | 404 `unknown team`, 0.7 ms, 25 B | Same. |
| `GET /insights?mode=casual&language=en&club_id=NBR&limit=60` | 200, 1.2 ms, 10.5 KB | Worked, but `club_id=NBR` was silently meaningless for this match. Now ignored on purpose (fix B3). Casual mode hides priority >= 4 insights, which is why it is smaller than analyst. |
| `GET /insights?mode=analyst&language=en&limit=60` | 200, 1.0 ms, 25 KB | Fine. |

On 8771 with the hero match loaded, `/teams/NBR` and `/teams/HBC` return 200 (0.3 ms, 0.6-1.1 KB). Nothing is slow
or odd about these four; the 404s come from stale frontend state. The fix belongs in the frontend (reset the
club when the loaded match changes).

## Routes

Status columns list every status seen in that state. "-" means identical to the column before.

| Method | Path | live | finished | archived | Non-2xx: reason | Class |
|---|---|---|---|---|---|---|
| GET | /health | 200 | - | - | | |
| GET | /insights | 200, 422 | - | - | 422: `limit` > 200, or `mode` not casual/analyst/player_focus | expected |
| GET | /insights/{id} | 200, 404 | - | - | 404 unknown insight | expected |
| GET | /overlays | 200 | - | - | | |
| GET | /catchup | 200, 422 | - | - | 422: `minutes` not 5, 15 or 30 | expected |
| GET | /recap | 200 | - | - | | |
| GET | /coach | 200, 404 | - | - | 404 `team_id` is not one of the two teams (incl. NBR on a DWO v BVD match) | expected |
| GET | /speech | 503 | - | - | Azure Speech not configured (`speech: null` in /health); the browser falls back to its own voices | config |
| GET | /polls | 200 | - | - | | |
| POST | /polls/{id}/vote | 200, 404 | 404, 409 | 404, 409 | 404 unknown poll; 409 `this poll has closed` after full time and on archived runs; 422 for unknown option / malformed voter id | expected |
| GET | /ratings | 200 | - | - | | |
| POST | /ratings | 200, 404, 422 | - | - | 404 unknown player; 422 score outside 1-10 or bad voter id | expected |
| GET | /agents/decisions | 200 | - | - | | |
| POST | /replay | 200 | - | - | 422: speed <= 0, start_minute > 100, and (after fix B2) seed outside 0..1,000,000 | expected |
| POST | /replay/restart | 200, 404 | - | - | **New.** 404 `no match to restart` | expected |
| GET | /feeds | 200 `[]` | - | - | no recorded feeds on this machine (`data/local/feeds` absent) | config |
| POST | /replay/feed | 404 | - | - | 404 `no such recorded match` (name validated by regex; `../x` is 422) | expected |
| GET | /runs | 200 | - | - | | |
| POST | /runs/{id}/open | 404 | - | - | 404 `no such run`; 200 for real ids (hero run resumes, season run opens archived) | expected |
| POST | /replay/control | 200, 422 | - | - | 422 speed <= 0. On an archived run it returns 200 but does nothing (nothing is playing). | expected |
| GET | /match | 200 | - | - | | |
| GET | /state | 200 | - | - | | |
| GET | /timeline | 200, **500** | - | - | `type=bogus` crashed with ValueError. Now 422. | **bug B1, fixed** |
| GET | /players/{id} | 200, 404 | - | - | 404 unknown player | expected |
| GET | /teams/{id} | 200 | - | 200, 404 | 404 id not in the loaded match | expected (see flagged table) |
| GET | /signals | 200 | - | - | | |
| GET | /moments/{id} | 200, 404 | - | - | 404 unknown moment | expected |
| GET | /pulse | 200 | - | - | | |
| POST | /ask | 200, 422 | - | - | 422 question shorter than 2 or longer than 200 chars; 429 when more than 40 asks a minute (not hit) | expected |
| GET | /scenarios | 200 | - | - | | |
| POST | /scenarios/draft | **503**, 422 | - | - | 503 was `needs the live models` when offline: **no longer** (offline now returns the closest preset, `source: "template"`). 422: prompt shorter than 8 chars. Online: 504 timeout, 502 no answer, 422 unplayable. | config, changed (task 3) |
| POST | /scenarios/kickoff | 404, 422 | - | - | 404 unknown preset; 422 neither preset nor storyline, an unplayable storyline, or speed <= 0 | expected |
| GET | /scenarios/check | 200 | - | 404 | 404 `not played from a storyline` for runs reopened from the archive | expected |
| GET | /dataset | 200 | - | - | 0.5 s, 1.2 MB after full time: builds a zip of events plus gzip tracking | expected |
| POST | /tactics/boards | 202, 404, 422 | - | - | 404 unknown moment; 422 bad body or unknown team; 429 when more than 10 requests a minute (not hit) | expected |
| GET | /tactics/boards/{id} | 200, 404 | - | - | 404 unknown board (boards live in memory, only the last 20 are kept) | expected |
| GET | /tactics/boards/{id}/rollouts/{tid} | 200, 404, 422 | - | - | 404 unknown board; 409 while the board is not ready (not hit); 422 `variant` not typical/best/worst | expected |
| POST | /tactics/simulate | 200, 422 | - | - | 422 bad body | expected |
| GET | /kb/teams, /kb/teams/{id}, /kb/players/{id}, /kb/matches, /kb/search | 404 | - | - | `knowledge base not configured`: `MATCHLENS_KB_DIR` is not set | config |
| WS | /ws | accepts, sends `hello` snapshot, pings every second | | | closes with `feed_status: no_match` when nothing is loaded | |
| any | (match routes) | | | | 404 `no active match` when nothing is loaded. Not reachable on 8771 (autostart); read from `_session()`. | expected |

Notes:

- Replay behaviour after full time is fine: everything read-only keeps working, polls close (409), the dataset gets
  bigger. No route fails only because the match finished.
- On an archived run `POST /tactics/boards` with `source.kind=live` still works: it freezes the last tracking frame.
- `POST /ask` echoes the whole submitted text in its 422 body when it is too long (3 KB for a 3 KB question). Harmless.

## Bugs found and fixed

| # | Bug | Fix | Test |
|---|---|---|---|
| B1 | `GET /timeline?type=bogus` -> 500 (uncaught `ValueError`) | `api/app.py` `timeline`: 422 listing the valid types | `test_api_audit.py::test_timeline_unknown_event_type_is_422_not_500` |
| B2 | `POST /replay` accepted any `seed` (negative, huge); kickoff already bounded it | `ReplayRequest.seed` is `0..1_000_000` | `test_replay_seed_is_bounded` |
| B3 | A club or player from another match (the browser keeps its profile across matches) was passed into the feed, catch-up, recap, ask and speech. With `mode=player_focus` a stale `player_id` hid every insight except the top priority, and it split the fan-output cache | `api/app.py` `_profile`: ids not in the loaded match are dropped | `test_ids_from_another_match_are_ignored` |

Changes from the brief that came out of the audit (tasks 3 and 4):

- **Restart ignored what was loaded.** `POST /replay {seed: 7}` always rebuilt the hero match. New
  `POST /replay/restart {speed?, start_minute?}` replays whatever is loaded (hero, a Scenario Lab storyline, a
  recorded feed, or an archived run from its stored events and frames). `runtime/manager.py` keeps a `Recipe` for
  the loaded match. The recipe is memory only: after a backend restart there is nothing to restart until a match is
  started or reopened (404).
- **Scenario Lab offline.** `POST /scenarios/draft` returned 503 offline. It now returns the closest preset by
  keyword with `source: "template"`. Writing a new match still needs the live model.
- **Tactics custom brief offline.** `POST /tactics/boards` never failed offline, but it ignored the brief and
  showed at most 3 cards. Offline, the brief now picks which built-in recipes are shown (`prompt_matched`,
  `prompt_note` on the board).
