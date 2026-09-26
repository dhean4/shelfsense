-- Runs once, on first boot of the postgres volume.
-- The application schema and the pgvector extension are owned by Alembic (P1);
-- this file only gives Langfuse its own database on the shared server.
CREATE DATABASE langfuse;
