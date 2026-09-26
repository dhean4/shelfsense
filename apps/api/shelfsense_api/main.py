"""FastAPI application factory and the ``shelfsense-api`` console entry point."""

import uvicorn
from fastapi import FastAPI

from shelfsense_api import __version__
from shelfsense_api.health import router as health_router


def create_app() -> FastAPI:
    """Build the application. Kept as a factory so tests get a fresh instance."""
    app = FastAPI(
        title="ShelfSense API",
        version=__version__,
        description=(
            "Shelf photos and cold-chain telemetry in, reviewed decisions out. "
            "Domain routes arrive from P1 onwards; the OpenAPI spec is written first."
        ),
    )
    app.include_router(health_router)
    return app


app = create_app()


def run() -> None:
    """Serve with uvicorn. Development convenience; production uses the Dockerfile CMD (P9)."""
    uvicorn.run("shelfsense_api.main:app", host="0.0.0.0", port=8000, reload=True)
