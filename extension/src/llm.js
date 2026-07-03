/**
 * LLM orchestration: the assistant drives the conversation and decides when to
 * call Buildium MCP tools.
 *
 * The MCP tool list is advertised to an OpenAI-compatible Chat Completions
 * endpoint as function tools. When the model emits tool calls, we execute them
 * through the {@link McpClient} and feed the results back until the model
 * produces a final answer. Assistant text is streamed to the UI as it arrives.
 *
 * The mapping/accumulation helpers are pure and unit-tested; the network loop
 * lives in {@link Agent}.
 */

/**
 * Convert MCP tool definitions into OpenAI "function" tool specs.
 * Pure function.
 * @param {Array<{name:string, description?:string, inputSchema?:object}>} mcpTools
 * @returns {Array<object>}
 */
export function toolsToOpenAi(mcpTools) {
  return (mcpTools || []).map((t) => ({
    type: "function",
    function: {
      name: t.name,
      description: t.description || "",
      parameters: t.inputSchema || { type: "object", properties: {} },
    },
  }));
}

/**
 * Flatten an MCP tool-call result into a string for the model.
 * Prefers `structuredContent`, else concatenates text content blocks.
 * Pure function.
 * @param {any} result
 * @returns {string}
 */
export function flattenMcpContent(result) {
  if (!result) return "";
  if (result.structuredContent !== undefined) {
    return JSON.stringify(result.structuredContent);
  }
  const content = result.content;
  if (Array.isArray(content)) {
    return content
      .map((c) => (c && c.type === "text" ? c.text : JSON.stringify(c)))
      .join("\n");
  }
  return JSON.stringify(result);
}

/** @returns {{content:string, toolCalls:Array}} a fresh streaming accumulator. */
export function newAccumulator() {
  return { content: "", toolCalls: [] };
}

/**
 * Merge a streamed Chat Completions delta into an accumulator, following the
 * OpenAI streaming tool-call protocol (tool calls arrive in indexed fragments).
 * Pure function; mutates and returns `acc`.
 * @param {{content:string, toolCalls:Array}} acc
 * @param {any} delta
 * @returns {{content:string, toolCalls:Array}}
 */
export function applyDelta(acc, delta) {
  if (!delta) return acc;
  if (typeof delta.content === "string") {
    acc.content += delta.content;
  }
  if (Array.isArray(delta.tool_calls)) {
    for (const tc of delta.tool_calls) {
      const idx = tc.index ?? 0;
      if (!acc.toolCalls[idx]) {
        acc.toolCalls[idx] = { id: "", type: "function", function: { name: "", arguments: "" } };
      }
      const slot = acc.toolCalls[idx];
      if (tc.id) slot.id = tc.id;
      if (tc.function?.name) slot.function.name += tc.function.name;
      if (tc.function?.arguments) slot.function.arguments += tc.function.arguments;
    }
  }
  return acc;
}

/**
 * Parse one SSE `data:` line from a Chat Completions stream.
 * Returns the delta object, the string "[DONE]", or null for non-data lines.
 * Pure function.
 * @param {string} line
 * @returns {any|null|"[DONE]"}
 */
export function parseChatSseLine(line) {
  const trimmed = line.trimStart();
  if (!trimmed.startsWith("data:")) return null;
  const payload = trimmed.slice("data:".length).trim();
  if (payload === "") return null;
  if (payload === "[DONE]") return "[DONE]";
  const json = JSON.parse(payload);
  return json.choices?.[0]?.delta ?? {};
}

/**
 * Safely JSON-parse tool-call arguments (the model may emit "" for no args).
 * Pure function.
 * @param {string} argsText
 * @returns {object}
 */
export function parseToolArguments(argsText) {
  if (!argsText || !argsText.trim()) return {};
  try {
    return JSON.parse(argsText);
  } catch {
    return {};
  }
}

const MAX_TOOL_ROUNDS = 8;

export class Agent {
  /**
   * @param {import('./config.js').ExtensionConfig} config
   * @param {import('./mcpClient.js').McpClient} mcpClient
   */
  constructor(config, mcpClient) {
    this.config = config;
    this.mcp = mcpClient;
    this._openAiTools = null;
  }

  async _ensureTools() {
    if (this._openAiTools) return this._openAiTools;
    const tools = await this.mcp.listTools();
    this._openAiTools = toolsToOpenAi(tools);
    return this._openAiTools;
  }

  async _streamCompletion(messages, tools, onToken) {
    const resp = await fetch(`${this.config.llmApiBase.replace(/\/$/, "")}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(this.config.llmApiKey ? { Authorization: "Bearer " + this.config.llmApiKey } : {}),
      },
      body: JSON.stringify({
        model: this.config.llmModel,
        messages,
        tools,
        tool_choice: "auto",
        stream: true,
      }),
    });
    if (!resp.ok) {
      const body = await resp.text();
      throw new Error(`LLM request failed (${resp.status}): ${body.slice(0, 300)}`);
    }

    const acc = newAccumulator();
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
        const delta = parseChatSseLine(line);
        if (delta === "[DONE]" || delta === null) continue;
        if (delta.content && onToken) onToken(delta.content);
        applyDelta(acc, delta);
      }
    }
    return acc;
  }

  /**
   * Run one user turn to completion, executing any tool calls the model requests.
   * @param {Array<object>} history  prior chat messages (role/content)
   * @param {{onToken?:(t:string)=>void,
   *          onToolCall?:(name:string, args:object)=>void,
   *          onToolResult?:(name:string, text:string)=>void}} [callbacks]
   * @returns {Promise<{content:string, messages:Array<object>}>}
   */
  async run(history, callbacks = {}) {
    const tools = await this._ensureTools();
    const messages = [
      { role: "system", content: this.config.systemPrompt },
      ...history,
    ];

    for (let round = 0; round < MAX_TOOL_ROUNDS; round += 1) {
      const acc = await this._streamCompletion(messages, tools, callbacks.onToken);

      const assistantMsg = { role: "assistant", content: acc.content || null };
      const toolCalls = acc.toolCalls.filter((c) => c && c.function && c.function.name);
      if (toolCalls.length > 0) assistantMsg.tool_calls = toolCalls;
      messages.push(assistantMsg);

      if (toolCalls.length === 0) {
        return { content: acc.content, messages };
      }

      for (const call of toolCalls) {
        const name = call.function.name;
        const args = parseToolArguments(call.function.arguments);
        if (callbacks.onToolCall) callbacks.onToolCall(name, args);
        let text;
        try {
          const result = await this.mcp.callTool(name, args);
          text = flattenMcpContent(result);
        } catch (err) {
          text = `Error calling tool ${name}: ${err.message}`;
        }
        if (callbacks.onToolResult) callbacks.onToolResult(name, text);
        messages.push({ role: "tool", tool_call_id: call.id, content: text });
      }
    }
    return {
      content: "Stopped after the maximum number of tool-call rounds.",
      messages,
    };
  }
}
