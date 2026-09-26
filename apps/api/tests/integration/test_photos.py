"""Upload → queue → worker → extraction, end to end with a scripted model."""

import io
import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image

from shelfsense_api.config import Settings, get_settings
from shelfsense_api.jobs import get_queue
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.models import Role
from shelfsense_api.seed import stable_id
from shelfsense_api.storage import PhotoStore

from .conftest import dev_headers

pytestmark = pytest.mark.integration

A = "lagos-fresh"
B = "surulere-chill"
PHOTOS = Path(__file__).resolve().parent.parent / "fixtures" / "photos"
DAIRY_SHELF = stable_id("shelf", f"{A}:Ikeja Depot Shop:Dairy Chiller")


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (640, 400), (200, 200, 200)).save(out, format="PNG")
    return out.getvalue()


def _extraction_json(sku_ids: list[str], *, stock_out_index: int = 0) -> str:
    items = [
        {
            "sku_id": sku,
            "label": f"sku {i}",
            "facings": 3,
            "region": {"x": i * 0.1, "y": 0.1, "w": 0.1, "h": 0.4},
            "confidence": 0.9,
        }
        for i, sku in enumerate(sku_ids)
        if i != stock_out_index
    ]
    n = len(items) or 1
    return json.dumps(
        {
            "items": items,
            "stock_outs": [sku_ids[stock_out_index]],
            "share_of_shelf": [{"sku_id": i["sku_id"], "percent": 100 / n} for i in items],
            "overall_confidence": 0.85,
            "notes": "scripted",
        }
    )


async def _planogram_sku_ids(client: AsyncClient) -> list[str]:
    response = await client.get(
        f"/v1/shelves/{DAIRY_SHELF}/planogram", headers=dev_headers(A, Role.reviewer)
    )
    return [slot["sku_id"] for slot in response.json()["slots"]]


async def test_upload_process_and_read_back(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _planogram_sku_ids(client)
    fake_llm.push_text(_extraction_json(sku_ids), model="fake-vision")

    upload = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("shelf.png", _png(), "image/png")},
        headers=dev_headers(A, Role.field_agent, user="agent_ada"),
    )
    assert upload.status_code == 202, upload.text
    body = upload.json()
    assert body["status"] == "queued"
    assert body["uploaded_by"] == "agent_ada"
    assert (body["width"], body["height"]) == (640, 400)
    assert body["download_url"].startswith(bucket.s3_endpoint_url)
    assert body["extraction"] is None
    photo_id = body["id"]

    # The bytes really are in the bucket.
    assert (
        await PhotoStore(bucket).get(f"{stable_id('tenant', A)}/{DAIRY_SHELF}/{photo_id}.png")
        == _png()
    )

    # Run the worker inline.
    queue = get_queue(get_settings())
    assert await queue.process_one() is True

    done = await client.get(f"/v1/photos/{photo_id}", headers=dev_headers(A, Role.reviewer))
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "done", body["error"]
    extraction = body["extraction"]
    assert extraction["model"] == "fake-vision"
    assert extraction["provider"] == "fake"
    assert extraction["attempts"] == 1
    assert extraction["cost_usd"] is None  # fake model is unpriced
    assert extraction["summary"]["stock_out_count"] == 1
    assert extraction["summary"]["slots"][0]["status"] == "out"
    assert extraction["extraction"]["stock_outs"] == [sku_ids[0]]

    # The model saw the planogram and the image.
    request = fake_llm.requests[0]
    assert request.output_schema is not None
    parts = request.messages[0].parts
    assert parts[0].type == "image"
    assert "Dairy Chiller" in parts[1].text  # type: ignore[union-attr]
    assert sku_ids[0] in parts[1].text  # type: ignore[union-attr]

    listed = await client.get(
        f"/v1/shelves/{DAIRY_SHELF}/photos", headers=dev_headers(A, Role.manager)
    )
    assert listed.json()[0]["id"] == photo_id


async def test_invalid_model_output_is_repaired_then_persisted(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    sku_ids = await _planogram_sku_ids(client)
    broken = json.loads(_extraction_json(sku_ids))
    broken["stock_outs"] = []  # inconsistent with the missing item
    fake_llm.push_text(json.dumps(broken))
    fake_llm.push_text(_extraction_json(sku_ids))

    upload = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("shelf.png", _png(), "image/png")},
        headers=dev_headers(A, Role.manager),
    )
    photo_id = upload.json()["id"]
    assert await get_queue(get_settings()).process_one()
    body = (await client.get(f"/v1/photos/{photo_id}", headers=dev_headers(A, Role.manager))).json()
    assert body["status"] == "done"
    assert body["extraction"]["attempts"] == 2


async def test_unfixable_output_marks_photo_failed_with_reason(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    for _ in range(get_settings().vision_max_repairs + 1):
        fake_llm.push_text(
            '{"items": [], "stock_outs": [], "share_of_shelf": [], '
            '"overall_confidence": 0.5, "notes": ""}'
        )
    upload = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("shelf.png", _png(), "image/png")},
        headers=dev_headers(A, Role.owner),
    )
    photo_id = upload.json()["id"]
    assert await get_queue(get_settings()).process_one()
    body = (await client.get(f"/v1/photos/{photo_id}", headers=dev_headers(A, Role.owner))).json()
    assert body["status"] == "failed"
    assert "missing from stock_outs" in body["error"]
    assert body["extraction"] is None


async def test_upload_rejections(client: AsyncClient, bucket: Settings) -> None:
    not_image = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("x.png", b"nope", "image/png")},
        headers=dev_headers(A, Role.field_agent),
    )
    assert not_image.status_code == 415

    reviewer = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("shelf.png", _png(), "image/png")},
        headers=dev_headers(A, Role.reviewer),
    )
    assert reviewer.status_code == 403

    other_tenant = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("shelf.png", _png(), "image/png")},
        headers=dev_headers(B, Role.owner),
    )
    assert other_tenant.status_code == 404


async def test_photos_are_tenant_isolated(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    upload = await client.post(
        f"/v1/shelves/{DAIRY_SHELF}/photos",
        files={"file": ("shelf.png", _png(), "image/png")},
        headers=dev_headers(A, Role.field_agent),
    )
    photo_id = upload.json()["id"]
    hidden = await client.get(f"/v1/photos/{photo_id}", headers=dev_headers(B, Role.owner))
    assert hidden.status_code == 404
