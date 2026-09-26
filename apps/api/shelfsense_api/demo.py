"""Demo activity on top of the seed: synthetic photos queued for audit, and telemetry.

Runs as the system role through the same code paths the API uses, so the dashboard shows
real runs, real anomalies and real review items rather than hand-inserted rows.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select

from shelfsense_api.config import Settings
from shelfsense_api.db import SYSTEM_ROLE, tenant_session
from shelfsense_api.images import inspect
from shelfsense_api.jobs import PROCESS_PHOTO, get_photo_store, get_queue
from shelfsense_api.models import Photo, PhotoStatus, Shelf, Store, TelemetryReading
from shelfsense_api.seed import TENANTS, stable_id
from shelfsense_api.synthetic import SCENARIOS
from shelfsense_api.telemetry import ReadingIn, ingest_readings, publish_events
from shelfsense_simulator.fleet import DEFAULT_FLEET, Excursion, Simulation

# Which synthetic photo goes to which seeded shelf.
PHOTO_TARGETS: dict[str, tuple[str, str, str]] = {
    "dairy_full": ("lagos-fresh", "Ikeja Depot Shop", "Dairy Chiller"),
    "dairy_two_stockouts": ("lagos-fresh", "Ikeja Depot Shop", "Dairy Chiller"),
    "drinks_with_intruder": ("lagos-fresh", "Ikeja Depot Shop", "Drinks Chiller"),
}


async def load_demo(settings: Settings, photos_dir: Path, telemetry_minutes: int) -> list[str]:
    """Queue the synthetic photos and ingest simulated telemetry with one excursion."""
    summary: list[str] = []
    store = get_photo_store(settings)
    await store.ensure_bucket()
    queue = get_queue(settings)

    for scenario in SCENARIOS:
        tenant_slug, store_name, shelf_label = PHOTO_TARGETS[scenario.name]
        tenant_id = stable_id("tenant", tenant_slug)
        shelf_id = stable_id("shelf", f"{tenant_slug}:{store_name}:{shelf_label}")
        data = (photos_dir / f"{scenario.name}.png").read_bytes()
        info = inspect(data)
        photo_id = uuid4()
        key = f"{tenant_id}/{shelf_id}/{photo_id}.png"
        await store.put(key, data, info.media_type)
        async with tenant_session(settings.database_url, tenant_id, SYSTEM_ROLE) as session:
            if await session.get(Shelf, shelf_id) is None:
                summary.append(f"skip {scenario.name}: shelf not seeded")
                continue
            session.add(
                Photo(
                    id=photo_id,
                    tenant_id=tenant_id,
                    shelf_id=shelf_id,
                    uploaded_by="demo",
                    object_key=key,
                    content_type=info.media_type,
                    size_bytes=len(data),
                    width=info.width,
                    height=info.height,
                    status=PhotoStatus.queued,
                )
            )
        await queue.enqueue(
            PROCESS_PHOTO,
            {"photo_id": str(photo_id), "tenant_id": str(tenant_id), "trigger_role": "field_agent"},
        )
        summary.append(f"queued photo {scenario.name} → {store_name} / {shelf_label}")

    if telemetry_minutes > 0:
        start = datetime.now(UTC) - timedelta(minutes=telemetry_minutes)
        sim = Simulation(
            fleet=DEFAULT_FLEET,
            excursions=(
                # Still hot at the last reading, so the rule opens an anomaly now and the
                # next live readings (simulator) resolve it.
                Excursion(
                    "fridge-ikeja-depot-shop",
                    start=timedelta(minutes=max(1, telemetry_minutes // 3)),
                    duration=timedelta(minutes=telemetry_minutes),
                ),
            ),
            interval=timedelta(minutes=1),
            start_at=start,
        )
        by_tenant: dict[str, list[ReadingIn]] = {}
        for _ in range(telemetry_minutes):
            for reading in sim.step():
                by_tenant.setdefault(reading.tenant_slug, []).append(
                    ReadingIn(
                        device_external_id=reading.device_external_id,
                        recorded_at=reading.recorded_at,
                        temperature_c=reading.temperature_c,
                        latitude=reading.latitude,
                        longitude=reading.longitude,
                        battery_pct=reading.battery_pct,
                    )
                )
        for tenant in TENANTS:
            readings = by_tenant.get(tenant.slug, [])
            if not readings:
                continue
            tenant_id = stable_id("tenant", tenant.slug)
            async with tenant_session(settings.database_url, tenant_id, SYSTEM_ROLE) as session:
                stores = list(await session.scalars(select(Store.name)))
                # The demo owns its window: earlier demo readings at the same timestamps
                # would interleave with these and break the excursion's trailing run.
                await session.execute(
                    delete(TelemetryReading).where(TelemetryReading.recorded_at >= start)
                )
                result = await ingest_readings(session, settings, tenant_id, readings)
            await publish_events(settings.redis_url, tenant_id, result.events)
            for anomaly_id in result.opened_anomalies:
                await queue.enqueue(
                    "plan_anomaly", {"anomaly_id": str(anomaly_id), "tenant_id": str(tenant_id)}
                )
            summary.append(
                f"{tenant.slug}: {result.accepted} readings over {telemetry_minutes} min for "
                f"{len(stores)} store(s), {len(result.opened_anomalies)} anomaly(ies) opened"
            )
    await queue.close()
    return summary
