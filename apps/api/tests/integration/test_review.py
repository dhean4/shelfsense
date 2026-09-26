"""Review queue: decide actions, label extractions, promote to the golden set."""

import io
import json
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image

from shelfsense_api.config import Settings, get_settings
from shelfsense_api.jobs import get_queue
from shelfsense_api.llm.fake import FakeProvider
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
    Image.new("RGB", (320, 200), (120, 120, 120)).save(out, format="PNG")
    return out.getvalue()


def _extraction(sku_ids: list[str], outs: int, confidence: float) -> dict[str, Any]:
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
    return {
        "items": items,
        "stock_outs": sku_ids[:outs],
        "share_of_shelf": [{"sku_id": i["sku_id"], "percent": 100 / len(items)} for i in items],
        "overall_confidence": confidence,
        "notes": "",
    }


async def _sku_ids(client: AsyncClient) -> list[str]:
    r = await client.get(
        f"/v1/shelves/{DAIRY_SHELF}/planogram", headers=dev_headers(A, Role.reviewer)
    )
    return [s["sku_id"] for s in r.json()["slots"]]


async def _pipeline(
    client: AsyncClient, fake_llm: FakeProvider, sku_ids: list[str], confidence: float, role: Role
) -> str:
    fake_llm.push_text(json.dumps(_extraction(sku_ids, outs=1, confidence=confidence)))
    fake_llm.push_tool_calls(
        [
            (
                "create_reorder",
                {"store_id": str(STORE), "sku_id": sku_ids[0], "quantity": 5, "reason": "out"},
            )
        ]
    )
    fake_llm.push_tool_calls(
        [
            (
                "submit_decisions",
                {"summary": "one reorder", "escalate": False, "escalation_reason": None},
            )
        ]
    )
    upload = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("s.png", _png(), "image/png")},
        headers=dev_headers(A, role),
    )
    queue = get_queue(get_settings())
    assert await queue.process_one() and await queue.process_one()
    return str(upload.json()["id"])


async def test_queue_lists_pending_actions_and_low_confidence_extractions(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    photo_id = await _pipeline(client, fake_llm, sku_ids, confidence=0.4, role=Role.field_agent)

    queue = await client.get("/v1/review/queue", headers=dev_headers(A, Role.reviewer))
    assert queue.status_code == 200
    body = queue.json()
    mine = [
        a for a in body["actions"] if a["payload"].get("quantity") == 5 and a["confidence"] == 0.4
    ]
    assert mine and mine[0]["status"] == "pending_review"
    candidate = next(c for c in body["extractions"] if c["photo_id"] == photo_id)
    assert candidate["shelf_label"] == "Dairy Chiller"
    assert candidate["store_name"] == "Ikeja Depot Shop"
    assert "below" in candidate["reason"]
    assert candidate["planogram"]["slots"][0]["sku_id"] == sku_ids[0]
    assert candidate["download_url"].startswith("http")

    # Field agents cannot see the queue; tenant B sees nothing of A's.
    assert (
        await client.get("/v1/review/queue", headers=dev_headers(A, Role.field_agent))
    ).status_code == 403
    other = (await client.get("/v1/review/queue", headers=dev_headers(B, Role.owner))).json()
    assert not [c for c in other["extractions"] if c["photo_id"] == photo_id]


async def test_approve_with_edited_quantity_reprices_and_reject_records_note(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    await _pipeline(client, fake_llm, sku_ids, confidence=0.95, role=Role.field_agent)
    await _pipeline(client, fake_llm, sku_ids, confidence=0.95, role=Role.field_agent)
    pending = (
        await client.get(
            "/v1/actions",
            params={"status": "pending_review"},
            headers=dev_headers(A, Role.reviewer),
        )
    ).json()
    first, second = pending[0], pending[1]

    approved = await client.post(
        f"/v1/actions/{first['id']}/approve",
        json={"quantity": 12, "note": "round up to a case"},
        headers=dev_headers(A, Role.reviewer, user="rev_ada"),
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["payload"]["quantity"] == 12 and body["payload"]["original_quantity"] == 5
    assert body["estimated_cost_kobo"] == 12 * body["payload"]["unit_price_kobo"]
    assert body["reviewed_by"] == "rev_ada" and body["review_note"] == "round up to a case"

    rejected = await client.post(
        f"/v1/actions/{second['id']}/reject",
        json={"note": "shelf refill instead"},
        headers=dev_headers(A, Role.manager),
    )
    assert rejected.json()["status"] == "rejected"

    again = await client.post(
        f"/v1/actions/{first['id']}/approve", json={}, headers=dev_headers(A, Role.reviewer)
    )
    assert again.status_code == 409
    forbidden = await client.post(
        f"/v1/actions/{second['id']}/approve", json={}, headers=dev_headers(A, Role.field_agent)
    )
    assert forbidden.status_code == 403


async def test_review_extraction_validates_corrections_and_promotes(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    photo_id = await _pipeline(client, fake_llm, sku_ids, confidence=0.5, role=Role.manager)
    extraction_id = (
        await client.get(f"/v1/photos/{photo_id}", headers=dev_headers(A, Role.manager))
    ).json()["extraction"]["id"]

    # A correction that references another tenant's SKU is refused like model output.
    bad = _extraction(sku_ids, outs=1, confidence=0.5)
    bad["items"][0]["sku_id"] = str(stable_id("sku", B, "Gala Sausage Roll"))
    refused = await client.post(
        f"/v1/extractions/{extraction_id}/review",
        json={"verdict": "corrected", "corrected": bad},
        headers=dev_headers(A, Role.reviewer),
    )
    assert refused.status_code == 422 and "not in the planogram" in refused.json()["detail"]

    missing = await client.post(
        f"/v1/extractions/{extraction_id}/review",
        json={"verdict": "corrected"},
        headers=dev_headers(A, Role.reviewer),
    )
    assert missing.status_code == 422

    # A real correction: the reviewer saw two stock-outs, not one.
    fixed = _extraction(sku_ids, outs=2, confidence=0.5)
    fixed["overall_confidence"] = 1.0
    review = await client.post(
        f"/v1/extractions/{extraction_id}/review",
        json={"verdict": "corrected", "corrected": fixed, "note": "second slot also empty"},
        headers=dev_headers(A, Role.reviewer, user="rev_ada"),
    )
    assert review.status_code == 201, review.text
    label = review.json()
    assert label["verdict"] == "corrected" and label["reviewer"] == "rev_ada"
    assert label["corrected"]["summary"]["stock_out_count"] == 2
    assert label["golden_case_id"] is None

    # It leaves the queue once reviewed.
    queue = (await client.get("/v1/review/queue", headers=dev_headers(A, Role.reviewer))).json()
    assert not [c for c in queue["extractions"] if c["photo_id"] == photo_id]

    promoted = await client.post(
        f"/v1/labels/{label['id']}/promote",
        json={"tags": ["chiller", "dark"]},
        headers=dev_headers(A, Role.manager, user="mgr_bola"),
    )
    assert promoted.status_code == 201, promoted.text
    case = promoted.json()
    assert case["source"] == "review" and case["promoted_by"] == "mgr_bola"
    assert case["tags"] == ["chiller", "corrected", "dark"]
    assert case["expected"]["summary"]["stock_out_count"] == 2
    assert case["planogram"]["shelf_label"] == "Dairy Chiller"
    assert case["object_key"].endswith(f"{photo_id}.png")

    twice = await client.post(
        f"/v1/labels/{label['id']}/promote", json={}, headers=dev_headers(A, Role.manager)
    )
    assert twice.status_code == 409

    labels = (await client.get("/v1/labels", headers=dev_headers(A, Role.reviewer))).json()
    assert (
        next(item for item in labels if item["id"] == label["id"])["golden_case_id"] == case["id"]
    )
    golden = (await client.get("/v1/golden", headers=dev_headers(A, Role.reviewer))).json()
    assert case["id"] in {g["id"] for g in golden}
    assert not [
        g
        for g in (await client.get("/v1/golden", headers=dev_headers(B, Role.owner))).json()
        if g["id"] == case["id"]
    ]


async def test_correct_and_unusable_verdicts(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _sku_ids(client)
    photo_id = await _pipeline(client, fake_llm, sku_ids, confidence=0.6, role=Role.manager)
    extraction_id = (
        await client.get(f"/v1/photos/{photo_id}", headers=dev_headers(A, Role.manager))
    ).json()["extraction"]["id"]

    correct = await client.post(
        f"/v1/extractions/{extraction_id}/review",
        json={"verdict": "correct"},
        headers=dev_headers(A, Role.owner),
    )
    assert correct.status_code == 201 and correct.json()["corrected"] is None
    promoted = await client.post(
        f"/v1/labels/{correct.json()['id']}/promote", json={}, headers=dev_headers(A, Role.owner)
    )
    assert promoted.status_code == 201
    assert (
        promoted.json()["expected"]["extraction"]["overall_confidence"] == 0.6
    )  # model output as-is
    assert promoted.json()["tags"] == ["correct"]

    unusable = await client.post(
        f"/v1/extractions/{extraction_id}/review",
        json={"verdict": "unusable", "note": "blurred"},
        headers=dev_headers(A, Role.reviewer),
    )
    assert unusable.status_code == 201
    blocked = await client.post(
        f"/v1/labels/{unusable.json()['id']}/promote",
        json={},
        headers=dev_headers(A, Role.reviewer),
    )
    assert blocked.status_code == 422
