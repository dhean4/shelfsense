"""Telemetry ingest → anomaly → planner, the live stream, and the MQTT ingester."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from shelfsense_api.config import Settings, get_settings
from shelfsense_api.jobs import get_queue
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.models import Role
from shelfsense_api.mqtt_ingest import Ingester
from shelfsense_api.seed import stable_id
from shelfsense_api.telemetry import event_stream

from .conftest import dev_headers

pytestmark = pytest.mark.integration

A = "lagos-fresh"
B = "surulere-chill"
FRIDGE = "fridge-yaba-market-store"  # a fridge no other test touches
STORE = stable_id("store", A, "Yaba Market Store")


def _readings(
    device: str, temps: list[float], start: datetime, step_minutes: int = 5
) -> list[dict[str, object]]:
    return [
        {
            "device_external_id": device,
            "recorded_at": (start + timedelta(minutes=i * step_minutes)).isoformat(),
            "temperature_c": t,
            "battery_pct": 90,
        }
        for i, t in enumerate(temps)
    ]


async def test_devices_are_seeded_per_store(client: AsyncClient) -> None:
    devices = (await client.get("/v1/devices", headers=dev_headers(A, Role.reviewer))).json()
    ids = {d["external_id"]: d for d in devices}
    assert {"fridge-ikeja-depot-shop", "fridge-yaba-market-store", "van-lagos-fresh-1"} <= set(ids)
    assert ids["fridge-yaba-market-store"]["store_name"] == "Yaba Market Store"
    assert ids["van-lagos-fresh-1"]["store_id"] is None
    other = (await client.get("/v1/devices", headers=dev_headers(B, Role.reviewer))).json()
    assert {d["external_id"] for d in other} == {
        "fridge-surulere-chill-store",
        "van-surulere-chill-1",
    }


async def test_excursion_opens_one_anomaly_triggers_planner_and_resolves(
    client: AsyncClient, bucket: Settings, fake_llm: FakeProvider
) -> None:
    start = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    # 8:00-8:10 fine, 8:15-8:35 hot (20 min above 8°C), 8:40 recovered.
    hot = _readings(FRIDGE, [4.1, 4.3, 4.0, 9.0, 9.8, 10.4, 11.0, 10.2], start)
    fake_llm.push_tool_calls(
        [
            (
                "dispatch_technician",
                {
                    "store_id": str(STORE),
                    "reason": "fridge above 8°C for 20 min",
                    "urgency": "high",
                },
            ),
            (
                "notify",
                {
                    "channel": "whatsapp",
                    "recipient_role": "manager",
                    "subject": "Fridge fault",
                    "body": "Yaba fridge at 11°C",
                },
            ),
        ]
    )
    fake_llm.push_tool_calls(
        [
            (
                "submit_decisions",
                {
                    "summary": "Technician dispatched, manager notified.",
                    "escalate": False,
                    "escalation_reason": None,
                },
            )
        ]
    )

    # Feed readings in two batches to prove the rule is evaluated on the stored series.
    first = await client.post(
        "/v1/telemetry", json={"readings": hot[:5]}, headers=dev_headers(A, Role.manager)
    )
    assert first.status_code == 202, first.text
    assert first.json()["accepted"] == 5 and first.json()["anomalies_opened"] == []

    second = await client.post(
        "/v1/telemetry", json={"readings": hot[5:]}, headers=dev_headers(A, Role.manager)
    )
    body = second.json()
    assert body["accepted"] == 3
    assert len(body["anomalies_opened"]) == 1, body
    anomaly_id = body["anomalies_opened"][0]

    anomalies = (
        await client.get(
            "/v1/anomalies", params={"status": "open"}, headers=dev_headers(A, Role.reviewer)
        )
    ).json()
    mine = next(a for a in anomalies if a["id"] == anomaly_id)
    assert mine["started_at"].startswith("2026-09-26T08:15")
    assert mine["peak_temperature_c"] == 11.0
    assert mine["run_id"] is None

    # The device list shows the open anomaly and the latest reading.
    devices = (await client.get("/v1/devices", headers=dev_headers(A, Role.reviewer))).json()
    fridge = next(d for d in devices if d["external_id"] == FRIDGE)
    assert fridge["open_anomaly"]["id"] == anomaly_id
    assert fridge["latest"]["temperature_c"] == 10.2

    # The planner run was queued and, with the fake model, dispatches a technician.
    assert await get_queue(get_settings()).process_one()
    run_id = next(
        a
        for a in (await client.get("/v1/anomalies", headers=dev_headers(A, Role.owner))).json()
        if a["id"] == anomaly_id
    )["run_id"]
    run = (await client.get(f"/v1/runs/{run_id}", headers=dev_headers(A, Role.owner))).json()
    assert run["status"] == "succeeded", run["error"]
    assert run["trigger_role"] == "system"
    assert [t["tool_name"] for t in run["tool_calls"]] == ["dispatch_technician", "notify"]
    dispatch = run["actions"][0]
    assert dispatch["kind"] == "dispatch" and dispatch["status"] == "pending_review"
    # Cost gating fires first (a call-out costs ₦15,000); role gating would hold it too.
    assert "above limit" in dispatch["review_reason"]
    planner_request = next(r for r in fake_llm.requests if r.metadata.get("agent") == "planner")
    context = planner_request.messages[0].parts[0].text  # type: ignore[union-attr]
    assert "triggered by telemetry" in context and "above 8°C" in context

    # Still hot: no second anomaly. Recovered: resolved with an end time.
    still_hot = await client.post(
        "/v1/telemetry",
        json={"readings": _readings(FRIDGE, [10.9], start + timedelta(minutes=40))},
        headers=dev_headers(A, Role.manager),
    )
    assert still_hot.json()["anomalies_opened"] == []
    cooled = await client.post(
        "/v1/telemetry",
        json={"readings": _readings(FRIDGE, [6.5], start + timedelta(minutes=45))},
        headers=dev_headers(A, Role.manager),
    )
    assert cooled.status_code == 202
    resolved = next(
        a
        for a in (await client.get("/v1/anomalies", headers=dev_headers(A, Role.owner))).json()
        if a["id"] == anomaly_id
    )
    assert resolved["status"] == "resolved" and resolved["ended_at"].startswith("2026-09-26T08:45")

    readings = (
        await client.get(
            f"/v1/devices/{fridge['id']}/telemetry",
            params={"limit": 3},
            headers=dev_headers(A, Role.reviewer),
        )
    ).json()
    assert [r["temperature_c"] for r in readings] == [6.5, 10.9, 10.2]


async def test_ingest_rejections_and_isolation(client: AsyncClient) -> None:
    now = datetime.now(UTC)
    unknown = await client.post(
        "/v1/telemetry",
        json={"readings": _readings("fridge-nope", [4.0], now)},
        headers=dev_headers(A, Role.manager),
    )
    assert unknown.status_code == 202 and unknown.json() == {
        "accepted": 0,
        "unknown_devices": ["fridge-nope"],
        "anomalies_opened": [],
    }
    # Tenant B cannot feed tenant A's fridge: it is simply unknown to B.
    cross = await client.post(
        "/v1/telemetry",
        json={"readings": _readings(FRIDGE, [4.0], now)},
        headers=dev_headers(B, Role.owner),
    )
    assert cross.json()["unknown_devices"] == [FRIDGE]
    denied = await client.post(
        "/v1/telemetry",
        json={"readings": _readings(FRIDGE, [4.0], now)},
        headers=dev_headers(A, Role.field_agent),
    )
    assert denied.status_code == 403
    bad = await client.post(
        "/v1/telemetry",
        json={"readings": [{"device_external_id": FRIDGE, "recorded_at": "yesterday"}]},
        headers=dev_headers(A, Role.manager),
    )
    assert bad.status_code == 422


async def test_register_device(client: AsyncClient) -> None:
    created = await client.post(
        "/v1/devices",
        json={
            "store_id": str(STORE),
            "kind": "fridge",
            "label": "Back-room fridge",
            "external_id": "fridge-yaba-back",
        },
        headers=dev_headers(A, Role.owner),
    )
    assert created.status_code == 201, created.text
    assert created.json()["latest"] is None
    dup = await client.post(
        "/v1/devices",
        json={"kind": "fridge", "label": "x", "external_id": "fridge-yaba-back"},
        headers=dev_headers(A, Role.owner),
    )
    assert dup.status_code == 409
    forbidden = await client.post(
        "/v1/devices",
        json={"kind": "fridge", "label": "x", "external_id": "fridge-z"},
        headers=dev_headers(A, Role.reviewer),
    )
    assert forbidden.status_code == 403


async def test_mqtt_ingester_handles_topics_and_drops_junk(bucket: Settings) -> None:
    ingester = Ingester(get_settings())
    now = datetime.now(UTC).isoformat()
    payload = json.dumps(
        {"recorded_at": now, "temperature_c": 3.9, "latitude": 6.5, "longitude": 3.3}
    ).encode()
    assert await ingester.handle(f"shelfsense/{A}/telemetry/van-lagos-fresh-1", payload) is True
    assert (
        await ingester.handle("shelfsense/no-such-tenant/telemetry/van-lagos-fresh-1", payload)
        is False
    )
    assert await ingester.handle(f"shelfsense/{A}/telemetry/ghost-device", payload) is False
    assert await ingester.handle(f"shelfsense/{A}/telemetry/van-lagos-fresh-1", b"{bad") is False
    assert await ingester.handle("weird/topic", payload) is False
    assert (ingester.handled, ingester.dropped) == (1, 4)


async def test_event_stream_delivers_published_readings(
    client: AsyncClient, bucket: Settings
) -> None:
    settings = get_settings()
    tenant_id = stable_id("tenant", A)
    stop = asyncio.Event()
    stream = event_stream(settings.redis_url, tenant_id, heartbeat_seconds=1, stop=stop)
    hello = await stream.__anext__()
    assert hello.startswith("event: hello")

    async def post_reading() -> None:
        await asyncio.sleep(0.2)
        await client.post(
            "/v1/telemetry",
            json={"readings": _readings("van-lagos-fresh-1", [None], datetime.now(UTC))},  # type: ignore[list-item]
            headers=dev_headers(A, Role.manager),
        )

    poster = asyncio.create_task(post_reading())
    received: list[str] = []
    for _ in range(6):
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=3)
        if chunk.startswith("event: reading"):
            received.append(chunk)
            break
    stop.set()
    await poster
    await stream.aclose()
    assert received, "no reading event arrived on the stream"
    data = json.loads(received[0].split("data: ", 1)[1])
    assert data["device_external_id"] == "van-lagos-fresh-1"
    assert data["type"] == "reading"
