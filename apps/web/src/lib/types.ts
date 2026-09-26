/** Aliases over the generated OpenAPI types so pages import short names. */
import type { components } from "@shelfsense/shared";

export type Schemas = components["schemas"];

export type ActionOut = Schemas["ActionOut"];
export type ActionKind = Schemas["ActionKind"];
export type ActionStatus = Schemas["ActionStatus"];
export type ExtractionCandidateOut = Schemas["ExtractionCandidateOut"];
export type ExtractionReviewOut = Schemas["ExtractionReviewOut"];
export type GoldenCaseOut = Schemas["GoldenCaseOut"];
export type ReviewQueueOut = Schemas["ReviewQueueOut"];
export type ShelfExtraction = Schemas["ShelfExtraction"];
export type DetectedItem = Schemas["DetectedItem"];
export type ExtractionSummary = Schemas["ExtractionSummary"];
export type SlotCompliance = Schemas["SlotCompliance"];
export type RunOut = Schemas["RunOut"];
export type ToolCallOut = Schemas["ToolCallOut"];
export type StoreOut = Schemas["StoreOut"];
export type MeOut = Schemas["MeOut"];
export type Role = Schemas["Role"];
export type PhotoOut = Schemas["PhotoOut"];

export interface PlanogramSnapshotSlot {
  sku_id: string;
  name: string;
  brand: string;
  position: number;
  expected_facings: number;
  min_facings: number;
}

export interface PlanogramSnapshot {
  store_name: string;
  shelf_label: string;
  version: number;
  slots: PlanogramSnapshotSlot[];
}

export const ROLES: Role[] = ["owner", "manager", "field_agent", "reviewer"];

export function naira(kobo: number): string {
  return `₦${(kobo / 100).toLocaleString("en-NG", { maximumFractionDigits: 0 })}`;
}

export function usd(value: number | null | undefined): string {
  return value == null ? "—" : `$${value.toFixed(4)}`;
}
