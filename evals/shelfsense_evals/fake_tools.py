"""An in-memory tool executor so planner cases run without a database.

It implements the same contracts as ``shelfsense_api.agents.tools`` against the case's
inventory, records what the planner decided, and mints deterministic ids so recorded
fixtures replay byte-for-byte.
"""

import json
import time
import uuid
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from shelfsense_api.agents.tools import (
    REGISTRY,
    ActionCreated,
    CreateReorderIn,
    DispatchIn,
    GeocodeIn,
    GeocodeOut,
    GetInventoryIn,
    GetInventoryOut,
    InventoryItem,
    NotifyIn,
    NotifyOut,
    ToolContext,
    ToolExecution,
)
from shelfsense_api.guardrails import ActionKind, tools_for_role
from shelfsense_api.models import ActionStatus
from shelfsense_evals.dataset import PlannerCase
from shelfsense_evals.scoring import PlannerObserved


class CaseToolExecutor:
    """Callable with ``execute``'s signature, bound to one planner case."""

    def __init__(self, case: PlannerCase, store_id: UUID) -> None:
        """Bind to the case's inventory and a stable store id."""
        self.case = case
        self.store_id = store_id
        self.observed = PlannerObserved()
        self.calls: list[ToolExecution] = []
        self._seq = 0
        self._inventory = {row.sku_id: row for row in case.inventory}

    def _id(self, kind: str) -> UUID:
        self._seq += 1
        return uuid.uuid5(uuid.NAMESPACE_URL, f"eval:{self.case.id}:{kind}:{self._seq}")

    async def __call__(
        self, ctx: ToolContext, name: str, arguments: dict[str, Any]
    ) -> ToolExecution:
        """Validate and run one tool against the case."""
        started = time.perf_counter()
        result: dict[str, Any] | None = None
        error: str | None = None
        tool = REGISTRY.get(name)
        if tool is None or name not in tools_for_role(ctx.role) or tool.handler is None:
            error = f"tool {name!r} is not available to role {ctx.role!r}"
        else:
            try:
                parsed = tool.input_model.model_validate(arguments)
                result = json.loads(self._run(name, parsed).model_dump_json())
            except ValidationError as exc:
                error = f"invalid arguments: {exc.errors()[:3]}"
            except KeyError as exc:
                error = f"not found: {exc}"
        execution = ToolExecution(
            name, arguments, result, error, int((time.perf_counter() - started) * 1000)
        )
        self.calls.append(execution)
        return execution

    def _run(self, name: str, args: Any) -> Any:
        if isinstance(args, GetInventoryIn):
            if args.store_id != self.store_id:
                raise KeyError(f"store {args.store_id}")
            rows = [
                InventoryItem(
                    sku_id=r.sku_id,
                    name=r.name,
                    on_hand=r.on_hand,
                    reorder_point=r.reorder_point,
                    case_size=r.case_size,
                    unit_price_kobo=r.unit_price_kobo,
                )
                for r in self.case.inventory
                if not args.sku_ids or r.sku_id in args.sku_ids
            ]
            return GetInventoryOut(store_id=self.store_id, items=rows)
        if isinstance(args, CreateReorderIn):
            row = self._inventory.get(args.sku_id)
            if args.store_id != self.store_id or row is None:
                self.observed.unknown_sku_reorders += 1
                raise KeyError("store or sku")
            self.observed.reorder_skus.add(args.sku_id)
            return ActionCreated(
                action_id=self._id("reorder"),
                kind=ActionKind.reorder,
                status=ActionStatus.proposed,
                estimated_cost_kobo=args.quantity * row.unit_price_kobo,
            )
        if isinstance(args, DispatchIn):
            if args.store_id != self.store_id:
                raise KeyError(f"store {args.store_id}")
            self.observed.dispatched = True
            return ActionCreated(
                action_id=self._id("dispatch"),
                kind=ActionKind.dispatch,
                status=ActionStatus.proposed,
                estimated_cost_kobo=1_500_000,
            )
        if isinstance(args, NotifyIn):
            self.observed.notified = True
            return NotifyOut(notification_ids=[self._id("notify")], recipients=1, status="stubbed")
        if isinstance(args, GeocodeIn):
            return GeocodeOut(
                latitude=6.5, longitude=3.35, matched=self.case.store_name, source="store_record"
            )
        raise KeyError(name)
