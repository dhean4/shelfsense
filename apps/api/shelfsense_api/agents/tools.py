"""Typed tool contracts the planner, the HTTP tool endpoint and the MCP server all share.

A tool is a name, a description, a Pydantic input model and an async handler that runs
under a tenant-scoped session. Every execution is logged to ``tool_calls`` with its
arguments, result, duration and error, whoever the caller was.
"""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shelfsense_api.config import Settings
from shelfsense_api.guardrails import ActionKind, tools_for_role
from shelfsense_api.llm.schema import api_schema
from shelfsense_api.llm.types import ToolSpec
from shelfsense_api.models import (
    Action,
    ActionStatus,
    InventoryLevel,
    Notification,
    Role,
    Sku,
    Store,
    ToolCallLog,
    User,
)

# --- context ----------------------------------------------------------------------------


@dataclass
class ToolContext:
    """Who is calling, under which tenant, and where to log."""

    session: AsyncSession
    settings: Settings
    tenant_id: UUID
    role: str
    run_id: UUID | None = None
    confidence: float | None = None
    _seq: int = 0

    def next_seq(self) -> int:
        """Monotonic call index within a run."""
        self._seq += 1
        return self._seq


class ToolError(Exception):
    """A tool could not do what was asked; the message is safe to show the model."""


# --- inputs and outputs -----------------------------------------------------------------


class GetInventoryIn(BaseModel):
    """Stock levels at a store."""

    store_id: UUID = Field(description="Store whose inventory to read.")
    sku_ids: list[UUID] = Field(
        default_factory=list, description="Limit to these SKUs; empty means every SKU stocked."
    )


class InventoryItem(BaseModel):
    """One SKU's stock position at a store."""

    sku_id: UUID
    name: str
    on_hand: int
    reorder_point: int
    case_size: int
    unit_price_kobo: int


class GetInventoryOut(BaseModel):
    """Inventory rows."""

    store_id: UUID
    items: list[InventoryItem]


class CreateReorderIn(BaseModel):
    """Propose a purchase order line."""

    store_id: UUID
    sku_id: UUID
    quantity: Annotated[int, Field(ge=1, le=10_000)] = Field(description="Units to order.")
    reason: Annotated[str, Field(min_length=3, max_length=500)]


class ActionCreated(BaseModel):
    """Result of a tool that proposed an action."""

    action_id: UUID
    kind: ActionKind
    status: ActionStatus
    estimated_cost_kobo: int


class DispatchIn(BaseModel):
    """Send a technician to a store (fridge faults, from P5 telemetry)."""

    store_id: UUID
    reason: Annotated[str, Field(min_length=3, max_length=500)]
    urgency: Literal["low", "medium", "high"] = "medium"


class NotifyIn(BaseModel):
    """Message the tenant's staff. Channels are stubs: the message is recorded, not sent."""

    channel: Literal["email", "whatsapp"]
    recipient_role: Literal["owner", "manager"] = Field(
        description="Who should receive it; the tool resolves the people."
    )
    subject: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=1, max_length=2000)]


class NotifyOut(BaseModel):
    """What was queued."""

    notification_ids: list[UUID]
    recipients: int
    status: Literal["stubbed"]


class GeocodeIn(BaseModel):
    """Coordinates for an address or store name."""

    query: Annotated[str, Field(min_length=2, max_length=300)]


class GeocodeOut(BaseModel):
    """A location."""

    latitude: float
    longitude: float
    matched: str
    source: Literal["store_record"]


class SubmitDecisionsIn(BaseModel):
    """Finish the run. Called exactly once, after every other tool call."""

    summary: Annotated[str, Field(min_length=1, max_length=2000)] = Field(
        description="Plain-language account of what was found and what was done."
    )
    escalate: bool = Field(description="True when a human must look at this shelf regardless.")
    escalation_reason: str | None = Field(
        default=None, description="Why a human must look, when escalate is true."
    )


# --- handlers ---------------------------------------------------------------------------


async def get_inventory(ctx: ToolContext, args: GetInventoryIn) -> GetInventoryOut:
    """Read stock levels; RLS keeps it to the caller's tenant."""
    store = await ctx.session.get(Store, args.store_id)
    if store is None:
        raise ToolError(f"store {args.store_id} not found")
    stmt = (
        select(InventoryLevel, Sku)
        .join(Sku, Sku.id == InventoryLevel.sku_id)
        .where(InventoryLevel.store_id == args.store_id)
        .order_by(Sku.name)
    )
    if args.sku_ids:
        stmt = stmt.where(InventoryLevel.sku_id.in_(args.sku_ids))
    rows = (await ctx.session.execute(stmt)).all()
    return GetInventoryOut(
        store_id=args.store_id,
        items=[
            InventoryItem(
                sku_id=level.sku_id,
                name=sku.name,
                on_hand=level.on_hand,
                reorder_point=level.reorder_point,
                case_size=level.case_size,
                unit_price_kobo=sku.unit_price_kobo,
            )
            for level, sku in rows
        ],
    )


async def create_reorder(ctx: ToolContext, args: CreateReorderIn) -> ActionCreated:
    """Record a proposed reorder; guardrails decide later whether it needs review."""
    store = await ctx.session.get(Store, args.store_id)
    sku = await ctx.session.get(Sku, args.sku_id)
    if store is None or sku is None:
        raise ToolError("store or sku not found")
    action = Action(
        tenant_id=ctx.tenant_id,
        run_id=ctx.run_id,
        store_id=store.id,
        kind=ActionKind.reorder,
        status=ActionStatus.proposed,
        payload={
            "sku_id": str(sku.id),
            "sku_name": sku.name,
            "quantity": args.quantity,
            "unit_price_kobo": sku.unit_price_kobo,
        },
        estimated_cost_kobo=args.quantity * sku.unit_price_kobo,
        confidence=ctx.confidence,
        rationale=args.reason,
    )
    ctx.session.add(action)
    await ctx.session.flush()
    return ActionCreated(
        action_id=action.id,
        kind=ActionKind.reorder,
        status=action.status,
        estimated_cost_kobo=action.estimated_cost_kobo,
    )


async def dispatch_technician(ctx: ToolContext, args: DispatchIn) -> ActionCreated:
    """Record a proposed call-out."""
    store = await ctx.session.get(Store, args.store_id)
    if store is None:
        raise ToolError(f"store {args.store_id} not found")
    action = Action(
        tenant_id=ctx.tenant_id,
        run_id=ctx.run_id,
        store_id=store.id,
        kind=ActionKind.dispatch,
        status=ActionStatus.proposed,
        payload={
            "store_name": store.name,
            "latitude": store.latitude,
            "longitude": store.longitude,
            "urgency": args.urgency,
        },
        estimated_cost_kobo=ctx.settings.dispatch_cost_kobo,
        confidence=ctx.confidence,
        rationale=args.reason,
    )
    ctx.session.add(action)
    await ctx.session.flush()
    return ActionCreated(
        action_id=action.id,
        kind=ActionKind.dispatch,
        status=action.status,
        estimated_cost_kobo=action.estimated_cost_kobo,
    )


async def notify(ctx: ToolContext, args: NotifyIn) -> NotifyOut:
    """Queue a message to every user with the given role. Delivery is stubbed (P9)."""
    recipients = list(
        await ctx.session.scalars(select(User).where(User.role == Role(args.recipient_role)))
    )
    if not recipients:
        raise ToolError(f"no users with role {args.recipient_role} in this tenant")
    ids: list[UUID] = []
    for user in recipients:
        # TODO(P9): real delivery via Resend / WhatsApp Cloud API; today this only records.
        note = Notification(
            tenant_id=ctx.tenant_id,
            run_id=ctx.run_id,
            channel=args.channel,
            recipient=user.email,
            subject=args.subject,
            body=args.body,
            status="stubbed",
        )
        ctx.session.add(note)
        await ctx.session.flush()
        ids.append(note.id)
    return NotifyOut(notification_ids=ids, recipients=len(ids), status="stubbed")


async def geocode(ctx: ToolContext, args: GeocodeIn) -> GeocodeOut:
    """Resolve against the tenant's own store records. No external geocoder is configured."""
    pattern = f"%{args.query.strip()}%"
    store = await ctx.session.scalar(
        select(Store)
        .where(or_(Store.name.ilike(pattern), Store.address.ilike(pattern)))
        .order_by(func.length(Store.name))
        .limit(1)
    )
    if store is None:
        # TODO(P9): fall back to Nominatim/Mapbox once a key and a rate limit exist.
        raise ToolError(f"no store matches {args.query!r}; external geocoding is not configured")
    return GeocodeOut(
        latitude=store.latitude,
        longitude=store.longitude,
        matched=f"{store.name}, {store.address}",
        source="store_record",
    )


# --- registry ---------------------------------------------------------------------------

Handler = Callable[[ToolContext, Any], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolDef:
    """A registered tool."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Handler | None  # None: handled by the planner loop itself

    def spec(self) -> ToolSpec:
        """The provider-neutral spec (schema stripped for the grammar)."""
        return ToolSpec(
            name=self.name, description=self.description, input_schema=api_schema(self.input_model)
        )


SUBMIT = "submit_decisions"

REGISTRY: dict[str, ToolDef] = {
    t.name: t
    for t in (
        ToolDef(
            "get_inventory",
            "Current stock on hand, reorder point and case size for SKUs at a store. "
            "Call this before deciding any reorder.",
            GetInventoryIn,
            get_inventory,
        ),
        ToolDef(
            "create_reorder",
            "Propose ordering a quantity of one SKU for a store. Returns the action id and "
            "the estimated cost. Large or low-confidence orders are held for human review.",
            CreateReorderIn,
            create_reorder,
        ),
        ToolDef(
            "dispatch_technician",
            "Propose sending a technician to a store, e.g. for a failing fridge. Expensive; "
            "only when telemetry or the photo shows an equipment fault.",
            DispatchIn,
            dispatch_technician,
        ),
        ToolDef(
            "notify",
            "Send a message to the tenant's owners or managers about something they must "
            "know now (multiple stock-outs, a fault, a shelf that needs a visit).",
            NotifyIn,
            notify,
        ),
        ToolDef(
            "geocode",
            "Coordinates for a store name or address, for routing a technician.",
            GeocodeIn,
            geocode,
        ),
        ToolDef(
            SUBMIT,
            "Finish the run with a summary. Call it exactly once, last, after all other tools.",
            SubmitDecisionsIn,
            None,
        ),
    )
}


def specs_for_role(role: str) -> list[ToolSpec]:
    """Tool specs the role may see, in registry order."""
    allowed = tools_for_role(role)
    return [t.spec() for t in REGISTRY.values() if t.name in allowed]


@dataclass(frozen=True)
class ToolExecution:
    """What happened when a tool ran."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    duration_ms: int

    @property
    def content(self) -> str:
        """JSON text for the model."""
        return json.dumps(self.result, default=str) if self.result is not None else str(self.error)


async def execute(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolExecution:
    """Validate, run and log one tool call. Never raises for tool-level problems."""
    started = time.perf_counter()
    result: dict[str, Any] | None = None
    error: str | None = None
    tool = REGISTRY.get(name)
    if tool is None or name not in tools_for_role(ctx.role):
        error = f"tool {name!r} is not available to role {ctx.role!r}"
    elif tool.handler is None:
        error = f"tool {name!r} is handled by the planner, not executed"
    else:
        try:
            parsed = tool.input_model.model_validate(arguments)
            output = await tool.handler(ctx, parsed)
            result = json.loads(output.model_dump_json())
        except ValidationError as exc:
            error = f"invalid arguments: {exc.errors()[:3]}"
        except ToolError as exc:
            error = str(exc)
    duration_ms = int((time.perf_counter() - started) * 1000)
    ctx.session.add(
        ToolCallLog(
            tenant_id=ctx.tenant_id,
            run_id=ctx.run_id,
            seq=ctx.next_seq(),
            tool_name=name,
            caller_role=ctx.role,
            arguments=arguments,
            result=result,
            error=error,
            duration_ms=duration_ms,
        )
    )
    await ctx.session.flush()
    return ToolExecution(name, arguments, result, error, duration_ms)
