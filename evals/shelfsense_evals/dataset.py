"""The golden dataset format: one JSON object per line, validated by these models.

Two kinds of case:

* ``vision``: a photo (path relative to the dataset file) plus the planogram it was taken
  against, and the truth: facings per SKU, stock-outs, unknown products.
* ``planner``: an audit summary plus inventory, and the decisions a competent planner
  should make (which SKUs to reorder, whether to notify, dispatch, escalate).

Cases come from three sources: ``synthetic`` (rendered shelves), ``review`` (promoted from
the human review queue) and ``real`` (photographed shelves labelled by hand).
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from shelfsense_api.agents.vision import ExtractionSummary, PlanogramContext, SlotContext

Source = Literal["synthetic", "review", "real"]


class PlanogramSlotCase(BaseModel):
    """A planogram slot as stored in a case (ids are stable across the dataset)."""

    sku_id: UUID
    name: str
    brand: str
    position: int
    expected_facings: int
    min_facings: int


class PlanogramCase(BaseModel):
    """Planogram snapshot for a case."""

    store_name: str
    shelf_label: str
    version: int = 1
    slots: list[PlanogramSlotCase]

    def context(self) -> PlanogramContext:
        """As the vision agent consumes it."""
        return PlanogramContext(
            store_name=self.store_name,
            shelf_label=self.shelf_label,
            planogram_version=self.version,
            slots=tuple(
                SlotContext(
                    sku_id=s.sku_id,
                    name=s.name,
                    brand=s.brand,
                    position=s.position,
                    expected_facings=s.expected_facings,
                    min_facings=s.min_facings,
                )
                for s in self.slots
            ),
        )


class VisionTruth(BaseModel):
    """What is actually on the shelf."""

    facings: dict[UUID, int] = Field(description="sku_id → facings; 0 means stocked out.")
    unknown_products: list[str] = Field(default_factory=list)

    @property
    def present(self) -> set[UUID]:
        """SKUs with at least one facing."""
        return {sku for sku, n in self.facings.items() if n > 0}

    @property
    def stock_outs(self) -> set[UUID]:
        """SKUs with none."""
        return {sku for sku, n in self.facings.items() if n == 0}


class VisionCase(BaseModel):
    """A photo to audit."""

    id: str
    kind: Literal["vision"] = "vision"
    source: Source
    tags: list[str] = Field(default_factory=list)
    image: str = Field(description="Path relative to the dataset file.")
    planogram: PlanogramCase
    truth: VisionTruth
    notes: str = ""

    @model_validator(mode="after")
    def _truth_covers_planogram(self) -> "VisionCase":
        slot_ids = {s.sku_id for s in self.planogram.slots}
        if set(self.truth.facings) != slot_ids:
            raise ValueError(f"case {self.id}: truth.facings must cover exactly the planogram SKUs")
        return self


class InventoryRow(BaseModel):
    """Stock position the planner would read through ``get_inventory``."""

    sku_id: UUID
    name: str
    on_hand: int
    reorder_point: int
    case_size: int
    unit_price_kobo: int


class PlannerExpected(BaseModel):
    """The decisions a competent planner makes for this case."""

    reorder_skus: list[UUID] = Field(
        default_factory=list, description="Sorted, so the JSON is byte-stable."
    )
    notify: bool = False
    dispatch: bool = False
    escalate: bool = False


class PlannerCase(BaseModel):
    """An audit (and maybe telemetry) to act on."""

    id: str
    kind: Literal["planner"] = "planner"
    source: Source
    tags: list[str] = Field(default_factory=list)
    store_name: str
    shelf_label: str | None
    trigger_role: str = "manager"
    summary: ExtractionSummary | None
    overall_confidence: float | None
    notes: str = ""
    telemetry: str | None = None
    inventory: list[InventoryRow]
    expected: PlannerExpected


Case = Annotated[VisionCase | PlannerCase, Field(discriminator="kind")]


class _CaseAdapter(BaseModel):
    case: Case


def parse_case(line: str) -> VisionCase | PlannerCase:
    """One JSONL line → case."""
    return _CaseAdapter.model_validate({"case": json.loads(line)}).case


def load_dataset(path: Path) -> list[VisionCase | PlannerCase]:
    """Every case in the file, in order. Raises on the first invalid line."""
    cases: list[VisionCase | PlannerCase] = []
    with path.open() as handle:
        for number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                cases.append(parse_case(raw))
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"{path}: duplicate case ids {dupes}")
    return cases


def write_dataset(path: Path, cases: list[VisionCase | PlannerCase]) -> None:
    """Write cases as JSONL (one object per line, stable key order)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for case in cases:
            handle.write(json.dumps(json.loads(case.model_dump_json()), sort_keys=True) + "\n")


def iter_kind(
    cases: list[VisionCase | PlannerCase], kind: str
) -> Iterator[VisionCase | PlannerCase]:
    """Filter by kind."""
    return (c for c in cases if c.kind == kind)
