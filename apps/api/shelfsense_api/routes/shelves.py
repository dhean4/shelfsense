"""Shelves addressed by id."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from shelfsense_api.deps import TenantSession
from shelfsense_api.models import Shelf
from shelfsense_api.routes._common import READ_ONE_RESPONSES
from shelfsense_api.schemas import ShelfOut

router = APIRouter(prefix="/v1/shelves", tags=["shelves"])


async def shelf_or_404(session: TenantSession, shelf_id: UUID) -> Shelf:
    """Load a shelf visible to the caller, or 404."""
    shelf = await session.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shelf not found")
    return shelf


@router.get("/{shelf_id}", response_model=ShelfOut, responses=READ_ONE_RESPONSES)
async def read_shelf(shelf_id: UUID, session: TenantSession) -> ShelfOut:
    """One shelf."""
    return ShelfOut.model_validate(await shelf_or_404(session, shelf_id))
