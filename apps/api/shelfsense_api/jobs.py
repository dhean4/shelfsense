"""Background jobs. Handlers take a JSON payload and run under the tenant's RLS context."""

import functools
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shelfsense_api.agents.planner import (
    PlannerFailed,
    PlannerRunSummary,
    planner_input_from_anomaly,
    planner_input_from_extraction,
    run_planner,
)
from shelfsense_api.agents.tools import ToolContext
from shelfsense_api.agents.vision import VisionFailed, result_payload, run_vision
from shelfsense_api.config import Settings, get_settings
from shelfsense_api.db import SYSTEM_ROLE, tenant_session
from shelfsense_api.events import RunEvents
from shelfsense_api.guardrails import CostCapExceeded, StepCapExceeded
from shelfsense_api.llm import LLMResponse, get_provider
from shelfsense_api.llm.pricing import cost_usd, load_prices
from shelfsense_api.llm.provider import LLMError
from shelfsense_api.models import (
    Action,
    ActionStatus,
    AgentRun,
    Anomaly,
    Device,
    Extraction,
    Photo,
    PhotoStatus,
    RunStatus,
    Shelf,
    Store,
    TelemetryReading,
)
from shelfsense_api.observability import AGENT_RUNS, current_trace_id, observation
from shelfsense_api.planogram_context import load_shelf_bundle
from shelfsense_api.queue import Handler, JobQueue
from shelfsense_api.storage import PhotoStore

log = logging.getLogger(__name__)

PROCESS_PHOTO = "process_photo"
PLAN_ACTIONS = "plan_actions"
PLAN_ANOMALY = "plan_anomaly"

_queues: dict[str, JobQueue] = {}
_stores: dict[str, PhotoStore] = {}


def get_queue(settings: Settings) -> JobQueue:
    """Process-wide queue for the configured Redis."""
    key = f"{settings.redis_url}#{settings.job_stream}"
    queue = _queues.get(key)
    if queue is None:
        queue = JobQueue(settings.redis_url, stream=settings.job_stream)
        queue.handlers[PROCESS_PHOTO] = process_photo
        queue.handlers[PLAN_ACTIONS] = plan_actions
        queue.handlers[PLAN_ANOMALY] = plan_anomaly
        _queues[key] = queue
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


def traced_job(name: str) -> Callable[[Handler], Handler]:
    """Run a job handler inside an agent-level observation carrying its payload."""

    def decorate(fn: Handler) -> Handler:
        @functools.wraps(fn)
        async def wrapper(payload: dict[str, Any]) -> None:
            with observation(name, as_type="agent", input=payload):
                await fn(payload)

        return wrapper

    return decorate


@traced_job("job.process_photo")
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

        bundle = await load_shelf_bundle(session, photo.shelf_id)
        if bundle is None:
            photo.status = PhotoStatus.failed
            photo.error = "shelf has no planogram; add one before uploading photos"
            return
        planogram, ctx = bundle.planogram, bundle.context

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
        events = RunEvents(settings.redis_url, tenant_id, run.id, run.kind)
        await events.emit("run_started")

        try:
            image = await store.get(photo.object_key)
            result = await run_vision(
                provider, settings, image, ctx, trace_tag=str(photo.id), on_event=events.hook
            )
        except VisionFailed as exc:
            await _finish(
                run, RunStatus.failed, _totals(exc.responses, settings), str(exc), events=events
            )
            photo.status = PhotoStatus.failed
            photo.error = str(exc)
            return
        except LLMError as exc:
            await _finish(run, RunStatus.failed, {}, str(exc), events=events)
            photo.status = PhotoStatus.failed if not exc.retryable else PhotoStatus.queued
            photo.error = str(exc)
            if exc.retryable:
                await session.flush()
                raise  # let the queue retry; the state above is committed by the session
            return

        await _finish(
            run, RunStatus.succeeded, _totals(result.responses, settings), None, events=events
        )
        extraction = Extraction(
            tenant_id=tenant_id,
            photo_id=photo.id,
            run_id=run.id,
            planogram_version=planogram.version,
            result=result_payload(result),
            overall_confidence=result.extraction.overall_confidence,
        )
        session.add(extraction)
        await session.flush()
        photo.status = PhotoStatus.done

    # Chain the planner once the extraction is committed (the session above has exited).
    await get_queue(settings).enqueue(
        PLAN_ACTIONS,
        {
            "extraction_id": str(extraction.id),
            "tenant_id": str(tenant_id),
            "trigger_role": payload.get("trigger_role", SYSTEM_ROLE),
        },
    )


@traced_job("job.plan_actions")
async def plan_actions(payload: dict[str, Any]) -> None:
    """Run the planner over an extraction.

    Payload: ``{"extraction_id", "tenant_id", "trigger_role"}``. Idempotent per
    extraction and trigger: a second delivery finds the finished run and returns.
    """
    settings = get_settings()
    extraction_id = UUID(payload["extraction_id"])
    tenant_id = UUID(payload["tenant_id"])
    trigger_role = str(payload.get("trigger_role", SYSTEM_ROLE))
    provider = get_provider(settings)

    async with tenant_session(settings.database_url, tenant_id, SYSTEM_ROLE) as session:
        extraction = await session.get(Extraction, extraction_id)
        if extraction is None:
            log.warning(
                "extraction %s not visible for tenant %s; dropping", extraction_id, tenant_id
            )
            return
        already = await session.scalar(
            select(AgentRun).where(
                AgentRun.kind == "planner",
                AgentRun.extraction_id == extraction_id,
                AgentRun.status == RunStatus.succeeded,
            )
        )
        if already is not None:
            return
        photo = await session.get(Photo, extraction.photo_id)
        shelf = (
            await session.scalar(
                select(Shelf).where(Shelf.id == photo.shelf_id).options(selectinload(Shelf.store))
            )
            if photo is not None
            else None
        )
        if photo is None or shelf is None:
            log.warning("extraction %s has no photo/shelf; dropping", extraction_id)
            return

        run = AgentRun(
            tenant_id=tenant_id,
            kind="planner",
            status=RunStatus.running,
            photo_id=photo.id,
            extraction_id=extraction.id,
            trigger_role=trigger_role,
            provider=provider.name,
            model=settings.planner_model,
        )
        session.add(run)
        await session.flush()
        events = RunEvents(settings.redis_url, tenant_id, run.id, run.kind)
        await events.emit("run_started")

        ctx = ToolContext(
            session=session,
            settings=settings,
            tenant_id=tenant_id,
            role=trigger_role,
            run_id=run.id,
            confidence=extraction.overall_confidence,
        )
        inp = planner_input_from_extraction(
            store_id=shelf.store.id,
            store_name=shelf.store.name,
            shelf_label=shelf.label,
            extraction_result=extraction.result,
            trigger_role=trigger_role,
        )
        try:
            result = await run_planner(provider, settings, ctx, inp, on_event=events.hook)
        except (CostCapExceeded, StepCapExceeded, PlannerFailed) as exc:
            responses = tuple(getattr(exc, "responses", ()))
            await _finish(
                run, RunStatus.failed, _totals(responses, settings), str(exc), events=events
            )
            await _hold_unfinished_actions(session, run.id, str(exc))
            return
        except LLMError as exc:
            await _finish(run, RunStatus.failed, {}, str(exc), events=events)
            await _hold_unfinished_actions(session, run.id, str(exc))
            if exc.retryable:
                await session.flush()
                raise
            return

        totals = _totals(tuple(result.responses), settings)
        await _finish(run, RunStatus.succeeded, totals, None, events=events)
        run.summary = json.loads(PlannerRunSummary.from_result(result).as_json())


@traced_job("job.plan_anomaly")
async def plan_anomaly(payload: dict[str, Any]) -> None:
    """Run the planner for an open cold-chain anomaly. Payload: ``{"anomaly_id", "tenant_id"}``."""
    settings = get_settings()
    anomaly_id = UUID(payload["anomaly_id"])
    tenant_id = UUID(payload["tenant_id"])
    provider = get_provider(settings)

    async with tenant_session(settings.database_url, tenant_id, SYSTEM_ROLE) as session:
        anomaly = await session.get(Anomaly, anomaly_id)
        if anomaly is None:
            log.warning("anomaly %s not visible for tenant %s; dropping", anomaly_id, tenant_id)
            return
        if anomaly.run_id is not None:
            return  # already planned (at-least-once delivery)
        device = await session.get(Device, anomaly.device_id)
        store = await session.get(Store, device.store_id) if device and device.store_id else None
        if device is None or store is None:
            log.warning("anomaly %s has no device/store; dropping", anomaly_id)
            return
        recent_rows = await session.execute(
            select(TelemetryReading.recorded_at, TelemetryReading.temperature_c)
            .where(TelemetryReading.device_id == device.id)
            .order_by(TelemetryReading.recorded_at.desc())
            .limit(6)
        )
        recent = ", ".join(
            f"{t:.1f}°C at {r.strftime('%H:%M')}" for r, t in recent_rows if t is not None
        )

        run = AgentRun(
            tenant_id=tenant_id,
            kind="planner",
            status=RunStatus.running,
            trigger_role=SYSTEM_ROLE,
            provider=provider.name,
            model=settings.planner_model,
        )
        session.add(run)
        await session.flush()
        events = RunEvents(settings.redis_url, tenant_id, run.id, run.kind)
        await events.emit("run_started")
        anomaly.run_id = run.id

        ctx = ToolContext(
            session=session, settings=settings, tenant_id=tenant_id, role=SYSTEM_ROLE, run_id=run.id
        )
        inp = planner_input_from_anomaly(
            store_id=store.id,
            store_name=store.name,
            device_label=device.label,
            started_at=anomaly.started_at.isoformat(timespec="minutes"),
            peak_temperature_c=anomaly.peak_temperature_c,
            max_temp_c=settings.fridge_max_temp_c,
            recent=recent or "none",
        )
        try:
            result = await run_planner(provider, settings, ctx, inp, on_event=events.hook)
        except (CostCapExceeded, StepCapExceeded, PlannerFailed) as exc:
            responses = tuple(getattr(exc, "responses", ()))
            await _finish(
                run, RunStatus.failed, _totals(responses, settings), str(exc), events=events
            )
            await _hold_unfinished_actions(session, run.id, str(exc))
            return
        except LLMError as exc:
            await _finish(run, RunStatus.failed, {}, str(exc), events=events)
            await _hold_unfinished_actions(session, run.id, str(exc))
            if exc.retryable:
                anomaly.run_id = None
                await session.flush()
                raise
            return
        await _finish(
            run,
            RunStatus.succeeded,
            _totals(tuple(result.responses), settings),
            None,
            events=events,
        )
        run.summary = json.loads(PlannerRunSummary.from_result(result).as_json())


async def _hold_unfinished_actions(session: AsyncSession, run_id: UUID, reason: str) -> None:
    """A run that died leaves its proposals for a human rather than auto-approving them."""
    for action in await session.scalars(
        select(Action).where(Action.run_id == run_id, Action.status == ActionStatus.proposed)
    ):
        action.status = ActionStatus.pending_review
        action.requires_review = True
        action.review_reason = f"run failed: {reason}"[:500]
    await session.flush()


async def _finish(
    run: AgentRun,
    status: RunStatus,
    totals: dict[str, Any],
    error: str | None,
    *,
    events: RunEvents | None = None,
) -> None:
    """Close a run: status, totals, trace id, metrics, and the finished event."""
    run.status = status
    run.error = error
    run.finished_at = datetime.now(UTC)
    run.trace_id = run.trace_id or current_trace_id()
    AGENT_RUNS.labels(run.kind, status.value).inc()
    for key, value in totals.items():
        if value is not None or key == "cost_usd":
            setattr(run, key, value)
    if events is not None:
        await events.emit(
            "run_finished",
            status=status.value,
            cost_usd=float(run.cost_usd) if run.cost_usd is not None else None,
            error=error,
        )
