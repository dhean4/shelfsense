"""Tenant isolation and role gating, through the HTTP surface."""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from shelfsense_api.models import Role
from shelfsense_api.seed import stable_id

from .conftest import dev_headers

pytestmark = pytest.mark.integration

A = "lagos-fresh"
B = "surulere-chill"


async def test_me_returns_the_seeded_tenant(client: AsyncClient) -> None:
    response = await client.get("/v1/me", headers=dev_headers(A, Role.reviewer))
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "reviewer"
    assert body["tenant"]["slug"] == A
    assert body["tenant"]["name"] == "Lagos Fresh Distributors"


async def test_me_for_unknown_tenant_is_403(client: AsyncClient) -> None:
    headers = {**dev_headers(A, Role.owner), "X-Dev-Tenant": str(uuid4())}
    response = await client.get("/v1/me", headers=headers)
    assert response.status_code == 403


async def test_each_tenant_sees_only_its_own_stores(client: AsyncClient) -> None:
    a = await client.get("/v1/stores", headers=dev_headers(A, Role.field_agent))
    b = await client.get("/v1/stores", headers=dev_headers(B, Role.field_agent))
    assert [s["name"] for s in a.json()] == ["Ikeja Depot Shop", "Yaba Market Store"]
    assert [s["name"] for s in b.json()] == ["Surulere Chill Store"]


async def test_reading_another_tenants_store_by_id_is_404(client: AsyncClient) -> None:
    b_store = stable_id("store", B, "Surulere Chill Store")
    response = await client.get(f"/v1/stores/{b_store}", headers=dev_headers(A, Role.owner))
    assert response.status_code == 404


async def test_sku_counts_and_category_filter(client: AsyncClient) -> None:
    a = await client.get("/v1/skus", headers=dev_headers(A, Role.reviewer))
    b = await client.get("/v1/skus", headers=dev_headers(B, Role.reviewer))
    assert len(a.json()) == 25
    assert len(b.json()) == 25
    dairy = await client.get(
        "/v1/skus", params={"category": "dairy"}, headers=dev_headers(A, Role.reviewer)
    )
    assert {s["category"] for s in dairy.json()} == {"dairy"}
    assert len(dairy.json()) == 6


async def test_shelves_and_seeded_planogram(client: AsyncClient) -> None:
    store = stable_id("store", A, "Ikeja Depot Shop")
    shelves = await client.get(
        f"/v1/stores/{store}/shelves", headers=dev_headers(A, Role.field_agent)
    )
    assert [s["label"] for s in shelves.json()] == ["Dairy Chiller", "Drinks Chiller"]
    dairy_shelf = shelves.json()[0]["id"]
    planogram = await client.get(
        f"/v1/shelves/{dairy_shelf}/planogram", headers=dev_headers(A, Role.field_agent)
    )
    assert planogram.status_code == 200
    body = planogram.json()
    assert body["version"] == 1
    assert [slot["position"] for slot in body["slots"]] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("role", [Role.field_agent, Role.reviewer])
async def test_read_only_roles_cannot_write(client: AsyncClient, role: Role) -> None:
    payload = {"name": "New Store", "address": "x", "latitude": 6.5, "longitude": 3.3}
    response = await client.post("/v1/stores", json=payload, headers=dev_headers(A, role))
    assert response.status_code == 403
    assert "may not perform" in response.json()["detail"]


async def test_owner_creates_store_and_conflict_on_duplicate(client: AsyncClient) -> None:
    payload = {
        "name": "Lekki Phase 1 Store",
        "address": "Admiralty Way",
        "latitude": 6.44,
        "longitude": 3.47,
    }
    created = await client.post("/v1/stores", json=payload, headers=dev_headers(A, Role.owner))
    assert created.status_code == 201, created.text
    assert created.json()["name"] == payload["name"]
    duplicate = await client.post("/v1/stores", json=payload, headers=dev_headers(A, Role.manager))
    assert duplicate.status_code == 409
    # Tenant B may use the same name: uniqueness is per tenant.
    other = await client.post("/v1/stores", json=payload, headers=dev_headers(B, Role.owner))
    assert other.status_code == 201


async def test_manager_adds_shelf_and_replaces_planogram(client: AsyncClient) -> None:
    store = stable_id("store", A, "Yaba Market Store")
    shelf = await client.post(
        f"/v1/stores/{store}/shelves",
        json={"label": "Promo End Cap", "position": 9},
        headers=dev_headers(A, Role.manager),
    )
    assert shelf.status_code == 201, shelf.text
    shelf_id = shelf.json()["id"]

    none_yet = await client.get(
        f"/v1/shelves/{shelf_id}/planogram", headers=dev_headers(A, Role.manager)
    )
    assert none_yet.status_code == 404

    skus = (
        await client.get(
            "/v1/skus", params={"category": "drinks"}, headers=dev_headers(A, Role.manager)
        )
    ).json()
    slots = [
        {"sku_id": s["id"], "position": i + 1, "expected_facings": 3, "min_facings": 1}
        for i, s in enumerate(skus[:3])
    ]
    first = await client.put(
        f"/v1/shelves/{shelf_id}/planogram",
        json={"slots": slots},
        headers=dev_headers(A, Role.manager),
    )
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1
    assert len(first.json()["slots"]) == 3

    second = await client.put(
        f"/v1/shelves/{shelf_id}/planogram",
        json={"slots": slots[:2]},
        headers=dev_headers(A, Role.owner),
    )
    assert second.json()["version"] == 2
    assert len(second.json()["slots"]) == 2


async def test_planogram_rejects_other_tenants_skus(client: AsyncClient) -> None:
    """The FK alone would accept it; the route must check visibility under RLS."""
    a_shelf = stable_id("shelf", f"{A}:Ikeja Depot Shop:Dairy Chiller")
    b_sku = stable_id("sku", B, "Gala Sausage Roll")
    slots = [{"sku_id": str(b_sku), "position": 1, "expected_facings": 2, "min_facings": 1}]
    response = await client.put(
        f"/v1/shelves/{a_shelf}/planogram",
        json={"slots": slots},
        headers=dev_headers(A, Role.owner),
    )
    assert response.status_code == 422
    assert "unknown sku ids" in response.json()["detail"]


async def test_planogram_validation_rules(client: AsyncClient) -> None:
    a_shelf = stable_id("shelf", f"{A}:Ikeja Depot Shop:Dairy Chiller")
    sku = stable_id("sku", A, "Peak Evaporated Milk 160g")
    dup = [{"sku_id": str(sku), "position": 1, "expected_facings": 2, "min_facings": 1}] * 2
    response = await client.put(
        f"/v1/shelves/{a_shelf}/planogram", json={"slots": dup}, headers=dev_headers(A, Role.owner)
    )
    assert response.status_code == 422
    assert "duplicate" in response.json()["detail"]
    bad_min = [{"sku_id": str(sku), "position": 1, "expected_facings": 2, "min_facings": 5}]
    response = await client.put(
        f"/v1/shelves/{a_shelf}/planogram",
        json={"slots": bad_min},
        headers=dev_headers(A, Role.owner),
    )
    assert response.status_code == 422
    assert "min_facings" in response.json()["detail"]


async def test_seed_is_idempotent(database: object) -> None:
    from shelfsense_api.seed import seed_database

    from .conftest import Database

    assert isinstance(database, Database)
    summary = await seed_database(database.owner_url)
    assert len(summary) == 2
    assert "2 stores, 4 shelves, 25 skus" in summary[0]
    assert "1 stores, 2 shelves, 25 skus" in summary[1]
