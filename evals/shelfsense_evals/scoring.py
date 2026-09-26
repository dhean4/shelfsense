"""Scoring functions. Pure: they take predictions and truth, and return numbers.

Vision metrics are per photo and averaged; planner metrics are per case and averaged.
Every metric is in [0, 1] except cost (USD) and latency (ms), and higher is better except
``hallucination_rate``, ``facings_mae``, cost and latency.
"""

from dataclasses import dataclass, field
from statistics import mean
from typing import Any
from uuid import UUID

from shelfsense_api.agents.vision import ShelfExtraction
from shelfsense_api.llm import LLMResponse
from shelfsense_api.llm.pricing import DEFAULT_PRICES, cost_usd
from shelfsense_evals.dataset import PlannerExpected, VisionTruth


def _prf(predicted: set[UUID], truth: set[UUID]) -> tuple[float, float, float]:
    if not predicted and not truth:
        return 1.0, 1.0, 1.0
    tp = len(predicted & truth)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


@dataclass
class VisionScore:
    """Scores for one photo."""

    case_id: str
    sku_precision: float
    sku_recall: float
    stock_out_f1: float
    facings_mae: float
    hallucination_rate: float
    exact_match: float
    cost_usd: float
    latency_ms: int
    attempts: int
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Flat dict for JSON."""
        return self.__dict__.copy()


def score_vision(
    case_id: str,
    extraction: ShelfExtraction | None,
    truth: VisionTruth,
    responses: list[LLMResponse],
    *,
    error: str | None = None,
) -> VisionScore:
    """Compare an extraction with the truth. A failed run scores zero, not NaN."""
    cost = sum(cost_usd(r.model, r.usage, DEFAULT_PRICES) or 0.0 for r in responses)
    latency = sum(r.latency_ms for r in responses)
    if extraction is None:
        return VisionScore(
            case_id,
            0,
            0,
            0,
            float(max(truth.facings.values(), default=0)),
            1.0,
            0,
            cost,
            latency,
            len(responses),
            error,
        )

    predicted_facings: dict[UUID, int] = {}
    for item in extraction.items:
        if item.sku_id is not None:
            predicted_facings[item.sku_id] = predicted_facings.get(item.sku_id, 0) + item.facings
    present = {sku for sku, n in predicted_facings.items() if n > 0}
    precision, recall, _ = _prf(present, truth.present)
    _, _, so_f1 = _prf(set(extraction.stock_outs), truth.stock_outs)
    mae = (
        mean(abs(predicted_facings.get(sku, 0) - n) for sku, n in truth.facings.items())
        if truth.facings
        else 0.0
    )

    unknown_pred = sum(1 for i in extraction.items if i.sku_id is None)
    false_presence = len(present - truth.present)
    extra_unknown = max(0, unknown_pred - len(truth.unknown_products))
    hallucinated = false_presence + extra_unknown
    hallucination_rate = hallucinated / len(extraction.items) if extraction.items else 0.0

    exact = float(present == truth.present and set(extraction.stock_outs) == truth.stock_outs)
    return VisionScore(
        case_id=case_id,
        sku_precision=precision,
        sku_recall=recall,
        stock_out_f1=so_f1,
        facings_mae=mae,
        hallucination_rate=hallucination_rate,
        exact_match=exact,
        cost_usd=cost,
        latency_ms=latency,
        attempts=len(responses),
    )


@dataclass
class PlannerScore:
    """Scores for one planner case."""

    case_id: str
    decision_accuracy: float
    reorder_f1: float
    notify_correct: float
    dispatch_correct: float
    escalate_correct: float
    unneeded_action_rate: float
    cost_usd: float
    latency_ms: int
    steps: int
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Flat dict for JSON."""
        return self.__dict__.copy()


@dataclass
class PlannerObserved:
    """What a planner run did, as the executor recorded it."""

    reorder_skus: set[UUID] = field(default_factory=set)
    notified: bool = False
    dispatched: bool = False
    escalated: bool = False
    unknown_sku_reorders: int = 0


def score_planner(
    case_id: str,
    observed: PlannerObserved | None,
    expected: PlannerExpected,
    responses: list[LLMResponse],
    *,
    error: str | None = None,
) -> PlannerScore:
    """Compare decisions with the expected policy outcome."""
    cost = sum(cost_usd(r.model, r.usage, DEFAULT_PRICES) or 0.0 for r in responses)
    latency = sum(r.latency_ms for r in responses)
    if observed is None:
        return PlannerScore(case_id, 0, 0, 0, 0, 0, 1.0, cost, latency, len(responses), error)
    expected_reorders = set(expected.reorder_skus)
    _, _, reorder_f1 = _prf(observed.reorder_skus, expected_reorders)
    notify_ok = float(observed.notified == expected.notify)
    dispatch_ok = float(observed.dispatched == expected.dispatch)
    escalate_ok = float(observed.escalated == expected.escalate)
    exact = float(
        observed.reorder_skus == expected_reorders
        and notify_ok == 1.0
        and dispatch_ok == 1.0
        and escalate_ok == 1.0
    )
    unneeded = len(observed.reorder_skus - expected_reorders) + observed.unknown_sku_reorders
    unneeded += int(observed.dispatched and not expected.dispatch)
    total_actions = (
        len(observed.reorder_skus)
        + observed.unknown_sku_reorders
        + int(observed.dispatched)
        + int(observed.notified)
    )
    return PlannerScore(
        case_id=case_id,
        decision_accuracy=exact,
        reorder_f1=reorder_f1,
        notify_correct=notify_ok,
        dispatch_correct=dispatch_ok,
        escalate_correct=escalate_ok,
        unneeded_action_rate=unneeded / total_actions if total_actions else 0.0,
        cost_usd=cost,
        latency_ms=latency,
        steps=len(responses),
    )


VISION_METRICS = (
    "sku_precision",
    "sku_recall",
    "stock_out_f1",
    "facings_mae",
    "hallucination_rate",
    "exact_match",
    "cost_usd",
    "latency_ms",
)
PLANNER_METRICS = (
    "decision_accuracy",
    "reorder_f1",
    "notify_correct",
    "dispatch_correct",
    "escalate_correct",
    "unneeded_action_rate",
    "cost_usd",
    "latency_ms",
)
LOWER_IS_BETTER = frozenset(
    {"facings_mae", "hallucination_rate", "unneeded_action_rate", "cost_usd", "latency_ms"}
)


def aggregate(scores: list[dict[str, Any]], metrics: tuple[str, ...]) -> dict[str, float]:
    """Mean of each metric over cases (cost is also summed as ``total_cost_usd``)."""
    if not scores:
        return {}
    out = {m: float(mean(float(s[m]) for s in scores)) for m in metrics}
    out["total_cost_usd"] = float(sum(float(s["cost_usd"]) for s in scores))
    out["cases"] = float(len(scores))
    out["failures"] = float(sum(1 for s in scores if s.get("error")))
    return out
