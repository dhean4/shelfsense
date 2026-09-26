"""Caller identity: who is asking, for which tenant, with which role.

Two modes, chosen by ``SHELFSENSE_AUTH_MODE``:

* ``jwks`` verifies a bearer JWT against the identity provider's JWKS (Clerk). The
  organisation claim is resolved to a tenant through ``resolve_tenant_by_org``.
* ``dev`` trusts ``X-Dev-Tenant`` / ``X-Dev-Role`` / ``X-Dev-User`` headers. Refused in
  production by :class:`shelfsense_api.config.Settings`.

See docs/decisions/0002-tenant-context-and-auth.md.
"""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import jwt
from fastapi import HTTPException, Request, status
from sqlalchemy import text

from shelfsense_api.config import Settings
from shelfsense_api.db import get_engine
from shelfsense_api.models import Role

# Clerk organisation roles → ShelfSense roles. ``org:admin`` is Clerk's default admin
# role, so a freshly created organisation works without renaming anything.
ORG_ROLE_MAP: dict[str, Role] = {
    "org:admin": Role.owner,
    "org:owner": Role.owner,
    "org:manager": Role.manager,
    "org:field_agent": Role.field_agent,
    "org:reviewer": Role.reviewer,
}


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller. Immutable so it can be shared across dependencies."""

    user_id: str
    tenant_id: UUID
    role: Role


class SigningKeyProvider(Protocol):
    """Anything that can turn a token into the key that verifies it."""

    def signing_key_for(self, token: str) -> Any: ...  # noqa: D102 — protocol


class JwksSigningKeys:
    """Fetches and caches keys from a JWKS URL (PyJWT handles rotation and caching)."""

    def __init__(self, jwks_url: str) -> None:
        """Create a client for ``jwks_url``; keys are fetched lazily on first use."""
        self._client = jwt.PyJWKClient(jwks_url, cache_keys=True)

    def signing_key_for(self, token: str) -> Any:
        """Return the public key whose ``kid`` matches the token header."""
        return self._client.get_signing_key_from_jwt(token).key


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_claims(token: str, settings: Settings, keys: SigningKeyProvider) -> dict[str, Any]:
    """Verify signature, expiry and (when configured) issuer/audience. Raise 401 otherwise."""
    try:
        key = keys.signing_key_for(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=settings.jwt_algorithms,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"verify_aud": settings.jwt_audience is not None},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized(f"invalid token: {exc}") from exc
    return claims


def role_from_claims(claims: dict[str, Any]) -> Role:
    """Map the identity provider's organisation role onto ours, or 403."""
    org_role = claims.get("org_role")
    role = ORG_ROLE_MAP.get(org_role) if isinstance(org_role, str) else None
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"organisation role {org_role!r} is not mapped to a ShelfSense role",
        )
    return role


async def resolve_tenant(settings: Settings, external_org_id: str) -> UUID:
    """Look up the tenant for an identity-provider organisation, or 403 if unknown."""
    async with get_engine(settings.database_url).connect() as conn:
        tenant_id = await conn.scalar(
            text("SELECT resolve_tenant_by_org(:org)"), {"org": external_org_id}
        )
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organisation is not registered as a ShelfSense tenant",
        )
    return UUID(str(tenant_id))


async def principal_from_bearer(
    request: Request, settings: Settings, keys: SigningKeyProvider
) -> Principal:
    """JWKS mode: ``Authorization: Bearer <jwt>`` → :class:`Principal`."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("missing bearer token")
    claims = decode_claims(token, settings, keys)
    subject = claims.get("sub")
    org_id = claims.get("org_id")
    if not isinstance(subject, str) or not isinstance(org_id, str):
        raise _unauthorized("token has no subject or active organisation")
    role = role_from_claims(claims)
    tenant_id = await resolve_tenant(settings, org_id)
    return Principal(user_id=subject, tenant_id=tenant_id, role=role)


def principal_from_dev_headers(request: Request) -> Principal:
    """Dev mode: identity comes from headers. Never enabled in production."""
    tenant = request.headers.get("x-dev-tenant")
    role_name = request.headers.get("x-dev-role")
    user = request.headers.get("x-dev-user", "dev-user")
    if not tenant or not role_name:
        raise _unauthorized("dev auth needs X-Dev-Tenant and X-Dev-Role headers")
    try:
        tenant_id = UUID(tenant)
        role = Role(role_name)
    except ValueError as exc:
        raise _unauthorized(f"bad dev auth header: {exc}") from exc
    return Principal(user_id=user, tenant_id=tenant_id, role=role)
