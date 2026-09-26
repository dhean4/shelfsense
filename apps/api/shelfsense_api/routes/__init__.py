"""HTTP routers, one module per resource. ``ALL_ROUTERS`` is what ``create_app`` mounts."""

from fastapi import APIRouter

from shelfsense_api.routes.me import router as me_router
from shelfsense_api.routes.planograms import router as planograms_router
from shelfsense_api.routes.shelves import router as shelves_router
from shelfsense_api.routes.skus import router as skus_router
from shelfsense_api.routes.stores import router as stores_router

ALL_ROUTERS: tuple[APIRouter, ...] = (
    me_router,
    stores_router,
    shelves_router,
    skus_router,
    planograms_router,
)
