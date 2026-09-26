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
make api         # FastAPI on :8000  →  curl localhost:8000/readyz
make web         # Next.js on :3000
make verify      # everything CI runs
make dev-full    # + Langfuse on :3001 (login: dev@shelfsense.local / shelfsense-dev-password)
```

Postgres sits on host port 5433 and Langfuse on 3001 to avoid the usual collisions;
override any port in `.env` (created from `.env.example` on first `make dev`).

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
| P1    | Data model, migrations, RLS, seed; OpenAPI spec → generated TS types | next   |
| P2    | Photo upload, job queue, vision agent, provider abstraction          |        |
| P3    | MCP tools, planner loop, tool-call logging, guardrails               |        |
| P4    | Human review queue + promote-to-golden-set                           |        |
| P5    | Simulator, MQTT ingest, SSE stream, anomaly rule                     |        |
| P6    | Evals package, dataset, scoring, CLI, CI PR comment                  |        |
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
