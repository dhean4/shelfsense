"""Runtime configuration, read once from the environment.

Every setting is prefixed ``SHELFSENSE_`` so it cannot collide with the Compose or
Langfuse variables that share the same ``.env`` file. Model IDs are added here in P2 and
P3; they are never hard-coded next to the prompts.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AuthMode = Literal["jwks", "dev"]


class Settings(BaseSettings):
    """Process-wide settings. Construct via :func:`get_settings`, not directly."""

    model_config = SettingsConfigDict(env_prefix="SHELFSENSE_", extra="ignore")

    env: Literal["development", "test", "production"] = "development"

    # --- data stores ---------------------------------------------------------------
    database_url: str = Field(
        default="postgresql://shelfsense_app:shelfsense_app@localhost:5433/shelfsense",
        description=(
            "DSN the API uses at runtime. Must be a non-superuser role so row-level "
            "security applies; see ADR-0002."
        ),
    )
    migration_database_url: str = Field(
        default="postgresql://shelfsense:shelfsense@localhost:5433/shelfsense",
        description="DSN for Alembic and the seed script: the schema owner, bypasses RLS.",
    )
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"
    mqtt_url: str = "mqtt://localhost:1883"
    readiness_timeout_seconds: float = Field(
        default=2.0, gt=0, description="Per-dependency timeout for /readyz probes."
    )

    # --- auth ----------------------------------------------------------------------
    auth_mode: AuthMode = Field(
        default="dev",
        description=(
            "'jwks' verifies bearer JWTs against jwks_url (Clerk). 'dev' trusts "
            "X-Dev-* headers and is refused in production."
        ),
    )
    jwks_url: str | None = Field(
        default=None, description="e.g. https://<clerk-domain>/.well-known/jwks.json"
    )
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_algorithms: list[str] = ["RS256"]

    @model_validator(mode="after")
    def _auth_mode_is_safe(self) -> Self:
        if self.env == "production" and self.auth_mode != "jwks":
            msg = "SHELFSENSE_AUTH_MODE must be 'jwks' when SHELFSENSE_ENV=production"
            raise ValueError(msg)
        if self.auth_mode == "jwks" and not self.jwks_url:
            msg = "SHELFSENSE_JWKS_URL is required when SHELFSENSE_AUTH_MODE=jwks"
            raise ValueError(msg)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance.

    Tests call ``get_settings.cache_clear()`` after changing the environment.
    """
    return Settings()
