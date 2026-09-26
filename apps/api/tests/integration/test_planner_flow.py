"""Photo → extraction → planner → actions, with real tools on the database."""

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
STORE = stable_id("store", A, "Ikeja Depot Shop")
DAIRY_SHELF = stable_id("shelf", f"{A}:Ikeja Depot Shop:Dairy Chiller")


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (320, 200), (180, 180, 180)).save(out, format="PNG")
    return out.getvalue()


def _extraction(sku_ids: list[str], outs: int, confidence: float) -> str:
    items = [
        {
            "sku_id": s,
            "label": "x",
            "facings": 3,
            "region": {"x": 0, "y": 0, "w": 0.1, "h": 0.1},
            "confidence": 0.9,
        }
        for s in sku_ids[outs:]
    ]
    return json.dumps(
        {
            "items": items,
            "stock_outs": sku_ids[:outs],
            "share_of_shelf": [{"sku_id": i["sku_id"], "percent": 100 / len(items)} for i in items],
            "overall_confidence": confidence,
            "notes": "",
        }
    )


async def _sku_ids(client: AsyncClient) -> list[str]:
    r = await client.get(
        f"/v1/shelves/{DAIRY_SHELF}/planogram", headers=dev_headers(A, Role.reviewer)
    )
    return [s["sku_id"] for s in r.json()["slots"]]


async def _run_pipeline(client: AsyncClient, role: Role) -> str:
    upload = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("s.png", _png(), "image/png")},
        headers=dev_headers(A, role),
    )
    assert upload.status_code == 202, upload.text
    queue = get_queue(get_settings())
    assert await queue.process_one()  # vision
    assert await queue.process_one()  # planner (chained)
    return str(upload.json()["id"])


async def test_manager_run_reorders_notifies_and_auto_approves(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    fake_llm.push_text(_extraction(sku_ids, outs=2, confidence=0.9))
    fake_llm.push_tool_calls(
        [("get_inventory", {"store_id": str(STORE), "sku_ids": sku_ids[:2]})], text="Checking stock"
    )
    fake_llm.push_tool_calls(
        [
            (
                "create_reorder",
                {
                    "store_id": str(STORE),
                    "sku_id": sku_ids[0],
                    "quantity": 4,
                    "reason": "stock-out, below reorder point",
                },
            ),
            (
                "notify",
                {
                    "channel": "whatsapp",
                    "recipient_role": "manager",
                    "subject": "2 stock-outs",
                    "body": "Dairy chiller has two stock-outs",
                },
            ),
        ]
    )
    fake_llm.push_tool_calls(
        [
            (
                "submit_decisions",
                {
                    "summary": "One reorder, manager notified.",
                    "escalate": False,
                    "escalation_reason": None,
                },
            )
        ]
    )

    photo_id = await _run_pipeline(client, Role.manager)

    runs = (
        await client.get(
            "/v1/runs", params={"kind": "planner"}, headers=dev_headers(A, Role.manager)
        )
    ).json()
    run = next(r for r in runs if r["photo_id"] == photo_id)
    assert run["status"] == "succeeded", run["error"]
    assert run["trigger_role"] == "manager"
    assert run["summary"]["summary"] == "One reorder, manager notified."
    assert [t["tool_name"] for t in run["tool_calls"]] == [
        "get_inventory",
        "create_reorder",
        "notify",
    ]
    inventory = run["tool_calls"][0]
    assert inventory["error"] is None and len(inventory["result"]["items"]) == 2
    assert inventory["caller_role"] == "manager"

    actions = run["actions"]
    assert len(actions) == 1
    reorder = actions[0]
    assert reorder["kind"] == "reorder"
    assert reorder["status"] == "approved" and reorder["requires_review"] is False
    assert reorder["estimated_cost_kobo"] == 4 * reorder["payload"]["unit_price_kobo"]
    assert reorder["payload"]["sku_id"] == sku_ids[0]

    # The tool result the model saw was JSON with real inventory rows.
    planner_requests = [r for r in fake_llm.requests if r.metadata.get("agent") == "planner"]
    assert '"on_hand"' in planner_requests[1].messages[2].parts[0].content  # type: ignore[union-attr]


async def test_field_agent_run_holds_reorders_for_review(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    fake_llm.push_text(_extraction(sku_ids, outs=1, confidence=0.95))
    fake_llm.push_tool_calls(
        [
            (
                "create_reorder",
                {"store_id": str(STORE), "sku_id": sku_ids[0], "quantity": 2, "reason": "out"},
            )
        ]
    )
    fake_llm.push_tool_calls(
        [
            (
                "submit_decisions",
                {"summary": "reorder proposed", "escalate": False, "escalation_reason": None},
            )
        ]
    )

    await _run_pipeline(client, Role.field_agent)

    pending = (
        await client.get(
            "/v1/actions",
            params={"status": "pending_review"},
            headers=dev_headers(A, Role.reviewer),
        )
    ).json()
    mine = [
        a
        for a in pending
        if a["payload"].get("sku_id") == sku_ids[0] and a["payload"]["quantity"] == 2
    ]
    assert mine, pending
    assert "cannot auto-approve reorder" in mine[0]["review_reason"]
    # And a field agent's run never offered dispatch.
    planner_requests = [r for r in fake_llm.requests if r.metadata.get("agent") == "planner"]
    assert "dispatch_technician" not in {t.name for t in planner_requests[0].tools}


async def test_low_confidence_and_expensive_actions_go_to_review(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    fake_llm.push_text(_extraction(sku_ids, outs=1, confidence=0.4))
    fake_llm.push_tool_calls(
        [
            (
                "create_reorder",
                {
                    "store_id": str(STORE),
                    "sku_id": sku_ids[0],
                    "quantity": 100,
                    "reason": "big order",
                },
            ),
            (
                "dispatch_technician",
                {"store_id": str(STORE), "reason": "chiller looks off", "urgency": "high"},
            ),
        ]
    )
    fake_llm.push_tool_calls(
        [
            (
                "submit_decisions",
                {"summary": "unsure", "escalate": True, "escalation_reason": "photo dark"},
            )
        ]
    )

    await _run_pipeline(client, Role.owner)

    pending = (
        await client.get(
            "/v1/actions", params={"status": "pending_review"}, headers=dev_headers(A, Role.owner)
        )
    ).json()
    kinds = {a["kind"]: a for a in pending if a["confidence"] == 0.4}
    assert set(kinds) == {"reorder", "dispatch", "escalate"}
    assert "confidence 0.40" in kinds["reorder"]["review_reason"]
    assert kinds["escalate"]["rationale"] == "photo dark"


async def test_cost_cap_fails_the_run_and_parks_its_actions(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    fake_llm.push_text(_extraction(sku_ids, outs=1, confidence=0.9))
    fake_llm.push_tool_calls(
        [
            (
                "create_reorder",
                {"store_id": str(STORE), "sku_id": sku_ids[1], "quantity": 3, "reason": "out"},
            )
        ],
        model="claude-opus-5",
        usage=Usage(input_tokens=50_000, output_tokens=1_000),
    )
    fake_llm.push_tool_calls(
        [("get_inventory", {"store_id": str(STORE), "sku_ids": []})],
        model="claude-opus-5",
        usage=Usage(input_tokens=200_000, output_tokens=20_000),
    )

    await _run_pipeline(client, Role.owner)

    runs = (
        await client.get(
            "/v1/runs",
            params={"kind": "planner", "status": "failed"},
            headers=dev_headers(A, Role.owner),
        )
    ).json()
    failed = runs[0]
    assert "cap" in failed["error"]
    assert failed["cost_usd"] is not None and failed["cost_usd"] > 0.5
    assert failed["actions"][0]["status"] == "pending_review"
    assert failed["actions"][0]["review_reason"].startswith("run failed")


async def test_direct_tool_calls_over_http(client: AsyncClient, bucket: Settings) -> None:
    listed = await client.get("/v1/tools", headers=dev_headers(A, Role.reviewer))
    assert [t["name"] for t in listed.json()] == ["get_inventory", "geocode", "submit_decisions"]

    inventory = await client.post(
        "/v1/tools/get_inventory",
        json={"store_id": str(STORE)},
        headers=dev_headers(A, Role.reviewer),
    )
    assert inventory.status_code == 200
    assert inventory.json()["error"] is None
    assert len(inventory.json()["result"]["items"]) == 17  # dairy + drinks planogram SKUs

    geocoded = await client.post(
        "/v1/tools/geocode", json={"query": "ikeja"}, headers=dev_headers(A, Role.reviewer)
    )
    assert geocoded.json()["result"]["latitude"] == 6.6018

    forbidden = await client.post(
        "/v1/tools/create_reorder", json={}, headers=dev_headers(A, Role.reviewer)
    )
    assert forbidden.status_code == 403

    bad_args = await client.post(
        "/v1/tools/get_inventory", json={"store_id": "nope"}, headers=dev_headers(A, Role.manager)
    )
    assert bad_args.status_code == 200 and "invalid arguments" in bad_args.json()["error"]

    other_tenant = await client.post(
        "/v1/tools/get_inventory", json={"store_id": str(STORE)}, headers=dev_headers(B, Role.owner)
    )
    assert "not found" in other_tenant.json()["error"]

    unknown = await client.post("/v1/tools/teleport", json={}, headers=dev_headers(A, Role.owner))
    assert unknown.status_code == 404


async def test_manual_plan_trigger_is_role_gated(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    fake_llm.push_text(_extraction(sku_ids, outs=0, confidence=0.9))
    fake_llm.push_tool_calls(
        [
            (
                "submit_decisions",
                {"summary": "all good", "escalate": False, "escalation_reason": None},
            )
        ]
    )
    photo_id = await _run_pipeline(client, Role.manager)
    extraction_id = (
        await client.get(f"/v1/photos/{photo_id}", headers=dev_headers(A, Role.manager))
    ).json()["extraction"]["id"]

    denied = await client.post(
        f"/v1/extractions/{extraction_id}/plan", headers=dev_headers(A, Role.reviewer)
    )
    assert denied.status_code == 403
    queued = await client.post(
        f"/v1/extractions/{extraction_id}/plan", headers=dev_headers(A, Role.manager)
    )
    assert queued.status_code == 202
    # Idempotent: the succeeded run exists, so the re-run returns without calling the model.
    before = len(fake_llm.requests)
    assert await get_queue(get_settings()).process_one()
    assert len(fake_llm.requests) == before
