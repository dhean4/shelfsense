"""SKU catalogue."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shelfsense_api.deps import CurrentPrincipal, TenantSession, require_role
from shelfsense_api.models import WRITE_ROLES, Sku
from shelfsense_api.routes._common import READ_RESPONSES, WRITE_RESPONSES
from shelfsense_api.schemas import SkuIn, SkuOut

router = APIRouter(prefix="/v1/skus", tags=["skus"])
writers = Depends(require_role(*WRITE_ROLES))


@router.get("", response_model=list[SkuOut], responses=READ_RESPONSES)
async def list_skus(
    session: TenantSession,
    category: str | None = Query(default=None, max_length=64),
) -> list[SkuOut]:
    """SKUs in the caller's tenant, optionally filtered by category, by name."""
    stmt = select(Sku).order_by(Sku.name)
    if category is not None:
        stmt = stmt.where(Sku.category == category)
    rows = await session.scalars(stmt)
    return [SkuOut.model_validate(row) for row in rows]


@router.post(
    "",
    response_model=SkuOut,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_RESPONSES,
    dependencies=[writers],
)
async def create_sku(body: SkuIn, principal: CurrentPrincipal, session: TenantSession) -> SkuOut:
    """Create a SKU. Owners and managers only."""
    sku = Sku(tenant_id=principal.tenant_id, **body.model_dump())
    session.add(sku)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "a SKU with that barcode exists") from exc
    await session.refresh(sku)
    return SkuOut.model_validate(sku)
