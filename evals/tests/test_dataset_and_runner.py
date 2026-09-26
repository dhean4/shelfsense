import json
from pathlib import Path

import pytest

from shelfsense_api.config import Settings
from shelfsense_api.llm import LLMRequest, LLMResponse, TextPart, ToolCall, Usage
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_evals.dataset import PlannerCase, VisionCase, load_dataset, parse_case
from shelfsense_evals.generate import generate
from shelfsense_evals.runner import run_dataset, write_report

DATASET = Path(__file__).resolve().parent.parent / "data" / "golden.jsonl"


def _store_id(request: LLMRequest) -> str:
    """The store id the planner context announces (``store_id <uuid>``)."""
    first = request.messages[0].parts[0]
    assert isinstance(first, TextPart)
    return first.text.split("store_id ")[1].split(")")[0]


def test_committed_dataset_is_valid_and_complete() -> None:
    cases = load_dataset(DATASET)
    assert len(cases) == 100
    kinds = {c.kind for c in cases}
    assert kinds == {"vision", "planner"}
    for case in cases:
        if isinstance(case, VisionCase):
            assert (DATASET.parent / case.image).exists(), case.id
            assert set(case.truth.facings) == {s.sku_id for s in case.planogram.slots}
    tags = {t for c in cases for t in c.tags}
    assert {"one_out", "intruder", "multi_stockout", "fault", "low_conf"} <= tags


def test_generate_is_deterministic(tmp_path: Path) -> None:
    a = tmp_path / "a" / "golden.jsonl"
    b = tmp_path / "b" / "golden.jsonl"
    assert generate(a, vision=4, planner=3, seed=5) == 7
    assert generate(b, vision=4, planner=3, seed=5) == 7
    assert a.read_text() == b.read_text()
    assert (a.parent / "photos" / "syn_000.png").read_bytes() == (
        b.parent / "photos" / "syn_000.png"
    ).read_bytes()


def test_invalid_case_lines_are_rejected() -> None:
    with pytest.raises(ValueError, match=r"truth\.facings must cover"):
        parse_case(
            json.dumps(
                {
                    "id": "bad",
                    "kind": "vision",
                    "source": "real",
                    "image": "x.png",
                    "planogram": {
                        "store_name": "s",
                        "shelf_label": "l",
                        "slots": [
                            {
                                "sku_id": str(__import__("uuid").UUID(int=1)),
                                "name": "n",
                                "brand": "b",
                                "position": 1,
                                "expected_facings": 2,
                                "min_facings": 1,
                            }
                        ],
                    },
                    "truth": {"facings": {}},
                }
            )
        )


async def test_runner_scores_vision_and_planner_with_a_scripted_model(tmp_path: Path) -> None:
    dataset = tmp_path / "golden.jsonl"
    generate(dataset, vision=2, planner=1, seed=11)
    cases = load_dataset(dataset)
    vision_cases = [c for c in cases if isinstance(c, VisionCase)]
    planner_case = next(c for c in cases if isinstance(c, PlannerCase))

    fake = FakeProvider()

    def script(request: LLMRequest) -> LLMResponse:
        tag = request.metadata.get("tag", "")
        if request.metadata.get("agent") == "vision":
            case = next(c for c in vision_cases if tag.endswith(c.id))
            items = [
                {
                    "sku_id": str(s),
                    "label": "x",
                    "facings": n,
                    "region": {"x": 0, "y": 0, "w": 1, "h": 1},
                    "confidence": 0.9,
                }
                for s, n in case.truth.facings.items()
                if n > 0
            ]
            body = {
                "items": items,
                "stock_outs": [str(s) for s in case.truth.stock_outs],
                "share_of_shelf": [],
                "overall_confidence": 0.9,
                "notes": "",
            }
            return LLMResponse(
                model="claude-opus-5",
                text=json.dumps(body),
                stop_reason="end_turn",
                usage=Usage(input_tokens=10, output_tokens=5),
                latency_ms=3,
                provider="fake",
            )
        # Planner: do exactly what the expected policy says, then submit.
        step = int(request.metadata.get("step", "1"))
        exp = planner_case.expected
        if step == 1:
            calls = [
                ToolCall(
                    id="c1",
                    name="get_inventory",
                    input={
                        "store_id": _store_id(request),
                        "sku_ids": [],
                    },
                )
            ]
            return LLMResponse(
                model="claude-opus-5",
                text="",
                tool_calls=calls,
                stop_reason="tool_use",
                usage=Usage(input_tokens=10, output_tokens=5),
                latency_ms=3,
                provider="fake",
            )
        calls = []
        store_id = _store_id(request)
        for i, sku in enumerate(sorted(exp.reorder_skus)):
            calls.append(
                ToolCall(
                    id=f"r{i}",
                    name="create_reorder",
                    input={
                        "store_id": store_id,
                        "sku_id": str(sku),
                        "quantity": 12,
                        "reason": "policy says so",
                    },
                )
            )
        if exp.notify:
            calls.append(
                ToolCall(
                    id="n",
                    name="notify",
                    input={
                        "channel": "email",
                        "recipient_role": "manager",
                        "subject": "s",
                        "body": "b",
                    },
                )
            )
        if exp.dispatch:
            calls.append(
                ToolCall(
                    id="d",
                    name="dispatch_technician",
                    input={"store_id": store_id, "reason": "fault", "urgency": "high"},
                )
            )
        calls.append(
            ToolCall(
                id="s",
                name="submit_decisions",
                input={
                    "summary": "done",
                    "escalate": exp.escalate,
                    "escalation_reason": "low confidence" if exp.escalate else None,
                },
            )
        )
        return LLMResponse(
            model="claude-opus-5",
            text="",
            tool_calls=calls,
            stop_reason="tool_use",
            usage=Usage(input_tokens=10, output_tokens=5),
            latency_ms=3,
            provider="fake",
        )

    fake.script = script
    report = await run_dataset(fake, Settings(llm_provider="fake"), dataset, concurrency=2)
    assert len(report.vision) == 2 and len(report.planner) == 1
    assert report.summary["vision"]["exact_match"] == 1.0
    assert report.summary["vision"]["sku_recall"] == 1.0
    assert report.summary["planner"]["decision_accuracy"] == 1.0, report.planner
    assert report.summary["planner"]["unneeded_action_rate"] == 0.0
    out = tmp_path / "report.json"
    write_report(report, out)
    saved = json.loads(out.read_text())
    assert saved["summary"]["vision"]["cases"] == 2 and saved["provider"] == "fake"
