# ADR-0001: Stack choices and local topology

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P0

## Context

ShelfSense is a portfolio project targeting AI-engineer and agentic-systems roles. The
brief fixes most of the stack (Next.js 15, FastAPI, Postgres + pgvector, Redis, MinIO,
Mosquitto, Langfuse, Anthropic SDK, own agent loop, MCP tools, evals in CI). This ADR
records the choices the brief left open and the local-topology decisions that every later
phase builds on.

## Decisions

### 1. Monorepo: pnpm workspaces + Turborepo for TS, a uv workspace for Python, one Makefile

Turborepo only orchestrates the TypeScript packages. Python services are uv workspace
members with shared ruff/mypy/pytest config in the root `pyproject.toml`. The Makefile is
the single entry point (`make verify`) and CI runs the same commands.

_Rejected_: Nx (heavier, plugin-driven), Bazel/Pants (overkill for two languages and five
packages), running Python through Turborepo (adds a layer with no caching benefit for uv).

### 2. Auth: Clerk, not Auth.js

The FastAPI backend must verify the identity the Next.js app holds. Clerk issues a JWT the
API verifies statelessly against Clerk's JWKS, and Clerk Organizations map one-to-one onto
`tenant_id` with org roles for owner / manager / field_agent / reviewer.

_Rejected_: Auth.js. It is free of vendor lock-in, but its session is Next.js-side; we would
need a custom JWT bridge to the API and hand-built organisation, membership and role
tables, which is exactly the surface area a portfolio project should not hand-roll.

_Tradeoff_: a third-party dependency and keys for the demo deployment. Clerk's free tier
covers the demo. The API only depends on a JWKS URL and a claims shape, so swapping to
another issuer is a config change plus one claims adapter.

Implementation lands in P1 with the RLS policies. This decision is reversible until then.

### 3. Telemetry storage: plain Postgres with a BRIN index, not TimescaleDB

Telemetry is append-only, time-ordered, and queried by (device, time range). A BRIN index
on `recorded_at` gives cheap range scans at this scale. Keeping one Postgres image (the
pgvector build) keeps Compose, CI testcontainers and Fly.io deployment simple.

_Rejected for now_: TimescaleDB. Better compression and continuous aggregates, but it
requires a different image, and the pgvector + Timescale combination on a managed host is
a deployment constraint we do not want in P0. Revisit in P5 if ingest volume or dashboard
queries demand it; the ingest table is designed so a hypertable conversion is a migration.

### 4. Langfuse v3 behind a Compose profile, sharing Postgres, Redis and MinIO

Langfuse v3 needs Postgres, Redis, ClickHouse and S3. We give it its own database on the
shared Postgres server (`infra/postgres/init.sql`), share Redis (running with
`maxmemory-policy noeviction`, which v3 requires and which is also the right policy for our
own job queue), and a `langfuse` bucket on MinIO. ClickHouse is the only extra container.
All three Langfuse services sit under the `observability` profile so `make dev` starts in
seconds and `make dev-full` brings up tracing. Headless bootstrap via `LANGFUSE_INIT_*`
means first boot needs no UI clicks.

_Rejected_: Langfuse v2 (single Postgres, but end-of-life), a separate Postgres for Langfuse
(one more container for no isolation we need locally).

### 5. Local ports: Postgres 5433, Langfuse 3001

The dev machine already runs a Postgres on 5432 and Next.js owns 3000. Both are
overridable through `.env`; nothing in code assumes them beyond the defaults in
`shelfsense_api/config.py`.

### 6. Python tooling: ruff + mypy --strict with the pydantic plugin, pinned

Same configuration as the owner's other projects, so the bar is consistent across the
portfolio. Versions are pinned in the root dev group so CI and local runs agree.

### 7. Health probes open fresh connections

`/readyz` connects to each dependency per request with a short timeout rather than reusing
application pools. Pools arrive in P1; a probe that shares them would report the pool's
state, not the dependency's. The probe registry is a plain dict so later phases append
MinIO and MQTT without touching the endpoint.

### 8. Next.js pinned to 15.x

The brief fixes the major. `create-next-app@15` produced 15.5 with Tailwind v4 and React 19,
and `shadcn init` (defaults: base-nova style, neutral palette, Base UI primitives) laid down
`components.json`, the theme tokens and the Geist font via `next/font`. Note that
`next/font/google` fetches the font once at build time, so `next build` needs network
access; acceptable for CI and Vercel, and the reason `make build` is not part of an
offline `make test`.

## Consequences

- `make dev` is four containers; `make dev-full` is seven.
- P1 must add Alembic (owns the schema and the `vector` extension), the OpenAPI-first
  workflow with the drift check in CI, and the Clerk JWKS verifier.
- Anyone changing a port, the auth provider or the telemetry store must supersede the
  relevant section with a new ADR rather than editing this one.
