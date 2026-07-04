/**
 * Chat client: talks to the server-side assistant over the `/chat` SSE endpoint.
 *
 * The assistant loop (model calls + MCP tool execution) now runs on the
 * server, so provider API keys never reach the browser. This client simply POSTs
 * the conversation, attaches the Entra access token, and translates the server's
 * Server-Sent Events into the callbacks the side panel already understands.
 *
 * `parseServerEvent` is pure and unit-tested.
 */

import { deriveEndpoint } from "./config.js";

/**
 * Parse one SSE `data:` line emitted by the server `/chat` endpoint.
 * Returns the decoded event object, or null for non-data/blank lines.
 * Pure function.
 * @param {string} line
 * @returns {any|null}
 */
export function parseServerEvent(line) {
  const trimmed = line.trimStart();
  if (!trimmed.startsWith("data:")) return null;
  const payload = trimmed.slice("data:".length).trim();
  if (payload === "") return null;
  return JSON.parse(payload);
}

export class ChatClient {
  /**
   * @param {import('./config.js').ExtensionConfig} config
   * @param {(opts?:{forceRefresh?:boolean}) => Promise<string>} getToken
   *   resolves a bearer access token; passed `{forceRefresh:true}` to mint a new
   *   one when the server rejects the cached token.
   */
  constructor(config, getToken) {
    this.config = config;
    this.getToken = getToken;
    this.chatUrl = deriveEndpoint(config.mcpServerUrl, "chat");
  }

  /** Issue the POST to /chat with a (optionally force-refreshed) bearer token. */
  async _send(history, tokenOpts) {
    const token = await this.getToken(tokenOpts);
    const body = { messages: history };
    if (this.config.llmModel && this.config.llmModel.trim()) {
      body.model = this.config.llmModel.trim();
    }
    return fetch(this.chatUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        Authorization: "Bearer " + token,
      },
      body: JSON.stringify(body),
    });
  }

  /**
   * Run one user turn against the server assistant, streaming events back.
   *
   * `history` messages may carry an `attachments` array on the latest user
   * turn (`[{ name, media_type, data }]`, where `data` is base64), which the
   * server reads to extract fields from an uploaded document.
   *
   * The server may also stream `artifact` events — files the assistant
   * generated for the user to download (a CSV of leases, a slide deck, …).
   * These are collected and returned as `artifacts` so the UI can present a
   * download link; `onArtifact` is invoked for each as it arrives.
   * @param {Array<{role:string, content:string, attachments?:Array<object>}>} history
   * @param {{onToken?:(t:string)=>void,
   *          onArtifact?:(artifact:object)=>void}} [callbacks]
   * @returns {Promise<{content:string, artifacts:Array<object>}>}
   */
  async run(history, callbacks = {}) {
    let resp = await this._send(history, {});
    // A cached access token can be rejected by the server (e.g. after a signing-
    // key rotation or server restart). Before declaring the session expired,
    // transparently mint a fresh token and retry once.
    if (resp.status === 401) {
      resp = await this._send(history, { forceRefresh: true });
    }

    if (resp.status === 401) {
      const err = new Error("Unauthorized (401): sign-in required or token expired.");
      err.code = 401;
      throw err;
    }
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Chat request failed (${resp.status}): ${text.slice(0, 300)}`);
    }

    const state = { content: "", artifacts: [] };
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        this._handle(parseServerEvent(line), state, callbacks);
      }
    }
    // Flush any trailing buffered line.
    if (buffer.trim()) {
      this._handle(parseServerEvent(buffer), state, callbacks);
    }
    return { content: state.content, artifacts: state.artifacts };
  }

  /** Dispatch a single decoded event, mutating `state` ({content, artifacts}). */
  _handle(event, state, callbacks) {
    if (!event) return;
    switch (event.type) {
      case "token":
        state.content += event.text || "";
        if (callbacks.onToken) callbacks.onToken(event.text || "");
        break;
      case "tool_call":
      case "tool_result":
        // Server-side /chat currently hides internal loop events from clients.
        break;
      case "artifact":
        state.artifacts.push(event);
        if (callbacks.onArtifact) callbacks.onArtifact(event);
        break;
      case "done":
        if (event.content) state.content = event.content;
        break;
      case "error":
        throw new Error(event.message || "Assistant error");
      default:
        break;
    }
  }
}
