"""Telemetry ingest, the cold-chain anomaly rule, and the live event stream.

The rule is deliberately simple and stated in one place: a fridge whose every reading has
been above ``fridge_max_temp_c`` for at least ``fridge_excursion_minutes`` has an open
anomaly. Time is the device's ``recorded_at``, not the server clock, so replayed or
accelerated telemetry (the simulator) behaves the same as live data.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import redis.asyncio as aioredis
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shelfsense_api.config import Settings
from shelfsense_api.db import get_engine
from shelfsense_api.models import Anomaly, AnomalyStatus, Device, DeviceKind, TelemetryReading
from shelfsense_api.observability import ANOMALIES

TEMP_EXCURSION = "temp_excursion"


class ReadingIn(BaseModel):
    """One sample as devices (and the simulator) send it."""

    device_external_id: str = Field(min_length=1, max_length=64)
    recorded_at: datetime
    temperature_c: float | None = Field(default=None, ge=-60, le=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    battery_pct: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _tz_aware(self) -> "ReadingIn":
        if self.recorded_at.tzinfo is None:
            self.recorded_at = self.recorded_at.replace(tzinfo=UTC)
        return self


@dataclass(frozen=True)
class Sample:
    """The minimum the rule needs."""

    recorded_at: datetime
    temperature_c: float


def excursion_start(
    samples: list[Sample], max_temp_c: float, min_duration: timedelta
) -> datetime | None:
    """Start of a qualifying excursion, or ``None``.

    ``samples`` is the device's recent history in time order. The trailing run of samples
    above ``max_temp_c`` qualifies when it spans at least ``min_duration``.
    """
    if not samples or samples[-1].temperature_c <= max_temp_c:
        return None
    start = samples[-1].recorded_at
    for sample in reversed(samples):
        if sample.temperature_c <= max_temp_c:
            break
        start = sample.recorded_at
    return start if samples[-1].recorded_at - start >= min_duration else None


@dataclass
class TelemetryEvent:
    """Something the live stream and the planner care about."""

    type: Literal["reading", "anomaly_opened", "anomaly_resolved"]
    device_id: UUID
    device_external_id: str
    payload: dict[str, Any]


@dataclass
class IngestResult:
    """What ingesting a batch did."""

    accepted: int = 0
    unknown_devices: list[str] = field(default_factory=list)
    events: list[TelemetryEvent] = field(default_factory=list)
    opened_anomalies: list[UUID] = field(default_factory=list)


async def ingest_readings(
    session: AsyncSession, settings: Settings, tenant_id: UUID, readings: list[ReadingIn]
) -> IngestResult:
    """Store readings for known devices and run the rule for each fridge touched."""
    result = IngestResult()
    if not readings:
        return result
    wanted = {r.device_external_id for r in readings}
    devices = {
        d.external_id: d
        for d in await session.scalars(select(Device).where(Device.external_id.in_(wanted)))
    }
    result.unknown_devices = sorted(wanted - set(devices))
    touched: dict[UUID, Device] = {}
    for reading in readings:
        device = devices.get(reading.device_external_id)
        if device is None:
            continue
        session.add(
            TelemetryReading(
                tenant_id=tenant_id,
                device_id=device.id,
                recorded_at=reading.recorded_at,
                temperature_c=reading.temperature_c,
                latitude=reading.latitude,
                longitude=reading.longitude,
                battery_pct=reading.battery_pct,
            )
        )
        result.accepted += 1
        result.events.append(
            TelemetryEvent(
                "reading",
                device.id,
                device.external_id,
                json.loads(reading.model_dump_json()),
            )
        )
        touched[device.id] = device
    await session.flush()

    for device in touched.values():
        if device.kind != DeviceKind.fridge:
            continue
        await _apply_rule(session, settings, tenant_id, device, result)
    await session.flush()
    return result


async def _recent_samples(session: AsyncSession, device_id: UUID, limit: int = 500) -> list[Sample]:
    rows = await session.execute(
        select(TelemetryReading.recorded_at, TelemetryReading.temperature_c)
        .where(TelemetryReading.device_id == device_id, TelemetryReading.temperature_c.is_not(None))
        .order_by(TelemetryReading.recorded_at.desc())
        .limit(limit)
    )
    samples = [Sample(recorded_at=r, temperature_c=float(t)) for r, t in rows if t is not None]
    samples.reverse()
    return samples


async def _apply_rule(
    session: AsyncSession, settings: Settings, tenant_id: UUID, device: Device, result: IngestResult
) -> None:
    samples = await _recent_samples(session, device.id)
    if not samples:
        return
    open_anomaly = await session.scalar(
        select(Anomaly).where(Anomaly.device_id == device.id, Anomaly.status == AnomalyStatus.open)
    )
    latest = samples[-1]
    if open_anomaly is not None:
        peak = max(open_anomaly.peak_temperature_c or latest.temperature_c, latest.temperature_c)
        open_anomaly.peak_temperature_c = peak
        if latest.temperature_c <= settings.fridge_max_temp_c:
            open_anomaly.status = AnomalyStatus.resolved
            ANOMALIES.labels("resolved").inc()
            open_anomaly.ended_at = latest.recorded_at
            result.events.append(
                TelemetryEvent(
                    "anomaly_resolved",
                    device.id,
                    device.external_id,
                    {
                        "anomaly_id": str(open_anomaly.id),
                        "ended_at": latest.recorded_at.isoformat(),
                        "peak_temperature_c": peak,
                    },
                )
            )
        return
    start = excursion_start(
        samples, settings.fridge_max_temp_c, timedelta(minutes=settings.fridge_excursion_minutes)
    )
    if start is None:
        return
    anomaly = Anomaly(
        tenant_id=tenant_id,
        device_id=device.id,
        kind=TEMP_EXCURSION,
        status=AnomalyStatus.open,
        started_at=start,
        peak_temperature_c=max(s.temperature_c for s in samples if s.recorded_at >= start),
    )
    session.add(anomaly)
    await session.flush()
    result.opened_anomalies.append(anomaly.id)
    ANOMALIES.labels("opened").inc()
    result.events.append(
        TelemetryEvent(
            "anomaly_opened",
            device.id,
            device.external_id,
            {
                "anomaly_id": str(anomaly.id),
                "started_at": start.isoformat(),
                "peak_temperature_c": anomaly.peak_temperature_c,
                "max_temp_c": settings.fridge_max_temp_c,
            },
        )
    )


# --- live stream over Redis pub/sub ------------------------------------------------------


def channel_for(tenant_id: UUID) -> str:
    """Redis channel carrying a tenant's telemetry events."""
    return f"shelfsense:telemetry:{tenant_id}"


async def publish_events(redis_url: str, tenant_id: UUID, events: list[TelemetryEvent]) -> None:
    """Fan events out to every open SSE stream for the tenant."""
    if not events:
        return
    client = aioredis.from_url(redis_url)
    try:
        for event in events:
            await client.publish(
                channel_for(tenant_id),
                json.dumps(
                    {
                        "type": event.type,
                        "device_id": str(event.device_id),
                        "device_external_id": event.device_external_id,
                        **event.payload,
                    },
                    default=str,
                ),
            )
    finally:
        await client.aclose()


async def event_stream(
    redis_url: str, tenant_id: UUID, *, heartbeat_seconds: int, stop: asyncio.Event | None = None
) -> AsyncGenerator[str]:
    """Server-sent events for a tenant: ``event:`` is the event type, ``data:`` the JSON."""
    client = aioredis.from_url(redis_url)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel_for(tenant_id))
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
            kind = json.loads(data).get("type", "reading")
            yield f"event: {kind}\ndata: {data}\n\n"
    finally:
        await pubsub.unsubscribe(channel_for(tenant_id))
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await client.aclose()


async def resolve_tenant_by_slug(settings: Settings, slug: str) -> UUID | None:
    """Tenant id for a topic's slug, through the SECURITY DEFINER lookup."""
    async with get_engine(settings.database_url).connect() as conn:
        value = await conn.scalar(text("SELECT resolve_tenant_by_slug(:slug)"), {"slug": slug})
    return UUID(str(value)) if value is not None else None
