from uuid import UUID

from shelfsense_api.agents.vision import ShelfExtraction
from shelfsense_api.llm import LLMResponse, Usage
from shelfsense_evals.compare import GATED_METRICS, deltas, markdown, regressions
from shelfsense_evals.dataset import PlannerExpected, VisionTruth
from shelfsense_evals.scoring import (
    PlannerObserved,
    aggregate,
    score_planner,
    score_vision,
)

A, B, C = UUID(int=1), UUID(int=2), UUID(int=3)


def _resp(model: str = "claude-opus-5", latency: int = 100) -> LLMResponse:
    return LLMResponse(
        model=model,
        text="",
        stop_reason="end_turn",
        usage=Usage(input_tokens=1000, output_tokens=100),
        latency_ms=latency,
        provider="fake",
    )


def _extraction(present: dict[UUID, int], outs: list[UUID], unknown: int = 0) -> ShelfExtraction:
    items = [
        {
            "sku_id": str(s),
            "label": "x",
            "facings": n,
            "region": {"x": 0, "y": 0, "w": 1, "h": 1},
            "confidence": 0.9,
        }
        for s, n in present.items()
    ]
    items += [
        {
            "sku_id": None,
            "label": f"unknown {i}",
            "facings": 1,
            "region": {"x": 0, "y": 0, "w": 1, "h": 1},
            "confidence": 0.5,
        }
        for i in range(unknown)
    ]
    return ShelfExtraction.model_validate(
        {
            "items": items,
            "stock_outs": [str(s) for s in outs],
            "share_of_shelf": [],
            "overall_confidence": 0.8,
            "notes": "",
        }
    )


def test_perfect_vision_scores_one() -> None:
    truth = VisionTruth(facings={A: 3, B: 0, C: 2})
    score = score_vision("c", _extraction({A: 3, C: 2}, [B]), truth, [_resp()])
    assert (score.sku_precision, score.sku_recall, score.stock_out_f1) == (1, 1, 1)
    assert score.facings_mae == 0 and score.hallucination_rate == 0 and score.exact_match == 1
    assert score.cost_usd == 1000 * 5e-6 + 100 * 25e-6
    assert score.latency_ms == 100


def test_hallucinated_presence_and_missed_stock_out() -> None:
    truth = VisionTruth(facings={A: 3, B: 0, C: 2})
    # Claims B present (hallucination), misses C (recall), and an extra unknown product.
    score = score_vision("c", _extraction({A: 2, B: 1}, [C], unknown=1), truth, [_resp()])
    assert score.sku_precision == 0.5 and score.sku_recall == 0.5
    assert score.stock_out_f1 == 0
    assert score.hallucination_rate == 2 / 3  # B plus the unknown, over three items
    assert score.exact_match == 0
    assert score.facings_mae == (1 + 1 + 2) / 3


def test_failed_vision_run_scores_zero_but_costs() -> None:
    truth = VisionTruth(facings={A: 3})
    score = score_vision("c", None, truth, [_resp(), _resp()], error="boom")
    assert score.sku_recall == 0 and score.hallucination_rate == 1.0
    assert score.attempts == 2 and score.error == "boom"


def test_planner_exact_and_partial() -> None:
    expected = PlannerExpected(reorder_skus=[A, B], notify=True)
    exact = score_planner(
        "p", PlannerObserved(reorder_skus={A, B}, notified=True), expected, [_resp()]
    )
    assert exact.decision_accuracy == 1 and exact.unneeded_action_rate == 0
    partial = score_planner(
        "p",
        PlannerObserved(reorder_skus={A, C}, notified=False, dispatched=True),
        expected,
        [_resp()],
    )
    assert partial.decision_accuracy == 0
    assert partial.reorder_f1 == 0.5
    assert partial.notify_correct == 0 and partial.dispatch_correct == 0
    assert partial.unneeded_action_rate == 2 / 3  # C and the dispatch, over three actions


def test_aggregate_and_deltas() -> None:
    scores = [
        score_vision(
            "a", _extraction({A: 3}, []), VisionTruth(facings={A: 3}), [_resp()]
        ).as_dict(),
        score_vision("b", None, VisionTruth(facings={A: 3}), [], error="x").as_dict(),
    ]
    summary = aggregate(scores, ("sku_recall", "hallucination_rate", "cost_usd", "latency_ms"))
    assert summary["sku_recall"] == 0.5 and summary["failures"] == 1 and summary["cases"] == 2
    base = {
        "summary": {
            "vision": {
                "sku_recall": 0.9,
                "hallucination_rate": 0.1,
                "cost_usd": 0.01,
                "latency_ms": 100,
            },
            "planner": {},
        }
    }
    cur = {
        "summary": {
            "vision": {
                "sku_recall": 0.8,
                "hallucination_rate": 0.05,
                "cost_usd": 0.02,
                "latency_ms": 90,
            },
            "planner": {},
        }
    }
    moved = {(d.metric, d.regressed) for d in deltas(base, cur) if d.change}
    assert ("sku_recall", True) in moved and ("hallucination_rate", False) in moved
    assert ("cost_usd", True) in moved and ("latency_ms", False) in moved
    failed = regressions(base, cur, tolerance=0.02, gated=GATED_METRICS)
    assert [d.metric for d in failed] == ["sku_recall"]
    body = markdown(base, cur, tolerance=0.02)
    assert "🔻 regression" in body and "| sku_recall | 0.900 | 0.800 |" in body
