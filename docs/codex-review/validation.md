# Final validation — 27 October 2026

Branch: `ui-redesign-codex`, based on `ui-redesign` at `afbea45`. Isolated checkout: `/private/tmp/matchlens-ui-redesign-codex`. The original vision checkout and the source redesign worktree were not edited.

## Automated checks

- Backend full suite, using the existing rich Python environment and local Cosmos emulator: **578 passed, 3 skipped**, 126.64 seconds. One existing FastAPI/httpx deprecation warning remains.
- Frontend lint and typecheck: passed after the final controls change.
- Native Turbopack production build: passed, including TypeScript and all 13 generated pages. A clean retry outside the filesystem/network sandbox was needed for Google Fonts and the CSS worker's loopback port. No source workaround or font substitution was needed.
- `git diff --check`: passed.

The full backend command used from `backend/` was:

```sh
PYTHONPATH=src MATCHLENS_COSMOS_TEST_ENDPOINT=http://127.0.0.1:8081 /Users/bhavyachanana/code/pl-microsoft-hackathon/.claude/worktrees/tactics-brain/backend/.venv/bin/python -m pytest -q
```

For a fresh checkout, `npm run setup` and the `leaguedata` dependency group provide the required dependencies; the existing environment above was reused to avoid modifying other worktrees.

## Synthetic generation and real Cosmos persistence

The read-only source was:

```text
/Users/bhavyachanana/code/pl-microsoft-hackathon/.claude/worktrees/tactics-brain/backend/data/local/leaguedata/curated/v1
```

Generation wrote only ignored `backend/data/generated/season-v2/`. It produced 12 fictional fixtures from a 1,752-match 2024–25 aggregate population, with explicit older-season validation priors. Repeated generation was byte-identical. The hero dataset and recorded cassette were preserved.

The final applied seed target is the local emulator at `http://127.0.0.1:8081`, database `matchlens_codex`. All **12 completion markers** were read back and verified:

| Seed-owned records | Count |
| --- | ---: |
| Events | 20,486 |
| Tracking frames | 69,018 |
| Signal revisions | 771 |
| Data-only insights | 767 |
| Timed overlays | 422 |

A repeated seed of the first actual stored archive verified successfully without changing any completion-marker etags or the active match pointer. Focused tests additionally cover partial failure/retry, unexpected records, corrupt marker counts, later fan activity, correction revisions and retractions. The importer does not use a memory fallback and requires an explicit emulator target. This evidence proves local Cosmos persistence, not Azure deployment.

The final first archive is `synthetic-v2-ml-season-20261027-01-cb846cf5548c`. Reopening it through the application's API rebuilt Axeville–Brindle at **2–2**, exposed all four goal insights, returned 24 Casual and 62 Analyst insights, and labelled all returned narrative provenance as data-only. Its Moments and Coach screens rendered in the production browser without exceptions. This checks the persisted archive through the actual app, beyond a store round trip.

The concrete local generation and seed commands, from `backend/`, are:

```sh
PYTHONPATH=src /Users/bhavyachanana/code/pl-microsoft-hackathon/.claude/worktrees/tactics-brain/backend/.venv/bin/python -m matchlens.dataset.season --source /Users/bhavyachanana/code/pl-microsoft-hackathon/.claude/worktrees/tactics-brain/backend/data/local/leaguedata/curated/v1 --out data/generated/season-v2
```

```sh
PYTHONPATH=src /Users/bhavyachanana/code/pl-microsoft-hackathon/.claude/worktrees/tactics-brain/backend/.venv/bin/python -m matchlens.store.seed --dataset data/generated/season-v2 --endpoint http://127.0.0.1:8081 --database matchlens_codex
```

```sh
PYTHONPATH=src /Users/bhavyachanana/code/pl-microsoft-hackathon/.claude/worktrees/tactics-brain/backend/.venv/bin/python -m matchlens.store.seed --dataset data/generated/season-v2 --endpoint http://127.0.0.1:8081 --database matchlens_codex --apply
```

## Production UI

The production frontend at `http://127.0.0.1:3030` uses the offline/cassette backend at `http://127.0.0.1:8770`. Responsive checks covered 390×844, 1024×1000 and 1440×1000:

- No Broadcast score/control rectangle overlap, horizontal document overflow or browser exceptions.
- Visible mobile full-time Coach report action before the graph, and inline Live actions.
- Broadcast visible in Explore and direct Live/search access; old `/broadcast` redirect works.
- Hero replay ends 3–2. Second-half kickoff precedes half-time in newest-first Highlights, including their elapsed-time tie.
- Zero early-stage overlays rescheduled near full time after a full replay warm-up. Delayed upgrades remain historical.

The earlier independent Sol review also exercised fan voting, catch-up, evidence details, moment replay, coach-mode feature visibility and full-time guards. The app was returned to the paused hero match after the archive check.

Screenshots and JSON evidence are intentionally ignored under `logs/final-review/`, including `production-ui.json`, `cosmos.json`, `archive-ui.json`, responsive Live/Coach/Broadcast captures and the synthetic archive screens.

## Limits

Online Azure model/Speech calls, deployment roles and latency, external OBS capture, PDF export and a detector-only video pipeline were not validated here. Historical source data, generated feeds and locally trained assets are not committed. The initial reports preserve findings at their reviewed snapshots; the final results above supersede issues subsequently fixed.
