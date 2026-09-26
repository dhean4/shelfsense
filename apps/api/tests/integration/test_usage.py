"""The usage report aggregates real runs."""

import io
import json

import pytest
from httpx import AsyncClient
from PIL import Image

from shelfsense_api.config import Settings, get_settings
from shelfsense_api.jobs import get_queue
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.llm.types import Usage
from shelfsense_api.models import Role
from shelfsense_api.seed import stable_id

from .conftest import dev_headers

pytestmark = pytest.mark.integration

A = "lagos-fresh"
B = "surulere-chill"
DAIRY_SHELF = stable_id("shelf", f"{A}:Ikeja Depot Shop:Dairy Chiller")


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (200, 120), (90, 90, 90)).save(out, format="PNG")
    return out.getvalue()


async def test_usage_report_reflects_runs_and_is_tenant_scoped(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    planogram = (
        await client.get(
            f"/v1/shelves/{DAIRY_SHELF}/planogram", headers=dev_headers(A, Role.reviewer)
        )
    ).json()
    sku_ids = [s["sku_id"] for s in planogram["slots"]]
    items = [
        {
            "sku_id": s,
            "label": "x",
            "facings": 2,
            "region": {"x": 0, "y": 0, "w": 0.1, "h": 0.1},
            "confidence": 0.9,
        }
        for s in sku_ids
    ]
    extraction = {
        "items": items,
        "stock_outs": [],
        "share_of_shelf": [{"sku_id": s, "percent": 100 / len(items)} for s in sku_ids],
        "overall_confidence": 0.9,
        "notes": "",
    }
    fake_llm.push_text(json.dumps(extraction), model="claude-opus-5")
    fake_llm.push_tool_calls(
        [
            (
                "submit_decisions",
                {"summary": "all fine", "escalate": False, "escalation_reason": None},
            )
        ],
        model="claude-opus-5",
        usage=Usage(input_tokens=1000, output_tokens=100),
    )
    before = (
        await client.get("/v1/usage", params={"days": 1}, headers=dev_headers(A, Role.owner))
    ).json()

    upload = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("s.png", _png(), "image/png")},
        headers=dev_headers(A, Role.manager),
    )
    assert upload.status_code == 202
    queue = get_queue(get_settings())
    assert await queue.process_one() and await queue.process_one()

    after = (
        await client.get("/v1/usage", params={"days": 1}, headers=dev_headers(A, Role.owner))
    ).json()
    assert after["days"] == 1
    assert after["totals"]["runs"] == before["totals"]["runs"] + 2
    assert after["totals"]["succeeded"] == before["totals"]["succeeded"] + 2
    assert after["totals"]["cost_usd"] > before["totals"]["cost_usd"]
    assert after["totals"]["p95_latency_ms"] >= 0
    kinds = {b["kind"] for b in after["by_day"]}
    assert {"vision", "planner"} <= kinds
    assert any(b["key"] == "claude-opus-5" for b in after["by_model"])
    assert (
        after["top_runs"] and after["top_runs"][0]["cost_usd"] >= after["top_runs"][-1]["cost_usd"]
    )

    # Runs carry a trace id only when Langfuse is configured; here it is not.
    run = (
        await client.get(
            f"/v1/runs/{after['top_runs'][0]['id']}", headers=dev_headers(A, Role.owner)
        )
    ).json()
    assert run["trace_id"] is None and run["trace_url"] is None

    other = (
        await client.get("/v1/usage", params={"days": 1}, headers=dev_headers(B, Role.owner))
    ).json()
    assert (
        other["totals"]["runs"] <= before["totals"]["runs"]
        or other["totals"]["runs"] < after["totals"]["runs"]
    )
