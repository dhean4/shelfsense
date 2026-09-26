"""Tenant-scoped live events over Redis pub/sub, served as server-sent events.

Used by telemetry (readings, anomalies) and by agent runs (turns, tool results, finish).
Any number of API replicas can serve a stream because the fan-out is in Redis.
"""

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis


def runs_channel(tenant_id: UUID) -> str:
    """Channel carrying a tenant's agent-run events."""
    return f"shelfsense:runs:{tenant_id}"


async def publish(redis_url: str, channel: str, payloads: list[dict[str, Any]]) -> None:
    """Publish JSON payloads to a channel (one connection per call; fine at our rates)."""
    if not payloads:
        return
    client = aioredis.from_url(redis_url)
    try:
        for payload in payloads:
            await client.publish(channel, json.dumps(payload, default=str))
    finally:
        await client.aclose()


async def sse_stream(
    redis_url: str,
    channel: str,
    *,
    heartbeat_seconds: int,
    stop: asyncio.Event | None = None,
    event_key: str = "type",
) -> AsyncGenerator[str]:
    """Server-sent events for a channel: ``event:`` from the payload's type, ``data:`` JSON."""
    client = aioredis.from_url(redis_url)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        yield "event: hello\ndata: {}\n\n"
        while stop is None or not stop.is_set():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=heartbeat_seconds
            )
            if message is None:
                yield ": heartbeat\n\n"
                continue
            raw = message["data"]
            data = raw.decode() if isinstance(raw, bytes) else str(raw)
            kind = json.loads(data).get(event_key, "message")
            yield f"event: {kind}\ndata: {data}\n\n"
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await client.aclose()


class RunEvents:
    """Collects a run's progress events and publishes them as they happen."""

    def __init__(self, redis_url: str, tenant_id: UUID, run_id: UUID, kind: str) -> None:
        """Bind to one run."""
        self._redis_url = redis_url
        self._channel = runs_channel(tenant_id)
        self.run_id = run_id
        self.kind = kind
        self.emitted: list[dict[str, Any]] = []

    async def emit(self, event_type: str, **fields: Any) -> None:
        """Publish one event, tagged with the run."""
        payload = {"type": event_type, "run_id": str(self.run_id), "kind": self.kind, **fields}
        self.emitted.append(payload)
        await publish(self._redis_url, self._channel, [payload])

    async def hook(self, event_type: str, fields: dict[str, Any]) -> None:
        """The shape the agent loops call: ``on_event(type, fields)``."""
        await self.emit(event_type, **fields)


EventHook = Callable[[str, dict[str, Any]], Awaitable[None]]
