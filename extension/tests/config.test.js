import { describe, expect, it } from "vitest";
import { DEFAULT_CONFIG, validateConfig, withDefaults } from "../src/config.js";

describe("config", () => {
  it("withDefaults fills missing fields", () => {
    const cfg = withDefaults({ mcpServerUrl: "https://x/mcp" });
    expect(cfg.mcpServerUrl).toBe("https://x/mcp");
    expect(cfg.llmModel).toBe(DEFAULT_CONFIG.llmModel);
    expect(cfg.systemPrompt).toBe(DEFAULT_CONFIG.systemPrompt);
  });

  it("validates a complete config as error-free", () => {
    const errors = validateConfig({
      mcpServerUrl: "https://host/mcp",
      entraTenantId: "tid",
      entraClientId: "cid",
      entraScopes: "api://x/MCP.Access",
      llmModel: "gpt-4o-mini",
    });
    expect(errors).toEqual([]);
  });

  it("reports each missing required field", () => {
    const errors = validateConfig({});
    expect(errors.length).toBeGreaterThanOrEqual(4);
    expect(errors.join(" ")).toMatch(/MCP server URL is required/);
    expect(errors.join(" ")).toMatch(/tenant/i);
    expect(errors.join(" ")).toMatch(/client/i);
    expect(errors.join(" ")).toMatch(/scope/i);
  });

  it("rejects a non-http MCP URL", () => {
    const errors = validateConfig({
      mcpServerUrl: "ftp://host/mcp",
      entraTenantId: "t",
      entraClientId: "c",
      entraScopes: "s",
      llmModel: "m",
    });
    expect(errors.join(" ")).toMatch(/http:\/\/ or https:\/\//);
  });
});
