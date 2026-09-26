"""Run progress events reach a subscriber while the worker processes a pipeline."""

import asyncio
import io
import json

import pytest
from httpx import AsyncClient
from PIL import Image

from shelfsense_api.config import Settings, get_settings
from shelfsense_api.events import runs_channel, sse_stream
from shelfsense_api.jobs import get_queue
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.models import Role
from shelfsense_api.seed import stable_id

from .conftest import dev_headers

pytestmark = pytest.mark.integration

A = "lagos-fresh"
DAIRY_SHELF = stable_id("shelf", f"{A}:Ikeja Depot Shop:Dairy Chiller")
STORE = stable_id("store", A, "Ikeja Depot Shop")


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (160, 100), (70, 70, 70)).save(out, format="PNG")
    return out.getvalue()


async def test_pipeline_emits_ordered_run_events(
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
        for s in sku_ids[1:]
    ]
    good = {
        "items": items,
        "stock_outs": [sku_ids[0]],
        "share_of_shelf": [{"sku_id": i["sku_id"], "percent": 100 / len(items)} for i in items],
        "overall_confidence": 0.9,
        "notes": "call 0803 111 2222",
    }
    bad = {**good, "stock_outs": []}  # first attempt inconsistent → repair turn
    fake_llm.push_text(json.dumps(bad))
    fake_llm.push_text(json.dumps(good))
    fake_llm.push_tool_calls(
        [("get_inventory", {"store_id": str(STORE), "sku_ids": []})],
        text="Checking stock for 0803 111 2222",
    )
    fake_llm.push_tool_calls(
        [("submit_decisions", {"summary": "ok", "escalate": False, "escalation_reason": None})]
    )

    settings = get_settings()
    stop = asyncio.Event()
    stream = sse_stream(
        settings.redis_url, runs_channel(stable_id("tenant", A)), heartbeat_seconds=1, stop=stop
    )
    assert (await stream.__anext__()).startswith("event: hello")

    async def drive() -> None:
        await asyncio.sleep(0.2)
        upload = await client.post(
            f"/v1/shelves/{DAIRY_SHELF}/photos",
            files={"file": ("s.png", _png(), "image/png")},
            headers=dev_headers(A, Role.manager),
        )
        assert upload.status_code == 202
        queue = get_queue(settings)
        assert await queue.process_one() and await queue.process_one()

    driver = asyncio.create_task(drive())
    events: list[dict[str, object]] = []
    for _ in range(40):
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=5)
        if not chunk.startswith("event: "):
            continue
        payload = json.loads(chunk.split("data: ", 1)[1])
        events.append(payload)
        if payload["type"] == "run_finished" and payload["kind"] == "planner":
            break
    stop.set()
    await driver
    await stream.aclose()

    types = [(e["kind"], e["type"]) for e in events]
    assert types[:4] == [
        ("vision", "run_started"),
        ("vision", "vision_attempt"),
        ("vision", "vision_attempt"),
        ("vision", "run_finished"),
    ]
    attempts = [e for e in events if e["type"] == "vision_attempt"]
    assert attempts[0]["ok"] is False and "missing from stock_outs" in str(attempts[0]["error"])
    assert attempts[1]["ok"] is True and attempts[1]["stock_outs"] == 1
    planner = [e for e in events if e["kind"] == "planner"]
    assert [e["type"] for e in planner] == [
        "run_started",
        "model_turn",
        "tool_result",
        "model_turn",
        "run_finished",
    ]
    turn = next(e for e in planner if e["type"] == "model_turn")
    assert turn["tool_calls"] == ["get_inventory"]
    assert "[phone]" in str(turn["text"]) and "0803" not in str(turn["text"])
    tool = next(e for e in planner if e["type"] == "tool_result")
    assert tool["name"] == "get_inventory" and tool["ok"] is True
    finished = planner[-1]
    assert finished["status"] == "succeeded" and finished["error"] is None
