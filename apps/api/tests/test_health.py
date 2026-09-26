import pytest
from httpx import AsyncClient

from shelfsense_api import health
from shelfsense_api.config import Settings


async def _ok(_: Settings) -> None:
    return None


async def _boom(_: Settings) -> None:
    raise ConnectionRefusedError("connection refused")


async def test_healthz_is_ok_without_any_dependency(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_is_ok_when_every_probe_passes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "PROBES", {"postgres": _ok, "redis": _ok})
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {
        "postgres": {"status": "ok", "detail": None},
        "redis": {"status": "ok", "detail": None},
    }


async def test_readyz_is_503_and_names_the_failing_dependency(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "PROBES", {"postgres": _ok, "redis": _boom})
    response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"]["status"] == "ok"
    assert body["checks"]["redis"] == {
        "status": "fail",
        "detail": "ConnectionRefusedError: connection refused",
    }


async def test_readyz_reports_a_hung_probe_as_a_timeout(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    async def _hang(_: Settings) -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(health, "PROBES", {"postgres": _hang})
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["postgres"]["detail"].startswith("TimeoutError")


@pytest.mark.integration
async def test_readyz_against_the_compose_stack(client: AsyncClient) -> None:
    """Runs only with ``make test-integration`` while ``make dev`` is up."""
    response = await client.get("/readyz")
    assert response.status_code == 200, response.json()
