/**
 * ShelfSense MCP server: a thin bridge from the MCP tool protocol to the API's
 * `/v1/tools` endpoints. The API owns the tool contracts, role checks and logging; this
 * server only lists what the API offers and forwards calls with the caller's credentials.
 */
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  type CallToolResult,
  type Tool,
} from "@modelcontextprotocol/sdk/types.js";

export interface ApiAuth {
  /** Clerk session JWT for `Authorization: Bearer`. */
  bearerToken?: string;
  /** Dev-mode identity (SHELFSENSE_AUTH_MODE=dev on the API). */
  devTenant?: string;
  devRole?: string;
  devUser?: string;
}

export interface BridgeOptions {
  apiUrl: string;
  auth: ApiAuth;
  fetchImpl?: typeof fetch;
}

interface ToolSpecOut {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

interface ToolRunOut {
  name: string;
  result: Record<string, unknown> | null;
  error: string | null;
  duration_ms: number;
}

export function authHeaders(auth: ApiAuth): Record<string, string> {
  const headers: Record<string, string> = {};
  if (auth.bearerToken) headers.Authorization = `Bearer ${auth.bearerToken}`;
  if (auth.devTenant) headers["X-Dev-Tenant"] = auth.devTenant;
  if (auth.devRole) headers["X-Dev-Role"] = auth.devRole;
  if (auth.devUser) headers["X-Dev-User"] = auth.devUser;
  return headers;
}

export function authFromEnv(env: NodeJS.ProcessEnv): ApiAuth {
  return {
    ...(env.SHELFSENSE_API_TOKEN ? { bearerToken: env.SHELFSENSE_API_TOKEN } : {}),
    ...(env.SHELFSENSE_DEV_TENANT ? { devTenant: env.SHELFSENSE_DEV_TENANT } : {}),
    ...(env.SHELFSENSE_DEV_ROLE ? { devRole: env.SHELFSENSE_DEV_ROLE } : {}),
    ...(env.SHELFSENSE_DEV_USER ? { devUser: env.SHELFSENSE_DEV_USER } : {}),
  };
}

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`API ${String(response.status)}: ${text.slice(0, 300)}`);
  }
  return JSON.parse(text) as T;
}

// The low-level Server is deliberate: our tools and their JSON Schemas come from the API
// at runtime, which the high-level McpServer (zod shapes at registration time) cannot
// express. This is the "advanced use case" its deprecation notice refers to.
/* eslint-disable @typescript-eslint/no-deprecated */
export function createBridgeServer(options: BridgeOptions): Server {
  const fetchImpl = options.fetchImpl ?? fetch;
  const base = options.apiUrl.replace(/\/$/, "");
  const headers = { ...authHeaders(options.auth), "content-type": "application/json" };

  const server = new Server(
    { name: "shelfsense-mcp", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    const specs = await readJson<ToolSpecOut[]>(await fetchImpl(`${base}/v1/tools`, { headers }));
    const tools: Tool[] = specs.map((s) => ({
      name: s.name,
      description: s.description,
      inputSchema: { type: "object" as const, ...s.input_schema },
    }));
    return { tools };
  });

  server.setRequestHandler(CallToolRequestSchema, async (request): Promise<CallToolResult> => {
    const { name, arguments: args } = request.params;
    const run = await readJson<ToolRunOut>(
      await fetchImpl(`${base}/v1/tools/${encodeURIComponent(name)}`, {
        method: "POST",
        headers,
        body: JSON.stringify(args ?? {}),
      }),
    );
    if (run.error !== null) {
      return { content: [{ type: "text", text: run.error }], isError: true };
    }
    return { content: [{ type: "text", text: JSON.stringify(run.result) }] };
  });

  return server;
}
