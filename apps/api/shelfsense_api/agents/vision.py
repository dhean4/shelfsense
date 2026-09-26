"""Vision agent: one shelf photo + its planogram → a validated :class:`ShelfExtraction`.

The model is asked for structured JSON (grammar-constrained by the provider). Everything
the grammar cannot express (SKU ids must belong to the planogram, stock-outs must be
consistent with facings, shares must add up) is validated here, and a failure is sent back
to the model as a repair turn, at most ``vision_max_repairs`` times.
"""

import json
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from shelfsense_api.config import Settings
from shelfsense_api.images import MediaType, downscale, inspect
from shelfsense_api.llm import ImagePart, LLMProvider, LLMRequest, LLMResponse, Message
from shelfsense_api.llm.schema import api_schema

# --- output schema ----------------------------------------------------------------------

Unit = Annotated[float, Field(ge=0, le=1)]


class BBox(BaseModel):
    """Region in normalised image coordinates: origin top-left, values in [0, 1]."""

    x: Unit
    y: Unit
    w: Unit
    h: Unit


class DetectedItem(BaseModel):
    """One product group seen on the shelf."""

    sku_id: UUID | None = Field(
        description="Planogram SKU id this product matches, or null if not in the planogram."
    )
    label: str = Field(description="What is visible, e.g. 'Peak Evaporated Milk 160g'.")
    facings: Annotated[int, Field(ge=0)] = Field(description="Front-facing units visible.")
    region: BBox
    confidence: Unit


class ShareOfShelf(BaseModel):
    """Share of visible facings held by one planogram SKU."""

    sku_id: UUID
    percent: Annotated[float, Field(ge=0, le=100)]


class ShelfExtraction(BaseModel):
    """What the vision agent returns. The contract for evals and the review queue."""

    items: list[DetectedItem]
    stock_outs: list[UUID] = Field(description="Planogram SKU ids with zero facings visible.")
    share_of_shelf: list[ShareOfShelf]
    overall_confidence: Unit
    notes: str = Field(description="Anything a reviewer should know: glare, occlusion, doubts.")


# --- context ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotContext:
    """A planogram slot as the model needs to see it."""

    sku_id: UUID
    name: str
    brand: str
    position: int
    expected_facings: int
    min_facings: int


@dataclass(frozen=True)
class PlanogramContext:
    """Everything about the shelf that is not the photo."""

    store_name: str
    shelf_label: str
    planogram_version: int
    slots: tuple[SlotContext, ...]

    @property
    def sku_ids(self) -> frozenset[UUID]:
        """Ids the model may reference."""
        return frozenset(slot.sku_id for slot in self.slots)


SYSTEM_PROMPT = """You are a retail shelf auditor for FMCG distributors in Lagos.

You receive one photo of a shelf or chiller and the planogram for that shelf: the list of
SKUs that should be on it, with ids. Report exactly what the photo shows.

Rules:
- A "facing" is one unit visible from the front. Count facings, not depth. Count each SKU
  once, merging all its facings into one item.
- Match products to planogram SKUs by name, brand, size and packaging. Use the given sku_id
  only when you are confident it is that SKU. If a product is not in the planogram, report
  it with sku_id null and a descriptive label.
- stock_outs lists every planogram sku_id with zero facings visible. A SKU cannot be both a
  stock-out and an item with facings above zero.
- share_of_shelf gives each planogram SKU's percentage of all visible facings (planogram
  SKUs and unknown products together). Percentages must sum to 100 or less.
- region is the bounding box of the SKU's facings in normalised coordinates (0 to 1, origin
  top-left).
- confidence values are your honest probability that the count and match are right.
  Lower overall_confidence when the photo is blurred, occluded, dark or cut off, and say
  why in notes.
- Never invent products that are not visible. If the photo is not a shelf, return no items,
  list every planogram SKU as a stock-out, and explain in notes with overall_confidence 0.

Return only the JSON object."""


def _context_text(ctx: PlanogramContext) -> str:
    lines = [
        f"Store: {ctx.store_name}",
        f"Shelf: {ctx.shelf_label} (planogram v{ctx.planogram_version})",
        "Planogram slots (position | sku_id | product | expected facings | minimum):",
    ]
    for slot in ctx.slots:
        lines.append(
            f"{slot.position} | {slot.sku_id} | {slot.brand} — {slot.name} | "
            f"{slot.expected_facings} | {slot.min_facings}"
        )
    lines.append("Audit the photo against this planogram.")
    return "\n".join(lines)


# --- validation beyond the schema -------------------------------------------------------


class ExtractionValidationError(ValueError):
    """The JSON parsed but violated a rule the model must fix."""


def validate_extraction(raw: str, ctx: PlanogramContext) -> ShelfExtraction:
    """Parse and check the model's JSON against the planogram. Raise with a fixable message."""
    try:
        extraction = ShelfExtraction.model_validate_json(raw)
    except ValidationError as exc:
        raise ExtractionValidationError(f"schema violation: {exc.errors()[:5]}") from exc

    problems: list[str] = []
    known = ctx.sku_ids
    seen: set[UUID] = set()
    for item in extraction.items:
        if item.sku_id is not None:
            if item.sku_id not in known:
                problems.append(
                    f"item '{item.label}' uses sku_id {item.sku_id} not in the planogram"
                )
            elif item.sku_id in seen:
                problems.append(f"sku_id {item.sku_id} appears in more than one item; merge them")
            seen.add(item.sku_id)
    for sku_id in extraction.stock_outs:
        if sku_id not in known:
            problems.append(f"stock_outs contains {sku_id}, which is not in the planogram")
    facings = {i.sku_id: i.facings for i in extraction.items if i.sku_id is not None}
    for sku_id in extraction.stock_outs:
        if facings.get(sku_id, 0) > 0:
            problems.append(f"{sku_id} is listed as a stock-out but has {facings[sku_id]} facings")
    for sku_id in known - set(extraction.stock_outs):
        if facings.get(sku_id, 0) == 0:
            problems.append(f"{sku_id} has no facings but is missing from stock_outs")
    for share in extraction.share_of_shelf:
        if share.sku_id not in known:
            problems.append(f"share_of_shelf contains unknown sku_id {share.sku_id}")
    total = sum(s.percent for s in extraction.share_of_shelf)
    if total > 100.5:
        problems.append(f"share_of_shelf percentages sum to {total:.1f}, above 100")
    if problems:
        raise ExtractionValidationError("; ".join(problems))
    return extraction


# --- derived summary --------------------------------------------------------------------

SlotStatus = Literal["ok", "low", "out"]


class SlotCompliance(BaseModel):
    """Expected versus observed facings for one planogram slot."""

    sku_id: UUID
    name: str
    position: int
    expected_facings: int
    min_facings: int
    observed_facings: int
    status: SlotStatus


class ExtractionSummary(BaseModel):
    """Numbers the dashboard and planner consume; derived, never model-authored."""

    slots: list[SlotCompliance]
    stock_out_count: int
    stock_out_rate: Annotated[float, Field(ge=0, le=1)]
    compliance_rate: Annotated[float, Field(ge=0, le=1)] = Field(
        description="Share of planogram slots at or above their minimum facings."
    )
    planogram_share_of_shelf: Annotated[float, Field(ge=0, le=100)] = Field(
        description="Percent of visible facings that belong to planogram SKUs."
    )
    unknown_item_count: int


def summarise(extraction: ShelfExtraction, ctx: PlanogramContext) -> ExtractionSummary:
    """Compare the extraction with the planogram."""
    observed = {i.sku_id: i.facings for i in extraction.items if i.sku_id is not None}
    slots: list[SlotCompliance] = []
    for slot in ctx.slots:
        seen = observed.get(slot.sku_id, 0)
        status: SlotStatus = "out" if seen == 0 else ("low" if seen < slot.min_facings else "ok")
        slots.append(
            SlotCompliance(
                sku_id=slot.sku_id,
                name=slot.name,
                position=slot.position,
                expected_facings=slot.expected_facings,
                min_facings=slot.min_facings,
                observed_facings=seen,
                status=status,
            )
        )
    n = len(slots) or 1
    outs = sum(1 for s in slots if s.status == "out")
    return ExtractionSummary(
        slots=slots,
        stock_out_count=outs,
        stock_out_rate=outs / n,
        compliance_rate=sum(1 for s in slots if s.status == "ok") / n,
        planogram_share_of_shelf=min(100.0, sum(s.percent for s in extraction.share_of_shelf)),
        unknown_item_count=sum(1 for i in extraction.items if i.sku_id is None),
    )


# --- the loop ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VisionResult:
    """Outcome of one photo: the extraction, its summary, and what it cost."""

    extraction: ShelfExtraction
    summary: ExtractionSummary
    responses: tuple[LLMResponse, ...]

    @property
    def attempts(self) -> int:
        """Calls made, including repairs."""
        return len(self.responses)


class VisionFailed(RuntimeError):
    """The model could not produce a valid extraction within the repair budget."""

    def __init__(self, message: str, responses: tuple[LLMResponse, ...]) -> None:
        """Keep the responses so the run can still be costed."""
        super().__init__(message)
        self.responses = responses


def prepare_image(data: bytes, settings: Settings) -> tuple[bytes, MediaType]:
    """Send small images untouched (keeps fixtures byte-stable); shrink large ones."""
    info = inspect(data)
    if max(info.width, info.height) <= settings.vision_max_image_edge and len(data) <= 4_000_000:
        return data, info.media_type
    return downscale(data, settings.vision_max_image_edge)


async def run_vision(
    provider: LLMProvider,
    settings: Settings,
    image: bytes,
    ctx: PlanogramContext,
    *,
    trace_tag: str = "",
) -> VisionResult:
    """Run the extraction with the repair loop."""
    data, media_type = prepare_image(image, settings)
    messages = [Message.user(ImagePart(media_type=media_type, data=data), _context_text(ctx))]
    responses: list[LLMResponse] = []
    last_error = ""
    for attempt in range(settings.vision_max_repairs + 1):
        request = LLMRequest(
            model=settings.vision_model,
            system=SYSTEM_PROMPT,
            messages=messages,
            max_tokens=settings.vision_max_tokens,
            effort=settings.vision_effort,
            output_schema=api_schema(ShelfExtraction),
            metadata={"agent": "vision", "attempt": str(attempt), "tag": trace_tag},
        )
        response = await provider.complete(request)
        responses.append(response)
        try:
            extraction = validate_extraction(response.text, ctx)
        except ExtractionValidationError as exc:
            last_error = str(exc)
            messages = [
                *messages,
                Message.assistant(response.text),
                Message.user(
                    "Your previous output failed validation: "
                    f"{last_error}. Return the corrected JSON object only."
                ),
            ]
            continue
        return VisionResult(
            extraction=extraction, summary=summarise(extraction, ctx), responses=tuple(responses)
        )
    raise VisionFailed(f"invalid after {len(responses)} attempts: {last_error}", tuple(responses))


def result_payload(result: VisionResult) -> dict[str, object]:
    """JSON stored in ``extractions.result``."""
    return {
        "extraction": json.loads(result.extraction.model_dump_json()),
        "summary": json.loads(result.summary.model_dump_json()),
    }
