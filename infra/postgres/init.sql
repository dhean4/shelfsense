-- Runs once, on first boot of the postgres volume.
-- The application schema and the pgvector extension are owned by Alembic (P1). This file
-- only creates what a migration must not own: Langfuse's database and the API's
-- non-superuser login role (a superuser would bypass row-level security).
--
-- Started the stack before this file existed? Apply it by hand once:
--   docker compose exec -T postgres psql -U shelfsense -d shelfsense < infra/postgres/init.sql
CREATE DATABASE langfuse;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shelfsense_app') THEN
    CREATE ROLE shelfsense_app LOGIN PASSWORD 'shelfsense_app';
  END IF;
END
$$;
