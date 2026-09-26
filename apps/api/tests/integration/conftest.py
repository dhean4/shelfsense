"""A throwaway Postgres (pgvector image) per test session, migrated and seeded.

Everything here is marked ``integration`` and needs Docker. The container is started
synchronously; async work inside uses ``asyncio.run`` and disposes its engines so the
session-scoped test loop never sees a connection bound to another loop.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from testcontainers.community.postgres import PostgresContainer

from shelfsense_api.cli import alembic_config
from shelfsense_api.config import get_settings
from shelfsense_api.db import dispose_engines
from shelfsense_api.main import create_app
from shelfsense_api.models import Role
from shelfsense_api.seed import seed_database, stable_id

pytestmark = pytest.mark.integration

# Docker Desktop on macOS reports its socket under ~/.docker/run, which containers cannot
# mount; the Ryuk cleanup sidecar then fails to start. The standard path is symlinked to
# the same daemon. A no-op on Linux runners.
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

IMAGE = "pgvector/pgvector:pg16"
OWNER = ("shelfsense", "shelfsense")
APP = ("shelfsense_app", "shelfsense_app")


@dataclass(frozen=True)
class Database:
    owner_url: str
    app_url: str


async def _create_app_role(owner_url: str) -> None:
    conn = await asyncpg.connect(owner_url)
    try:
        await conn.execute(f"CREATE ROLE {APP[0]} LOGIN PASSWORD '{APP[1]}'")
    finally:
        await conn.close()


async def _seed_and_dispose(owner_url: str) -> None:
    await seed_database(owner_url)
    await dispose_engines()


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    container = PostgresContainer(
        IMAGE, username=OWNER[0], password=OWNER[1], dbname="shelfsense", driver=None
    )
    with container:
        owner_url = container.get_connection_url()
        asyncio.run(_create_app_role(owner_url))
        command.upgrade(alembic_config(owner_url), "head")
        asyncio.run(_seed_and_dispose(owner_url))
        app_url = owner_url.replace(f"{OWNER[0]}:{OWNER[1]}@", f"{APP[0]}:{APP[1]}@", 1)
        yield Database(owner_url=owner_url, app_url=app_url)


@pytest.fixture(autouse=True)
def _point_settings_at_container(database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELFSENSE_DATABASE_URL", database.app_url)
    monkeypatch.setenv("SHELFSENSE_MIGRATION_DATABASE_URL", database.owner_url)
    monkeypatch.setenv("SHELFSENSE_AUTH_MODE", "dev")
    get_settings.cache_clear()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
async def _dispose_after_session() -> AsyncIterator[None]:
    yield
    await dispose_engines()


def tenant_id(slug: str) -> UUID:
    """The seeded tenant id for a slug (UUIDv5, so no lookup needed)."""
    return stable_id("tenant", slug)


def dev_headers(slug: str, role: Role, user: str | None = None) -> dict[str, str]:
    """Dev-mode auth headers for a seeded tenant."""
    return {
        "X-Dev-Tenant": str(tenant_id(slug)),
        "X-Dev-Role": role.value,
        "X-Dev-User": user or f"dev_{slug}_{role.value}",
    }
