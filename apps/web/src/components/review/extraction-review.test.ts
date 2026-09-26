import { describe, expect, it } from "vitest";

import type { PlanogramSnapshot, ShelfExtraction } from "@/lib/types";

import { buildCorrection } from "@/lib/correction";

const A = "00000000-0000-0000-0000-000000000001";
const B = "00000000-0000-0000-0000-000000000002";

const planogram: PlanogramSnapshot = {
  store_name: "s",
  shelf_label: "l",
  version: 1,
  slots: [
    { sku_id: A, name: "Peak", brand: "Peak", position: 1, expected_facings: 3, min_facings: 1 },
    { sku_id: B, name: "Milo", brand: "Nestle", position: 2, expected_facings: 3, min_facings: 1 },
  ],
};

const original: ShelfExtraction = {
  items: [
    {
      sku_id: A,
      label: "Peak milk",
      facings: 1,
      region: { x: 0.1, y: 0.2, w: 0.3, h: 0.4 },
      confidence: 0.7,
    },
    {
      sku_id: null,
      label: "Lucozade",
      facings: 2,
      region: { x: 0, y: 0, w: 1, h: 1 },
      confidence: 0.5,
    },
  ],
  stock_outs: [B],
  share_of_shelf: [{ sku_id: A, percent: 33 }],
  overall_confidence: 0.6,
  notes: "dark",
};

describe("buildCorrection", () => {
  it("keeps the model's region for SKUs it saw and derives stock-outs from facings", () => {
    const corrected = buildCorrection(original, planogram, { [A]: 3, [B]: 0 });
    const peak = corrected.items.find((i) => i.sku_id === A);
    expect(peak?.facings).toBe(3);
    expect(peak?.region).toEqual({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 });
    expect(corrected.stock_outs).toEqual([B]);
    expect(corrected.overall_confidence).toBe(1);
    expect(corrected.items.some((i) => i.sku_id === null)).toBe(true);
  });
  it("adds SKUs the model missed with a full-frame region and shares that sum to 100", () => {
    const corrected = buildCorrection(original, planogram, { [A]: 2, [B]: 2 });
    const milo = corrected.items.find((i) => i.sku_id === B);
    expect(milo?.region).toEqual({ x: 0, y: 0, w: 1, h: 1 });
    expect(corrected.stock_outs).toEqual([]);
    const total = corrected.share_of_shelf.reduce((s, x) => s + x.percent, 0);
    expect(total).toBeLessThanOrEqual(100.001);
  });
});
