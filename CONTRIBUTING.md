# Contributing

Thanks for looking. This is a portfolio project, but it is run like a small product, so the
bar is: green checks, a reviewable diff, and a decision record when something is a choice.

## Setup

```sh
make install && make dev && make migrate && make seed
make api      # :8000        make worker      # jobs
make web      # :3000        make ingest      # MQTT
make verify   # ruff, mypy --strict, pytest, prettier, eslint, tsc, next build
```

Docker, Node 22, pnpm 10 and uv are required. `make` on macOS may be the Xcode stub; use
`brew install make` or run the underlying commands from the Makefile.

## How changes land

1. Open an issue or a short design note for anything bigger than a bug fix.
2. Branch from `main`; keep commits in conventional-commit form (`feat(api): …`).
3. `make verify` locally. Integration tests need Docker (testcontainers).
4. If you changed a prompt, a schema or a guardrail, re-record fixtures
   (`make eval-rerecord`, needs an API key) and commit them with the new baseline when
   the scores are acceptable. CI posts the delta on the PR.
5. If you made a choice someone might reasonably have made differently, add
   `docs/decisions/NNNN-*.md`.

## Conventions

- **Contract first**: edit `apps/api/openapi.yaml`, then implement; CI fails if the
  generated document or the TS types drift.
- **Every tenant-owned table** carries `tenant_id`, RLS enabled and forced, and both
  policies (migrations 0001–0005 are the pattern).
- **No silent stubs**: if something cannot run here, say so and leave a `TODO(Pn)` with
  the reason.
- **Model IDs and prices are configuration**, never literals near a prompt.
- Python: ruff (google docstrings), `mypy --strict`. TypeScript: strict, typescript-eslint
  strict-type-checked, prettier.

## Reporting a security issue

Email the maintainer (see the GitHub profile) rather than opening a public issue.
