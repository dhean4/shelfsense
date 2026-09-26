"""Agent runs (timeline) and manual planner triggers."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload

from shelfsense_api.config import Settings
from shelfsense_api.deps import CurrentPrincipal, SettingsDep, TenantSession, require_role
from shelfsense_api.events import runs_channel, sse_stream
from shelfsense_api.jobs import PLAN_ACTIONS, get_queue
from shelfsense_api.models import AgentRun, Extraction, Role, RunStatus
from shelfsense_api.observability import trace_url
from shelfsense_api.routes._common import READ_ONE_RESPONSES, READ_RESPONSES
from shelfsense_api.routes.photos import QueueDep
from shelfsense_api.schemas import RunOut, RunQueued

router = APIRouter(prefix="/v1", tags=["runs"])
planners = Depends(require_role(Role.owner, Role.manager, Role.field_agent))


def _runs_query() -> Select[AgentRun]:
    return select(AgentRun).options(
        selectinload(AgentRun.tool_calls), selectinload(AgentRun.actions)
    )


def _run_out(run: AgentRun, settings: Settings) -> RunOut:
    out = RunOut.model_validate(run)
    out.trace_url = trace_url(settings, run.trace_id)
    return out


@router.get("/runs", response_model=list[RunOut], responses=READ_RESPONSES)
async def list_runs(
    session: TenantSession,
    settings: SettingsDep,
    kind: str | None = Query(default=None, max_length=32),
    status_filter: RunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RunOut]:
    """Most recent runs first, optionally by kind or status."""
    stmt = _runs_query().order_by(AgentRun.started_at.desc()).limit(limit)
    if kind is not None:
        stmt = stmt.where(AgentRun.kind == kind)
    if status_filter is not None:
        stmt = stmt.where(AgentRun.status == status_filter)
    rows = await session.scalars(stmt)
    return [_run_out(run, settings) for run in rows]


@router.get(
    "/runs/stream",
    responses={**READ_RESPONSES, 200: {"content": {"text/event-stream": {}}, "description": "SSE"}},
)
async def stream_runs(principal: CurrentPrincipal, settings: SettingsDep) -> StreamingResponse:
    """Server-sent events for the tenant's runs.

    Event types: ``run_started``, ``model_turn``, ``tool_result``, ``vision_attempt``,
    ``run_finished``. Each carries ``run_id`` and ``kind``.
    """
    return StreamingResponse(
        sse_stream(
            settings.redis_url,
            runs_channel(principal.tenant_id),
            heartbeat_seconds=settings.telemetry_heartbeat_seconds,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}", response_model=RunOut, responses=READ_ONE_RESPONSES)
async def read_run(run_id: UUID, session: TenantSession, settings: SettingsDep) -> RunOut:
    """One run with every tool call and action."""
    run = await session.scalar(_runs_query().where(AgentRun.id == run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return _run_out(run, settings)


@router.post(
    "/extractions/{extraction_id}/plan",
    response_model=RunQueued,
    status_code=status.HTTP_202_ACCEPTED,
    responses=READ_ONE_RESPONSES,
    dependencies=[planners],
)
async def plan_extraction(
    extraction_id: UUID, principal: CurrentPrincipal, session: TenantSession, queue: QueueDep
) -> RunQueued:
    """Queue a planner run over an existing extraction (it also runs automatically)."""
    extraction = await session.get(Extraction, extraction_id)
    if extraction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "extraction not found")
    await queue.enqueue(
        PLAN_ACTIONS,
        {
            "extraction_id": str(extraction_id),
            "tenant_id": str(principal.tenant_id),
            "trigger_role": principal.role.value,
        },
    )
    return RunQueued(extraction_id=extraction_id)


__all__ = ["get_queue", "router"]
