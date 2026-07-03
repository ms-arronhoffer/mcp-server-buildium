import { describe, expect, it, vi } from "vitest";
import {
  Agent,
  applyDelta,
  flattenMcpContent,
  newAccumulator,
  parseChatSseLine,
  parseToolArguments,
  toolsToOpenAi,
} from "../src/llm.js";

describe("llm pure helpers", () => {
  it("maps MCP tools to OpenAI function specs", () => {
    const openai = toolsToOpenAi([
      { name: "list_leases", description: "List leases", inputSchema: { type: "object" } },
    ]);
    expect(openai[0]).toEqual({
      type: "function",
      function: {
        name: "list_leases",
        description: "List leases",
        parameters: { type: "object" },
      },
    });
  });

  it("provides a default schema when inputSchema is missing", () => {
    const openai = toolsToOpenAi([{ name: "x" }]);
    expect(openai[0].function.parameters).toEqual({ type: "object", properties: {} });
  });

  it("flattens structuredContent", () => {
    expect(flattenMcpContent({ structuredContent: { a: 1 } })).toBe('{"a":1}');
  });

  it("flattens text content blocks", () => {
    const text = flattenMcpContent({
      content: [
        { type: "text", text: "line1" },
        { type: "text", text: "line2" },
      ],
    });
    expect(text).toBe("line1\nline2");
  });

  it("accumulates streamed content deltas", () => {
    const acc = newAccumulator();
    applyDelta(acc, { content: "Hel" });
    applyDelta(acc, { content: "lo" });
    expect(acc.content).toBe("Hello");
  });

  it("accumulates indexed tool-call fragments", () => {
    const acc = newAccumulator();
    applyDelta(acc, { tool_calls: [{ index: 0, id: "call_1", function: { name: "list_" } }] });
    applyDelta(acc, { tool_calls: [{ index: 0, function: { name: "leases", arguments: '{"lim' } }] });
    applyDelta(acc, { tool_calls: [{ index: 0, function: { arguments: 'it":5}' } }] });
    expect(acc.toolCalls[0].id).toBe("call_1");
    expect(acc.toolCalls[0].function.name).toBe("list_leases");
    expect(acc.toolCalls[0].function.arguments).toBe('{"limit":5}');
  });

  it("parses chat SSE lines", () => {
    expect(parseChatSseLine("data: [DONE]")).toBe("[DONE]");
    expect(parseChatSseLine(": comment")).toBeNull();
    const delta = parseChatSseLine('data: {"choices":[{"delta":{"content":"hi"}}]}');
    expect(delta).toEqual({ content: "hi" });
  });

  it("parses tool arguments, tolerating empty/invalid JSON", () => {
    expect(parseToolArguments('{"a":1}')).toEqual({ a: 1 });
    expect(parseToolArguments("")).toEqual({});
    expect(parseToolArguments("not json")).toEqual({});
  });
});

/** Build a fake streaming fetch Response yielding the given SSE text. */
function streamResponse(sseText, { ok = true, status = 200 } = {}) {
  const encoder = new TextEncoder();
  let sent = false;
  return {
    ok,
    status,
    text: async () => sseText,
    body: {
      getReader() {
        return {
          async read() {
            if (sent) return { done: true, value: undefined };
            sent = true;
            return { done: false, value: encoder.encode(sseText) };
          },
        };
      },
    },
  };
}

const baseConfig = {
  llmApiBase: "https://llm.example/v1",
  llmModel: "gpt-4o-mini",
  llmApiKey: "",
  systemPrompt: "sys",
};

describe("Agent orchestration", () => {
  it("streams a direct answer with no tool calls", async () => {
    const mcp = { listTools: async () => [], callTool: vi.fn() };
    const sse =
      'data: {"choices":[{"delta":{"content":"Hello "}}]}\n' +
      'data: {"choices":[{"delta":{"content":"world"}}]}\n' +
      "data: [DONE]\n";
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(sse)));

    const tokens = [];
    const agent = new Agent(baseConfig, mcp);
    const { content } = await agent.run([{ role: "user", content: "hi" }], {
      onToken: (t) => tokens.push(t),
    });

    expect(content).toBe("Hello world");
    expect(tokens.join("")).toBe("Hello world");
    expect(mcp.callTool).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("executes a tool call then streams the final answer", async () => {
    const mcp = {
      listTools: async () => [{ name: "list_leases", inputSchema: { type: "object" } }],
      callTool: vi.fn(async () => ({ structuredContent: { count: 2 } })),
    };

    const first =
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"list_leases","arguments":"{}"}}]}}]}\n' +
      "data: [DONE]\n";
    const second =
      'data: {"choices":[{"delta":{"content":"You have 2 leases."}}]}\n' + "data: [DONE]\n";

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(streamResponse(first))
      .mockResolvedValueOnce(streamResponse(second));
    vi.stubGlobal("fetch", fetchMock);

    const toolCalls = [];
    const toolResults = [];
    const agent = new Agent(baseConfig, mcp);
    const { content } = await agent.run([{ role: "user", content: "how many leases?" }], {
      onToolCall: (name, args) => toolCalls.push([name, args]),
      onToolResult: (name, text) => toolResults.push([name, text]),
    });

    expect(mcp.callTool).toHaveBeenCalledWith("list_leases", {});
    expect(toolCalls).toEqual([["list_leases", {}]]);
    expect(toolResults[0][0]).toBe("list_leases");
    expect(toolResults[0][1]).toBe('{"count":2}');
    expect(content).toBe("You have 2 leases.");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });

  it("reports tool errors back to the model instead of throwing", async () => {
    const mcp = {
      listTools: async () => [{ name: "boom" }],
      callTool: vi.fn(async () => {
        throw new Error("kaboom");
      }),
    };
    const first =
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"boom","arguments":"{}"}}]}}]}\n' +
      "data: [DONE]\n";
    const second = 'data: {"choices":[{"delta":{"content":"handled"}}]}\n' + "data: [DONE]\n";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(streamResponse(first))
      .mockResolvedValueOnce(streamResponse(second));
    vi.stubGlobal("fetch", fetchMock);

    const toolResults = [];
    const agent = new Agent(baseConfig, mcp);
    const { content } = await agent.run([{ role: "user", content: "go" }], {
      onToolResult: (name, text) => toolResults.push(text),
    });

    expect(toolResults[0]).toMatch(/kaboom/);
    expect(content).toBe("handled");
    vi.unstubAllGlobals();
  });
});
