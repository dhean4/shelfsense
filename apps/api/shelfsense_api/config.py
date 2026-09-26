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
    mqtt_url: str = "mqtt://localhost:1883"
    readiness_timeout_seconds: float = Field(
        default=2.0, gt=0, description="Per-dependency timeout for /readyz probes."
    )

    # --- object storage (MinIO locally, any S3 in production) ------------------------
    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "shelfsense"
    s3_secret_key: str = "shelfsense-dev-secret"
    s3_bucket_photos: str = "photos"
    s3_presign_seconds: int = Field(default=900, ge=60, le=86_400)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)

    # --- LLM ------------------------------------------------------------------------
    llm_provider: Literal["anthropic", "replay", "fake"] = Field(
        default="anthropic",
        description=(
            "'anthropic' calls the API; 'replay' serves recorded fixtures and fails on a "
            "miss; 'fake' serves scripted responses (integration tests)."
        ),
    )
    llm_record: bool = Field(
        default=False,
        description="With provider=anthropic, also write every response to llm_fixtures_dir.",
    )
    llm_fixtures_dir: str = "apps/api/tests/fixtures/llm"
    llm_pricing_json: str | None = Field(
        default=None,
        description='Override the price table: {"model": [input, output, cache_read, cache_write]}',
    )
    vision_model: str = "claude-opus-5"
    vision_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    vision_max_tokens: int = Field(default=8_000, ge=256)
    vision_max_repairs: int = Field(default=2, ge=0, le=5)
    vision_max_image_edge: int = Field(default=1568, ge=256)

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
