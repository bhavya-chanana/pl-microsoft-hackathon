# Backend review and implementation plan

Reviewed 27 October 2026 against `ui-redesign` / `afbea4559456fec0e3afdb65d4318a32088ad76f` in the isolated `ui-redesign-codex` worktree. This review changes documentation only. Runtime findings below were reproduced with the current code, separately from the existing test suite.

## Decision

The core product works: fictional match generation feeds a deterministic football engine, which publishes immediate data insights and upgrades selected moments through an Agent Framework workflow. Cosmos persistence, replay history, coach/pundit services and the Tactics Board are implemented. The principal gap is integration and correctness at boundaries, not the absence of an AI pipeline.

Fix the fact-check acceptance rules, shutdown durability and replay task lifecycle first. Then add a bounded, reproducible synthetic season and a strict idempotent seed command. Keep the existing real league corpus local and read-only; use only population aggregates to calibrate new fictional records. Connecting the real-name KB directly to the public synthetic experience would conflict with the user's requested data boundary.

## What is actually connected

| Pipeline | Implemented path and evidence | Current limit |
|---|---|---|
| Synthetic live match | `runtime/manager.py:73-89` calls `generate_match`; `ingest/adapters.py:80-93` replays events and tracking; `runtime/session.py:95-139` applies them to `MatchEngine`, records them and publishes socket messages. | One active match per process. Every replay gets a new timestamp/random run id. No season/catalog orchestrator. |
| Deterministic interpretation | `interpret/engine.py` maintains score, team/player stats, correction revisions and signals; `runtime/session.py:108-129` publishes state and signals before agent completion. | Metrics are simulator/event definitions, not a claim that real tracking or licensed live data are available. |
| Live agent explanation | `agents/pipeline.py:83-104,338-351,441-459`: rule-based coordinator chooses full/light/data-only/skip, publishes data first, then tactical+narrator → deterministic checker/optional rewrite → producer+personaliser → publish. `pipeline.py:507-540` imposes queue/timeout/supersession controls. | The coordinator is deterministic policy, not a model call. A full job can make five model calls plus one rewrite. Verification holes are detailed below. |
| Overlay and fan personalization | `pipeline.py:308-335,381-391,473-491`; `agents/services.py:177-226`. All three modes (`casual`, `analyst`, `player_focus`) and languages (`en`, `hi`, `es`) are represented. | A verified variant currently means number/claim checks passed, not semantic validation of every sentence or translation. |
| Catch-up, recap, coach, pundit | `/api/catchup`, `/api/recap`, `/api/coach`, `/api/ask`; `agents/services.py:59-103,105-173`; `agents/pundit.py:502-542`. Cached fan outputs and model-run telemetry go to the run store. | No single-flight protection around identical cache misses; concurrent viewers can duplicate calls. Status/model source labels are coarse. |
| Recorded AI demo | `agents/llm.py:214-240` wraps clients with `CassetteChatClient`; `agents/cassette.py:71-98,160-185` keys and replays answers. | A cassette is recorded agent output, not an alternate match-data pipeline. Offline misses fail into data-only fallbacks; when the cassette is disabled for another seed/storyline, offline templates answer instead. |
| Cosmos run history | `api/app.py:84-107` attaches store/writer; `store/repository.py:137-206` records metadata, raw events/frames, signals, insights, overlays, decisions, agent runs, fan outputs/votes. | No schema migration ledger, strict seed importer, catalog, or persistence of Tactics Board jobs/results. It is a run store, not the league warehouse. |
| Resume and archived runs | `runtime/manager.py:106-184` rebuilds engine from stored feed and restores outputs; `agents/pipeline.py:417-439` restores editorial state and pending work. | Only the current default storyline can be regenerated. Custom scenarios and provider/video feeds reopen as archives. Generator version is not pinned in stored runs. |
| League warehouse and KB | `leaguedata/__main__.py:37-44` wires fetch/build/curate/reason/playbooks/export; `leaguedata/kb.py:559-610` exports JSON. `/api/kb/*` uses `MATCHLENS_KB_DIR` (`api/kb_routes.py:22-26`). | Separate local batch pipeline. The API returns 404 when unconfigured. The KB contains real identities/match summaries and should not be publicly seeded under this request. |
| Tactics reasoning and simulation | `tactics/board.py:208-266,409-487` freezes a frame and creates an in-memory board. Personas propose validated structured intents; paired Monte Carlo rollouts compare each idea with the baseline; a constrained verdict reports the result. | Boards are an LRU of 20 in memory (`board.py:11-13,65,409-415`), not durable Cosmos records. Live match continues. Explicit limitations on simulated probabilities are appropriate and already present. |
| Tactical learned model | `tacticsml/registry.py:17-35` loads exported numpy weights when available, otherwise baseline. `tacticsml/learned.py:49-69,88-123,181-195` checks manifests/hashes. Training/export/evaluation commands exist. | No weights ship in this checkout/image. Existing ignored weights are in a different worktree; fallback to baseline is expected here. Learned evaluation is distinct from LLM reasoning. |
| Optional video | `vision/__main__.py:41-66` produces a local feed; `/api/replay/feed` (`api/app.py:339-347`) accepts it through the same runtime. | The SoccerTrack command uses supplied labelled tracks/ball/projector; `--detect` separately benchmarks a detector (`vision/__main__.py:69-91`). Do not describe that path as an end-to-end raw broadcast detector. It emits fictional identities, but derived real events remain local. |

These pipelines have different jobs: the simulator creates the evidence; the engine computes facts; LLMs explain those facts; the cassette reproduces an earlier LLM response; the league ETL builds optional historical knowledge; tactical training fits a local numerical evaluator; video is another ingest experiment. They should share explicit contracts/provenance, not be collapsed into one catch-all workflow.

## Critical defects with reproduction and fixes

### P0: “verified” accepts malformed and unverifiable claims

Evidence:

- `agents/contracts.py:81-94`: required-by-kind fields are only guarded in `to_claim`; an incomplete structured claim returns `None` and disappears.
- `agents/factchecker.py:82-88`: only `CONTRADICTED` results become problems. `UNVERIFIABLE` results are accepted.
- `agents/factchecker.py:34-38,89-102`: every number 0–11 and 45/90/100 is globally allowed, with no binding to subject/stat. Drafts with no claims can get `verified` for false small-number statements. Producer and personalized variants are explicitly checked with `claims=[]` (`pipeline.py:279-283,311-319`).

Temporary probe on a fresh, scoreless `MatchEngine(default_match_meta())`:

```text
score claim, home=None/away=None, text="The score is 3-2" → verified, results=[], problems=[]
team_stat(NBR, trophies, 3), text="Northbridge have won 3 cups" → verified, result=unverifiable
text="Kenji has scored 3 goals.", claims=[] → verified, problems=[]
```

Fix `AgentClaim` validation so each kind requires its corresponding fields; malformed claims must trigger a visible fallback. Treat unverifiable factual claims as problems. Preserve `data_only` for trusted deterministic templates. Add a truthful check scope/provenance field for prose that only passed number checks; do not claim semantic fact verification when no supported claims exist. For scores, attribution and key stat output, require typed supported claims or direct deterministic rendering. Keep minimal numeric formatting checks as a secondary filter; globally deleting all small-number allowances without accounting for formations/halves would break legitimate text.

Acceptance: the three examples above cannot be `verified`; unknown subjects/stats and wrong low-number scores/stats fail; legitimate formations, minute labels and deterministic fallback text still render. A current draft with supported typed claims still passes, and the existing correction replay continues to retract wrong scorer output. Add focused tests, including producer/personalizer output, rather than a test that only validates `to_claim` in isolation.

### P0: shutdown silently loses an in-flight database batch

Evidence: `store/writer.py:65-71` cancels the background writer before flushing; `flush` moves all pending entries into a local variable before awaiting I/O (`83-88`). Cancellation drops that local batch. It is no longer present for the final flush and is not counted as dropped.

Temporary reproduction: MemoryStore subclass sets an `entered` event then blocks in `upsert_many`; enqueue one document, start writer, wait for `entered`, call `close`. Result:

```text
pending=0, written=0, dropped=0, persisted=0
```

Fix with an orderly stop signal: stop accepting/enqueuing new work after producers are stopped, wake the loop, finish the current flush and drain all remaining work before closing the store. Alternatively requeue an entire cancelled in-flight batch before propagating cancellation, with idempotent upserts. Track in-flight counts and surface failed drains. A bounded drain timeout must report a failure; it must not pretend successful durability.

Acceptance: a close during slow active upsert retains every accepted document once the store responds; explicit cancellation/failure leaves recoverable queued work or an explicit failure count; docs written during a flush are drained; multiple close calls are safe. Seed command must use the strict path and exit nonzero on dropped writes.

### P0/P1: replay/reset leaves old agent work running against shared pipeline state

Evidence: `runtime/session.py:81-85` cancels only the feed task, but signal handler tasks live separately (`160-167`). `agents/pipeline.py:409-412` resets coordinator/scheduler/decisions without cancelling old handlers/workflows. `_run_agents` later reads/writes the same pipeline object and saves its state (`530-540`). Only restore-created jobs are registered in pipeline `_pending`; normal live handlers belong to the session.

Temporary reproduction: spawn a handler waiting on an event, call `session.stop`, then release it. Handler count after stop was 1 and it completed afterwards. During a replay, this permits prior-session work to consume model calls and overlay scheduler state after reset, even though it retains the old session as its output sink.

Fix: `MatchSession.stop` cancels and awaits all handler tasks, including handlers created while the feed is stopping; cancel restore jobs through an explicit async pipeline lifecycle too. Stop the old session and pipeline completely before reset. Add a session/run-generation guard before publication as defense in depth. Shutdown order must settle all producers before writer drain.

Acceptance: blocked old-session handlers are cancelled/awaited on replay/open/shutdown; no old insight/overlay/pipeline write occurs after the new run starts; the new coordinator cap and scheduler remain independent. No leaked asyncio tasks after test teardown.

### P1: pause leaks one item, then resume catches up in a burst

Evidence: `ingest/adapters.py:67-77` checks pause before sleeping, yields without a second check, and tests `control.paused` only after the pause wait has returned. `resume()` has already set it false, so same-speed resumes never re-anchor wall time.

Temporary timed probe: frames at 0.0/0.1/0.2/0.3/0.4 s; pause at 0.04 s and resume at 0.44 s. Frame 0.1 arrived at 0.101 while paused; 0.2, 0.3 and 0.4 all arrived at 0.442 immediately after resume.

Fix pause-aware interruptible pacing with explicit control revision/pause duration and a new wall/elapsed anchor on each pause/resume or speed change. Re-check control after a timer wakes. Preserve immediate deterministic warm-up before `start_elapsed`.

Acceptance: no paced item while paused; same-speed resume preserves frame spacing without a catch-up burst; speed changes are timely; stop/cancellation wakes waits; start-minute warm-up remains correct.

### P1: durability/resume and archive guarantees are stronger in docs than in code

- Custom storyline JSON is not persisted: `RunRecorder.begin` stores only its title/seed (`repository.py:137-155`); `manager.resume` regenerates the current default and compares only the title (`116-122`). Recordings have `seed=None` and never auto-resume (`91-100,106-114`). Fix future runs with persisted full storyline, generator/config/calibration version/hash and match metadata; retain explicit archive fallback for old runs. Do not silently regenerate an altered simulator version under an old run id.
- Per-container batches are atomic, not a cross-container run transaction. `writer.py:85-88` groups collections and can drop an earlier batch then persist later ones (`90-103`). The statement “a stored event implies every earlier event is stored” is not guaranteed during a persistent failure. Use a durable contiguous feed checkpoint/manifest committed only after all constituent writes succeed. Rebuild only to that checkpoint; surface incomplete archives and failed drains.
- Archived opening invokes pipeline restore after setting `archived=True` (`manager.py:161-164`); restore can requeue recent `stage=data` work (`pipeline.py:429-438`) without checking archived. Guard requeue for archives. Docs say “nothing new is written,” yet fan votes and fan outputs still write (`manager.py:162`, `agents/services.py:64-72`). Decide explicitly whether archives permit those writes and document the contract.
- Cosmos open checks only the `runs` container (`store/cosmos.py:48-51`). Missing containers can let startup succeed and then drop writes. Strict operations must validate all required containers before writing.

The first implementation can address lifecycle/strict seed and truthful diagnostics without building a full event-log checkpoint protocol. If checkpointing is deferred, document the best-effort recovery limit and do not advertise exact crash recovery under partial write failure.

## Existing local data: verified inventory, not an import request

The source is outside this checkout:

```text
/Users/bhavyachanana/code/pl-microsoft-hackathon/.claude/worktrees/tactics-brain/backend/data/local/leaguedata
/Users/bhavyachanana/code/pl-microsoft-hackathon/.claude/worktrees/tactics-brain/backend/data/local/tacticsml
```

The `ui-redesign` source worktree has no `backend/data/local` and no backend `.env`. The main checkout has video/provider caches but not the league warehouse. Gitignored data is not transferred by creating a worktree.

Read-only inspection of the local league `curated/v1/manifest.json`, `coverage.json` and `kb/index.json` found:

| Artifact | Observed coverage |
|---|---|
| Raw warehouse | 776 files, about 945 MB; Understat, football-data, FPL, Transfermarkt, StatsBomb, openfootball, Wikidata, Wikipedia. No cached ClubElo source folder. |
| Curated v1 | 15,033 matches; 566,953 lineup rows; 206,161 match-event rows; 14,223 players; 208 clubs; 6,054 shots; 2,280 team-match-stat rows; 930 player-match rows. |
| 2024-25 domestic seasons | EPL/La Liga/Serie A each 380 matches; Bundesliga/Ligue 1 each 306. All are Transfermarkt-only in this snapshot. Lineups/formations/managers/goal-card-substitution events are available; **no shots, xG, possession, odds or team-match-stat rows** for these season cells. |
| 2024-25 UCL | 189 matches, Transfermarkt-only, also no shots/xG/team stats. Do not assume domestic double round-robin geometry for UCL. |
| Richer 2023-24 domestic cells | EPL/La Liga/Serie A each 380 matches, 760 team rows, match xG present. Detailed shot samples are only 292/220/239 rows respectively, not full-season shot coverage. Possession and Elo are missing. |
| Existing KB | Built 26 October; 79 teams, 92 team-seasons, 930 player profiles, 40 reasonings and 79 playbooks. **All team styles are 2023-24**, across EPL/La Liga/Serie A/UCL. It is not a 2024-25 KB. |
| Tactical training | Existing shards: Metrica 2/918 rows; SkillCorner 8/23,769; Sportec 7/17,100; StatsBomb360 47/99,182; synthetic 20/90,623. Ensemble (3 seeds), cVAE, retrieval index, manifest and evaluation report exist. These artifacts were found, not re-trained or independently re-evaluated in this review. |

Use 2024-25 to estimate score/result/timing/card/formation distributions with adequate cohorts. Use explicitly named 2023-24 populations for xG/shot/foul/corner priors where present, retaining per-metric coverage and the fallback season. Never present unavailable 2024-25 xG as calibrated. Event/pass/tracking priors cannot be inferred from Transfermarkt's sparse goal/card/substitution events. Keep existing StatsBomb aggregate reference for pass geometry and published tracking ranges until a suitable local reference is measured.

Current calibration is a committed aggregate profile of 100 2015/16 StatsBomb matches (`simulator/reference.py:1-16,38-40`, `reference_profile.json:1-5`), not a 2024-25 calibration. `simulator/calibration.py:58-75,86-92` checks neutral generated sample means against those aggregates; it does not fit model parameters or generate a season. `dataset/export.py:92-105` exports one hero plus four neutral matches, all using the same default fictional roster. It does not seed a DB.

The data tools already carry local-use/licensing separation in `docs/LEAGUEDATA.md`. Do not copy `raw`, `tables`, `curated`, `kb`, source match ids, real names, match-level vectors, source footage or retrieved real moments into public artifacts or Cosmos. Renaming a real match is not synthetic generation. Calibration must discard individual identities/records and emit independently sampled fictional matches.

## LLM and runtime configuration

- `LlmSettings.from_env` (`agents/llm.py:103-126`) loads dotenv and selects online automatically when an endpoint exists. Defaults are `gpt-5-mini` for fast and strong, strong model for creative; efforts low/high/medium, scenario medium. These are deployment names and can be overridden; reasoning-family detection for creative is name-based with an override.
- Fast tier: tactical/narrator/checker rewrite/producer/personaliser/catch-up/coach/pundit. Strong tier: recap and scenario. Creative tier: Tactics Board personas/verdict (`agents/pipeline.py:83-104`, `agents/tactician.py`, `agents/llm.py:156-210`). League reasoner/playbooks use separate batch entrypoints and the same configured client tiers; they are not called on live startup.
- `npm run dev:offline` explicitly uses templates. `dev:demo` adds the hero cassette. `dev:db` adds the emulator endpoint. Plain `npm run dev` in this checkout, with no inherited endpoint/config, uses memory + templates. Source main/tactics-brain `.env` files set the fast/strong deployments but are not automatically the intended config for the isolated worktree. No secrets were copied or printed.
- Docker copies only source and the demo cassette; `.dockerignore` excludes `data/`, so neither KB nor trained weights are deployed. Infra sets Cosmos and model settings, not KB/weight paths (`backend/Dockerfile`, `backend/.dockerignore`, `infra/app.bicep:120-131`). A deployed board therefore uses the baseline unless an explicit safe asset packaging step is added.
- The runtime label distinguishes offline/recorded/online at the client-bundle level (`llm.py:169-176`) but agent-run records use `self.team.label`, not each actual response model (`pipeline.py:493-505`). Mixed cassette/live/fallback output needs actual per-call source, deployment, prompt version, duration, timeout/fallback reason and supported-claim counts. Avoid displaying a global “AI on” state as proof a particular card was generated live.

## Database and seed plan for the implementer

### Phase A — correctness and honest capabilities

Implement the four reproduced defects first, with focused regression tests in `test_store.py`, `test_runtime.py`, `test_agents.py` / a dedicated fact-check test, and replay-control tests. Keep prior redesign fixes: yellow/red wording, momentum gating, no uncertain tactics recommendation, coach full-time behavior, restart flags, unit normalization, player minutes/duels, half-by-half possession, board shortfall. Those are already implemented and tested in this branch; the older `docs/redesign/backend-issues.md` is historical, not the present backlog.

Expose an additive capability object from `/api/health` or a small `/api/capabilities`: configured/actual store, durable/degraded state, dropped/in-flight writes, actual model mode, cassette enabled for current run, KB availability/version/season coverage, evaluator version/source, source kind and generator/calibration version. Existing health always reports `status=ok` (`api/app.py:169-182`) even after Cosmos fallback; retain liveness while exposing a separate readiness/durability state. Do not require cloud services for offline usage.

### Phase B — versioned aggregate calibration

Add an explicit read-only calibration entrypoint, preferably `simulator/season.py` plus a small aggregate-profile helper, rather than changing the live default simulator/cassette IDs. Input is a caller-supplied curated directory; do not auto-search the user's home or fetch data. Open Parquet read-only with the existing optional DuckDB dependency. Verify the curated manifest/hash and filter valid source season/league cohorts.

Output a small versioned aggregate JSON profile: format/profile version; source season/league population counts; per-metric available/missing counts; broad histogram/rate or mean/spread; source-manifest hash; exact metric definitions; explicit fallback reasons. No team/player names, source ids, match keys, per-match rows, URLs tied to a real match, lineup arrays or free text. Pool sufficiently large populations (e.g. at least 20 matches); no small-club cohorts that recreate a real team profile. Profiles can be regenerated from the local corpus; tests use fictional tiny fixtures with a test-only minimum-cohort override.

Acceptance: same source snapshot produces byte-identical aggregates; source files remain unchanged; 2024-25 xG is marked missing or explicitly sourced from a 2023-24 fallback; profile does not claim event/tracking coverage absent from the input; malformed/incomplete input fails clearly. Network and LLMs are not needed. A supplied path must stay an input, never become an output target.

### Phase C — fictional synthetic season bundle

Use new names/IDs/rosters, independently generated dates and a deterministic round-robin fixture schedule. Add season seed, generator/profile version/hash and a per-fixture seed derived from stable inputs. Do not use Python's process-randomized `hash()`. Do not transfer real fixture order, scores, scorers, club style vectors or player identities. A `2024-25` label denotes the calibration/fictional season range, not historical replay.

Use the existing `MatchSimulator` with caller-provided `MatchMeta`, neutral `Storyline(scripted_goals_only=False)` and bounded, independently sampled styles/strengths. If a profile metric cannot currently affect the simulator, mark it as a validation target rather than pretending it was applied. Preserve the seed-7 hero and its cassette separately. Use the deterministic `MatchEngine` to derive all persisted scores/stats/signals; never hand-write inconsistent totals or fabricate LLM insight records.

Bound the first default run to a small whole season (e.g. four fictional clubs, twelve home/away fixtures) and make larger counts explicit. Write one fixture at a time to keep memory bounded. Do not default to 380 full-tracking runs: the current engine generates roughly thousands of 23-entity frames per fixture. Record estimated fixture/document/byte costs in the manifest before seeding. Full event/tracking bundles are needed for archived `open_run`; summary-only matches require a distinct catalog contract and must not masquerade as complete runs.

Bundle contract: `matchlens-season/1`, season id/label, seed and fictional-entity manifest, schedule, per-fixture content digest and generated-match bundle, calibration provenance/limitations, derived season table and player totals. Standings/player totals are computed from fixture output and are internally consistent. Write staging files then atomically publish a completed bundle. Support resume only after validating each existing artifact digest. Never overwrite the source corpus.

Acceptance: identical inputs yield identical bundles and stable IDs; no duplicate/self fixtures, each pair meets home/away once; all entities belong to the fictional registry; event/metadata IDs agree; scores/standings/player aggregates recompute exactly; corrections are retained and reflected in totals; calibrated sample statistics are reported with tolerance/coverage rather than a false exact realism promise. Existing hero dataset/cassette and golden simulator tests remain unchanged.

### Phase D — strict idempotent DB seed, then schema migration support

Add `python -m matchlens.store.seed` (proposed, does not exist today) with explicit input bundle and `--dry-run` default / `--apply`. Memory is acceptable for preview/tests; applied persistence must require an explicit Cosmos target and fail if Cosmos falls back. Show database name, fixture/doc counts and schema version, while keeping secrets out of output. Do not load an unrelated source worktree `.env` implicitly for writes.

Add `RunRecorder.begin(..., activate=False)` or a dedicated import writer so seeding finished archives never calls the current `begin` path that sets `_active` (`repository.py:153-155`). Stable synthetic archive run ids derive from season/fixture/generator version; do not use `new_run_id` timestamps. Store `doc_type=run`, `status=finished`, actual full metadata/events/frames, deterministic signals/stats, source kind `synthetic-season`, schema version and bundle digest. Publish a completion marker only after counts/digests are verified. Do not seed fabricated fan votes, fake model traces, or falsely agent-verified text. Optional deterministic data-only insights must carry truthful provenance.

On second application, matching digests are a no-op; mismatched existing content must fail clearly unless an explicit versioned replacement/import mode was selected. Resume partial imports by verifying existing content rather than treating the presence of a run header as completion. Flush strictly and verify the durable result; any dropped batch yields nonzero exit and no completion marker. Leave the currently active run pointer untouched, including on failure.

For schema evolution, add additive `schema_version` to new run/import documents and a compatibility reader that treats existing documents as v1. Infrastructure remains the authority for managed-identity container creation; the emulator's `create_if_not_exists` is not a cloud migration engine (`store/cosmos.py:41-51`). The first migration command should inventory/dry-run versions and perform only required idempotent additive backfills, with a completion ledger/version. Avoid rewriting every large event/frame just to add a field unless readers need it. Coordinate any new container/index with `CONTAINERS`, `infra/cosmos.bicep` and existing parity tests.

Acceptance: MemoryStore integration proves seed→list runs→open archive→identical score/stats/timeline/frame; apply twice yields unchanged counts; partial-failure retry completes without duplicates; no change to `_active`; no real names/source records enter stored documents; a failed/misconfigured Cosmos target produces nonzero exit, never “seed complete”; an emulator round trip is run only if a local emulator is actually available. Do not claim Azure persistence from MemoryStore tests.

Proposed commands should be documented once implemented, including a concrete local source path for calibration, a separate output under this worktree's ignored generated-data directory, an explicit small fixture count and a preview before apply. Separate `data:fetch/build/curate` (mutates the real local warehouse), aggregate calibration (reads it), season generation (writes fictional output) and DB seed (writes synthetic archives).

## Validation performed and limits

The existing backend suite was run from this worktree with `PYTHONPATH=src MATCHLENS_LLM=offline MATCHLENS_STORE=memory` using the source worktree's existing Python 3.12 virtual environment: **519 passed, 31 skipped, 1 FastAPI/httpx deprecation warning, 93.23 seconds**. The passing suite does not cover the failure cases reproduced above. No online LLM calls, source downloads, Cosmos writes, model training or source-data changes were performed. The trained local manifest/evaluation file was inspected as an existing artifact; its numerical claims were not independently revalidated. The source inventory is a point-in-time local snapshot.

Suggested implementation order: fact-check integrity → task shutdown → writer drain → pacing → truthful capability/config reporting → aggregate profile → small synthetic season → strict idempotent seed → archive/resume contract improvements. Review each phase's concrete outputs before expanding seasons, model usage or deployment.

## Independent post-fix review — 27 October 2026

Reviewed the uncommitted core diff plus the stable `dataset/season.py` and `store/seed.py` implementation. This section describes the implementation state at the second review; later implementer fixes should be checked against these reproductions. No implementation code was edited during this review. The full suite was not repeated.

The normal shutdown/control fixes address the original causes: writer close now wakes/joins the current flush; session stop cancels/joins live and restored handler tasks; archived/finished pipeline restoration stops before requeue; pacing maintains scaled progress across pause/speed changes. Rejecting malformed/unverifiable structured claims and exposing `verification_scope` are appropriate improvements. Three actionable issues remain at this snapshot:

### [P1] Strict seed verification accepts extra feed records that change reopened results

`store/seed.py:121-126` verifies that every expected document is present and equal, but never checks that the persisted set contains no unexpected event/frame/signal documents. Existing completion markers are checked for the manifest hash only (`111-113`), not expected counts or their whole payload. `manager.open_run` loads the entire event/frame partition, including those extra records.

Reproduced entirely in MemoryStore with the generated `data/generated/season-v2` first fixture: import one fixture, append a new GOAL with an unused event id, sequence after full-time and elapsed/clock one second later, then seed the same fixture again. The second seed succeeded, but reopening yielded **3-2 instead of the seeded 2-2**, with one extra persisted event. This is a strict-verification failure, not a fault in initial generation or a reason to touch the user's source corpus.

Owner: synthetic season/seed implementer. Compare exact ID sets and payloads for imported feed/derived collections, and compare run companion documents with an explicit allowance for the completion marker and any intentionally permitted later run records. Validate the complete marker/count contract, not its hash alone. Reject unknown records; do not silently delete or overwrite a supposedly complete conflicting archive. Add regression coverage for an extra event, extra frame, extra signal, corrupted marker counts, and an unchanged successful repeat. Parent should rerun strict verification of its real local-emulator import after this patch.

### [P1] Truthful producer/personalizer count statements cannot pass the new checker

`agents/factchecker.py`'s added count gate accepts supported stat claims, `context.player` stats, or exact coach issue evidence. Actual live `signal_pack` provides `players: [...]` and `teams: {...}` (`agents/context.py:105-118`), not a singular `player`. The rendering contracts (`agents/contracts.py:145-174`) have no claims field; their call sites deliberately supply `claims=[]` (`agents/pipeline.py:279-283,311-319`). Consequently even accurate named-player/team counts are discarded and silently replaced with fallback renderings.

Fresh-engine reproduction with `p = list(engine.meta.players.values())[0]` and context containing `match_header(engine)`, `players=[player_brief(engine,p.player_id)]`, and `teams={'NBR':engine.team_stats('NBR')}`: `"Mateo Kalnins has 0 goals."` and `"Northbridge have 0 shots."` are both blocked as `Unclaimed statistic`, although both values are grounded. Preserve the false-count rejection while adding a subject/stat-bound path for actual render contexts or compatible typed rendering claims. Test positive accurate counts alongside wrong subject/value counts; do not fix this by accepting any matching number anywhere in the context.

Owner: backend core implementer, notified with the exact probe. This is a regression introduced by the fix and should land before calling numeric personalization complete. A sweep of every tactical template over the current seed-7 feed found **zero template check failures**, so the demonstrated problem is specifically the rendering contract/context mismatch, not a general claim that all existing templates break.

### [P2] Highlights order is wrong on equal elapsed at a period boundary

The frontend review's visible observation is confirmed, but the exact cause is an **elapsed tie**, not an elapsed reset. Seed 7 produces `period:half_time` at `period=1, clock=2761.4, elapsed=2761.4, 45+2'` and `period:kickoff:2` at `period=2, clock=2700.0, elapsed=2761.4, 46'`. `agents/services.py:225` performs a stable sort on descending elapsed only, so older half-time remains above the second-half kickoff in a newest-first feed.

Owner: backend core implementer; retain the correctly ordered frontend Timeline. Keep monotonic elapsed as the primary sort key, then break ties by period/clock and originating feed sequence (or an equivalent explicit chronology key). Expose the ordering fields in personalized feed rows if clients need them. Preserve a correction revision's intended moment identity/order. Add a real demo regression asserting descending `[period:kickoff:2, period:half_time]`, plus same-instant event ordering. Sorting display minute labels lexicographically would be incorrect.

The synthetic season code otherwise uses aggregate queries against local Parquet, fictional entities and independently sampled fixture choices. `inspect_dataset` regenerates each fixture before apply; the CLI explicitly restricts writes to a local emulator and never uses the fallback store. These are sound data-boundary choices. The implemented sampled-score/scripted-goal approach is a deliberate alternative to the earlier neutral-goal proposal, and is accurately disclosed in `docs/SYNTHETIC_SEASONS.md`; 2023-24 priors are validation-only and missing 2024-25 xG is explicit. Atomic staging, a source-manifest digest/cohort minimum, and a general migration ledger from the original plan remain future work and should not be claimed as delivered. This review did not apply data to Cosmos or re-evaluate trained model accuracy.

## Resolution and integration evidence

All three actionable second-review findings were subsequently fixed: seed-owned records and completion markers are verified exactly, grounded producer/personaliser counts retain subject/stat binding, and period-boundary ties have an explicit chronology. Fan output/votes and their agent-run records are permitted extensions to a completed synthetic archive. An additional production review found late historical graphics being rescheduled at full time; publication now preserves their original expired timing.

The integrator's final full suite passed **578 tests with 3 skipped**, including Cosmos integration. Twelve synthetic archives were imported into the local emulator, completion markers/counts read back, one archive reverified without pointer/marker changes, and its 2–2 state, four goal insights and audience feeds reopened through the production app. See [validation.md](validation.md) for exact commands, counts, browser checks and remaining production limits. The earlier snapshot findings and proposed larger extensions above remain an audit trail, not a claim that the resolved defects are still present.
