"""MQTT subscriber: ``<prefix>/<tenant slug>/telemetry/<device external id>`` → ingest.

Runs as its own process (``shelfsense-api ingest``). Each message becomes one reading
ingested under the tenant's RLS context as the system role; events go to the SSE stream
and an opened anomaly queues a planner run.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

import aiomqtt
from pydantic import ValidationError

from shelfsense_api.config import Settings, get_settings
from shelfsense_api.db import SYSTEM_ROLE, tenant_session
from shelfsense_api.jobs import PLAN_ANOMALY, get_queue
from shelfsense_api.telemetry import (
    ReadingIn,
    ingest_readings,
    publish_events,
    resolve_tenant_by_slug,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopicParts:
    """What a telemetry topic encodes."""

    tenant_slug: str
    device_external_id: str


def parse_topic(topic: str, prefix: str) -> TopicParts | None:
    """``prefix/<slug>/telemetry/<device>`` → parts, or ``None`` for anything else."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != prefix or parts[2] != "telemetry":
        return None
    if not parts[1] or not parts[3]:
        return None
    return TopicParts(tenant_slug=parts[1], device_external_id=parts[3])


def parse_payload(device_external_id: str, payload: bytes) -> ReadingIn | None:
    """JSON body → reading; the topic's device id wins over one in the body."""
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            return None
        data["device_external_id"] = device_external_id
        return ReadingIn.model_validate(data)
    except (ValueError, ValidationError) as exc:
        log.warning("bad telemetry payload for %s: %s", device_external_id, exc)
        return None


def broker_from_url(url: str) -> tuple[str, int]:
    """``mqtt://host:port`` → (host, port)."""
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or 1883


class Ingester:
    """Holds the tenant-slug cache and does the per-message work (testable without MQTT)."""

    def __init__(self, settings: Settings) -> None:
        """Bind to settings; the queue and Redis come from them."""
        self.settings = settings
        self._tenants: dict[str, UUID | None] = {}
        self.handled = 0
        self.dropped = 0

    async def tenant_for(self, slug: str) -> UUID | None:
        """Cached slug → tenant id."""
        if slug not in self._tenants:
            self._tenants[slug] = await resolve_tenant_by_slug(self.settings, slug)
        return self._tenants[slug]

    async def handle(self, topic: str, payload: bytes) -> bool:
        """Ingest one message. Returns whether it was accepted."""
        parts = parse_topic(topic, self.settings.mqtt_topic_prefix)
        if parts is None:
            self.dropped += 1
            return False
        reading = parse_payload(parts.device_external_id, payload)
        tenant_id = await self.tenant_for(parts.tenant_slug) if reading else None
        if reading is None or tenant_id is None:
            log.warning("dropping message on %s (unknown tenant or bad payload)", topic)
            self.dropped += 1
            return False
        async with tenant_session(self.settings.database_url, tenant_id, SYSTEM_ROLE) as session:
            result = await ingest_readings(session, self.settings, tenant_id, [reading])
        if result.unknown_devices:
            log.warning(
                "unknown device %s for tenant %s", result.unknown_devices, parts.tenant_slug
            )
            self.dropped += 1
            return False
        await publish_events(self.settings.redis_url, tenant_id, result.events)
        queue = get_queue(self.settings)
        for anomaly_id in result.opened_anomalies:
            await queue.enqueue(
                PLAN_ANOMALY, {"anomaly_id": str(anomaly_id), "tenant_id": str(tenant_id)}
            )
        self.handled += 1
        return True


async def run_ingest(
    settings: Settings | None = None, *, stop: asyncio.Event | None = None
) -> None:
    """Subscribe and ingest until ``stop`` is set (or forever)."""
    settings = settings or get_settings()
    host, port = broker_from_url(settings.mqtt_url)
    ingester = Ingester(settings)
    topic = f"{settings.mqtt_topic_prefix}/+/telemetry/+"
    while stop is None or not stop.is_set():
        try:
            async with aiomqtt.Client(host, port, identifier="shelfsense-ingest") as client:
                await client.subscribe(topic)
                log.info("subscribed to %s on %s:%d", topic, host, port)
                async for message in client.messages:
                    if stop is not None and stop.is_set():
                        break
                    payload = message.payload if isinstance(message.payload, bytes) else b""
                    await ingester.handle(str(message.topic), payload)
        except aiomqtt.MqttError as exc:
            log.warning("mqtt connection lost (%s); reconnecting in 3s", exc)
            await asyncio.sleep(3)
