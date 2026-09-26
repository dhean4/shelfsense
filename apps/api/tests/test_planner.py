"""The planner loop with a scripted model and a stubbed tool executor (no database)."""

import json
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shelfsense_api.agents import planner
from shelfsense_api.agents.planner import PlannerFailed, PlannerInput, run_planner
from shelfsense_api.agents.tools import ToolContext, ToolExecution
from shelfsense_api.agents.vision import ExtractionSummary, SlotCompliance
from shelfsense_api.config import Settings
from shelfsense_api.guardrails import CostCapExceeded, StepCapExceeded
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.llm.types import ToolResultPart, Usage

STORE = UUID(int=1)
SKU_A = UUID(int=11)
SKU_B = UUID(int=12)


def _input(trigger_role: str = "manager", confidence: float = 0.9) -> PlannerInput:
    summary = ExtractionSummary(
        slots=[
            SlotCompliance(
                sku_id=SKU_A,
                name="Peak Milk",
                position=1,
                expected_facings=3,
                min_facings=1,
                observed_facings=0,
                status="out",
            ),
            SlotCompliance(
                sku_id=SKU_B,
                name="Milo",
                position=2,
                expected_facings=3,
                min_facings=1,
                observed_facings=3,
                status="ok",
            ),
        ],
        stock_out_count=1,
        stock_out_rate=0.5,
        compliance_rate=0.5,
        planogram_share_of_shelf=100,
        unknown_item_count=0,
    )
    return PlannerInput(
        store_id=STORE,
        store_name="Ikeja Depot Shop",
        shelf_label="Dairy Chiller",
        extraction_summary=summary,
        overall_confidence=confidence,
        notes="ask Ada on 0803 123 4567",
        trigger_role=trigger_role,
    )


class StubExecutor:
    """Replaces agents.tools.execute: records calls, returns canned results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self, ctx: ToolContext, name: str, arguments: dict[str, Any]
    ) -> ToolExecution:
        self.calls.append((name, arguments))
        if name == "get_inventory":
            return ToolExecution(
                name,
                arguments,
                {
                    "store_id": str(STORE),
                    "items": [{"sku_id": str(SKU_A), "on_hand": 2, "reorder_point": 6}],
                },
                None,
                1,
            )
        if name == "create_reorder":
            return ToolExecution(
                name,
                arguments,
                {"action_id": str(uuid4()), "estimated_cost_kobo": 540_000},
                None,
                1,
            )
        return ToolExecution(name, arguments, None, f"tool {name!r} is not available", 1)


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    stub = StubExecutor()
    monkeypatch.setattr(planner, "execute", stub)

    async def no_gating(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(planner, "_gate_actions", no_gating)
    return stub


def _ctx() -> ToolContext:
    # No database in these tests: the executor is stubbed, so the session is never touched.
    session = cast(AsyncSession, None)
    return ToolContext(
        session=session, settings=Settings(), tenant_id=UUID(int=9), role="manager", run_id=uuid4()
    )


async def test_loop_executes_tools_feeds_results_back_and_submits(executor: StubExecutor) -> None:
    fake = FakeProvider()
    fake.push_tool_calls(
        [("get_inventory", {"store_id": str(STORE), "sku_ids": []})], text="Checking stock."
    )
    fake.push_tool_calls(
        [
            (
                "create_reorder",
                {
                    "store_id": str(STORE),
                    "sku_id": str(SKU_A),
                    "quantity": 12,
                    "reason": "out of stock, on hand below reorder point",
                },
            )
        ]
    )
    fake.push_tool_calls(
        [
            (
                "submit_decisions",
                {
                    "summary": "Reordered 12 Peak Milk.",
                    "escalate": False,
                    "escalation_reason": None,
                },
            )
        ]
    )

    result = await run_planner(fake, Settings(llm_provider="fake"), _ctx(), _input())

    assert result.steps == 3
    assert [c[0] for c in executor.calls] == ["get_inventory", "create_reorder"]
    assert result.decisions.summary == "Reordered 12 Peak Milk."
    # The second request carried the first tool result back to the model.
    second = fake.requests[1].messages
    assert second[1].role == "assistant" and second[1].parts[1].type == "tool_use"
    result_part = second[2].parts[0]
    assert isinstance(result_part, ToolResultPart)
    assert '"on_hand": 2' in result_part.content
    # Tools offered match the trigger role, and PII in notes was scrubbed.
    assert {t.name for t in fake.requests[0].tools} >= {
        "get_inventory",
        "create_reorder",
        "submit_decisions",
    }
    first_text = fake.requests[0].messages[0].parts[0].text  # type: ignore[union-attr]
    assert "[phone]" in first_text and "0803" not in first_text


async def test_field_agent_does_not_see_dispatch(executor: StubExecutor) -> None:
    fake = FakeProvider()
    fake.push_tool_calls(
        [("submit_decisions", {"summary": "ok", "escalate": False, "escalation_reason": None})]
    )
    await run_planner(
        fake, Settings(llm_provider="fake"), _ctx(), _input(trigger_role="field_agent")
    )
    assert "dispatch_technician" not in {t.name for t in fake.requests[0].tools}


async def test_model_that_stops_without_submitting_is_nudged_once(executor: StubExecutor) -> None:
    fake = FakeProvider()
    fake.push_text("I think we are done.")
    fake.push_tool_calls(
        [
            (
                "submit_decisions",
                {"summary": "done", "escalate": True, "escalation_reason": "photo unclear"},
            )
        ]
    )
    result = await run_planner(fake, Settings(llm_provider="fake"), _ctx(), _input())
    assert result.decisions.escalate is True
    nudge = fake.requests[1].messages[-1].parts[0].text  # type: ignore[union-attr]
    assert "Call submit_decisions" in nudge


async def test_model_that_never_submits_fails(executor: StubExecutor) -> None:
    fake = FakeProvider()
    fake.push_text("done")
    fake.push_text("really done")
    with pytest.raises(PlannerFailed, match="without calling submit_decisions"):
        await run_planner(fake, Settings(llm_provider="fake"), _ctx(), _input())


async def test_step_cap(executor: StubExecutor) -> None:
    fake = FakeProvider()
    for _ in range(3):
        fake.push_tool_calls([("get_inventory", {"store_id": str(STORE), "sku_ids": []})])
    with pytest.raises(StepCapExceeded):
        await run_planner(
            fake, Settings(llm_provider="fake", planner_max_steps=3), _ctx(), _input()
        )


async def test_cost_cap_uses_priced_model_usage(executor: StubExecutor) -> None:
    fake = FakeProvider()
    fake.push_tool_calls(
        [("get_inventory", {"store_id": str(STORE), "sku_ids": []})],
        model="claude-opus-5",
        usage=Usage(input_tokens=200_000, output_tokens=10_000),  # $1.00 + $0.25
    )
    with pytest.raises(CostCapExceeded, match=r"spent \$1\.25"):
        await run_planner(
            fake, Settings(llm_provider="fake", planner_max_cost_usd=0.5), _ctx(), _input()
        )


def test_invalid_submit_payload_is_a_planner_failure(executor: StubExecutor) -> None:
    fake = FakeProvider()
    fake.push_tool_calls([("submit_decisions", {"escalate": "maybe"})])
    import asyncio

    with pytest.raises(PlannerFailed, match="invalid submit_decisions"):
        asyncio.run(run_planner(fake, Settings(llm_provider="fake"), _ctx(), _input()))


def test_run_summary_round_trips() -> None:
    from shelfsense_api.agents.planner import PlannerResult, PlannerRunSummary
    from shelfsense_api.agents.tools import SubmitDecisionsIn

    result = PlannerResult(
        decisions=SubmitDecisionsIn(summary="s", escalate=False, escalation_reason=None),
        responses=[],
        executions=[],
        cost_usd=0.01,
        steps=2,
        actions_for_review=[UUID(int=5)],
    )
    payload = json.loads(PlannerRunSummary.from_result(result).as_json())
    assert payload["actions_for_review"] == [str(UUID(int=5))]
    assert payload["tool_calls"] == 0
