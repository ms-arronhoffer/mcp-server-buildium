import { describe, expect, it, vi } from "vitest";
import { ChatClient, parseServerEvent } from "../src/llm.js";

describe("parseServerEvent", () => {
  it("decodes a data event", () => {
    expect(parseServerEvent('data: {"type":"token","text":"hi"}')).toEqual({
      type: "token",
      text: "hi",
    });
  });

  it("ignores non-data and blank lines", () => {
    expect(parseServerEvent(": comment")).toBeNull();
    expect(parseServerEvent("data:")).toBeNull();
    expect(parseServerEvent("")).toBeNull();
  });
});

/** Build a fake streaming fetch Response yielding the given SSE text once. */
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

const config = { mcpServerUrl: "https://host/mcp", llmModel: "" };

describe("ChatClient", () => {
  it("posts to the derived /chat endpoint with a bearer token", async () => {
    const fetchMock = vi.fn(async () =>
      streamResponse('data: {"type":"done","content":"ok"}\n'),
    );
    vi.stubGlobal("fetch", fetchMock);

    const chat = new ChatClient(config, async () => "tok123");
    await chat.run([{ role: "user", content: "hi" }]);

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("https://host/chat");
    expect(opts.headers.Authorization).toBe("Bearer " + "tok123");
    expect(JSON.parse(opts.body)).toEqual({ messages: [{ role: "user", content: "hi" }] });
    vi.unstubAllGlobals();
  });

  it("includes the model only when configured", async () => {
    const fetchMock = vi.fn(async () => streamResponse('data: {"type":"done","content":"ok"}\n'));
    vi.stubGlobal("fetch", fetchMock);

    const chat = new ChatClient({ ...config, llmModel: "gpt-4o" }, async () => "t");
    await chat.run([{ role: "user", content: "hi" }]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).model).toBe("gpt-4o");
    vi.unstubAllGlobals();
  });

  it("forwards message attachments in the request body", async () => {
    const fetchMock = vi.fn(async () => streamResponse('data: {"type":"done","content":"ok"}\n'));
    vi.stubGlobal("fetch", fetchMock);

    const attachments = [{ name: "lease.pdf", media_type: "application/pdf", data: "JVBERg==" }];
    const chat = new ChatClient(config, async () => "t");
    await chat.run([{ role: "user", content: "extract", attachments }]);

    const sent = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(sent.messages[0].attachments).toEqual(attachments);
    vi.unstubAllGlobals();
  });

  it("streams tokens and returns the final content", async () => {
    const sse =
      'data: {"type":"token","text":"Hello "}\n' +
      'data: {"type":"token","text":"world"}\n' +
      'data: {"type":"done","content":"Hello world"}\n';
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(sse)));

    const tokens = [];
    const chat = new ChatClient(config, async () => "t");
    const { content } = await chat.run([{ role: "user", content: "hi" }], {
      onToken: (t) => tokens.push(t),
    });

    expect(tokens.join("")).toBe("Hello world");
    expect(content).toBe("Hello world");
    vi.unstubAllGlobals();
  });

  it("surfaces tool call and result events", async () => {
    const sse =
      'data: {"type":"tool_call","name":"list_leases","arguments":{"limit":5}}\n' +
      'data: {"type":"tool_result","name":"list_leases","text":"{\\"count\\":2}"}\n' +
      'data: {"type":"done","content":"You have 2 leases."}\n';
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(sse)));

    const calls = [];
    const results = [];
    const chat = new ChatClient(config, async () => "t");
    const { content } = await chat.run([{ role: "user", content: "how many?" }], {
      onToolCall: (name, args) => calls.push([name, args]),
      onToolResult: (name, text) => results.push([name, text]),
    });

    expect(calls).toEqual([["list_leases", { limit: 5 }]]);
    expect(results).toEqual([["list_leases", '{"count":2}']]);
    expect(content).toBe("You have 2 leases.");
    vi.unstubAllGlobals();
  });

  it("collects artifact events and returns them", async () => {
    const sse =
      'data: {"type":"token","text":"Your file is ready."}\n' +
      'data: {"type":"done","content":"Your file is ready."}\n' +
      'data: {"type":"artifact","name":"leases.csv","media_type":"text/csv","size":42,"data":"QUJD"}\n';
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(sse)));

    const seen = [];
    const chat = new ChatClient(config, async () => "t");
    const { content, artifacts } = await chat.run([{ role: "user", content: "export" }], {
      onArtifact: (a) => seen.push(a),
    });

    expect(content).toBe("Your file is ready.");
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0].name).toBe("leases.csv");
    expect(artifacts[0].data).toBe("QUJD");
    expect(seen).toEqual(artifacts);
    vi.unstubAllGlobals();
  });

  it("throws on an error event", async () => {
    const sse = 'data: {"type":"error","message":"provider down"}\n';
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse(sse)));

    const chat = new ChatClient(config, async () => "t");
    await expect(chat.run([{ role: "user", content: "x" }])).rejects.toThrow(/provider down/);
    vi.unstubAllGlobals();
  });

  it("raises a 401 with a code for expired sessions", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse("", { ok: false, status: 401 })));
    const chat = new ChatClient(config, async () => "t");
    await expect(chat.run([{ role: "user", content: "x" }])).rejects.toMatchObject({ code: 401 });
    vi.unstubAllGlobals();
  });

  it("force-refreshes the token and retries once on a 401", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(streamResponse("", { ok: false, status: 401 }))
      .mockResolvedValueOnce(streamResponse('data: {"type":"done","content":"ok"}\n'));
    vi.stubGlobal("fetch", fetchMock);

    const tokenOpts = [];
    const getToken = vi.fn(async (opts) => {
      tokenOpts.push(opts);
      return opts && opts.forceRefresh ? "fresh" : "stale";
    });
    const chat = new ChatClient(config, getToken);
    const { content } = await chat.run([{ role: "user", content: "x" }]);

    expect(content).toBe("ok");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // First attempt uses the cached token; the retry forces a refresh.
    expect(tokenOpts[0]).toEqual({});
    expect(tokenOpts[1]).toEqual({ forceRefresh: true });
    vi.unstubAllGlobals();
  });

  it("throws a 401 when even a refreshed token is rejected", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => streamResponse("", { ok: false, status: 401 })));
    const getToken = vi.fn(async () => "t");
    const chat = new ChatClient(config, getToken);
    await expect(chat.run([{ role: "user", content: "x" }])).rejects.toMatchObject({ code: 401 });
    // One initial attempt plus one forced-refresh retry.
    expect(getToken).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });
});
