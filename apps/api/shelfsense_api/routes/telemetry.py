"""Devices, readings, anomalies, HTTP ingest and the live SSE stream."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from shelfsense_api.deps import CurrentPrincipal, SettingsDep, TenantSession, require_role
from shelfsense_api.jobs import PLAN_ANOMALY
from shelfsense_api.models import Anomaly, AnomalyStatus, Device, Role, Store, TelemetryReading
from shelfsense_api.routes._common import READ_ONE_RESPONSES, READ_RESPONSES, WRITE_RESPONSES
from shelfsense_api.routes.photos import QueueDep
from shelfsense_api.schemas import (
    AnomalyOut,
    DeviceIn,
    DeviceOut,
    IngestIn,
    IngestOut,
    ReadingOut,
)
from shelfsense_api.telemetry import event_stream, ingest_readings, publish_events

router = APIRouter(prefix="/v1", tags=["telemetry"])
writers = Depends(require_role(Role.owner, Role.manager))


async def _device_out(session: TenantSession, device: Device) -> DeviceOut:
    latest = await session.scalar(
        select(TelemetryReading)
        .where(TelemetryReading.device_id == device.id)
        .order_by(TelemetryReading.recorded_at.desc())
        .limit(1)
    )
    open_anomaly = await session.scalar(
        select(Anomaly).where(Anomaly.device_id == device.id, Anomaly.status == AnomalyStatus.open)
    )
    store_name = None
    if device.store_id is not None:
        store_name = await session.scalar(select(Store.name).where(Store.id == device.store_id))
    return DeviceOut(
        id=device.id,
        store_id=device.store_id,
        store_name=store_name,
        kind=device.kind,
        label=device.label,
        external_id=device.external_id,
        created_at=device.created_at,
        latest=ReadingOut.model_validate(latest) if latest else None,
        open_anomaly=AnomalyOut.model_validate(open_anomaly) if open_anomaly else None,
    )


@router.get("/devices", response_model=list[DeviceOut], responses=READ_RESPONSES)
async def list_devices(session: TenantSession) -> list[DeviceOut]:
    """Every device with its latest reading and open anomaly, if any."""
    rows = await session.scalars(select(Device).order_by(Device.kind, Device.label))
    return [await _device_out(session, d) for d in rows]


@router.post(
    "/devices",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_RESPONSES,
    dependencies=[writers],
)
async def create_device(
    body: DeviceIn, principal: CurrentPrincipal, session: TenantSession
) -> DeviceOut:
    """Register a fridge or vehicle. Owners and managers only."""
    if body.store_id is not None and await session.get(Store, body.store_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    device = Device(tenant_id=principal.tenant_id, **body.model_dump())
    session.add(device)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a device with that external id exists"
        ) from exc
    await session.refresh(device)
    return await _device_out(session, device)


@router.get(
    "/devices/{device_id}/telemetry", response_model=list[ReadingOut], responses=READ_ONE_RESPONSES
)
async def device_readings(
    device_id: UUID,
    session: TenantSession,
    since: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[ReadingOut]:
    """Readings, newest first, optionally since a timestamp."""
    if await session.get(Device, device_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    stmt = (
        select(TelemetryReading)
        .where(TelemetryReading.device_id == device_id)
        .order_by(TelemetryReading.recorded_at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(TelemetryReading.recorded_at >= since)
    rows = await session.scalars(stmt)
    return [ReadingOut.model_validate(r) for r in rows]


@router.get("/anomalies", response_model=list[AnomalyOut], responses=READ_RESPONSES)
async def list_anomalies(
    session: TenantSession,
    status_filter: AnomalyStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AnomalyOut]:
    """Excursions, newest first."""
    stmt = select(Anomaly).order_by(Anomaly.started_at.desc()).limit(limit)
    if status_filter is not None:
        stmt = stmt.where(Anomaly.status == status_filter)
    rows = await session.scalars(stmt)
    return [AnomalyOut.model_validate(a) for a in rows]


@router.post(
    "/telemetry",
    response_model=IngestOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=WRITE_RESPONSES,
    dependencies=[writers],
)
async def ingest_http(
    body: IngestIn,
    principal: CurrentPrincipal,
    session: TenantSession,
    settings: SettingsDep,
    queue: QueueDep,
) -> IngestOut:
    """Ingest readings over HTTP (devices without MQTT, tests, backfills)."""
    result = await ingest_readings(session, settings, principal.tenant_id, body.readings)
    await session.flush()
    await publish_events(settings.redis_url, principal.tenant_id, result.events)
    for anomaly_id in result.opened_anomalies:
        await queue.enqueue(
            PLAN_ANOMALY, {"anomaly_id": str(anomaly_id), "tenant_id": str(principal.tenant_id)}
        )
    return IngestOut(
        accepted=result.accepted,
        unknown_devices=result.unknown_devices,
        anomalies_opened=result.opened_anomalies,
    )


@router.get(
    "/telemetry/stream",
    responses={**READ_RESPONSES, 200: {"content": {"text/event-stream": {}}, "description": "SSE"}},
)
async def stream(principal: CurrentPrincipal, settings: SettingsDep) -> StreamingResponse:
    """Server-sent events: ``reading``, ``anomaly_opened``, ``anomaly_resolved``."""
    return StreamingResponse(
        event_stream(
            settings.redis_url,
            principal.tenant_id,
            heartbeat_seconds=settings.telemetry_heartbeat_seconds,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/telemetry/summary", response_model=dict[str, int], responses=READ_RESPONSES)
async def summary(session: TenantSession) -> dict[str, int]:
    """Counts for the dashboard tiles."""
    devices = await session.scalar(select(func.count()).select_from(Device)) or 0
    open_count = (
        await session.scalar(
            select(func.count()).select_from(Anomaly).where(Anomaly.status == AnomalyStatus.open)
        )
        or 0
    )
    readings = await session.scalar(select(func.count()).select_from(TelemetryReading)) or 0
    return {"devices": devices, "open_anomalies": open_count, "readings": readings}


Since = Annotated[datetime | None, Query()]
