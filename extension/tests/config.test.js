import { describe, expect, it } from "vitest";
import {
  DEFAULT_CONFIG,
  deriveEndpoint,
  pickKnownFields,
  validateConfig,
  withDefaults,
} from "../src/config.js";

describe("config", () => {
  it("withDefaults fills missing fields", () => {
    const cfg = withDefaults({ mcpServerUrl: "https://x/mcp" });
    expect(cfg.mcpServerUrl).toBe("https://x/mcp");
    expect(cfg.llmModel).toBe(DEFAULT_CONFIG.llmModel);
    expect(cfg.entraScopes).toBe(DEFAULT_CONFIG.entraScopes);
  });

  it("derives sibling endpoints from the MCP URL", () => {
    expect(deriveEndpoint("https://host/mcp", "chat")).toBe("https://host/chat");
    expect(deriveEndpoint("https://host/mcp", "capabilities")).toBe("https://host/capabilities");
  });

  it("validates a complete config as error-free", () => {
    const errors = validateConfig({
      mcpServerUrl: "https://host/mcp",
      entraTenantId: "tid",
      entraClientId: "cid",
      entraScopes: "api://x/MCP.Access",
    });
    expect(errors).toEqual([]);
  });

  it("does not require a model (server controls the default)", () => {
    const errors = validateConfig({
      mcpServerUrl: "https://host/mcp",
      entraTenantId: "tid",
      entraClientId: "cid",
      entraScopes: "s",
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
    });
    expect(errors.join(" ")).toMatch(/http:\/\/ or https:\/\//);
  });

  it("pickKnownFields keeps only recognised, non-empty string fields", () => {
    const picked = pickKnownFields({
      mcpServerUrl: "https://baked/mcp",
      entraScopes: "",
      unknownKey: "ignored",
      entraTenantId: "baked-tid",
    });
    expect(picked).toEqual({ mcpServerUrl: "https://baked/mcp", entraTenantId: "baked-tid" });
  });

  it("lets user-stored settings override baked defaults, which override built-ins", () => {
    // With no baked config checked in, user values still win over DEFAULT_CONFIG.
    const cfg = withDefaults({ mcpServerUrl: "https://user/mcp" });
    expect(cfg.mcpServerUrl).toBe("https://user/mcp");
    // Unset fields fall back to the built-in defaults.
    expect(cfg.entraTenantId).toBe(DEFAULT_CONFIG.entraTenantId);
  });
});
