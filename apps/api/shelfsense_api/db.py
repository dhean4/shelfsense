"""Async engine, sessions and the tenant context that makes row-level security work.

Every request runs inside one transaction that first calls ``set_config`` for
``app.tenant_id`` and ``app.role``. The RLS policies (migration 0001) read those settings,
so a query that forgets the context sees no rows rather than every row.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from shelfsense_api.models import Role

_engines: dict[str, AsyncEngine] = {}

# The background worker acts on the tenant's behalf without being a user. Policies on
# system-written tables (migration 0002) list it explicitly.
SYSTEM_ROLE = "system"


def sqlalchemy_url(dsn: str) -> URL:
    """Turn a plain ``postgresql://`` DSN into the asyncpg dialect URL SQLAlchemy needs."""
    return make_url(dsn).set(drivername="postgresql+asyncpg")


def get_engine(dsn: str) -> AsyncEngine:
    """Return the process-wide engine for ``dsn``, creating it on first use."""
    engine = _engines.get(dsn)
    if engine is None:
        engine = create_async_engine(sqlalchemy_url(dsn), pool_pre_ping=True)
        _engines[dsn] = engine
    return engine


async def dispose_engines() -> None:
    """Close every pool. Called on application shutdown and between test sessions."""
    for engine in _engines.values():
        await engine.dispose()
    _engines.clear()


def session_factory(dsn: str) -> async_sessionmaker[AsyncSession]:
    """Session maker bound to the engine for ``dsn``."""
    return async_sessionmaker(get_engine(dsn), expire_on_commit=False)


SET_TENANT_CONTEXT = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), set_config('app.role', :role, true)"
)


@asynccontextmanager
async def tenant_session(
    dsn: str, tenant_id: UUID, role: Role | str
) -> AsyncIterator[AsyncSession]:
    """One transaction scoped to ``tenant_id``. Commits on success, rolls back on error.

    ``set_config(..., true)`` is transaction-local, so the context cannot leak into the
    next request that reuses the pooled connection. ``role`` is a user role or
    :data:`SYSTEM_ROLE`.
    """
    role_name = role.value if isinstance(role, Role) else role
    async with session_factory(dsn)() as session, session.begin():
        await session.execute(SET_TENANT_CONTEXT, {"tenant_id": str(tenant_id), "role": role_name})
        yield session
