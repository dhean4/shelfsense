"""Stores and the shelves inside them."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shelfsense_api.deps import CurrentPrincipal, TenantSession, require_role
from shelfsense_api.models import WRITE_ROLES, Shelf, Store
from shelfsense_api.routes._common import READ_ONE_RESPONSES, READ_RESPONSES, WRITE_RESPONSES
from shelfsense_api.schemas import ShelfIn, ShelfOut, StoreIn, StoreOut

router = APIRouter(prefix="/v1/stores", tags=["stores"])
writers = Depends(require_role(*WRITE_ROLES))


async def _store_or_404(session: TenantSession, store_id: UUID) -> Store:
    store = await session.get(Store, store_id)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    return store


@router.get("", response_model=list[StoreOut], responses=READ_RESPONSES)
async def list_stores(session: TenantSession) -> list[StoreOut]:
    """Every store in the caller's tenant, by name."""
    rows = await session.scalars(select(Store).order_by(Store.name))
    return [StoreOut.model_validate(row) for row in rows]


@router.post(
    "",
    response_model=StoreOut,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_RESPONSES,
    dependencies=[writers],
)
async def create_store(
    body: StoreIn, principal: CurrentPrincipal, session: TenantSession
) -> StoreOut:
    """Create a store. Owners and managers only."""
    store = Store(tenant_id=principal.tenant_id, **body.model_dump())
    session.add(store)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "a store with that name exists") from exc
    await session.refresh(store)
    return StoreOut.model_validate(store)


@router.get("/{store_id}", response_model=StoreOut, responses=READ_ONE_RESPONSES)
async def read_store(store_id: UUID, session: TenantSession) -> StoreOut:
    """One store."""
    return StoreOut.model_validate(await _store_or_404(session, store_id))


@router.get("/{store_id}/shelves", response_model=list[ShelfOut], responses=READ_ONE_RESPONSES)
async def list_shelves(store_id: UUID, session: TenantSession) -> list[ShelfOut]:
    """Shelves of a store, by position then label."""
    await _store_or_404(session, store_id)
    rows = await session.scalars(
        select(Shelf).where(Shelf.store_id == store_id).order_by(Shelf.position, Shelf.label)
    )
    return [ShelfOut.model_validate(row) for row in rows]


@router.post(
    "/{store_id}/shelves",
    response_model=ShelfOut,
    status_code=status.HTTP_201_CREATED,
    responses={**WRITE_RESPONSES, **READ_ONE_RESPONSES},
    dependencies=[writers],
)
async def create_shelf(
    store_id: UUID, body: ShelfIn, principal: CurrentPrincipal, session: TenantSession
) -> ShelfOut:
    """Add a shelf to a store. Owners and managers only."""
    await _store_or_404(session, store_id)
    shelf = Shelf(tenant_id=principal.tenant_id, store_id=store_id, **body.model_dump())
    session.add(shelf)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "a shelf with that label exists") from exc
    await session.refresh(shelf)
    return ShelfOut.model_validate(shelf)
