"""Load a shelf's planogram as the context the agents and the reviewer need."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shelfsense_api.agents.vision import PlanogramContext, SlotContext
from shelfsense_api.models import Planogram, Shelf, Sku


@dataclass(frozen=True)
class ShelfBundle:
    """The shelf, its store, its planogram and the agent-facing context."""

    shelf: Shelf
    planogram: Planogram
    context: PlanogramContext


async def load_shelf_bundle(session: AsyncSession, shelf_id: UUID) -> ShelfBundle | None:
    """Everything about a shelf, or ``None`` when it has no planogram (or is invisible)."""
    shelf = await session.scalar(
        select(Shelf).where(Shelf.id == shelf_id).options(selectinload(Shelf.store))
    )
    planogram = await session.scalar(
        select(Planogram)
        .where(Planogram.shelf_id == shelf_id)
        .options(selectinload(Planogram.slots))
    )
    if shelf is None or planogram is None or not planogram.slots:
        return None
    skus = {
        s.id: s
        for s in await session.scalars(
            select(Sku).where(Sku.id.in_([slot.sku_id for slot in planogram.slots]))
        )
    }
    context = PlanogramContext(
        store_name=shelf.store.name,
        shelf_label=shelf.label,
        planogram_version=planogram.version,
        slots=tuple(
            SlotContext(
                sku_id=slot.sku_id,
                name=skus[slot.sku_id].name,
                brand=skus[slot.sku_id].brand,
                position=slot.position,
                expected_facings=slot.expected_facings,
                min_facings=slot.min_facings,
            )
            for slot in sorted(planogram.slots, key=lambda s: s.position)
        ),
    )
    return ShelfBundle(shelf=shelf, planogram=planogram, context=context)


def planogram_snapshot(context: PlanogramContext) -> dict[str, Any]:
    """JSON-able copy of a planogram, stored with golden cases so they outlive edits."""
    return {
        "store_name": context.store_name,
        "shelf_label": context.shelf_label,
        "version": context.planogram_version,
        "slots": [
            {
                "sku_id": str(s.sku_id),
                "name": s.name,
                "brand": s.brand,
                "position": s.position,
                "expected_facings": s.expected_facings,
                "min_facings": s.min_facings,
            }
            for s in context.slots
        ],
    }
