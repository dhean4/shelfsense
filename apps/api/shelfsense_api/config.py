"""Runtime configuration, read once from the environment.

Every setting is prefixed ``SHELFSENSE_`` so it cannot collide with the Compose or
Langfuse variables that share the same ``.env`` file. Model IDs will be added here in
P2 and P3; they are never hard-coded next to the prompts.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. Construct via :func:`get_settings`, not directly."""

    model_config = SettingsConfigDict(env_prefix="SHELFSENSE_", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = Field(
        default="postgresql://shelfsense:shelfsense@localhost:5433/shelfsense",
        description="asyncpg-style DSN. Host port 5433 by default; see ADR-0001.",
    )
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"
    mqtt_url: str = "mqtt://localhost:1883"
    readiness_timeout_seconds: float = Field(
        default=2.0, gt=0, description="Per-dependency timeout for /readyz probes."
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance.

    Tests call ``get_settings.cache_clear()`` after changing the environment.
    """
    return Settings()
