"""HTTP routers, one module per resource. ``ALL_ROUTERS`` is what ``create_app`` mounts."""

from fastapi import APIRouter

from shelfsense_api.routes.actions import router as actions_router
from shelfsense_api.routes.me import router as me_router
from shelfsense_api.routes.photos import router as photos_router
from shelfsense_api.routes.planograms import router as planograms_router
from shelfsense_api.routes.review import router as review_router
from shelfsense_api.routes.runs import router as runs_router
from shelfsense_api.routes.shelves import router as shelves_router
from shelfsense_api.routes.skus import router as skus_router
from shelfsense_api.routes.stores import router as stores_router
from shelfsense_api.routes.telemetry import router as telemetry_router
from shelfsense_api.routes.tools import router as tools_router

ALL_ROUTERS: tuple[APIRouter, ...] = (
    me_router,
    stores_router,
    shelves_router,
    skus_router,
    planograms_router,
    photos_router,
    tools_router,
    runs_router,
    actions_router,
    review_router,
    telemetry_router,
)
