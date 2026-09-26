"""The vision agent against recorded model responses and scripted failures."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from shelfsense_api.agents.vision import (
    ExtractionValidationError,
    PlanogramContext,
    ShelfExtraction,
    VisionFailed,
    run_vision,
    summarise,
    validate_extraction,
)
from shelfsense_api.config import Settings
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.llm.replay import ReplayProvider
from shelfsense_api.synthetic import SCENARIOS, load_truth, planogram_context, sku_id_for

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PHOTOS = FIXTURES / "photos"
LLM = FIXTURES / "llm"


def _settings(**overrides: object) -> Settings:
    return Settings(llm_provider="replay", llm_fixtures_dir=str(LLM), **overrides)  # type: ignore[arg-type]


def _ctx(name: str) -> PlanogramContext:
    scenario = next(s for s in SCENARIOS if s.name == name)
    return planogram_context(scenario.tenant_slug, scenario.store_name, scenario.shelf_label)


# --- validation rules (no model needed) -------------------------------------------------


def _valid_json(ctx: PlanogramContext, **overrides: object) -> str:
    first = ctx.slots[0]
    payload: dict[str, object] = {
        "items": [
            {
                "sku_id": str(first.sku_id),
                "label": first.name,
                "facings": 3,
                "region": {"x": 0, "y": 0, "w": 0.2, "h": 0.4},
                "confidence": 0.9,
            }
        ],
        "stock_outs": [str(s.sku_id) for s in ctx.slots[1:]],
        "share_of_shelf": [{"sku_id": str(first.sku_id), "percent": 100}],
        "overall_confidence": 0.8,
        "notes": "",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_validate_accepts_consistent_output() -> None:
    ctx = _ctx("dairy_full")
    extraction = validate_extraction(_valid_json(ctx), ctx)
    assert isinstance(extraction, ShelfExtraction)
    summary = summarise(extraction, ctx)
    assert summary.stock_out_count == len(ctx.slots) - 1
    assert summary.slots[0].status == "ok"
    assert summary.planogram_share_of_shelf == 100


@pytest.mark.parametrize(
    "override, message",
    [
        ({"stock_outs": []}, "missing from stock_outs"),
        ({"stock_outs": [str(UUID(int=1))]}, "not in the planogram"),
        ({"share_of_shelf": [{"sku_id": str(UUID(int=2)), "percent": 10}]}, "unknown sku_id"),
        ({"overall_confidence": 2}, "schema violation"),
    ],
)
def test_validate_rejects_inconsistent_output(override: dict[str, object], message: str) -> None:
    ctx = _ctx("dairy_full")
    with pytest.raises(ExtractionValidationError, match=message):
        validate_extraction(_valid_json(ctx, **override), ctx)


def test_stock_out_with_facings_is_rejected() -> None:
    ctx = _ctx("dairy_full")
    first = ctx.slots[0]
    raw = _valid_json(ctx, stock_outs=[str(s.sku_id) for s in ctx.slots])
    with pytest.raises(ExtractionValidationError, match=f"{first.sku_id} is listed as a stock-out"):
        validate_extraction(raw, ctx)


# --- repair loop with a scripted provider ------------------------------------------------


async def test_repair_loop_sends_the_error_back_and_recovers() -> None:
    ctx = _ctx("dairy_full")
    fake = FakeProvider()
    fake.push_text(_valid_json(ctx, stock_outs=[]))  # invalid: missing stock-outs
    fake.push_text(_valid_json(ctx))  # repaired
    settings = Settings(llm_provider="fake", vision_max_repairs=2)
    image = (PHOTOS / "dairy_full.png").read_bytes()

    result = await run_vision(fake, settings, image, ctx)

    assert result.attempts == 2
    repair = fake.requests[1].messages
    assert repair[1].role == "assistant"
    assert "failed validation" in repair[2].parts[0].text  # type: ignore[union-attr]
    assert "missing from stock_outs" in repair[2].parts[0].text  # type: ignore[union-attr]


async def test_repair_budget_exhausted_raises_with_responses() -> None:
    ctx = _ctx("dairy_full")
    fake = FakeProvider()
    for _ in range(3):
        fake.push_text(_valid_json(ctx, stock_outs=[]))
    settings = Settings(llm_provider="fake", vision_max_repairs=2)
    image = (PHOTOS / "dairy_full.png").read_bytes()
    with pytest.raises(VisionFailed) as exc:
        await run_vision(fake, settings, image, ctx)
    assert len(exc.value.responses) == 3


# --- recorded model responses ------------------------------------------------------------


@pytest.mark.parametrize("scenario", [s.name for s in SCENARIOS])
async def test_recorded_extraction_matches_ground_truth(scenario: str) -> None:
    """Replays the real model's answer for each synthetic shelf.

    Asserts the things the product depends on: every stock-out in the truth is reported,
    no invented SKUs, facings within one of the truth for most slots.
    """
    ctx = _ctx(scenario)
    truth = load_truth(PHOTOS, scenario)
    image = (PHOTOS / f"{scenario}.png").read_bytes()

    result = await run_vision(ReplayProvider(LLM), _settings(), image, ctx)

    expected_outs = {sku_id_for(truth, name) for name in truth["stock_outs"]}
    assert expected_outs <= set(result.extraction.stock_outs), "missed a stock-out"
    facings = truth["facings"]
    assert isinstance(facings, dict)
    close = 0
    for slot in result.summary.slots:
        if abs(slot.observed_facings - int(facings[slot.name])) <= 1:
            close += 1
    assert close / len(result.summary.slots) >= 0.6, [
        (s.name, s.observed_facings, facings[s.name]) for s in result.summary.slots
    ]
    assert result.extraction.overall_confidence > 0.3
    assert result.summary.unknown_item_count == len(truth["unknown_products"])
