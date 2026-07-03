/**
 * Minimal MCP (Model Context Protocol) client over the Streamable HTTP transport.
 *
 * Implements just what the sidebar needs: `initialize`, `tools/list`, and
 * `tools/call`. Each call is an HTTP POST of a JSON-RPC message; responses arrive
 * either as JSON or as a single Server-Sent Event (`text/event-stream`). A bearer
 * access token (from Entra) is attached to every request.
 *
 * `parseSseData` and `jsonRpcRequest` are pure and unit-tested.
 */

/**
 * Extract the JSON-RPC payload from a Streamable HTTP response body, which may be
 * raw JSON or SSE frames. Returns the last `data:` JSON object found.
 * Pure function.
 * @param {string} body
 * @param {string} [contentType]
 * @returns {any}
 */
export function parseSseData(body, contentType = "") {
  const text = body.trim();
  if (contentType.includes("application/json") || (!contentType.includes("event-stream") && text.startsWith("{"))) {
    return JSON.parse(text);
  }
  let last = null;
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trimStart();
    if (trimmed.startsWith("data:")) {
      const payload = trimmed.slice("data:".length).trim();
      if (payload && payload !== "[DONE]") {
        last = JSON.parse(payload);
      }
    }
  }
  if (last === null) {
    throw new Error("No JSON-RPC data found in MCP response.");
  }
  return last;
}

/**
 * Build a JSON-RPC 2.0 request object. Pure function.
 * @param {number|string|null} id  request id, or null for a notification
 * @param {string} method
 * @param {any} [params]
 * @returns {object}
 */
export function jsonRpcRequest(id, method, params) {
  const msg = { jsonrpc: "2.0", method };
  if (id !== null && id !== undefined) msg.id = id;
  if (params !== undefined) msg.params = params;
  return msg;
}

const PROTOCOL_VERSION = "2025-06-18";

export class McpClient {
  /**
   * @param {string} serverUrl  Streamable HTTP endpoint (e.g. https://host/mcp)
   * @param {() => Promise<string>} getToken  resolves a bearer access token
   */
  constructor(serverUrl, getToken) {
    this.serverUrl = serverUrl;
    this.getToken = getToken;
    this.sessionId = null;
    this._id = 0;
    this._initialized = false;
  }

  _nextId() {
    this._id += 1;
    return this._id;
  }

  async _post(message) {
    const token = await this.getToken();
    /** @type {Record<string,string>} */
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      Authorization: "Bearer " + token,
    };
    if (this.sessionId) headers["Mcp-Session-Id"] = this.sessionId;

    const resp = await fetch(this.serverUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(message),
    });

    const sid = resp.headers.get("Mcp-Session-Id");
    if (sid) this.sessionId = sid;

    if (resp.status === 401) {
      const err = new Error("Unauthorized (401): sign-in required or token expired.");
      err.code = 401;
      throw err;
    }
    if (!resp.ok) {
      const body = await resp.text();
      throw new Error(`MCP request failed (${resp.status}): ${body.slice(0, 300)}`);
    }
    // Notifications (no id) may return 202 with an empty body.
    if (message.id === undefined || resp.status === 202) {
      return null;
    }
    const contentType = resp.headers.get("Content-Type") || "";
    const parsed = parseSseData(await resp.text(), contentType);
    if (parsed.error) {
      throw new Error(`MCP error ${parsed.error.code}: ${parsed.error.message}`);
    }
    return parsed.result;
  }

  /** Perform the MCP initialize handshake. */
  async initialize() {
    if (this._initialized) return;
    await this._post(
      jsonRpcRequest(this._nextId(), "initialize", {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: "buildium-mcp-sidebar", version: "0.1.0" },
      }),
    );
    // Complete the handshake with the required notification.
    await this._post(jsonRpcRequest(null, "notifications/initialized"));
    this._initialized = true;
  }

  /**
   * List available tools.
   * @returns {Promise<Array<{name:string, description?:string, inputSchema?:object}>>}
   */
  async listTools() {
    await this.initialize();
    const result = await this._post(jsonRpcRequest(this._nextId(), "tools/list", {}));
    return result?.tools ?? [];
  }

  /**
   * Call a tool by name.
   * @param {string} name
   * @param {object} [args]
   * @returns {Promise<any>} the tool result (content + optional structuredContent)
   */
  async callTool(name, args = {}) {
    await this.initialize();
    return this._post(
      jsonRpcRequest(this._nextId(), "tools/call", { name, arguments: args }),
    );
  }
}
