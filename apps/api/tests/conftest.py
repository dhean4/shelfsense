from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from shelfsense_api.config import get_settings
from shelfsense_api.main import create_app


@pytest.fixture(autouse=True)
def _test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in the ``test`` environment with a fresh settings cache."""
    monkeypatch.setenv("SHELFSENSE_ENV", "test")
    monkeypatch.setenv("SHELFSENSE_READINESS_TIMEOUT_SECONDS", "0.5")
    get_settings.cache_clear()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An httpx client bound directly to the ASGI app. No sockets, no live services."""
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        yield c
