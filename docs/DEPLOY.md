# Deploying Matchlens to Azure

Matchlens runs as two Azure Container Apps in one environment:

```text
browser ──▶ frontend (Next.js, public HTTPS) ──▶ backend (FastAPI + agents, internal only)
                                                   ├──▶ Cosmos DB serverless (managed identity): every run, resumable
                                                   ├──▶ recorded demo match (baked into the image)
                                                   └──▶ Azure OpenAI in Foundry (managed identity, cache misses only)
```

| Resource | Purpose |
|---|---|
| Container Apps environment + Log Analytics | Hosting and logs |
| `ca-frontend-*` | Public site; proxies an allow-list of read-only API paths and the live stream |
| `ca-backend-*` | Stats engine, agent workflow, MCP server; internal ingress only, max 1 replica (one live match per process) |
| `cosmos-*` (serverless) | The store: database `matchlens`, nine containers partitioned by run (see [DATABASE.md](DATABASE.md)). Key auth disabled |
| Container registry (Basic) | Images, built remotely by ACR (no local Docker needed) |
| User-assigned managed identity | Pulls images; calls Azure OpenAI with the *Cognitive Services OpenAI User* role; reads and writes Cosmos DB with the *Cosmos DB Built-in Data Contributor* role. No keys anywhere |

Both apps and the database scale to zero when nobody is watching (BRD §10.5). A cold start resumes the stored live
match where it stopped; once a match has finished, the next cold start begins a new one from kick-off.

## The recorded demo match

Judges should get instant, free, repeatable output, so the backend replays a **recorded demo match**
(`backend/demo/hero-match.json`): every agent answer the model gave when the hero match (seed 7, "The Kestrel Park
Comeback") was played online. On replay, the agent workflow still runs for real (coordinator, fact-checker,
overlay scheduling, personalised feed), it just reads answers instead of calling the model.

| Request | Replayed when |
|---|---|
| Live insight (tactical, narrator, rewrite, producer, personaliser) | Same signal id and revision |
| Catch-up (5/15/30 min), every mode and language | Same score and key moments in the window |
| Half-time and full-time recap, every mode and language | Same score and key moments |

A miss (an on-demand recap mid-match, a favourite-club catch-up that was not recorded) goes to the live model when
an endpoint is configured. Offline, a catch-up falls back to an unfocused recording if there is one, otherwise the
data-only output, labelled as data. A different seed or storyline turns the recording off.

The committed recording holds 1,516 answers: 149 for the 38 agent-explained signals, 1,349 catch-ups and all 18
recaps, each in casual, analyst and player-focus modes and English, Hindi and Spanish. Recording it took about
25 minutes and 1,225 `gpt-5-mini` calls.

Record or refresh it (needs `az login` and `backend/.env`):

```bash
npm run record:demo
```

Re-running resumes: recorded answers are kept and only missing ones are requested, so a run cut short by
rate limits (HTTP 429) is finished by running it again, ideally with `--concurrency 4`. `--modes` and `--languages`
limit the profiles recorded. Delete the file to record from scratch, for example after changing prompts.

Try it locally without Azure:

```bash
npm run dev:demo
```

## Deploy with the Azure Developer CLI

Prerequisites: [`azd`](https://aka.ms/azd-install) (`brew install azd`), an Azure subscription where you can assign
roles, and an existing Azure OpenAI / AI Services account with a `gpt-5-mini` deployment.

```bash
azd auth login
azd env new matchlens-demo
azd env set AZURE_LOCATION centralindia
azd env set MATCHLENS_OPENAI_ACCOUNT <ai-services-account-name>
azd env set MATCHLENS_OPENAI_RESOURCE_GROUP <its-resource-group>
azd up
```

`azd up` provisions `infra/` (Bicep), builds both images in ACR from `backend/Dockerfile` and `frontend/Dockerfile`,
and deploys them. It prints `FRONTEND_URL`: that is the demo link.

Optional settings (`azd env set …`):

| Variable | Default | Effect |
|---|---|---|
| `MATCHLENS_OPENAI_ENDPOINT` | `https://<account>.openai.azure.com/openai/v1/` | Override the model endpoint |
| `MATCHLENS_MODEL_FAST` / `MATCHLENS_MODEL_STRONG` | `gpt-5-mini` | Deployment names for live agents / recaps |

Leave `MATCHLENS_OPENAI_ACCOUNT` unset to deploy with no model access at all: the recorded demo and data-only
templates only, which costs nothing per request.

To keep the backend warm during judging (no cold-start pause while the run is restored), set `backendMinReplicas` to `1` in
`infra/main.bicep` and run `azd provision`. Set it back to `0` afterwards to save credit.

Redeploy code changes with `azd deploy`. Tear everything down with `azd down`.

## Check a deployment

```bash
curl -s "$(azd env get-value FRONTEND_URL)/api/backend/health"
```

`llm` should read `recorded-demo+azure:…` (or `recorded-demo` without a model), and `store.backend` should read
`cosmos:matchlens`. If it reads `memory (cosmos unavailable: …)`, the data-contributor role was not active yet when
the backend started (it can take a few minutes after the first `azd up`); restart the backend revision. Backend logs:

```bash
az containerapp logs show -g "$(azd env get-value AZURE_RESOURCE_GROUP)" \
  -n "$(az containerapp list -g "$(azd env get-value AZURE_RESOURCE_GROUP)" --query "[?tags.\"azd-service-name\"=='backend'].name" -o tsv)" --follow
```

## Run the containers locally

```bash
docker network create matchlens-net
docker build -t matchlens-backend backend && docker build -t matchlens-frontend frontend
docker run -d --name ml-backend --network matchlens-net --network-alias backend -e MATCHLENS_SPEED=20 matchlens-backend
docker run -d --name ml-frontend --network matchlens-net -p 3100:3000 -e MATCHLENS_BACKEND_URL=http://backend:8765 matchlens-frontend
open http://localhost:3100
```

## Cost notes

- Container Apps consumption plan with scale to zero: idle costs nothing; ACR Basic is about USD 5 a month.
- Cosmos DB serverless: nothing while idle, about USD 0.02 of request units per full match replay plus about 11 MB
  of storage (USD 0.285 per GB-month in Central India).
- The recorded demo means a judge's visit costs no model calls unless they ask for something that was not recorded.
- Recording the demo costs a few dollars of `gpt-5-mini` usage, once.
