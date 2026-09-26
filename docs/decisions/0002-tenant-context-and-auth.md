# ADR-0002: Tenant context, row-level security and caller identity

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P1

## Context

Every row belongs to a tenant and no query may cross tenants. ADR-0001 chose Postgres RLS
as the mechanism and Clerk as the identity provider. This ADR records how the two meet.

## Decisions

### 1. Two database roles

The Compose superuser (`shelfsense`) owns the schema and runs Alembic and the seed. The API
connects as `shelfsense_app`, a plain login role with table privileges granted by the
migration. Superusers bypass RLS unconditionally, so a single-role setup would have made
every policy decorative. Both DSNs are settings (`SHELFSENSE_DATABASE_URL`,
`SHELFSENSE_MIGRATION_DATABASE_URL`); the role itself is created by `infra/postgres/init.sql`
locally and by the operator in production, because a migration must not carry a password.

### 2. Transaction-local context, fail closed

Each request runs in one transaction that first calls
`set_config('app.tenant_id', …, true)` and `set_config('app.role', …, true)`. Policies read
`NULLIF(current_setting('app.tenant_id', true), '')::uuid`, so a missing or empty setting
matches no rows. `FORCE ROW LEVEL SECURITY` is on so even the owner is subject to policies.
The `tenant_write` policy additionally requires `app.role` in `('owner','manager')`, which
duplicates the API's 403 check as defence in depth.

_Rejected_: a `tenant_id` filter in every query (one forgotten `WHERE` leaks data), and
one schema per tenant (migrations multiply, connection pools fragment).

### 3. Foreign keys are not tenant-aware

A FK check runs as the table owner and ignores RLS, so a caller could reference another
tenant's row by id. Routes that accept foreign ids (planogram slots → SKUs) re-select the
ids under the caller's context and reject any that are not visible. Tested in
`test_rls.py::test_planogram_rejects_other_tenants_skus`.

### 4. Identity: verified JWT, organisation → tenant via SECURITY DEFINER lookup

In `jwks` mode the API verifies the bearer token against Clerk's JWKS (PyJWT, RS256,
issuer checked, audience when configured) and reads `sub`, `org_id`, `org_role`. The
organisation id must be turned into a tenant id before any tenant context exists, which
RLS would block; `resolve_tenant_by_org(text)` is a `SECURITY DEFINER` function owned by
the schema owner that performs exactly that one lookup. Clerk roles map through
`auth.ORG_ROLE_MAP`; `org:admin` (Clerk's default) is an owner. Unmapped roles get 403.

### 5. A dev mode that cannot reach production

`SHELFSENSE_AUTH_MODE=dev` takes identity from `X-Dev-Tenant`, `X-Dev-Role`, `X-Dev-User`.
It exists so the seed and the integration suite run without Clerk. `Settings` refuses to
construct with `env=production` and any mode but `jwks`, so the bypass cannot be deployed
by accident.

## Consequences

- Clerk **Organizations** must be enabled on the instance and each organisation's id stored
  in `tenants.external_org_id` (the seed leaves it null; set it when linking a real org).
- Every new tenant-owned table needs: `tenant_id`, RLS enabled and forced, the two policies.
  `test_rls_is_forced_on_every_tenant_table` lists the tables explicitly so a new table
  fails the test until it is added.
- Connection pools are shared across tenants; isolation is per transaction, never per
  connection. Anything that runs outside `tenant_session` sees nothing.
- A misconfigured `SHELFSENSE_DATABASE_URL` pointing at the schema owner would silently
  disable isolation (this happened once during P1 with a stale `.env`). The API therefore
  inspects `pg_roles` for its own role at startup and in `/readyz`
  (`health.check_rls_enforced`) and refuses to serve as a superuser or `BYPASSRLS` role.
