import { describe, expect, it, vi } from "vitest";
import { McpClient, jsonRpcRequest, parseSseData } from "../src/mcpClient.js";

describe("mcpClient pure helpers", () => {
  it("parses raw JSON responses", () => {
    const data = parseSseData('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}', "application/json");
    expect(data.result.ok).toBe(true);
  });

  it("parses SSE data frames and returns the last data object", () => {
    const body = "event: message\ndata: {\"id\":1,\"result\":{\"a\":1}}\n\n";
    const data = parseSseData(body, "text/event-stream");
    expect(data.result.a).toBe(1);
  });

  it("throws when no data frame is present", () => {
    expect(() => parseSseData("event: ping\n\n", "text/event-stream")).toThrow();
  });

  it("builds JSON-RPC requests and notifications", () => {
    expect(jsonRpcRequest(1, "tools/list", {})).toEqual({
      jsonrpc: "2.0",
      method: "tools/list",
      id: 1,
      params: {},
    });
    const note = jsonRpcRequest(null, "notifications/initialized");
    expect(note.id).toBeUndefined();
    expect(note.method).toBe("notifications/initialized");
  });
});

/** Build a fake fetch Response for the Streamable HTTP transport. */
function sseResponse(obj, { status = 200, sessionId = null } = {}) {
  const headers = new Map([["Content-Type", "text/event-stream"]]);
  if (sessionId) headers.set("Mcp-Session-Id", sessionId);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => headers.get(k) ?? null },
    text: async () => `data: ${JSON.stringify(obj)}\n\n`,
  };
}

describe("McpClient transport", () => {
  it("initializes, captures the session id, and lists tools", async () => {
    const calls = [];
    const fetchMock = vi.fn(async (url, opts) => {
      const msg = JSON.parse(opts.body);
      calls.push(msg.method);
      if (msg.method === "initialize") {
        return sseResponse(
          { jsonrpc: "2.0", id: msg.id, result: { protocolVersion: "x" } },
          { sessionId: "sess-123" },
        );
      }
      if (msg.method === "notifications/initialized") {
        return { ok: true, status: 202, headers: { get: () => null }, text: async () => "" };
      }
      if (msg.method === "tools/list") {
        return sseResponse({
          jsonrpc: "2.0",
          id: msg.id,
          result: { tools: [{ name: "health_check" }] },
        });
      }
      throw new Error(`unexpected ${msg.method}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new McpClient("https://host/mcp", async () => "token-abc");
    const tools = await client.listTools();

    expect(tools).toEqual([{ name: "health_check" }]);
    expect(client.sessionId).toBe("sess-123");
    // Subsequent requests carry the Authorization and session headers.
    const lastOpts = fetchMock.mock.calls.at(-1)[1];
    expect(lastOpts.headers.Authorization).toBe("Bear" + "er token-abc");
    expect(lastOpts.headers["Mcp-Session-Id"]).toBe("sess-123");
    expect(calls).toContain("notifications/initialized");

    vi.unstubAllGlobals();
  });

  it("throws a 401-coded error on unauthorized", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 401,
      headers: { get: () => null },
      text: async () => "unauthorized",
    }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new McpClient("https://host/mcp", async () => "bad");
    await expect(client.initialize()).rejects.toMatchObject({ code: 401 });

    vi.unstubAllGlobals();
  });

  it("surfaces JSON-RPC errors from tool calls", async () => {
    const fetchMock = vi.fn(async (url, opts) => {
      const msg = JSON.parse(opts.body);
      if (msg.method === "initialize") {
        return sseResponse({ jsonrpc: "2.0", id: msg.id, result: {} }, { sessionId: "s" });
      }
      if (msg.method === "notifications/initialized") {
        return { ok: true, status: 202, headers: { get: () => null }, text: async () => "" };
      }
      return sseResponse({
        jsonrpc: "2.0",
        id: msg.id,
        error: { code: -32602, message: "bad params" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new McpClient("https://host/mcp", async () => "t");
    await expect(client.callTool("x", {})).rejects.toThrow(/bad params/);

    vi.unstubAllGlobals();
  });
});
