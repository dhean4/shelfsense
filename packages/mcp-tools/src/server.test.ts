import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, it } from "vitest";

import { authHeaders, createBridgeServer } from "./server.js";

const SPECS = [
  {
    name: "get_inventory",
    description: "Stock levels.",
    input_schema: {
      type: "object",
      properties: { store_id: { type: "string" } },
      required: ["store_id"],
      additionalProperties: false,
    },
  },
];

function urlOf(input: string | URL | Request): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function fakeApi(calls: { url: string; init?: RequestInit }[]): typeof fetch {
  const impl = (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const url = urlOf(input);
    calls.push({ url, ...(init ? { init } : {}) });
    if (url.endsWith("/v1/tools")) {
      return Promise.resolve(new Response(JSON.stringify(SPECS), { status: 200 }));
    }
    if (url.endsWith("/v1/tools/get_inventory")) {
      const raw = typeof init?.body === "string" ? init.body : "{}";
      const body = JSON.parse(raw) as { store_id?: string };
      if (!body.store_id) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              name: "get_inventory",
              result: null,
              error: "invalid arguments",
              duration_ms: 1,
            }),
          ),
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            name: "get_inventory",
            result: { store_id: body.store_id, items: [] },
            error: null,
            duration_ms: 3,
          }),
        ),
      );
    }
    return Promise.resolve(new Response("nope", { status: 404 }));
  };
  return impl;
}

async function connected(calls: { url: string; init?: RequestInit }[]) {
  const server = createBridgeServer({
    apiUrl: "http://api.test/",
    auth: { devTenant: "t-1", devRole: "manager" },
    fetchImpl: fakeApi(calls),
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  const client = new Client({ name: "test", version: "0.0.0" });
  await client.connect(clientTransport);
  return client;
}

describe("shelfsense-mcp bridge", () => {
  it("lists the API's tools as MCP tools with their schemas", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const client = await connected(calls);
    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name)).toEqual(["get_inventory"]);
    expect(tools[0]?.inputSchema.required).toEqual(["store_id"]);
    expect(calls[0]?.url).toBe("http://api.test/v1/tools");
    const headers = calls[0]?.init?.headers as Record<string, string>;
    expect(headers["X-Dev-Tenant"]).toBe("t-1");
    expect(headers["X-Dev-Role"]).toBe("manager");
  });

  it("forwards a call and returns the API's result", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const client = await connected(calls);
    const result = await client.callTool({ name: "get_inventory", arguments: { store_id: "s-1" } });
    expect(result.isError).toBeFalsy();
    const content = result.content as { type: string; text: string }[];
    expect(JSON.parse(content[0]?.text ?? "{}")).toEqual({ store_id: "s-1", items: [] });
    const post = calls.find((c) => c.url.endsWith("/v1/tools/get_inventory"));
    expect(post?.init?.method).toBe("POST");
  });

  it("surfaces tool-level errors as MCP errors without throwing", async () => {
    const client = await connected([]);
    const result = await client.callTool({ name: "get_inventory", arguments: {} });
    expect(result.isError).toBe(true);
  });

  it("builds bearer headers when a token is given", () => {
    expect(authHeaders({ bearerToken: "abc" })).toEqual({ Authorization: "Bearer abc" });
  });
});
