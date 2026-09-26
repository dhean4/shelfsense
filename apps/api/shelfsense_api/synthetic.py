"""Synthetic shelf photos with known ground truth.

Real photos are the goal (P6 explains how to add them); until then these rendered shelves
let us record genuine model responses for fixtures and score the extractor against a
truth we control. Deterministic: the same scenario always renders the same PNG.
"""

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from PIL import Image, ImageDraw, ImageFont

from shelfsense_api.agents.vision import PlanogramContext, SlotContext
from shelfsense_api.seed import CATALOGUE, SHELF_CATEGORIES, TENANTS, stable_id

WIDTH, HEIGHT = 1280, 800
PALETTE = [
    (198, 40, 40),
    (25, 118, 210),
    (56, 142, 60),
    (245, 124, 0),
    (123, 31, 162),
    (0, 121, 107),
    (93, 64, 55),
    (69, 90, 100),
]


@dataclass(frozen=True)
class Scenario:
    """Which seeded shelf to render and how many facings each SKU gets."""

    name: str
    tenant_slug: str
    store_name: str
    shelf_label: str
    facings: dict[str, int]  # sku name → facings (0 = stock-out)
    unknown_products: tuple[str, ...] = ()
    description: str = ""


def planogram_context(tenant_slug: str, store_name: str, shelf_label: str) -> PlanogramContext:
    """Rebuild the seed's planogram for a shelf without a database."""
    tenant = next(t for t in TENANTS if t.slug == tenant_slug)
    start, end = tenant.sku_range
    catalogue = CATALOGUE[start:end]
    slots: list[SlotContext] = []
    position = 0
    for category in SHELF_CATEGORIES[shelf_label]:
        for item in catalogue:
            if item.category != category:
                continue
            position += 1
            slots.append(
                SlotContext(
                    sku_id=stable_id("sku", tenant_slug, item.name),
                    name=item.name,
                    brand=item.brand,
                    position=position,
                    expected_facings=3 if position % 3 else 4,
                    min_facings=1,
                )
            )
    return PlanogramContext(
        store_name=store_name, shelf_label=shelf_label, planogram_version=1, slots=tuple(slots)
    )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="dairy_full",
        tenant_slug="lagos-fresh",
        store_name="Ikeja Depot Shop",
        shelf_label="Dairy Chiller",
        facings={
            "Peak Evaporated Milk 160g": 3,
            "Peak Powdered Milk 400g": 2,
            "Three Crowns Evaporated Milk 160g": 3,
            "Cowbell Milk Sachet 12g": 4,
            "Dano Cool Cow Milk 400g": 3,
            "Hollandia Yoghurt 1L": 2,
        },
        description="Every planogram SKU present near expected facings.",
    ),
    Scenario(
        name="dairy_two_stockouts",
        tenant_slug="lagos-fresh",
        store_name="Ikeja Depot Shop",
        shelf_label="Dairy Chiller",
        facings={
            "Peak Evaporated Milk 160g": 4,
            "Peak Powdered Milk 400g": 0,
            "Three Crowns Evaporated Milk 160g": 2,
            "Cowbell Milk Sachet 12g": 0,
            "Dano Cool Cow Milk 400g": 1,
            "Hollandia Yoghurt 1L": 3,
        },
        description="Two stock-outs, one SKU below minimum is not (min is 1).",
    ),
    Scenario(
        name="drinks_with_intruder",
        tenant_slug="lagos-fresh",
        store_name="Ikeja Depot Shop",
        shelf_label="Drinks Chiller",
        facings={
            "Coca-Cola 50cl PET": 4,
            "Pepsi 50cl PET": 3,
            "Fanta Orange 50cl PET": 0,
            "Sprite 50cl PET": 2,
            "Bigi Cola 60cl": 3,
            "Eva Water 75cl": 3,
            "Ragolis Water 75cl": 2,
            "Chivita Orange 1L": 0,
            "Five Alive Citrus 1L": 2,
            "Maltina 33cl Can": 3,
            "Amstel Malta 33cl Can": 2,
        },
        unknown_products=("Lucozade Boost 50cl",),
        description="Two stock-outs plus a product that is not in the planogram.",
    ),
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def render(scenario: Scenario) -> tuple[bytes, dict[str, object]]:
    """Draw the shelf. Returns PNG bytes and the ground truth JSON-able dict."""
    ctx = planogram_context(scenario.tenant_slug, scenario.store_name, scenario.shelf_label)
    img = Image.new("RGB", (WIDTH, HEIGHT), (236, 233, 226))
    draw = ImageDraw.Draw(img)
    # Two shelf boards.
    for y in (HEIGHT * 0.48, HEIGHT * 0.93):
        draw.rectangle((0, y, WIDTH, y + 14), fill=(120, 110, 95))
    draw.text(
        (20, 12),
        f"{scenario.store_name} · {scenario.shelf_label}",
        fill=(60, 60, 60),
        font=_font(22),
    )

    products: list[tuple[str, int, tuple[int, int, int]]] = []
    for i, slot in enumerate(ctx.slots):
        count = scenario.facings.get(slot.name, 0)
        if count > 0:
            products.append((f"{slot.brand}\n{slot.name}", count, PALETTE[i % len(PALETTE)]))
    for j, name in enumerate(scenario.unknown_products):
        products.append((f"?\n{name}", 2, (150, 150, 150) if j % 2 else (200, 180, 60)))

    total_facings = sum(c for _, c, _ in products) or 1
    row_capacity = max(1, (total_facings + 1) // 2)
    unit_w = min(110, (WIDTH - 40) // row_capacity)
    truth: dict[str, int] = {}
    x, row = 20, 0
    for label, count, colour in products:
        for _ in range(count):
            if x + unit_w > WIDTH - 20:
                x, row = 20, row + 1
            top = 60 + row * int(HEIGHT * 0.45)
            box = (x, top, x + unit_w - 8, top + int(HEIGHT * 0.36))
            draw.rectangle(box, fill=colour, outline=(30, 30, 30), width=2)
            draw.rectangle((box[0] + 6, box[1] + 6, box[2] - 6, box[1] + 70), fill=(250, 250, 250))
            draw.multiline_text(
                (box[0] + 9, box[1] + 9), label, fill=(20, 20, 20), font=_font(13), spacing=2
            )
            x += unit_w
        head = label.split("\n", 1)[1]
        truth[head] = count

    for slot in ctx.slots:
        truth.setdefault(slot.name, 0)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue(), {
        "scenario": scenario.name,
        "description": scenario.description,
        "tenant_slug": scenario.tenant_slug,
        "store_name": scenario.store_name,
        "shelf_label": scenario.shelf_label,
        "facings": truth,
        "stock_outs": [s.name for s in ctx.slots if truth.get(s.name, 0) == 0],
        "unknown_products": list(scenario.unknown_products),
        "sku_ids": {s.name: str(s.sku_id) for s in ctx.slots},
    }


def write_all(directory: Path) -> list[Path]:
    """Render every scenario into ``directory`` as ``<name>.png`` + ``<name>.json``."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for scenario in SCENARIOS:
        png, truth = render(scenario)
        png_path = directory / f"{scenario.name}.png"
        png_path.write_bytes(png)
        (directory / f"{scenario.name}.json").write_text(json.dumps(truth, indent=2) + "\n")
        written.append(png_path)
    return written


def load_truth(directory: Path, name: str) -> dict[str, Any]:
    """Read a scenario's ground truth."""
    data: dict[str, Any] = json.loads((directory / f"{name}.json").read_text())
    return data


def sku_id_for(truth: dict[str, object], sku_name: str) -> UUID:
    """Ground-truth helper: the seeded id for a SKU name."""
    ids = truth["sku_ids"]
    assert isinstance(ids, dict)
    return UUID(str(ids[sku_name]))
