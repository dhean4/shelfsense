"""Planner agent: extraction + context → actions, through a plan → act → observe loop.

Each turn the model may call tools; the loop executes them (logging every call), feeds
the results back, and stops when the model calls ``submit_decisions``. Guardrails bound
the loop (cost cap, step cap) and decide afterwards which actions need a human.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from shelfsense_api.agents.tools import (
    REGISTRY,
    SUBMIT,
    SubmitDecisionsIn,
    ToolContext,
    ToolExecution,
    execute,
    specs_for_role,
)
from shelfsense_api.agents.vision import ExtractionSummary
from shelfsense_api.config import Settings
from shelfsense_api.guardrails import (
    ActionKind,
    CostCapExceeded,
    StepCapExceeded,
    review_decision,
)
from shelfsense_api.llm import LLMProvider, LLMRequest, LLMResponse, Message
from shelfsense_api.llm.pricing import cost_usd, load_prices
from shelfsense_api.llm.types import ToolResultPart
from shelfsense_api.models import Action, ActionStatus
from shelfsense_api.pii import scrub

SYSTEM_PROMPT = """You are the operations planner for an FMCG distributor in Lagos.

You receive the result of a shelf audit (what a photo showed against the planogram) and,
when available, cold-chain telemetry for the store's fridge. Decide what to do, using the
tools, then finish with submit_decisions.

How to work:
1. Read the audit. Stock-outs and slots below their minimum facings are the problem.
2. Call get_inventory for the store before proposing any reorder. If the store already
   holds enough stock to refill the shelf, the fix is a shelf refill, not an order: say so
   in the summary and notify a manager instead of reordering.
3. For SKUs that are out or low AND whose on_hand is at or below the reorder point,
   call create_reorder. Order in whole cases: quantity is a multiple of case_size, enough
   to reach expected_facings x 3 units plus the reorder point. Never order what is not
   in the planogram.
4. Notify a manager when two or more planogram SKUs are stocked out, when the audit's
   confidence is low, or when you propose a dispatch.
5. dispatch_technician only for equipment faults (fridge temperature out of range in the
   telemetry, or visible damage). Not for empty shelves.
6. If the photo could not be trusted (low confidence, not a shelf, occluded), do not
   guess: escalate with a reason.
7. Call submit_decisions exactly once, last, with a summary a store manager can act on.

Be economical: one get_inventory call per store, parallel tool calls where independent,
no repeated calls. Money is in kobo (100 kobo = ₦1)."""


@dataclass(frozen=True)
class PlannerInput:
    """Everything the planner knows before it calls a tool."""

    store_id: UUID
    store_name: str
    trigger_role: str
    shelf_label: str | None = None
    extraction_summary: ExtractionSummary | None = None
    overall_confidence: float | None = None
    notes: str = ""
    telemetry: str | None = None  # fridge/GPS context rendered as text (P5)


def _context_text(inp: PlannerInput) -> str:
    lines = [f"Store: {inp.store_name} (store_id {inp.store_id})"]
    s = inp.extraction_summary
    if s is not None and inp.overall_confidence is not None:
        lines += [
            f"Shelf: {inp.shelf_label or 'unknown'}",
            f"Audit confidence: {inp.overall_confidence:.2f}",
            f"Stock-outs: {s.stock_out_count} of {len(s.slots)} planogram SKUs; "
            f"compliance {s.compliance_rate:.0%}; planogram share of shelf "
            f"{s.planogram_share_of_shelf:.0f}%; unknown products seen: {s.unknown_item_count}",
            "Slots (sku_id | product | expected | minimum | observed | status):",
        ]
        for slot in s.slots:
            lines.append(
                f"{slot.sku_id} | {slot.name} | {slot.expected_facings} | {slot.min_facings} | "
                f"{slot.observed_facings} | {slot.status}"
            )
    else:
        lines.append("Shelf audit: none (this run was triggered by telemetry, not a photo).")
    if inp.notes:
        lines.append(f"Auditor notes: {scrub(inp.notes)}")
    lines.append(f"Telemetry: {scrub(inp.telemetry) if inp.telemetry else 'none available'}")
    lines.append(f"Triggered by role: {inp.trigger_role}")
    return "\n".join(lines)


@dataclass
class PlannerResult:
    """Outcome of a run."""

    decisions: SubmitDecisionsIn
    responses: list[LLMResponse]
    executions: list[ToolExecution]
    cost_usd: float
    steps: int
    actions_for_review: list[UUID] = field(default_factory=list)
    actions_auto_approved: list[UUID] = field(default_factory=list)


class PlannerFailed(RuntimeError):
    """The loop ended without a valid ``submit_decisions``."""

    def __init__(self, message: str, responses: list[LLMResponse]) -> None:
        """Keep responses so the run can still be costed."""
        super().__init__(message)
        self.responses = responses


def _spend(responses: list[LLMResponse], settings: Settings) -> float:
    prices = load_prices(settings.llm_pricing_json)
    return sum(cost_usd(r.model, r.usage, prices) or 0.0 for r in responses)


Executor = Callable[[ToolContext, str, dict[str, Any]], Awaitable[ToolExecution]]


async def run_planner(
    provider: LLMProvider,
    settings: Settings,
    ctx: ToolContext,
    inp: PlannerInput,
    *,
    executor: Executor | None = None,
    gate: bool = True,
) -> PlannerResult:
    """Run the loop under ``ctx`` (a tenant session), then apply review gating.

    ``executor`` defaults to the database-backed :func:`execute`; the evals pass an
    in-memory one and turn ``gate`` off because there are no action rows to gate.
    """
    run_tool = executor or execute
    tools = specs_for_role(inp.trigger_role)
    messages = [Message.user(_context_text(inp))]
    responses: list[LLMResponse] = []
    executions: list[ToolExecution] = []
    nudged = False

    for step in range(1, settings.planner_max_steps + 1):
        request = LLMRequest(
            model=settings.planner_model,
            system=SYSTEM_PROMPT,
            messages=messages,
            max_tokens=settings.planner_max_tokens,
            effort=settings.planner_effort,
            tools=tools,
            metadata={"agent": "planner", "step": str(step), "run": str(ctx.run_id)},
        )
        response = await provider.complete(request)
        responses.append(response)
        spent = _spend(responses, settings)
        if spent > settings.planner_max_cost_usd:
            raise CostCapExceeded(
                f"planner spent ${spent:.4f} after {step} steps, "
                f"cap ${settings.planner_max_cost_usd:.2f}",
                tuple(responses),
            )

        submit = next((c for c in response.tool_calls if c.name == SUBMIT), None)
        if submit is not None:
            try:
                decisions = SubmitDecisionsIn.model_validate(submit.input)
            except ValueError as exc:
                raise PlannerFailed(f"invalid submit_decisions: {exc}", responses) from exc
            # Any other calls in the same turn still run: the model may batch its last
            # action with the submission.
            others = [c for c in response.tool_calls if c.name != SUBMIT]
            for call in others:
                executions.append(await run_tool(ctx, call.name, call.input))
            result = PlannerResult(
                decisions=decisions,
                responses=responses,
                executions=executions,
                cost_usd=spent,
                steps=step,
            )
            if gate:
                await _gate_actions(ctx, inp, result, settings)
            return result

        if not response.tool_calls:
            if nudged:
                raise PlannerFailed("model ended without calling submit_decisions", responses)
            nudged = True
            messages = [
                *messages,
                Message.assistant(response.text or "(no output)"),
                Message.user(
                    "You have not finished. Call submit_decisions with your summary now, "
                    "after any remaining tool calls."
                ),
            ]
            continue

        results: list[ToolResultPart] = []
        for call in response.tool_calls:
            execution = await run_tool(ctx, call.name, call.input)
            executions.append(execution)
            results.append(
                ToolResultPart(
                    tool_use_id=call.id,
                    content=execution.content,
                    is_error=execution.error is not None,
                )
            )
        messages = [
            *messages,
            Message.assistant(response.text, response.tool_calls),
            Message.tool_results(results),
        ]

    raise StepCapExceeded(
        f"planner used {settings.planner_max_steps} steps without submitting decisions",
        tuple(responses),
    )


async def _gate_actions(
    ctx: ToolContext, inp: PlannerInput, result: PlannerResult, settings: Settings
) -> None:
    """Move each proposed action to approved or pending_review; record an escalation."""
    if result.decisions.escalate:
        escalation = Action(
            tenant_id=ctx.tenant_id,
            run_id=ctx.run_id,
            store_id=inp.store_id,
            kind=ActionKind.escalate,
            status=ActionStatus.proposed,
            payload={"shelf_label": inp.shelf_label or "", "store_name": inp.store_name},
            estimated_cost_kobo=0,
            confidence=inp.overall_confidence,
            rationale=result.decisions.escalation_reason or result.decisions.summary,
        )
        ctx.session.add(escalation)
        await ctx.session.flush()

    proposed = await ctx.session.scalars(
        select(Action).where(Action.run_id == ctx.run_id, Action.status == ActionStatus.proposed)
    )
    for action in proposed:
        decision = review_decision(
            ActionKind(action.kind),
            action.estimated_cost_kobo,
            inp.overall_confidence,
            inp.trigger_role,
            settings,
        )
        action.requires_review = decision.required
        action.review_reason = decision.reason
        if decision.required:
            action.status = ActionStatus.pending_review
            result.actions_for_review.append(action.id)
        else:
            action.status = ActionStatus.approved
            result.actions_auto_approved.append(action.id)
    await ctx.session.flush()


def planner_input_from_extraction(
    *,
    store_id: UUID,
    store_name: str,
    shelf_label: str,
    extraction_result: dict[str, Any],
    trigger_role: str,
    telemetry: str | None = None,
) -> PlannerInput:
    """Build the input from an ``extractions.result`` payload."""
    summary = ExtractionSummary.model_validate(extraction_result["summary"])
    extraction = extraction_result["extraction"]
    return PlannerInput(
        store_id=store_id,
        store_name=store_name,
        shelf_label=shelf_label,
        extraction_summary=summary,
        overall_confidence=float(extraction["overall_confidence"]),
        notes=str(extraction.get("notes", "")),
        trigger_role=trigger_role,
        telemetry=telemetry,
    )


def planner_input_from_anomaly(
    *,
    store_id: UUID,
    store_name: str,
    device_label: str,
    started_at: str,
    peak_temperature_c: float | None,
    max_temp_c: float,
    recent: str,
    trigger_role: str = "system",
) -> PlannerInput:
    """Build the input for a run triggered by a cold-chain excursion."""
    telemetry = (
        f"Fridge '{device_label}' at {store_name} has been above {max_temp_c:.0f}°C since "
        f"{started_at} (peak {peak_temperature_c if peak_temperature_c is not None else '?'}°C). "
        f"Recent readings: {recent}. Stock in that fridge is at risk."
    )
    return PlannerInput(
        store_id=store_id, store_name=store_name, trigger_role=trigger_role, telemetry=telemetry
    )


def tool_names() -> list[str]:
    """Every registered tool, for docs and tests."""
    return list(REGISTRY)


class PlannerRunSummary(BaseModel):
    """Stored on the run for the timeline UI."""

    summary: str
    escalate: bool
    escalation_reason: str | None
    steps: int
    tool_calls: int
    actions_for_review: list[UUID]
    actions_auto_approved: list[UUID]

    @classmethod
    def from_result(cls, result: PlannerResult) -> "PlannerRunSummary":
        """Flatten a result."""
        return cls(
            summary=result.decisions.summary,
            escalate=result.decisions.escalate,
            escalation_reason=result.decisions.escalation_reason,
            steps=result.steps,
            tool_calls=len(result.executions),
            actions_for_review=result.actions_for_review,
            actions_auto_approved=result.actions_auto_approved,
        )

    def as_json(self) -> str:
        """Serialised for ``agent_runs.summary``."""
        return json.dumps(json.loads(self.model_dump_json()))
