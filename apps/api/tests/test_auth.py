import time
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from httpx import AsyncClient

from shelfsense_api import auth
from shelfsense_api.config import Settings
from shelfsense_api.models import Role

TENANT = UUID("11111111-1111-1111-1111-111111111111")


class StaticKeys:
    def __init__(self, public_key: Any) -> None:
        self.public_key = public_key

    def signing_key_for(self, token: str) -> Any:
        return self.public_key


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwks_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SHELFSENSE_AUTH_MODE", "jwks")
    monkeypatch.setenv("SHELFSENSE_JWKS_URL", "https://example.test/.well-known/jwks.json")
    monkeypatch.setenv("SHELFSENSE_JWT_ISSUER", "https://example.test")
    return Settings()


@pytest.fixture(autouse=True)
def _resolve_tenant_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(settings: Settings, external_org_id: str) -> UUID:
        if external_org_id == "org_known":
            return TENANT
        raise HTTPException(403, "organisation is not registered as a ShelfSense tenant")

    monkeypatch.setattr(auth, "resolve_tenant", fake_resolve)


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": "/"})


def _token(key: rsa.RSAPrivateKey, **claims: Any) -> str:
    now = int(time.time())
    payload = {
        "sub": "user_1",
        "org_id": "org_known",
        "org_role": "org:admin",
        "iss": "https://example.test",
        "iat": now,
        "exp": now + 60,
    }
    payload.update(claims)
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "k1"})


# --- jwks mode --------------------------------------------------------------------------


async def test_valid_token_yields_principal(
    rsa_key: rsa.RSAPrivateKey, jwks_settings: Settings
) -> None:
    principal = await auth.principal_from_bearer(
        _request({"Authorization": f"Bearer {_token(rsa_key)}"}),
        jwks_settings,
        StaticKeys(rsa_key.public_key()),
    )
    assert principal == auth.Principal(user_id="user_1", tenant_id=TENANT, role=Role.owner)


@pytest.mark.parametrize(
    "org_role, expected",
    [
        ("org:manager", Role.manager),
        ("org:field_agent", Role.field_agent),
        ("org:reviewer", Role.reviewer),
    ],
)
async def test_org_roles_map(
    rsa_key: rsa.RSAPrivateKey, jwks_settings: Settings, org_role: str, expected: Role
) -> None:
    principal = await auth.principal_from_bearer(
        _request({"Authorization": f"Bearer {_token(rsa_key, org_role=org_role)}"}),
        jwks_settings,
        StaticKeys(rsa_key.public_key()),
    )
    assert principal.role is expected


async def test_expired_token_is_401(rsa_key: rsa.RSAPrivateKey, jwks_settings: Settings) -> None:
    token = _token(rsa_key, exp=int(time.time()) - 10)
    with pytest.raises(HTTPException) as exc:
        await auth.principal_from_bearer(
            _request({"Authorization": f"Bearer {token}"}),
            jwks_settings,
            StaticKeys(rsa_key.public_key()),
        )
    assert exc.value.status_code == 401


async def test_wrong_issuer_is_401(rsa_key: rsa.RSAPrivateKey, jwks_settings: Settings) -> None:
    token = _token(rsa_key, iss="https://attacker.test")
    with pytest.raises(HTTPException) as exc:
        await auth.principal_from_bearer(
            _request({"Authorization": f"Bearer {token}"}),
            jwks_settings,
            StaticKeys(rsa_key.public_key()),
        )
    assert exc.value.status_code == 401


async def test_token_signed_by_another_key_is_401(
    rsa_key: rsa.RSAPrivateKey, jwks_settings: Settings
) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as exc:
        await auth.principal_from_bearer(
            _request({"Authorization": f"Bearer {_token(other)}"}),
            jwks_settings,
            StaticKeys(rsa_key.public_key()),
        )
    assert exc.value.status_code == 401


async def test_unmapped_org_role_is_403(
    rsa_key: rsa.RSAPrivateKey, jwks_settings: Settings
) -> None:
    with pytest.raises(HTTPException) as exc:
        await auth.principal_from_bearer(
            _request({"Authorization": f"Bearer {_token(rsa_key, org_role='org:intern')}"}),
            jwks_settings,
            StaticKeys(rsa_key.public_key()),
        )
    assert exc.value.status_code == 403


async def test_unknown_org_is_403(rsa_key: rsa.RSAPrivateKey, jwks_settings: Settings) -> None:
    with pytest.raises(HTTPException) as exc:
        await auth.principal_from_bearer(
            _request({"Authorization": f"Bearer {_token(rsa_key, org_id='org_unknown')}"}),
            jwks_settings,
            StaticKeys(rsa_key.public_key()),
        )
    assert exc.value.status_code == 403


async def test_token_without_active_org_is_401(
    rsa_key: rsa.RSAPrivateKey, jwks_settings: Settings
) -> None:
    now = int(time.time())
    token = jwt.encode(
        {"sub": "user_1", "iss": "https://example.test", "iat": now, "exp": now + 60},
        rsa_key,
        algorithm="RS256",
    )
    with pytest.raises(HTTPException) as exc:
        await auth.principal_from_bearer(
            _request({"Authorization": f"Bearer {token}"}),
            jwks_settings,
            StaticKeys(rsa_key.public_key()),
        )
    assert exc.value.status_code == 401


async def test_missing_header_is_401(jwks_settings: Settings, rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(HTTPException) as exc:
        await auth.principal_from_bearer(
            _request({}), jwks_settings, StaticKeys(rsa_key.public_key())
        )
    assert exc.value.status_code == 401
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}


# --- dev mode ---------------------------------------------------------------------------


def test_dev_headers_yield_principal() -> None:
    tenant = uuid4()
    principal = auth.principal_from_dev_headers(
        _request({"X-Dev-Tenant": str(tenant), "X-Dev-Role": "reviewer", "X-Dev-User": "ada"})
    )
    assert principal == auth.Principal(user_id="ada", tenant_id=tenant, role=Role.reviewer)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Dev-Tenant": "not-a-uuid", "X-Dev-Role": "owner"},
        {"X-Dev-Tenant": str(uuid4()), "X-Dev-Role": "superuser"},
    ],
)
def test_bad_dev_headers_are_401(headers: dict[str, str]) -> None:
    with pytest.raises(HTTPException) as exc:
        auth.principal_from_dev_headers(_request(headers))
    assert exc.value.status_code == 401


async def test_dev_mode_end_to_end_requires_headers(client: AsyncClient) -> None:
    response = await client.get("/v1/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


# --- settings guard rails ---------------------------------------------------------------


def test_production_refuses_dev_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELFSENSE_ENV", "production")
    monkeypatch.setenv("SHELFSENSE_AUTH_MODE", "dev")
    with pytest.raises(ValueError, match="must be 'jwks'"):
        Settings()


def test_jwks_mode_needs_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELFSENSE_AUTH_MODE", "jwks")
    monkeypatch.delenv("SHELFSENSE_JWKS_URL", raising=False)
    with pytest.raises(ValueError, match="JWKS_URL"):
        Settings()
