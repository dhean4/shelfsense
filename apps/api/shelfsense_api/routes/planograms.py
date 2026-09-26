"""A shelf's planogram: read it, or replace it wholesale."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shelfsense_api.deps import CurrentPrincipal, TenantSession, require_role
from shelfsense_api.models import WRITE_ROLES, Planogram, PlanogramSlot, Sku
from shelfsense_api.routes._common import READ_ONE_RESPONSES, WRITE_RESPONSES
from shelfsense_api.routes.shelves import shelf_or_404
from shelfsense_api.schemas import PlanogramIn, PlanogramOut

router = APIRouter(prefix="/v1/shelves", tags=["planograms"])
writers = Depends(require_role(*WRITE_ROLES))


async def _load_planogram(session: TenantSession, shelf_id: UUID) -> Planogram | None:
    return await session.scalar(
        select(Planogram)
        .where(Planogram.shelf_id == shelf_id)
        .options(selectinload(Planogram.slots))
    )


@router.get("/{shelf_id}/planogram", response_model=PlanogramOut, responses=READ_ONE_RESPONSES)
async def read_planogram(shelf_id: UUID, session: TenantSession) -> PlanogramOut:
    """The planogram for a shelf. 404 if the shelf has none yet."""
    await shelf_or_404(session, shelf_id)
    planogram = await _load_planogram(session, shelf_id)
    if planogram is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shelf has no planogram")
    return PlanogramOut.model_validate(planogram)


@router.put(
    "/{shelf_id}/planogram",
    response_model=PlanogramOut,
    responses={**WRITE_RESPONSES, **READ_ONE_RESPONSES},
    dependencies=[writers],
)
async def put_planogram(
    shelf_id: UUID, body: PlanogramIn, principal: CurrentPrincipal, session: TenantSession
) -> PlanogramOut:
    """Replace the planogram. Creates it on first write, bumps ``version`` after that."""
    await shelf_or_404(session, shelf_id)

    positions = [slot.position for slot in body.slots]
    if len(set(positions)) != len(positions):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate slot positions")
    for slot in body.slots:
        if slot.min_facings > slot.expected_facings:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"slot {slot.position}: min_facings exceeds expected_facings",
            )

    # A foreign key does not consult RLS, so a caller could otherwise reference another
    # tenant's SKU. Only SKUs the caller can see count.
    wanted = {slot.sku_id for slot in body.slots}
    visible = set(await session.scalars(select(Sku.id).where(Sku.id.in_(wanted))))
    missing = wanted - visible
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unknown sku ids: {', '.join(str(m) for m in sorted(missing))}",
        )

    planogram = await _load_planogram(session, shelf_id)
    if planogram is None:
        planogram = Planogram(tenant_id=principal.tenant_id, shelf_id=shelf_id, version=1)
        session.add(planogram)
    else:
        planogram.version += 1
        planogram.slots.clear()
        await session.flush()

    planogram.slots.extend(
        PlanogramSlot(tenant_id=principal.tenant_id, **slot.model_dump())
        for slot in sorted(body.slots, key=lambda s: s.position)
    )
    await session.flush()
    await session.refresh(planogram, attribute_names=["slots", "version", "updated_at"])
    return PlanogramOut.model_validate(planogram)
