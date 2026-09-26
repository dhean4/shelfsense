#!/usr/bin/env node
/**
 * `shelfsense-mcp`: stdio MCP server bridging to a ShelfSense API.
 *
 * Environment:
 *   SHELFSENSE_API_URL      default http://localhost:8000
 *   SHELFSENSE_API_TOKEN    Clerk session JWT (production)
 *   SHELFSENSE_DEV_TENANT / SHELFSENSE_DEV_ROLE / SHELFSENSE_DEV_USER (dev auth mode)
 */
import { pathToFileURL } from "node:url";

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { authFromEnv, createBridgeServer } from "./server.js";

export { authFromEnv, authHeaders, createBridgeServer } from "./server.js";
export type { ApiAuth, BridgeOptions } from "./server.js";

export async function main(env: NodeJS.ProcessEnv = process.env): Promise<void> {
  const server = createBridgeServer({
    apiUrl: env.SHELFSENSE_API_URL ?? "http://localhost:8000",
    auth: authFromEnv(env),
  });
  await server.connect(new StdioServerTransport());
}

const entrypoint = process.argv[1];
if (entrypoint !== undefined && import.meta.url === pathToFileURL(entrypoint).href) {
  main().catch((error: unknown) => {
    process.stderr.write(`shelfsense-mcp: ${String(error)}\n`);
    process.exit(1);
  });
}
