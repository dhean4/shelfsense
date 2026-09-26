"""Request and response bodies. These are the shapes the OpenAPI contract promises."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shelfsense_api.agents.vision import ExtractionSummary, ShelfExtraction
from shelfsense_api.guardrails import ActionKind
from shelfsense_api.models import ActionStatus, PhotoStatus, ReviewVerdict, Role, RunStatus


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


# --- tools, runs, actions ---------------------------------------------------------------


class ToolSpecOut(BaseModel):
    """A tool the caller may invoke, with its JSON Schema."""

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolRunOut(BaseModel):
    """Result of a direct tool call."""

    name: str
    result: dict[str, Any] | None
    error: str | None
    duration_ms: int


class ToolCallOut(_FromORM):
    """One logged tool call."""

    id: UUID
    seq: int
    tool_name: str
    caller_role: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    duration_ms: int
    created_at: datetime


class ActionOut(_FromORM):
    """A planner decision and its review state."""

    id: UUID
    run_id: UUID | None
    store_id: UUID | None
    kind: ActionKind
    status: ActionStatus
    requires_review: bool
    review_reason: str | None
    payload: dict[str, Any]
    estimated_cost_kobo: int
    confidence: float | None
    rationale: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime


class RunOut(_FromORM):
    """An agent run with its tool calls and actions: the timeline the dashboard shows."""

    id: UUID
    kind: str
    status: RunStatus
    trigger_role: str | None
    photo_id: UUID | None
    extraction_id: UUID | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    latency_ms: int
    attempts: int
    error: str | None
    summary: dict[str, Any] | None
    started_at: datetime
    finished_at: datetime | None
    tool_calls: list[ToolCallOut]
    actions: list[ActionOut]


class RunQueued(BaseModel):
    """Acknowledgement that a planner run was queued."""

    extraction_id: UUID
    queued: bool = True


# --- review queue -----------------------------------------------------------------------


class ActionDecisionIn(BaseModel):
    """Approve or reject an action, optionally editing a reorder's quantity."""

    note: Annotated[str | None, Field(max_length=1000)] = None
    quantity: Annotated[int | None, Field(ge=1, le=10_000)] = Field(
        default=None, description="For reorders: replace the proposed quantity."
    )


class ExtractionCandidateOut(BaseModel):
    """An extraction that needs a human look, with what they need to judge it."""

    extraction_id: UUID
    photo_id: UUID
    shelf_id: UUID
    shelf_label: str
    store_name: str
    confidence: float
    reason: str
    created_at: datetime
    download_url: str
    extraction: ShelfExtraction
    summary: ExtractionSummary
    planogram: dict[str, Any]


class ReviewQueueOut(BaseModel):
    """Everything waiting for a reviewer."""

    actions: list[ActionOut]
    extractions: list[ExtractionCandidateOut]


class ExtractionReviewIn(BaseModel):
    """A reviewer's verdict. ``corrected`` is required when the verdict is ``corrected``."""

    verdict: ReviewVerdict
    corrected: ShelfExtraction | None = None
    note: Annotated[str | None, Field(max_length=2000)] = None


class ExtractionReviewOut(_FromORM):
    """A stored verdict (a labelled example)."""

    id: UUID
    extraction_id: UUID
    photo_id: UUID
    reviewer: str
    verdict: ReviewVerdict
    corrected: dict[str, Any] | None
    note: str | None
    created_at: datetime
    golden_case_id: UUID | None = None


class PromoteIn(BaseModel):
    """Promote a review into the golden set."""

    tags: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=64)]], Field(max_length=20)
    ] = []


class GoldenCaseOut(_FromORM):
    """One eval example."""

    id: UUID
    review_id: UUID | None
    photo_id: UUID | None
    object_key: str
    source: str
    planogram: dict[str, Any]
    expected: dict[str, Any]
    tags: list[str]
    promoted_by: str
    created_at: datetime
