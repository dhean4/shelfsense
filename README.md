# ShelfSense

Agentic retail and cold-chain field-ops platform. Field agents upload shelf photos and
fridges stream telemetry; AI agents turn both into decisions (reorder, dispatch, escalate)
with a human review queue, production-grade evals and full tracing.

**Why.** Small distributors in Lagos audit shelves over WhatsApp photos and Excel, and
chill-chain fridges fail silently. ShelfSense is a portfolio project built to the standard
of an internal platform: multi-tenant, typed tool contracts, evals in CI, cost and latency
per agent run.

> Status: **P0 scaffold**. No domain features yet. See the phase table below.

## Quickstart

Requirements: Docker, Node 22, pnpm 10, uv (Python 3.12).

```sh
make install     # uv sync + pnpm install
make dev         # postgres :5433, redis :6379, minio :9000, mqtt :1883
make migrate     # alembic upgrade head
make seed        # 2 tenants, 3 stores, 6 shelves, 40 SKUs, planograms (prints tenant ids)
make api         # FastAPI on :8000  →  curl localhost:8000/readyz
make worker      # background jobs: vision extraction (needs ANTHROPIC_API_KEY in .env)
make web         # Next.js on :3000
make verify      # everything CI runs
make dev-full    # + Langfuse on :3001 (login: dev@shelfsense.local / shelfsense-dev-password)
```

Postgres sits on host port 5433 and Langfuse on 3001 to avoid the usual collisions;
override any port in `.env` (created from `.env.example` on first `make dev`).

> Started the stack before P1 existed? The API's database role is created by
> `infra/postgres/init.sql` on first boot only. Apply it once by hand:
> `docker compose exec -T postgres psql -U shelfsense -d shelfsense < infra/postgres/init.sql`

### Calling the API locally

In the default `SHELFSENSE_AUTH_MODE=dev`, identity comes from headers. Use a tenant id
printed by `make seed`:

```sh
curl -s localhost:8000/v1/stores \
  -H "X-Dev-Tenant: ef23fb7b-a8db-5875-a68a-feddef32c864" \
  -H "X-Dev-Role: manager" | jq .
```

With `SHELFSENSE_AUTH_MODE=jwks` the API verifies Clerk session tokens instead. That needs
Clerk **Organizations** enabled on the instance, and each organisation's id stored in
`tenants.external_org_id`. See [ADR-0002](docs/decisions/0002-tenant-context-and-auth.md).

### Uploading a shelf photo

```sh
SHELF=<shelf id from /v1/stores/{id}/shelves>
curl -s -X POST localhost:8000/v1/shelves/$SHELF/photos \
  -H "X-Dev-Tenant: ef23fb7b-a8db-5875-a68a-feddef32c864" -H "X-Dev-Role: field_agent" \
  -F "file=@apps/api/tests/fixtures/photos/dairy_two_stockouts.png;type=image/png"
make process-jobs          # or keep `make worker` running
curl -s localhost:8000/v1/photos/<photo id> -H "X-Dev-Tenant: ..." -H "X-Dev-Role: reviewer"
```

The response carries the model's extraction (items, stock-outs, share of shelf, regions),
a derived planogram compliance summary, and the run's tokens, cost and latency. Model,
effort and provider are settings; tests replay recorded responses and never call the API.
See [ADR-0003](docs/decisions/0003-llm-layer-and-jobs.md).

### What happens next: the planner

A finished extraction queues a planner run automatically. The planner reads inventory,
proposes reorders, notifies managers, and may dispatch a technician, through typed tools
that are logged call by call. Guardrails decide which actions a human must review
(low confidence, high cost, or a kind the triggering role may not approve):

```sh
curl -s localhost:8000/v1/runs?kind=planner -H "X-Dev-Tenant: ..." -H "X-Dev-Role: manager"
curl -s "localhost:8000/v1/actions?status=pending_review" -H ...
curl -s localhost:8000/v1/tools -H ...                       # tools your role may call
curl -s -X POST localhost:8000/v1/tools/get_inventory -H ... -d '{"store_id": "..."}'
```

The same tools are exposed to any MCP client by `packages/mcp-tools`:

```sh
pnpm --filter @shelfsense/mcp-tools build
SHELFSENSE_DEV_TENANT=<tenant id> SHELFSENSE_DEV_ROLE=manager node packages/mcp-tools/dist/index.js
```

See [ADR-0004](docs/decisions/0004-planner-tools-and-guardrails.md).

### Human review and the golden set

`make web` serves the dashboard on http://localhost:3000. In dev auth mode the control at
the top right switches tenant and role. The **Review queue** page lists held actions
(approve with an edited quantity, or reject) and low-confidence photos (correct the facings
per slot, mark the model right, or mark the photo unusable). A stored verdict can be
promoted into the golden set that the evals (P6) run against.

```sh
curl -s localhost:8000/v1/review/queue -H ... -H "X-Dev-Role: reviewer"
curl -s -X POST localhost:8000/v1/actions/<id>/approve -H ... -d '{"quantity": 12}'
curl -s -X POST localhost:8000/v1/extractions/<id>/review -H ... -d '{"verdict": "correct"}'
curl -s -X POST localhost:8000/v1/labels/<id>/promote -H ... -d '{"tags": ["chiller"]}'
```

See [ADR-0005](docs/decisions/0005-review-queue-and-web-auth.md).

### Cold chain: telemetry and anomalies

```sh
make ingest      # subscribes to MQTT: shelfsense/<tenant slug>/telemetry/<device id>
make simulate    # fridges + vans at 10x speed; the Ikeja fridge overheats after 2 simulated minutes
make worker      # the anomaly queues a planner run (dispatch proposals go to review)
```

Open the **Cold chain** page for live readings (SSE) and anomalies. Readings can also be
posted over HTTP (`POST /v1/telemetry`). Rule: above 8°C for 15 minutes of device time.
See [ADR-0006](docs/decisions/0006-telemetry-and-anomalies.md).

## Layout

```
apps/api            FastAPI, SQLAlchemy 2 async, Alembic          (Python, uv)
apps/simulator      MQTT telemetry publisher                       (Python, uv)
apps/web            Next.js 15 App Router, Tailwind, shadcn/ui     (pnpm)
packages/shared     zod schemas + TS types generated from OpenAPI  (pnpm)
packages/mcp-tools  MCP server exposing inventory/notify/maps      (pnpm)
evals/              golden set, scorers, CLI, CI score-delta       (P6)
infra/              mosquitto + postgres init config
docs/decisions/     ADRs
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
| P6    | Evals package, dataset, scoring, CLI, CI PR comment                  | next   |
| P7    | OTel + Langfuse spans, cost, Prometheus, dashboard pages             |        |
| P8    | Web polish: streaming agent UI, maps, mobile upload PWA, a11y        |        |
| P9    | Deploy (Fly + Vercel), demo tenant, architecture docs, Loom script   |        |

Definition of done per phase: tests green, `make verify` clean, docs updated, conventional
commit proposed.

## Decisions

Architecture decisions are recorded in [docs/decisions/](docs/decisions/). Start with
[ADR-0001: stack choices](docs/decisions/0001-stack-choices.md).

## License

[MIT](LICENSE)
