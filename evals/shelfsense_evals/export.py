"""Export promoted golden cases from the database into the dataset format."""

from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from shelfsense_api.config import get_settings
from shelfsense_api.db import SYSTEM_ROLE, tenant_session
from shelfsense_api.models import GoldenCase
from shelfsense_api.storage import PhotoStore
from shelfsense_evals.dataset import (
    PlannerCase,
    PlanogramCase,
    PlanogramSlotCase,
    VisionCase,
    VisionTruth,
    write_dataset,
)


def case_from_golden(golden: GoldenCase, image_rel: str) -> VisionCase:
    """A ``golden_cases`` row → a vision case. Truth comes from the reviewed extraction."""
    planogram = PlanogramCase(
        store_name=str(golden.planogram["store_name"]),
        shelf_label=str(golden.planogram["shelf_label"]),
        version=int(golden.planogram.get("version", 1)),
        slots=[PlanogramSlotCase.model_validate(s) for s in golden.planogram["slots"]],
    )
    extraction = golden.expected["extraction"]
    facings: dict[UUID, int] = {s.sku_id: 0 for s in planogram.slots}
    for item in extraction["items"]:
        if item.get("sku_id"):
            facings[UUID(item["sku_id"])] = facings.get(UUID(item["sku_id"]), 0) + int(
                item["facings"]
            )
    unknown = [str(i["label"]) for i in extraction["items"] if not i.get("sku_id")]
    return VisionCase(
        id=f"review_{str(golden.id)[:8]}",
        source="review",
        tags=sorted(set(golden.tags)),
        image=image_rel,
        planogram=planogram,
        truth=VisionTruth(facings=facings, unknown_products=unknown),
        notes=f"promoted by {golden.promoted_by} on {golden.created_at:%Y-%m-%d}",
    )


async def export_from_database(out: Path, tenant_id: str) -> int:
    """Write every golden case of a tenant to ``out`` and download its photo next to it."""
    settings = get_settings()
    store = PhotoStore(settings)
    photos_dir = out.parent / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    cases: list[VisionCase | PlannerCase] = []
    async with tenant_session(settings.database_url, UUID(tenant_id), SYSTEM_ROLE) as session:
        rows = await session.scalars(select(GoldenCase).order_by(GoldenCase.created_at))
        for golden in rows:
            suffix = Path(golden.object_key).suffix or ".jpg"
            image_path = photos_dir / f"review_{str(golden.id)[:8]}{suffix}"
            image_path.write_bytes(await store.get(golden.object_key))
            cases.append(case_from_golden(golden, str(image_path.relative_to(out.parent))))
    write_dataset(out, cases)
    return len(cases)
