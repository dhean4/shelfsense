"""Liveness and readiness probes.

``/healthz`` answers as soon as the process serves HTTP. ``/readyz`` opens a fresh
connection to each hard dependency with a short timeout and reports per-dependency
status, returning 503 when any of them fails so an orchestrator stops routing traffic.
The probe functions are module-level so tests can replace them without a live stack.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from shelfsense_api.config import Settings, get_settings

router = APIRouter(tags=["health"])

CheckStatus = Literal["ok", "fail"]


class CheckResult(BaseModel):
    """Outcome of probing one dependency."""

    status: CheckStatus
    detail: str | None = None


class HealthResponse(BaseModel):
    """Body of ``/healthz``."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Body of ``/readyz``."""

    status: Literal["ok", "degraded"]
    checks: dict[str, CheckResult]


async def check_postgres(settings: Settings) -> None:
    """Open a connection, run ``SELECT 1`` and close. Raises on any failure."""
    conn = await asyncpg.connect(settings.database_url, timeout=settings.readiness_timeout_seconds)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()


class RlsBypassError(RuntimeError):
    """The runtime database role can bypass row-level security."""


async def check_rls_enforced(settings: Settings) -> None:
    """Fail if the runtime role is a superuser or has BYPASSRLS.

    Such a role sees every tenant's rows, so tenant isolation would be silently off.
    Checked at startup and on every ``/readyz``; see ADR-0002.
    """
    conn = await asyncpg.connect(settings.database_url, timeout=settings.readiness_timeout_seconds)
    try:
        row = await conn.fetchrow(
            "SELECT current_user AS role, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )
    finally:
        await conn.close()
    if row is None:  # pragma: no cover — current_user always exists
        raise RlsBypassError("could not inspect the current database role")
    if row["rolsuper"] or row["rolbypassrls"]:
        raise RlsBypassError(
            f"database role {row['role']!r} bypasses row-level security "
            "(superuser or BYPASSRLS); point SHELFSENSE_DATABASE_URL at the app role"
        )


async def check_redis(settings: Settings) -> None:
    """``PING`` Redis over a throwaway client. Raises on any failure."""
    client = aioredis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


Probe = Callable[[Settings], Awaitable[None]]

# Name → probe. The object store and the MQTT broker are candidates for later.
PROBES: dict[str, Probe] = {
    "postgres": check_postgres,
    "rls": check_rls_enforced,
    "redis": check_redis,
}


async def _run_probe(name: str, probe: Probe, settings: Settings) -> tuple[str, CheckResult]:
    try:
        await asyncio.wait_for(probe(settings), timeout=settings.readiness_timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — a probe must never take the endpoint down
        return name, CheckResult(status="fail", detail=f"{type(exc).__name__}: {exc}")
    return name, CheckResult(status="ok")


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness: the process is up and serving HTTP."""
    return HealthResponse()


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readyz(response: Response) -> ReadinessResponse:
    """Readiness: every hard dependency answered within the timeout."""
    settings = get_settings()
    results = await asyncio.gather(
        *(_run_probe(name, probe, settings) for name, probe in PROBES.items())
    )
    checks = dict(results)
    healthy = all(check.status == "ok" for check in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if healthy else "degraded", checks=checks)
