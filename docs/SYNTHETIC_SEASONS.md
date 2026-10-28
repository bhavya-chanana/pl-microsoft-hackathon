# Synthetic seasons

Generate a separate, fictional four-club league with 12 home-and-away fixtures, full simulated event and 1 Hz tracking feeds. The hero demo and cassette are unaffected. The source is read-only; generation writes only under `backend/data/generated/`, which is ignored by Git. Do not publish the source corpus or copy it into the database.

From the repository root, supplying the local curated directory:

```sh
npm run data:season -- --source /path/to/leaguedata/curated/v1 --out data/generated/season-v1
npm run db:seed:season -- --dataset data/generated/season-v1 --endpoint http://127.0.0.1:8081 --database matchlens
npm run db:seed:season -- --dataset data/generated/season-v1 --endpoint http://127.0.0.1:8081 --database matchlens --apply
```

Paths are relative to `backend`. Generation requires the `leaguedata` dependency group (DuckDB). The seed command previews by default, requires an explicit database, never reads dotenv or falls back to memory, and by default accepts only a local emulator endpoint (see [Seeding the hosted Azure account](#seeding-the-hosted-azure-account) for the explicit `--azure` target). Preview validates hashes and regenerates every match to ensure it is an independently simulated feed using the current generator version. It does not connect to the database. The emulator apply step creates/validates the required containers, writes deterministic archive IDs, verifies every document, and commits each archive's completion marker last. A retry resumes partial work with idempotent upserts; completed archives are reverified without rewriting. Verification requires exact seeded run, event, frame, signal, insight, overlay and decision documents, while allowing later fan outputs, fan votes and agent-run records from archive interactions. It does not alter the active run pointer. These archives contain deterministic engine signals and stats, plus data-only template insights and scheduled overlays with personalised variants. They make no model calls and record no model-generated agent traces. Seed schema `synthetic-archive/2` has distinct stable run IDs from earlier imports.

`profile.json` contains aggregate-only 2024–25 domestic score, result, goal timing, card and formation statistics with coverage. Each fixture independently samples a score from the aggregate score distribution, and goal times from the aggregate 15-minute bins; invented players score scripted beats while the simulator generates the surrounding play and tracking. Times are sampled within bins, excluding the final minutes of each half so beats can complete. Supported formations are sampled using the observed counts, renormalized to the two formations implemented by the simulator. Fictional clubs also receive small seed-derived shot-appetite and tempo variations, unrelated to real clubs. Card and other team metrics remain validation references, not fitted simulator parameters. Its 2023–24 xG and team-stat priors list sample counts by league; missing 2024–25 xG remains missing. Pass and tracking dynamics retain the simulator's existing reference. The profile exports no names, source identifiers or match-level rows.

`manifest.json` records the seed, file hashes, match summaries, standings and provenance. Repeating generation with identical source aggregates, software version and seed produces identical output. Generator changes intentionally cause seed verification to fail until a season is regenerated. A completion marker is a per-archive verification, not a cross-container transaction: an interrupted import may leave partial documents until retried. Do not treat a missing marker as a completed archive.

## Seeding the hosted Azure account

An offline, explicit, opt-in target. Nothing in the app's runtime calls it, and without `--azure` an `*.documents.azure.com` endpoint is rejected. With `--azure` the endpoint must be exactly `https://<account>.documents.azure.com[:443]/` (https, no credentials, path or query). Authentication is Microsoft Entra only (`DefaultAzureCredential`, i.e. `az login`); no account key is used, no dotenv is read and there is no in-memory fallback. The hosted account has local (key) auth disabled. The data-plane role cannot create databases or containers, so the command never creates any: it only attaches to the containers declared in `infra/cosmos.bicep` and reads each one before writing. Apply also requires `--confirm-azure <account>`, which must equal the account in the endpoint, so a pasted command cannot hit the wrong account. Preview still never connects.

One-time, a human with rights on the account grants the signed-in user data access. The built-in role `00000000-0000-0000-0000-000000000002` is Cosmos DB Built-in Data Contributor. Scope `/` is the whole account, which holds only the `matchlens` database; the SDK reads account metadata at `/` when it opens a client, so a database-level scope is not enough:

```sh
az login
az cosmosdb sql role assignment create -g rg-matchlens-demo -a cosmos-7npbct7a7fan6 \
  --role-definition-id 00000000-0000-0000-0000-000000000002 \
  --principal-id "$(az ad signed-in-user show --query id -o tsv)" --scope "/"
```

Then, from the repository root (paths relative to `backend`):

```sh
# preview: validates and regenerates the dataset, does not connect
npm run db:seed:season -- --dataset data/generated/season-v2 --azure \
  --endpoint https://cosmos-7npbct7a7fan6.documents.azure.com:443/ --database matchlens
# apply
npm run db:seed:season -- --dataset data/generated/season-v2 --azure \
  --endpoint https://cosmos-7npbct7a7fan6.documents.azure.com:443/ --database matchlens \
  --apply --confirm-azure cosmos-7npbct7a7fan6
```

The same guarantees apply as for the emulator: every document is verified, a retry resumes with idempotent upserts, each archive's completion marker is written last, the active run pointer is untouched, and later fan outputs, votes and agent runs are allowed. A 403 means the signed-in principal lacks the role above (the error prints its object id when available; role assignments can take a few minutes to take effect); a firewall on the account can also cause a 403.
