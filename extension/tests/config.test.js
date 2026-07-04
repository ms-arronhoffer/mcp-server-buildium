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

  it("pickKnownFields keeps only recognised values by field type", () => {
    const picked = pickKnownFields({
      mcpServerUrl: "https://baked/mcp",
      entraScopes: "",
      unknownKey: "ignored",
      entraTenantId: "baked-tid",
      notificationFeatureEnabled: true,
      notificationPollMinutes: "20",
    });
    expect(picked).toEqual({
      mcpServerUrl: "https://baked/mcp",
      entraTenantId: "baked-tid",
      notificationFeatureEnabled: true,
      notificationPollMinutes: 20,
    });
  });

  it("validates notification poll interval bounds", () => {
    const errors = validateConfig({
      mcpServerUrl: "https://host/mcp",
      entraTenantId: "tid",
      entraClientId: "cid",
      entraScopes: "api://x/MCP.Access",
      notificationPollMinutes: 0,
    });
    expect(errors.join(" ")).toMatch(/between 1 and 1440/);
  });

  it("defaults the font size to medium", () => {
    expect(DEFAULT_CONFIG.fontSize).toBe("medium");
    expect(withDefaults({}).fontSize).toBe("medium");
  });

  it("accepts the supported font sizes and rejects others", () => {
    const base = {
      mcpServerUrl: "https://host/mcp",
      entraTenantId: "tid",
      entraClientId: "cid",
      entraScopes: "api://x/MCP.Access",
    };
    for (const size of ["small", "medium", "large", "xlarge"]) {
      expect(validateConfig({ ...base, fontSize: size })).toEqual([]);
    }
    expect(validateConfig({ ...base, fontSize: "huge" }).join(" ")).toMatch(/Font size must be one of/);
  });

  it("lets user-stored settings override baked defaults, which override built-ins", () => {
    // With no baked config checked in, user values still win over DEFAULT_CONFIG.
    const cfg = withDefaults({ mcpServerUrl: "https://user/mcp" });
    expect(cfg.mcpServerUrl).toBe("https://user/mcp");
    // Unset fields fall back to the built-in defaults.
    expect(cfg.entraTenantId).toBe(DEFAULT_CONFIG.entraTenantId);
  });
});
