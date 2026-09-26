"""FastAPI application factory."""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from shelfsense_api import __version__
from shelfsense_api.config import get_settings
from shelfsense_api.db import dispose_engines
from shelfsense_api.health import check_rls_enforced
from shelfsense_api.health import router as health_router
from shelfsense_api.observability import HTTP_REQUESTS, configure_tracing, flush
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
    {"name": "review", "description": "Human review: decide actions, label extractions, promote."},
    {"name": "telemetry", "description": "Devices, readings, anomalies and the live stream."},
    {"name": "usage", "description": "Cost, tokens and latency of agent runs over time."},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Refuse to serve with a role that bypasses RLS; release pools and traces on shutdown."""
    settings = get_settings()
    if settings.env != "test":
        # Fail fast: a superuser connection would silently disable tenant isolation.
        await check_rls_enforced(settings)
    yield
    flush()
    await dispose_engines()


async def _http_metrics(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    started = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    template = getattr(route, "path", request.url.path)
    HTTP_REQUESTS.labels(request.method, template, str(response.status_code)).observe(
        time.perf_counter() - started
    )
    return response


def create_app() -> FastAPI:
    """Build the application. Kept as a factory so tests get a fresh instance."""
    settings = get_settings()
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(_http_metrics)
    app.include_router(health_router)
    for router in ALL_ROUTERS:
        app.include_router(router)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        """Prometheus exposition (a plain route: a mount would redirect to a slash)."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if configure_tracing(settings) is not None:
        # HTTP spans join the Langfuse traces; agent and tool spans nest under them.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    return app


app = create_app()
