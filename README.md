# ShelfSense

**Agentic retail and cold-chain field operations.** Field agents photograph shelves and
fridges stream telemetry; AI agents turn both into decisions (reorder, dispatch, escalate)
behind a human review queue, with production-grade evals in CI and every model call traced
and priced.

Built as a flagship portfolio project for AI-engineer and agentic-systems roles. The
real-world context is Lagos: small FMCG distributors who audit shelves over WhatsApp
photos and Excel, and chill-chain fridges that fail silently.

> Status: **P0–P9 complete.** Deploy configuration is written and documented but the first
> production deploy has not been executed (see _Known limitations_).

## Architecture

```mermaid
flowchart LR
  subgraph Field
    FA[Field agent<br/>mobile PWA] -->|photo| API
    FR[Fridges / vans] -->|MQTT| ING[Ingester]
  end

  subgraph Platform
    API[FastAPI<br/>RLS per tenant] -->|job| Q[(Redis Streams)]
    Q --> W[Worker]
    W --> V[Vision agent<br/>structured output + repair loop]
    V --> P[Planner agent<br/>plan → act → observe]
    P -->|typed tools| T[get_inventory · create_reorder<br/>dispatch_technician · notify · geocode]
    T --> G{Guardrails<br/>confidence · cost · role}
    G -->|auto-approve| ACT[(Actions)]
    G -->|hold| RQ[Review queue]
    ING -->|rule: > 8°C for 15 min| AN[(Anomalies)] --> Q
    API --- PG[(Postgres 16<br/>pgvector · BRIN)]
    API --- S3[(S3 (RustFS locally))]
    MCP[MCP server] -->|/v1/tools| API
  end

  subgraph People
    RQ --> REV[Reviewer<br/>approve · correct · promote]
    REV -->|labelled examples| GOLD[(Golden set)]
  end

  subgraph Quality
    GOLD --> EV[Evals CLI<br/>replay in CI · PR delta]
    W --> LF[Langfuse traces]
    W --> PM[Prometheus]
  end

  WEB[Next.js dashboard<br/>runs · review · map · costs] --> API
```

Everything above runs locally with `docker compose`; the agents call Claude through a
provider-neutral layer and replay recorded responses in tests.

## Quickstart

Requirements: Docker, Node 22, pnpm 10, uv (Python 3.12), an Anthropic API key for live
model calls (tests and evals need none).

```sh
make install     # uv sync + pnpm install
make dev         # postgres :5433, redis :6379, s3 :9000, mqtt :1883   (make dev-full adds Langfuse :3001)
make migrate     # alembic upgrade head
make seed        # 2 tenants, 3 stores, 6 shelves, 59 SKUs, planograms, inventory, devices
make api         # FastAPI on :8000        make worker    # agents        make ingest   # MQTT
make web         # Next.js on :3000        make simulate  # fridges + vans at 10x speed
make demo        # queue the synthetic photos and an hour of telemetry with one fridge fault
make verify      # ruff, mypy --strict, pytest, prettier, eslint, tsc, vitest, next build
```

Postgres sits on host port 5433 and Langfuse on 3001 to avoid the usual collisions;
override any port in `.env` (created from `.env.example` on first `make dev`). On macOS,
`/usr/bin/make` may be the Xcode stub: `brew install make` or run the Makefile's commands.

### A first run, end to end

1. `make demo` queues three synthetic shelf photos and an hour of telemetry.
2. `make process-jobs` (or leave `make worker` running): the vision agent audits each photo
   against its planogram, the planner reads inventory and proposes actions, the fridge
   excursion triggers a technician-dispatch proposal.
3. Open http://localhost:3000. Dev auth mode is on: the control at the top right switches
   tenant and role. **Review queue** holds what the guardrails caught; **Agent runs** shows
   every call; **Costs** shows what it cost.

To try it without spending: `SHELFSENSE_LLM_PROVIDER=replay SHELFSENSE_LLM_FIXTURES_DIR=apps/api/tests/fixtures/llm make process-jobs`
replays the recorded vision responses for the three demo photos.

### Calling the API

```sh
curl -s localhost:8000/v1/stores -H "X-Dev-Tenant: <tenant id from make seed>" -H "X-Dev-Role: manager"
curl -s -X POST localhost:8000/v1/shelves/<shelf id>/photos -H ... -F "file=@shelf.jpg;type=image/jpeg"
curl -s localhost:8000/v1/runs?kind=planner -H ...
curl -s -X POST localhost:8000/v1/actions/<id>/approve -H ... -d '{"quantity": 12}'
curl -s -X POST localhost:8000/v1/telemetry -H ... -d '{"readings": [...]}'
curl -N localhost:8000/v1/runs/stream -H ...              # SSE of run progress
```

The contract is written first in [apps/api/openapi.yaml](apps/api/openapi.yaml); the
generated document and the TypeScript types are checked for drift in CI. With
`SHELFSENSE_AUTH_MODE=jwks` the API verifies Clerk session tokens instead of dev headers
(Clerk **Organizations** must be enabled; each organisation's id goes in
`tenants.external_org_id`). The web app pairs with it via `NEXT_PUBLIC_AUTH_MODE=clerk`:
sign-in and sign-up are Clerk modals in the header (also at `/sign-in` and `/sign-up`),
styled with Clerk's shadcn theme; the Clerk CLI setup is in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#0-prerequisites).

## What is in the box

| Area          | Where                                              | Highlights                                                                                                 |
| ------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Multi-tenancy | `apps/api/migrations`, ADR-0002                    | `tenant_id` on every row, RLS **forced**, non-superuser runtime role, API refuses to start as superuser    |
| Vision agent  | `apps/api/shelfsense_api/agents/vision.py`         | grammar-constrained structured output, planogram-aware validation, repair loop, derived compliance summary |
| Planner agent | `agents/planner.py`, `agents/tools.py`             | own plan → act → observe loop, typed tools logged call by call, step and cost caps                         |
| Guardrails    | `guardrails.py`, ADR-0004                          | tool visibility by role, review gating by confidence/cost/role, PII scrub before prompts                   |
| MCP           | `packages/mcp-tools`                               | stdio server bridging any MCP client to the same tools with the caller's identity                          |
| Review        | `routes/review.py`, ADR-0005                       | approve/edit/reject actions; correct extractions; promote to the golden set                                |
| Cold chain    | `telemetry.py`, `mqtt_ingest.py`, `apps/simulator` | MQTT and HTTP ingest, BRIN time series, 15-minute excursion rule, SSE live stream                          |
| Evals         | `evals/`, ADR-0007                                 | 100 synthetic cases, 167 recorded model responses, replay in CI, PR delta comment, 2 % regression gate     |
| Observability | `observability.py`, ADR-0008                       | Langfuse traces (agent → generation → tool), Prometheus metrics, cost per run, p50/p95                     |
| Web           | `apps/web`                                         | review queue, live run timeline, fleet map, mobile upload PWA, evals and costs pages                       |
| Contracts     | `apps/api/openapi.yaml`, `packages/shared`         | OpenAPI-first, generated TS types, zod schemas, CI drift check                                             |

## Repository layout

```
apps/api            FastAPI, SQLAlchemy 2 async, Alembic, agents, tools, jobs   (Python, uv)
apps/simulator      MQTT telemetry publisher                                     (Python, uv)
apps/web            Next.js 15 App Router, Tailwind 4, shadcn/ui, TanStack Query (pnpm)
packages/shared     zod schemas + TS types generated from OpenAPI                (pnpm)
packages/mcp-tools  MCP server exposing the API's tools                          (pnpm)
evals/              golden dataset, scorers, CLI runner, baseline                (Python, uv)
infra/              mosquitto + postgres init
docs/decisions/     ADRs 0001–0009        docs/DEPLOYMENT.md   docs/LOOM.md
```

## Phases

| Phase | Scope                                                                | Status |
| ----- | -------------------------------------------------------------------- | ------ |
| P0    | Monorepo, tooling, Compose, CI skeleton, ADR-0001                    | done   |
| P1    | Data model, migrations, RLS, seed; OpenAPI spec → generated TS types | done   |
| P2    | Photo upload, job queue, vision agent, provider abstraction          | done   |
| P3    | MCP tools, planner loop, tool-call logging, guardrails               | done   |
| P4    | Human review queue + promote-to-golden-set                           | done   |
| P5    | Simulator, MQTT ingest, SSE stream, anomaly rule                     | done   |
| P6    | Evals package, dataset, scoring, CLI, CI PR comment                  | done   |
| P7    | OTel + Langfuse spans, cost, Prometheus, dashboard pages             | done   |
| P8    | Web polish: streaming agent UI, maps, mobile upload PWA, a11y        | done   |
| P9    | Deploy config (Fly + Vercel), demo data, docs, Loom script           | done   |

Each phase is one commit with green checks: `make verify` runs what CI runs.

## Decisions & known limitations

Decisions are recorded in [docs/decisions/](docs/decisions/), one ADR per phase. The ones
most worth reading: [RLS and identity](docs/decisions/0002-tenant-context-and-auth.md),
[the LLM layer and fixtures](docs/decisions/0003-llm-layer-and-jobs.md),
[planner, tools and guardrails](docs/decisions/0004-planner-tools-and-guardrails.md),
[evals](docs/decisions/0007-evals.md).

Known limitations, stated rather than hidden:

- **Not deployed yet.** `fly.toml`, the Dockerfile and `apps/web/vercel.json` are written
  and the image builds; the runbook is [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). The
  first deploy needs `flyctl`, a Clerk instance with Organizations enabled, and credits.
- **Synthetic evals are easy.** Vision scores 100 % on rendered shelves. Real photos will
  score lower; [evals/README.md](evals/README.md) is the labelling workflow for the 30
  real photos, and their baseline will be gated separately. Six planner fixtures are
  missing because the API account ran out of credits mid-recording; `make eval-record`
  fills them.
- **Planner decision accuracy is 67.5 %** against the written policy on synthetic cases,
  mostly notify/escalate judgement calls. The eval exists to make prompt changes
  measurable; the prompt has not yet been tuned against it.
- **Reasoning streams per turn, not per token.** Structured output and tool decisions need
  the complete response; the run page streams turns and tool results.
- **Notifications are stubs.** `notify` records the message for every user in the target
  role; no WhatsApp or email provider is wired. `geocode` answers only from store records.
- **Anomalies are evaluated on the trailing readings.** A batch that both starts and ends
  an excursion within itself opens nothing; live ingest sees every reading. A per-device
  state row is the next step for a real fleet.
- **Map tiles** come from public OpenStreetMap servers; production needs a tile provider.
- **Bounding boxes are not editable** in the review UI; corrections edit facings per slot.

## Contributing & licence

[CONTRIBUTING.md](CONTRIBUTING.md) · [MIT](LICENSE)
