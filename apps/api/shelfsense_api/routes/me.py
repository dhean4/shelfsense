"""``GET /v1/me``: the caller's identity and tenant."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from shelfsense_api.deps import CurrentPrincipal, TenantSession
from shelfsense_api.models import Tenant
from shelfsense_api.routes._common import READ_RESPONSES
from shelfsense_api.schemas import MeOut, TenantOut

router = APIRouter(prefix="/v1", tags=["identity"])


@router.get("/me", response_model=MeOut, responses=READ_RESPONSES)
async def read_me(principal: CurrentPrincipal, session: TenantSession) -> MeOut:
    """Return the caller's user id, role and tenant."""
    tenant = await session.scalar(select(Tenant).where(Tenant.id == principal.tenant_id))
    if tenant is None:
        # The tenant in the token does not exist (or RLS hid it). Either way: no access.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant not found")
    return MeOut(
        user_id=principal.user_id, role=principal.role, tenant=TenantOut.model_validate(tenant)
    )
