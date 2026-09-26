"""A throwaway Postgres (pgvector image) per test session, migrated and seeded.

Everything here is marked ``integration`` and needs Docker. The container is started
synchronously; async work inside uses ``asyncio.run`` and disposes its engines so the
session-scoped test loop never sees a connection bound to another loop.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from testcontainers.community.minio import MinioContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from shelfsense_api.cli import alembic_config
from shelfsense_api.config import Settings, get_settings
from shelfsense_api.db import dispose_engines
from shelfsense_api.jobs import reset_job_singletons
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.llm.provider import get_provider, reset_providers
from shelfsense_api.main import create_app
from shelfsense_api.models import Role
from shelfsense_api.seed import seed_database, stable_id
from shelfsense_api.storage import PhotoStore

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


@dataclass(frozen=True)
class ObjectStore:
    endpoint_url: str
    access_key: str
    secret_key: str


@pytest.fixture(scope="session")
def object_store() -> Iterator[ObjectStore]:
    # Same images as docker-compose.yml, so nothing extra is pulled.
    with MinioContainer(image="minio/minio:latest") as minio:
        host, port = minio.get_container_host_ip(), minio.get_exposed_port(9000)
        yield ObjectStore(
            endpoint_url=f"http://{host}:{port}",
            access_key=minio.access_key,
            secret_key=minio.secret_key,
        )


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer(image="redis:7-alpine") as redis:
        host, port = redis.get_container_host_ip(), redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(autouse=True)
def _point_settings_at_containers(
    database: Database, object_store: ObjectStore, redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELFSENSE_DATABASE_URL", database.app_url)
    monkeypatch.setenv("SHELFSENSE_MIGRATION_DATABASE_URL", database.owner_url)
    monkeypatch.setenv("SHELFSENSE_AUTH_MODE", "dev")
    monkeypatch.setenv("SHELFSENSE_REDIS_URL", redis_url)
    # A fresh stream per test so a job chained by one test never leaks into the next.
    monkeypatch.setenv("SHELFSENSE_JOB_STREAM", f"test:jobs:{uuid4().hex}")
    monkeypatch.setenv("SHELFSENSE_S3_ENDPOINT_URL", object_store.endpoint_url)
    monkeypatch.setenv("SHELFSENSE_S3_ACCESS_KEY", object_store.access_key)
    monkeypatch.setenv("SHELFSENSE_S3_SECRET_KEY", object_store.secret_key)
    monkeypatch.setenv("SHELFSENSE_S3_BUCKET_PHOTOS", "photos-test")
    monkeypatch.setenv("SHELFSENSE_LLM_PROVIDER", "fake")
    get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def _reset_singletons() -> Iterator[None]:
    reset_job_singletons()
    reset_providers()
    yield
    reset_job_singletons()
    reset_providers()


@pytest.fixture
async def bucket(object_store: ObjectStore) -> Settings:
    """Settings for the test bucket, created if missing."""
    settings = get_settings()
    await PhotoStore(settings).ensure_bucket()
    return settings


@pytest.fixture
def fake_llm() -> FakeProvider:
    """The FakeProvider the worker will use, cleared for this test."""
    provider = get_provider(get_settings())
    provider = getattr(provider, "inner", provider)  # unwrap the tracing layer
    assert isinstance(provider, FakeProvider)
    provider.queue.clear()
    provider.requests.clear()
    provider.script = None
    return provider


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
