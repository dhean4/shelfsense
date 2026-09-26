# Working agreement

You are pairing with a senior full-stack engineer. Work in small, reviewable steps.
Before writing code for a phase: restate the plan in ≤10 bullets, list the files you will
create/modify, then wait for "go".
After each phase: run the tests, run lint/typecheck, summarise what changed, propose the
commit message (conventional commits, don't add claude as a contributor to the commit on github), and STOP.
Never fake data, never stub an integration silently — if something can't run here, say so
and leave a TODO with the exact reason.
Prefer boring, well-documented choices over clever ones; explain any tradeoff in a
`docs/decisions/NNNN-*.md` ADR.

# Repo conventions

- **Phases** are the unit of work. The phase list and the definition of done live in
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). One phase per session; do not start the next
  without "go".
- **Commits**: conventional commits, no co-author trailers. The owner commits after review;
  propose the message, do not run `git commit`.
- **Verification** is `make verify` (ruff, ruff format, mypy --strict, pytest, prettier,
  eslint, tsc, next build). CI runs the same commands. Green before proposing a commit.
- **Python**: uv workspace at the root; each service is a member under `apps/`. Google
  docstrings, `mypy --strict`, no blind `except Exception` without a `# noqa: BLE001` and a
  reason. Tests never hit a live service unless marked `integration` or `network`.
- **TypeScript**: pnpm workspace + Turborepo. `tsconfig.base.json` is strict and every
  package extends it. `apps/web` has its own Next.js ESLint config; `packages/*` use the
  root `eslint.config.mjs`.
- **Contracts**: the OpenAPI spec in `apps/api` is written before the route (P1 onwards).
  `packages/shared` regenerates TS types from it; a CI drift job (P1) fails when they
  diverge. Zod schemas in `packages/shared` mirror the Pydantic models.
- **Config**: every runtime value comes from `SHELFSENSE_*` env vars via
  `shelfsense_api/config.py`. Model IDs are config, never literals near a prompt.
- **Ports**: Postgres is on host 5433 and Langfuse on 3001; see ADR-0001 before changing.
- **Multi-tenancy** (P1 onwards): `tenant_id` on every table, RLS on, roles
  owner / manager / field_agent / reviewer. No query bypasses RLS outside migrations.
