"""Deterministic demo data: 2 tenants, 3 stores, 6 shelves, 40 SKUs, a planogram per shelf.

Runs as the schema owner (bypasses RLS) and is idempotent: every id is a UUIDv5 of a
stable key, and every insert is ``ON CONFLICT DO UPDATE``.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shelfsense_api.db import session_factory
from shelfsense_api.models import Planogram, PlanogramSlot, Role, Shelf, Sku, Store, Tenant, User

NAMESPACE = uuid.UUID("7f2b9a5e-3c41-4d6e-9b8a-5e1c2d3f4a5b")


def stable_id(*parts: str) -> uuid.UUID:
    """UUIDv5 from a stable key, so re-running the seed never duplicates rows."""
    return uuid.uuid5(NAMESPACE, ":".join(parts))


@dataclass(frozen=True)
class SkuSpec:
    """One catalogue entry."""

    name: str
    brand: str
    category: str
    price_kobo: int


# 40 SKUs a Lagos FMCG distributor would actually carry. Prices are indicative naira
# converted to kobo; they only need to be plausible for cost-cap guardrails (P3).
CATALOGUE: tuple[SkuSpec, ...] = (
    SkuSpec("Peak Evaporated Milk 160g", "Peak", "dairy", 45_000),
    SkuSpec("Peak Powdered Milk 400g", "Peak", "dairy", 320_000),
    SkuSpec("Three Crowns Evaporated Milk 160g", "Three Crowns", "dairy", 40_000),
    SkuSpec("Cowbell Milk Sachet 12g", "Cowbell", "dairy", 5_000),
    SkuSpec("Dano Cool Cow Milk 400g", "Dano", "dairy", 300_000),
    SkuSpec("Hollandia Yoghurt 1L", "Hollandia", "dairy", 180_000),
    SkuSpec("Indomie Chicken 70g", "Indomie", "noodles", 25_000),
    SkuSpec("Indomie Onion Chicken 120g", "Indomie", "noodles", 40_000),
    SkuSpec("Golden Penny Spaghetti 500g", "Golden Penny", "pasta", 95_000),
    SkuSpec("Dangote Spaghetti 500g", "Dangote", "pasta", 90_000),
    SkuSpec("Golden Penny Semovita 1kg", "Golden Penny", "staples", 140_000),
    SkuSpec("Milo 400g", "Nestle", "beverages", 380_000),
    SkuSpec("Bournvita 500g", "Cadbury", "beverages", 350_000),
    SkuSpec("Lipton Yellow Label 50 bags", "Lipton", "beverages", 120_000),
    SkuSpec("Coca-Cola 50cl PET", "Coca-Cola", "drinks", 30_000),
    SkuSpec("Pepsi 50cl PET", "Pepsi", "drinks", 30_000),
    SkuSpec("Fanta Orange 50cl PET", "Coca-Cola", "drinks", 30_000),
    SkuSpec("Sprite 50cl PET", "Coca-Cola", "drinks", 30_000),
    SkuSpec("Bigi Cola 60cl", "Bigi", "drinks", 25_000),
    SkuSpec("Eva Water 75cl", "Eva", "drinks", 20_000),
    SkuSpec("Ragolis Water 75cl", "Ragolis", "drinks", 20_000),
    SkuSpec("Chivita Orange 1L", "Chivita", "drinks", 150_000),
    SkuSpec("Five Alive Citrus 1L", "Coca-Cola", "drinks", 140_000),
    SkuSpec("Maltina 33cl Can", "Nigerian Breweries", "drinks", 45_000),
    SkuSpec("Amstel Malta 33cl Can", "Nigerian Breweries", "drinks", 45_000),
    SkuSpec("Gala Sausage Roll", "UAC Foods", "snacks", 25_000),
    SkuSpec("Beloxxi Cream Crackers", "Beloxxi", "snacks", 15_000),
    SkuSpec("McVitie's Digestive 250g", "McVitie's", "snacks", 120_000),
    SkuSpec("Cabin Biscuit 200g", "Cabin", "snacks", 60_000),
    SkuSpec("Sunlight Detergent 900g", "Unilever", "household", 160_000),
    SkuSpec("Omo Detergent 1kg", "Unilever", "household", 175_000),
    SkuSpec("Dettol Soap 110g", "Reckitt", "personal_care", 55_000),
    SkuSpec("Lux Soap 80g", "Unilever", "personal_care", 40_000),
    SkuSpec("Close Up Toothpaste 140g", "Unilever", "personal_care", 95_000),
    SkuSpec("Titus Sardines 125g", "Titus", "canned", 85_000),
    SkuSpec("Geisha Mackerel 155g", "Geisha", "canned", 95_000),
    SkuSpec("Knorr Cubes 8 pack", "Unilever", "seasoning", 20_000),
    SkuSpec("Maggi Cubes 100 pack", "Nestle", "seasoning", 180_000),
    SkuSpec("Power Oil 1L", "Power Oil", "oils", 210_000),
    SkuSpec("Kings Vegetable Oil 1L", "Kings", "oils", 220_000),
)

# Which shelf holds which categories. Facings scale with the shelf's role.
SHELF_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Dairy Chiller": ("dairy",),
    "Drinks Chiller": ("drinks",),
    "Dry Goods": ("noodles", "pasta", "staples", "seasoning", "oils", "canned"),
    "Beverages & Snacks": ("beverages", "snacks"),
    "Household": ("household", "personal_care"),
    "Chiller": ("dairy", "drinks"),
}


@dataclass(frozen=True)
class TenantSpec:
    """A tenant and everything under it."""

    slug: str
    name: str
    external_org_id: str | None
    stores: tuple[tuple[str, str, float, float, tuple[str, ...]], ...]
    sku_range: tuple[int, int]


TENANTS: tuple[TenantSpec, ...] = (
    TenantSpec(
        slug="lagos-fresh",
        name="Lagos Fresh Distributors",
        external_org_id=None,
        stores=(
            (
                "Ikeja Depot Shop",
                "12 Obafemi Awolowo Way, Ikeja",
                6.6018,
                3.3515,
                ("Dairy Chiller", "Drinks Chiller"),
            ),
            (
                "Yaba Market Store",
                "Tejuosho Market, Yaba",
                6.5158,
                3.3796,
                ("Dry Goods", "Household"),
            ),
        ),
        sku_range=(0, 25),
    ),
    TenantSpec(
        slug="surulere-chill",
        name="Surulere Chill Mart",
        external_org_id=None,
        stores=(
            (
                "Surulere Chill Store",
                "Adeniran Ogunsanya St, Surulere",
                6.4969,
                3.3546,
                ("Chiller", "Beverages & Snacks"),
            ),
        ),
        sku_range=(15, 40),
    ),
)


def _barcode(index: int) -> str:
    return f"615{index:010d}"


async def _upsert_tenant(session: AsyncSession, spec: TenantSpec) -> uuid.UUID:
    tenant_id = stable_id("tenant", spec.slug)
    stmt = insert(Tenant).values(
        id=tenant_id, slug=spec.slug, name=spec.name, external_org_id=spec.external_org_id
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Tenant.id],
            set_={"name": stmt.excluded.name, "external_org_id": stmt.excluded.external_org_id},
        )
    )
    for role in Role:
        external_id = f"dev_{spec.slug}_{role.value}"
        user_stmt = insert(User).values(
            id=stable_id("user", spec.slug, role.value),
            tenant_id=tenant_id,
            external_id=external_id,
            email=f"{role.value}@{spec.slug}.example",
            name=f"{role.value.replace('_', ' ').title()} ({spec.name})",
            role=role,
        )
        await session.execute(
            user_stmt.on_conflict_do_update(
                index_elements=[User.id], set_={"role": user_stmt.excluded.role}
            )
        )
    return tenant_id


async def _upsert_skus(
    session: AsyncSession, spec: TenantSpec, tenant_id: uuid.UUID
) -> dict[str, list[uuid.UUID]]:
    by_category: dict[str, list[uuid.UUID]] = {}
    start, end = spec.sku_range
    for index in range(start, end):
        item = CATALOGUE[index]
        sku_id = stable_id("sku", spec.slug, item.name)
        stmt = insert(Sku).values(
            id=sku_id,
            tenant_id=tenant_id,
            name=item.name,
            brand=item.brand,
            barcode=_barcode(index + 1),
            category=item.category,
            unit_price_kobo=item.price_kobo,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Sku.id],
                set_={
                    "name": stmt.excluded.name,
                    "brand": stmt.excluded.brand,
                    "category": stmt.excluded.category,
                    "unit_price_kobo": stmt.excluded.unit_price_kobo,
                },
            )
        )
        by_category.setdefault(item.category, []).append(sku_id)
    return by_category


async def _upsert_planogram(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    shelf_key: str,
    shelf_id: uuid.UUID,
    sku_ids: list[uuid.UUID],
) -> int:
    planogram_id = stable_id("planogram", shelf_key)
    stmt = insert(Planogram).values(
        id=planogram_id, tenant_id=tenant_id, shelf_id=shelf_id, version=1
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Planogram.id], set_={"version": stmt.excluded.version}
        )
    )
    # Replace slots wholesale so the seed converges on the same planogram every run.
    await session.execute(delete(PlanogramSlot).where(PlanogramSlot.planogram_id == planogram_id))
    for position, sku_id in enumerate(sku_ids, start=1):
        expected = 3 if position % 3 else 4
        await session.execute(
            insert(PlanogramSlot).values(
                id=stable_id("slot", shelf_key, str(position)),
                tenant_id=tenant_id,
                planogram_id=planogram_id,
                sku_id=sku_id,
                position=position,
                expected_facings=expected,
                min_facings=1,
            )
        )
    return len(sku_ids)


async def seed_database(dsn: str) -> list[str]:
    """Load everything. Returns human-readable summary lines including tenant ids."""
    summary: list[str] = []
    async with session_factory(dsn)() as session, session.begin():
        for spec in TENANTS:
            tenant_id = await _upsert_tenant(session, spec)
            skus = await _upsert_skus(session, spec, tenant_id)
            shelf_count = 0
            slot_count = 0
            for name, address, lat, lng, shelf_labels in spec.stores:
                store_id = stable_id("store", spec.slug, name)
                store_stmt = insert(Store).values(
                    id=store_id,
                    tenant_id=tenant_id,
                    name=name,
                    address=address,
                    latitude=lat,
                    longitude=lng,
                )
                await session.execute(
                    store_stmt.on_conflict_do_update(
                        index_elements=[Store.id],
                        set_={"address": store_stmt.excluded.address},
                    )
                )
                for position, label in enumerate(shelf_labels):
                    shelf_key = f"{spec.slug}:{name}:{label}"
                    shelf_id = stable_id("shelf", shelf_key)
                    shelf_stmt = insert(Shelf).values(
                        id=shelf_id,
                        tenant_id=tenant_id,
                        store_id=store_id,
                        label=label,
                        position=position,
                    )
                    await session.execute(
                        shelf_stmt.on_conflict_do_update(
                            index_elements=[Shelf.id],
                            set_={"position": shelf_stmt.excluded.position},
                        )
                    )
                    sku_ids = [
                        sku_id
                        for category in SHELF_CATEGORIES[label]
                        for sku_id in skus.get(category, [])
                    ]
                    slot_count += await _upsert_planogram(
                        session, tenant_id, shelf_key, shelf_id, sku_ids
                    )
                    shelf_count += 1
            sku_total = sum(len(v) for v in skus.values())
            summary.append(
                f"{spec.name} ({spec.slug}) tenant_id={tenant_id}: "
                f"{len(spec.stores)} stores, {shelf_count} shelves, "
                f"{sku_total} skus, {slot_count} slots"
            )
    return summary
