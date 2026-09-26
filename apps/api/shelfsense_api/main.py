"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shelfsense_api import __version__
from shelfsense_api.config import get_settings
from shelfsense_api.db import dispose_engines
from shelfsense_api.health import check_rls_enforced
from shelfsense_api.health import router as health_router
from shelfsense_api.routes import ALL_ROUTERS

OPENAPI_TAGS = [
    {"name": "health", "description": "Liveness and readiness probes."},
    {"name": "identity", "description": "Who the caller is."},
    {"name": "stores", "description": "Outlets and their shelves."},
    {"name": "shelves", "description": "Shelves addressed by id."},
    {"name": "skus", "description": "The tenant's product catalogue."},
    {"name": "planograms", "description": "What each shelf should hold."},
    {"name": "photos", "description": "Shelf photo uploads and their extractions."},
    {"name": "tools", "description": "Typed tools, callable directly or by the planner."},
    {"name": "runs", "description": "Agent runs: tokens, cost, tool calls, decisions."},
    {"name": "actions", "description": "What the planner decided; the review queue's rows."},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Refuse to serve with a role that bypasses RLS; release pools on shutdown."""
    settings = get_settings()
    if settings.env != "test":
        # Fail fast: a superuser connection would silently disable tenant isolation.
        await check_rls_enforced(settings)
    yield
    await dispose_engines()


def create_app() -> FastAPI:
    """Build the application. Kept as a factory so tests get a fresh instance."""
    app = FastAPI(
        title="ShelfSense API",
        version=__version__,
        description=(
            "Shelf photos and cold-chain telemetry in, reviewed decisions out. "
            "The contract is written first in apps/api/openapi.yaml; this document is "
            "generated from the implementation and checked against it in CI."
        ),
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.include_router(health_router)
    for router in ALL_ROUTERS:
        app.include_router(router)
    return app


app = create_app()
