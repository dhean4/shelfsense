"""FastAPI dependencies: settings, the caller, role guards and the tenant-scoped session."""

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from shelfsense_api.auth import (
    JwksSigningKeys,
    Principal,
    SigningKeyProvider,
    principal_from_bearer,
    principal_from_dev_headers,
)
from shelfsense_api.config import Settings, get_settings
from shelfsense_api.db import tenant_session
from shelfsense_api.models import Role

SettingsDep = Annotated[Settings, Depends(get_settings)]

_signing_keys: dict[str, SigningKeyProvider] = {}


def get_signing_keys(settings: SettingsDep) -> SigningKeyProvider | None:
    """One JWKS client per URL for the life of the process. Tests override this."""
    if settings.auth_mode != "jwks" or settings.jwks_url is None:
        return None
    provider = _signing_keys.get(settings.jwks_url)
    if provider is None:
        provider = JwksSigningKeys(settings.jwks_url)
        _signing_keys[settings.jwks_url] = provider
    return provider


async def get_principal(
    request: Request,
    settings: SettingsDep,
    keys: Annotated[SigningKeyProvider | None, Depends(get_signing_keys)],
) -> Principal:
    """Authenticate the request according to the configured mode."""
    if settings.auth_mode == "dev":
        return principal_from_dev_headers(request)
    if keys is None:  # pragma: no cover — Settings validation prevents this
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "JWKS not configured")
    return await principal_from_bearer(request, settings, keys)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_role(*allowed: Role) -> Callable[..., Coroutine[Any, Any, Principal]]:
    """Dependency factory: 403 unless the caller holds one of ``allowed``."""

    async def _guard(principal: CurrentPrincipal) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {principal.role.value!r} may not perform this action",
            )
        return principal

    return _guard


async def get_tenant_session(
    principal: CurrentPrincipal, settings: SettingsDep
) -> AsyncIterator[AsyncSession]:
    """A session whose transaction carries the caller's tenant and role for RLS."""
    async with tenant_session(settings.database_url, principal.tenant_id, principal.role) as s:
        yield s


TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
