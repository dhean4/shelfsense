"""Background jobs. Handlers take a JSON payload and run under the tenant's RLS context."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shelfsense_api.agents.vision import (
    PlanogramContext,
    SlotContext,
    VisionFailed,
    result_payload,
    run_vision,
)
from shelfsense_api.config import Settings, get_settings
from shelfsense_api.db import SYSTEM_ROLE, tenant_session
from shelfsense_api.llm import LLMResponse, get_provider
from shelfsense_api.llm.pricing import cost_usd, load_prices
from shelfsense_api.llm.provider import LLMError
from shelfsense_api.models import (
    AgentRun,
    Extraction,
    Photo,
    PhotoStatus,
    Planogram,
    RunStatus,
    Shelf,
    Sku,
)
from shelfsense_api.queue import JobQueue
from shelfsense_api.storage import PhotoStore

log = logging.getLogger(__name__)

PROCESS_PHOTO = "process_photo"

_queues: dict[str, JobQueue] = {}
_stores: dict[str, PhotoStore] = {}


def get_queue(settings: Settings) -> JobQueue:
    """Process-wide queue for the configured Redis."""
    queue = _queues.get(settings.redis_url)
    if queue is None:
        queue = JobQueue(settings.redis_url)
        queue.handlers[PROCESS_PHOTO] = process_photo
        _queues[settings.redis_url] = queue
    return queue


def get_photo_store(settings: Settings) -> PhotoStore:
    """Process-wide S3 client wrapper."""
    key = f"{settings.s3_endpoint_url}/{settings.s3_bucket_photos}"
    store = _stores.get(key)
    if store is None:
        store = PhotoStore(settings)
        _stores[key] = store
    return store


def reset_job_singletons() -> None:
    """Drop cached queues and stores (tests)."""
    _queues.clear()
    _stores.clear()


def _totals(responses: tuple[LLMResponse, ...], settings: Settings) -> dict[str, Any]:
    prices = load_prices(settings.llm_pricing_json)
    cost = 0.0
    priced = False
    for r in responses:
        c = cost_usd(r.model, r.usage, prices)
        if c is not None:
            cost += c
            priced = True
    return {
        # The model that actually answered (fallbacks may differ from the configured one).
        "model": responses[-1].model if responses else None,
        "input_tokens": sum(r.usage.input_tokens for r in responses),
        "output_tokens": sum(r.usage.output_tokens for r in responses),
        "cache_read_tokens": sum(r.usage.cache_read_tokens for r in responses),
        "cache_write_tokens": sum(r.usage.cache_write_tokens for r in responses),
        "latency_ms": sum(r.latency_ms for r in responses),
        "attempts": len(responses),
        "cost_usd": Decimal(str(round(cost, 6))) if priced else None,
    }


async def process_photo(payload: dict[str, Any]) -> None:
    """Extract one photo. Payload: ``{"photo_id": ..., "tenant_id": ...}``.

    Raises on retryable provider errors so the queue re-delivers; records terminal
    failures on the photo and run and returns normally so they are acked.
    """
    settings = get_settings()
    photo_id = UUID(payload["photo_id"])
    tenant_id = UUID(payload["tenant_id"])
    provider = get_provider(settings)
    store = get_photo_store(settings)

    async with tenant_session(settings.database_url, tenant_id, SYSTEM_ROLE) as session:
        photo = await session.get(Photo, photo_id)
        if photo is None:
            log.warning("photo %s not visible for tenant %s; dropping job", photo_id, tenant_id)
            return
        if photo.status == PhotoStatus.done:
            return  # at-least-once delivery: already handled
        photo.status = PhotoStatus.processing
        photo.error = None

        shelf = await session.scalar(
            select(Shelf).where(Shelf.id == photo.shelf_id).options(selectinload(Shelf.store))
        )
        planogram = await session.scalar(
            select(Planogram)
            .where(Planogram.shelf_id == photo.shelf_id)
            .options(selectinload(Planogram.slots))
        )
        if shelf is None or planogram is None or not planogram.slots:
            photo.status = PhotoStatus.failed
            photo.error = "shelf has no planogram; add one before uploading photos"
            return
        # Slots' SKUs, loaded explicitly: no lazy loads inside an async session.
        skus = {
            s.id: s
            for s in await session.scalars(
                select(Sku).where(Sku.id.in_([slot.sku_id for slot in planogram.slots]))
            )
        }
        ctx = PlanogramContext(
            store_name=shelf.store.name,
            shelf_label=shelf.label,
            planogram_version=planogram.version,
            slots=tuple(
                SlotContext(
                    sku_id=slot.sku_id,
                    name=skus[slot.sku_id].name,
                    brand=skus[slot.sku_id].brand,
                    position=slot.position,
                    expected_facings=slot.expected_facings,
                    min_facings=slot.min_facings,
                )
                for slot in sorted(planogram.slots, key=lambda s: s.position)
            ),
        )

        run = AgentRun(
            tenant_id=tenant_id,
            kind="vision",
            status=RunStatus.running,
            photo_id=photo.id,
            provider=provider.name,
            model=settings.vision_model,
        )
        session.add(run)
        await session.flush()

        try:
            image = await store.get(photo.object_key)
            result = await run_vision(provider, settings, image, ctx, trace_tag=str(photo.id))
        except VisionFailed as exc:
            _finish(run, RunStatus.failed, _totals(exc.responses, settings), str(exc))
            photo.status = PhotoStatus.failed
            photo.error = str(exc)
            return
        except LLMError as exc:
            _finish(run, RunStatus.failed, {}, str(exc))
            photo.status = PhotoStatus.failed if not exc.retryable else PhotoStatus.queued
            photo.error = str(exc)
            if exc.retryable:
                await session.flush()
                raise  # let the queue retry; the state above is committed by the session
            return

        _finish(run, RunStatus.succeeded, _totals(result.responses, settings), None)
        session.add(
            Extraction(
                tenant_id=tenant_id,
                photo_id=photo.id,
                run_id=run.id,
                planogram_version=planogram.version,
                result=result_payload(result),
                overall_confidence=result.extraction.overall_confidence,
            )
        )
        photo.status = PhotoStatus.done


def _finish(run: AgentRun, status: RunStatus, totals: dict[str, Any], error: str | None) -> None:
    run.status = status
    run.error = error
    run.finished_at = datetime.now(UTC)
    for key, value in totals.items():
        if value is not None or key == "cost_usd":
            setattr(run, key, value)
