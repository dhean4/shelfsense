#!/usr/bin/env node
/**
 * ShelfSense MCP server.
 *
 * P3 implements the server with @modelcontextprotocol/sdk and exposes `get_inventory`,
 * `create_reorder`, `dispatch_technician`, `notify` and `geocode`, each backed by the API
 * and validated with the zod schemas from @shelfsense/shared. Until then the binary
 * refuses to start rather than pretending to serve tools.
 */
import { pathToFileURL } from "node:url";

import { SHELFSENSE_SHARED_VERSION } from "@shelfsense/shared";

export const MCP_TOOLS_STATUS = "not-implemented-until-P3" as const;

const entrypoint = process.argv[1];
const runAsBinary = entrypoint !== undefined && import.meta.url === pathToFileURL(entrypoint).href;

if (runAsBinary) {
  // TODO(P3): replace with the real MCP stdio server.
  process.stderr.write(
    `shelfsense-mcp: server arrives in P3 (shared contracts v${SHELFSENSE_SHARED_VERSION}).\n`,
  );
  process.exit(2);
}
