# Architecture

Everything below runs locally with `docker compose`. The agents call Claude through a
provider-neutral layer and replay recorded responses in tests, so the test suite and the
eval gate need no API key. For the plain-language tour, start with the
[README](../README.md).

## System diagram

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

## One photo, end to end

1. A field agent posts a photo to `POST /v1/shelves/{id}/photos`. The API stores the file in
   S3, writes a `photos` row under the caller's tenant (RLS) and queues a `process_photo` job
   on a Redis Stream.
2. The worker claims the job and runs the **vision agent**: one model call with a
   grammar-constrained `ShelfExtraction` schema. The result is validated against the shelf's
   planogram; a contradiction (a stock-out with facings, an unknown SKU) is sent back to the
   model as an error and repaired, at most `vision_max_repairs` times.
3. The extraction is summarised (stock-outs, compliance, confidence) and a `plan_actions`
   job is chained.
4. The **planner agent** runs its own plan → act → observe loop with typed tools
   (`get_inventory`, `create_reorder`, `dispatch_technician`, `notify`, `geocode`) and ends
   with the terminal `submit_decisions` tool. Every tool call is logged with its arguments,
   result and latency; step and cost caps abort a runaway run.
5. **Guardrails** decide, per action, whether it auto-approves or waits: tool visibility by
   role, a cost limit, a confidence threshold, and "escalations always go to a human".
6. Approved actions are recorded; held ones appear in the **review queue**, where a reviewer
   approves, edits or rejects them, and can correct an extraction and promote it to the
   golden set.
7. Run progress is published on Redis pub/sub and streamed to the browser over SSE; the run
   row stores tokens, cost, latency and a Langfuse trace id.

Telemetry follows the same shape: readings arrive over MQTT (or HTTP), the excursion rule
opens an anomaly, and the anomaly queues a `plan_anomaly` job for the planner as the system
role.

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

## Configuration knobs worth knowing

All runtime values come from `SHELFSENSE_*` environment variables read by
`apps/api/shelfsense_api/config.py`. The ones that shape behaviour:

- `review_cost_limit_kobo` (₦5,000): an action costing more waits for a human.
- `review_confidence_threshold` (0.7): below this audit confidence, every action waits.
- `dispatch_cost_kobo` (₦15,000): the assumed cost of a technician call-out, so a dispatch
  always crosses the cost limit.
- `fridge_max_temp_c` (8 °C) and `fridge_excursion_minutes` (15): the excursion rule.
- `planner_max_steps` (8) and `planner_max_cost_usd` ($0.50): caps that abort a run.
- `vision_max_repairs` (2): how many times a contradicted extraction is sent back.
- `SHELFSENSE_VISION_MODEL` / `SHELFSENSE_PLANNER_MODEL`: model ids are config, never
  literals near a prompt.

## Auth modes

`SHELFSENSE_AUTH_MODE=dev` trusts `X-Dev-Tenant` / `X-Dev-Role` / `X-Dev-User` headers and
is refused in production. `SHELFSENSE_AUTH_MODE=jwks` verifies Clerk session tokens against
the instance's JWKS; Clerk **Organizations** must be enabled and each organisation's id goes
in `tenants.external_org_id`. The web app pairs with the API through
`NEXT_PUBLIC_AUTH_MODE=dev|clerk`; the Clerk CLI setup is in
[DEPLOYMENT.md](DEPLOYMENT.md#0-prerequisites).

## Repository layout

```
apps/api            FastAPI, SQLAlchemy 2 async, Alembic, agents, tools, jobs   (Python, uv)
apps/simulator      MQTT telemetry publisher                                     (Python, uv)
apps/web            Next.js 15 App Router, Tailwind 4, shadcn/ui, TanStack Query (pnpm)
packages/shared     zod schemas + TS types generated from OpenAPI                (pnpm)
packages/mcp-tools  MCP server exposing the API's tools                          (pnpm)
evals/              golden dataset, scorers, CLI runner, baseline                (Python, uv)
infra/              mosquitto + postgres init
scripts/            capture-screenshots.cjs (docs/images from a running stack)
docs/decisions/     ADRs 0001–0010        docs/DEPLOYMENT.md   docs/LOOM.md   docs/article/
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

## Decisions

One ADR per significant choice, in [decisions/](decisions/):

- [0001](decisions/0001-stack-choices.md) Stack choices and local topology
- [0002](decisions/0002-tenant-context-and-auth.md) Tenant context, row-level security and caller identity
- [0003](decisions/0003-llm-layer-and-jobs.md) LLM provider layer, structured extraction, fixtures and the job queue
- [0004](decisions/0004-planner-tools-and-guardrails.md) Planner loop, typed tools, MCP bridge and guardrails
- [0005](decisions/0005-review-queue-and-web-auth.md) Review queue, labelled examples, golden set, and web identity
- [0006](decisions/0006-telemetry-and-anomalies.md) Telemetry ingest, the excursion rule, and the live stream
- [0007](decisions/0007-evals.md) Evals: dataset, scoring, replay in CI, and the regression gate
- [0008](decisions/0008-observability.md) Observability: Langfuse traces, Prometheus metrics, cost per run
- [0009](decisions/0009-web-polish.md) Live run timeline, fleet map, mobile upload PWA, loading and accessibility
- [0010](decisions/0010-object-store.md) RustFS replaces MinIO for local and test object storage
