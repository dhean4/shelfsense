/**
 * Shared contracts between apps/web, packages/mcp-tools and (through the OpenAPI spec)
 * apps/api.
 *
 * P0 ships only the package wiring. P1 writes the OpenAPI spec first, then
 * `pnpm --filter @shelfsense/shared generate` produces `src/generated/api.d.ts`, and the
 * zod schemas for request bodies live here so the web app and MCP tools validate the
 * same shapes the API enforces with Pydantic.
 */
export { z } from "zod";

/** Marker export so the package has a non-empty public surface until P1. */
export const SHELFSENSE_SHARED_VERSION = "0.1.0" as const;
