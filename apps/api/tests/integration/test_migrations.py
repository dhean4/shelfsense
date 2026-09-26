"""Migrations apply, and the models do not drift from what the migrations built."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from shelfsense_api.db import get_engine
from shelfsense_api.models import TENANT_TABLES, Base

from .conftest import Database

pytestmark = pytest.mark.integration


async def test_head_revision_is_applied(database: Database) -> None:
    async with get_engine(database.owner_url).connect() as conn:
        version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    assert version == "0005"


async def test_models_match_migrations(database: Database) -> None:
    def _diff(sync_conn: Connection) -> list[object]:
        context = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        diffs: list[object] = compare_metadata(context, Base.metadata)
        return diffs

    async with get_engine(database.owner_url).connect() as conn:
        diffs = await conn.run_sync(_diff)
    assert diffs == [], f"models and migrations have drifted:\n{diffs}"


async def test_rls_is_forced_on_every_tenant_table(database: Database) -> None:
    async with get_engine(database.owner_url).connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname IN :names"
            ).bindparams(bindparam("names", expanding=True)),
            {"names": ["tenants", *TENANT_TABLES]},
        )
        flags = {name: (enabled, forced) for name, enabled, forced in rows}
    assert len(flags) == len(TENANT_TABLES) + 1
    assert all(enabled and forced for enabled, forced in flags.values()), flags


async def test_superuser_connection_is_refused(database: Database) -> None:
    """The guard that turns a misconfigured DSN into a loud failure instead of a leak."""
    from shelfsense_api.config import Settings
    from shelfsense_api.health import RlsBypassError, check_rls_enforced

    with pytest.raises(RlsBypassError, match="bypasses row-level security"):
        await check_rls_enforced(Settings(database_url=database.owner_url))
    # And the app role passes the same check.
    await check_rls_enforced(Settings(database_url=database.app_url))


async def test_app_role_without_context_sees_nothing(database: Database) -> None:
    """Fail closed: a query that forgot to set the tenant returns zero rows."""
    async with get_engine(database.app_url).connect() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM stores"))
    assert count == 0
