/**
 * Shared contracts between apps/web, packages/mcp-tools and (through the OpenAPI spec)
 * apps/api.
 *
 * `generated/api.ts` is produced from apps/api/openapi.json by
 * `pnpm --filter @shelfsense/shared generate`; CI fails when it is stale. Zod schemas for
 * request bodies live here so the web app and MCP tools validate the same shapes the API
 * enforces with Pydantic.
 */
import { z } from "zod";

export { z } from "zod";
export type { components, operations, paths } from "./generated/api.js";

export const SHELFSENSE_SHARED_VERSION = "0.1.0" as const;

export const Role = z.enum(["owner", "manager", "field_agent", "reviewer"]);
export type Role = z.infer<typeof Role>;

export const StoreIn = z.object({
  name: z.string().min(1).max(200),
  address: z.string().min(1).max(500),
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
});
export type StoreIn = z.infer<typeof StoreIn>;

export const ShelfIn = z.object({
  label: z.string().min(1).max(100),
  position: z.number().int().min(0).default(0),
});
export type ShelfIn = z.infer<typeof ShelfIn>;

export const SkuIn = z.object({
  name: z.string().min(1).max(200),
  brand: z.string().min(1).max(100),
  barcode: z
    .string()
    .min(4)
    .max(32)
    .regex(/^[0-9A-Za-z-]+$/),
  category: z.string().min(1).max(64),
  unit_price_kobo: z.number().int().min(0),
});
export type SkuIn = z.infer<typeof SkuIn>;

export const PlanogramSlotIn = z.object({
  sku_id: z.uuid(),
  position: z.number().int().min(1),
  expected_facings: z.number().int().min(1).max(100),
  min_facings: z.number().int().min(0).max(100),
});
export type PlanogramSlotIn = z.infer<typeof PlanogramSlotIn>;

export const PlanogramIn = z.object({
  slots: z.array(PlanogramSlotIn).min(1).max(200),
});
export type PlanogramIn = z.infer<typeof PlanogramIn>;
