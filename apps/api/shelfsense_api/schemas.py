"""Request and response bodies. These are the shapes the OpenAPI contract promises."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shelfsense_api.agents.vision import ExtractionSummary, ShelfExtraction
from shelfsense_api.models import PhotoStatus, Role


class ErrorResponse(BaseModel):
    """Body of every 4xx/5xx the API raises deliberately."""

    detail: str


class _FromORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- tenants and identity ---------------------------------------------------------------


class TenantOut(_FromORM):
    """Public view of a tenant."""

    id: UUID
    slug: str
    name: str


class MeOut(BaseModel):
    """Who the caller is, as the API understands it."""

    user_id: str
    role: Role
    tenant: TenantOut


# --- stores -----------------------------------------------------------------------------

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class StoreIn(BaseModel):
    """Create a store."""

    name: Annotated[str, Field(min_length=1, max_length=200)]
    address: Annotated[str, Field(min_length=1, max_length=500)]
    latitude: Latitude
    longitude: Longitude


class StoreOut(_FromORM):
    """A store."""

    id: UUID
    name: str
    address: str
    latitude: float
    longitude: float
    created_at: datetime
    updated_at: datetime


# --- shelves ----------------------------------------------------------------------------


class ShelfIn(BaseModel):
    """Create a shelf inside a store."""

    label: Annotated[str, Field(min_length=1, max_length=100)]
    position: Annotated[int, Field(ge=0)] = 0


class ShelfOut(_FromORM):
    """A shelf."""

    id: UUID
    store_id: UUID
    label: str
    position: int
    created_at: datetime
    updated_at: datetime


# --- skus -------------------------------------------------------------------------------


class SkuIn(BaseModel):
    """Create a SKU."""

    name: Annotated[str, Field(min_length=1, max_length=200)]
    brand: Annotated[str, Field(min_length=1, max_length=100)]
    barcode: Annotated[str, Field(min_length=4, max_length=32, pattern=r"^[0-9A-Za-z\-]+$")]
    category: Annotated[str, Field(min_length=1, max_length=64)]
    unit_price_kobo: Annotated[int, Field(ge=0)]


class SkuOut(_FromORM):
    """A SKU."""

    id: UUID
    name: str
    brand: str
    barcode: str
    category: str
    unit_price_kobo: int
    created_at: datetime
    updated_at: datetime


# --- planograms -------------------------------------------------------------------------


class PlanogramSlotIn(BaseModel):
    """One slot of a planogram being written."""

    sku_id: UUID
    position: Annotated[int, Field(ge=1)]
    expected_facings: Annotated[int, Field(ge=1, le=100)]
    min_facings: Annotated[int, Field(ge=0, le=100)]


class PlanogramIn(BaseModel):
    """Replace a shelf's planogram wholesale."""

    slots: Annotated[list[PlanogramSlotIn], Field(min_length=1, max_length=200)]


class PlanogramSlotOut(_FromORM):
    """One slot of a planogram."""

    id: UUID
    sku_id: UUID
    position: int
    expected_facings: int
    min_facings: int


class PlanogramOut(_FromORM):
    """A shelf's planogram."""

    id: UUID
    shelf_id: UUID
    version: int
    updated_at: datetime
    slots: list[PlanogramSlotOut]


# --- photos and extractions -------------------------------------------------------------


class ExtractionOut(BaseModel):
    """A finished vision run: the model's extraction, the derived summary, and its cost."""

    id: UUID
    run_id: UUID
    model: str
    provider: str
    planogram_version: int
    overall_confidence: float
    extraction: ShelfExtraction
    summary: ExtractionSummary
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: int
    attempts: int
    created_at: datetime


class PhotoOut(BaseModel):
    """A shelf photo and where its processing stands."""

    id: UUID
    shelf_id: UUID
    status: PhotoStatus
    content_type: str
    size_bytes: int
    width: int
    height: int
    uploaded_by: str
    error: str | None
    created_at: datetime
    updated_at: datetime
    download_url: str = Field(description="Time-limited URL for the original image.")
    extraction: ExtractionOut | None
