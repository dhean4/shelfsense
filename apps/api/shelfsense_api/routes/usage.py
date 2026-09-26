"""Cost, tokens and latency of agent runs, aggregated for the dashboard."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import Date, cast, func, select

from shelfsense_api.deps import TenantSession
from shelfsense_api.models import AgentRun, RunStatus
from shelfsense_api.routes._common import READ_RESPONSES
from shelfsense_api.schemas import UsageBucket, UsageOut, UsageRun, UsageTotals

router = APIRouter(prefix="/v1/usage", tags=["usage"])


@router.get("", response_model=UsageOut, responses=READ_RESPONSES)
async def usage(session: TenantSession, days: int = Query(default=7, ge=1, le=90)) -> UsageOut:
    """Aggregates over the last ``days`` days of runs, by day+kind and by model."""
    since = datetime.now(UTC) - timedelta(days=days)
    base = select(AgentRun).where(AgentRun.started_at >= since).subquery()

    cost = func.coalesce(func.sum(base.c.cost_usd), 0)
    p50 = func.percentile_cont(0.5).within_group(base.c.latency_ms)
    p95 = func.percentile_cont(0.95).within_group(base.c.latency_ms)

    totals_row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(base.c.status == RunStatus.succeeded.value),
                func.count().filter(base.c.status == RunStatus.failed.value),
                func.coalesce(func.sum(base.c.input_tokens), 0),
                func.coalesce(func.sum(base.c.output_tokens), 0),
                func.coalesce(func.sum(base.c.cache_read_tokens), 0),
                cost,
                p50,
                p95,
            )
        )
    ).one()
    totals = UsageTotals(
        runs=int(totals_row[0]),
        succeeded=int(totals_row[1]),
        failed=int(totals_row[2]),
        input_tokens=int(totals_row[3]),
        output_tokens=int(totals_row[4]),
        cache_read_tokens=int(totals_row[5]),
        cost_usd=float(totals_row[6]),
        p50_latency_ms=float(totals_row[7] or 0),
        p95_latency_ms=float(totals_row[8] or 0),
    )

    day = cast(base.c.started_at, Date)
    by_day_rows = await session.execute(
        select(
            day,
            base.c.kind,
            func.count(),
            func.coalesce(func.sum(base.c.input_tokens), 0),
            func.coalesce(func.sum(base.c.output_tokens), 0),
            cost,
            p95,
        )
        .group_by(day, base.c.kind)
        .order_by(day, base.c.kind)
    )
    by_day = [
        UsageBucket(
            key=str(d),
            kind=str(kind),
            runs=int(n),
            input_tokens=int(i),
            output_tokens=int(o),
            cost_usd=float(c),
            p95_latency_ms=float(p or 0),
        )
        for d, kind, n, i, o, c, p in by_day_rows
    ]

    by_model_rows = await session.execute(
        select(
            base.c.model,
            base.c.kind,
            func.count(),
            func.coalesce(func.sum(base.c.input_tokens), 0),
            func.coalesce(func.sum(base.c.output_tokens), 0),
            cost,
            p95,
        )
        .group_by(base.c.model, base.c.kind)
        .order_by(cost.desc())
    )
    by_model = [
        UsageBucket(
            key=str(model),
            kind=str(kind),
            runs=int(n),
            input_tokens=int(i),
            output_tokens=int(o),
            cost_usd=float(c),
            p95_latency_ms=float(p or 0),
        )
        for model, kind, n, i, o, c, p in by_model_rows
    ]

    top = await session.scalars(
        select(AgentRun)
        .where(AgentRun.started_at >= since, AgentRun.cost_usd.is_not(None))
        .order_by(AgentRun.cost_usd.desc())
        .limit(10)
    )
    top_runs = [
        UsageRun(
            id=r.id,
            kind=r.kind,
            status=r.status,
            model=r.model,
            cost_usd=float(r.cost_usd or 0),
            latency_ms=r.latency_ms,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            started_at=r.started_at,
        )
        for r in top
    ]
    return UsageOut(days=days, totals=totals, by_day=by_day, by_model=by_model, top_runs=top_runs)
