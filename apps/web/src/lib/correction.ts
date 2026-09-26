import type { DetectedItem, PlanogramSnapshot, ShelfExtraction } from "./types";

/** Build a corrected extraction from per-slot facings the reviewer typed. */
export function buildCorrection(
  original: ShelfExtraction,
  planogram: PlanogramSnapshot,
  facings: Record<string, number>,
): ShelfExtraction {
  const byId = new Map(
    original.items.filter((i) => i.sku_id).map((i) => [i.sku_id as string, i] as const),
  );
  const items: DetectedItem[] = [];
  for (const slot of planogram.slots) {
    const count = facings[slot.sku_id] ?? 0;
    if (count <= 0) continue;
    const seen = byId.get(slot.sku_id);
    items.push({
      sku_id: slot.sku_id,
      label: seen?.label ?? `${slot.brand} ${slot.name}`,
      facings: count,
      region: seen?.region ?? { x: 0, y: 0, w: 1, h: 1 },
      confidence: 1,
    });
  }
  // Unknown products the model saw are kept: the reviewer cannot re-identify them here.
  for (const item of original.items) if (item.sku_id === null) items.push(item);
  const total = items.reduce((sum, i) => sum + i.facings, 0) || 1;
  return {
    items,
    stock_outs: planogram.slots.filter((s) => (facings[s.sku_id] ?? 0) <= 0).map((s) => s.sku_id),
    share_of_shelf: items
      .filter((i) => i.sku_id)
      .map((i) => ({ sku_id: i.sku_id as string, percent: (100 * i.facings) / total })),
    overall_confidence: 1,
    notes: `Reviewer correction. Model notes: ${original.notes}`.slice(0, 2000),
  };
}
