/**
 * Extension configuration: defaults, validation, and persistence.
 *
 * Settings are stored in `storage.local` (they are not secrets — tokens live in
 * `storage.session`). Neither the Buildium API key nor the LLM provider API keys
 * ever live in the browser: both stay server-side behind the MCP server, which
 * now also runs the assistant loop and exposes a `/chat` endpoint.
 */

import { getApi } from "./browser.js";

/** @typedef {Object} ExtensionConfig
 * @property {string} mcpServerUrl   Base URL of the MCP Streamable HTTP endpoint (e.g. https://host/mcp)
 * @property {string} entraTenantId  Entra ID tenant GUID (or 'common'/'organizations')
 * @property {string} entraClientId  App registration (public client) ID for THIS extension
 * @property {string} entraScopes    Space-separated scopes to request (e.g. 'api://<api-id>/MCP.Access')
 * @property {string} llmModel       Optional model to request (blank = the server's default)
 */

/** @type {ExtensionConfig} */
export const DEFAULT_CONFIG = {
  mcpServerUrl: "http://localhost:8000/mcp",
  entraTenantId: "",
  entraClientId: "",
  entraScopes: "",
  llmModel: "",
};

const STORAGE_KEY = "buildium_mcp_config";

/**
 * Derive a sibling endpoint URL (e.g. the `/chat` or `/capabilities` route) from
 * the configured MCP endpoint. The server serves these next to the MCP path, so
 * `https://host/mcp` → `https://host/chat`. Pure function.
 * @param {string} mcpServerUrl
 * @param {string} name  the sibling path segment, e.g. 'chat' or 'capabilities'
 * @returns {string}
 */
export function deriveEndpoint(mcpServerUrl, name) {
  return new URL(name, mcpServerUrl).toString();
}

/**
 * Validate a configuration object, returning a list of human-readable errors.
 * Pure function — safe to unit test.
 * @param {Partial<ExtensionConfig>} cfg
 * @returns {string[]} validation error messages (empty when valid)
 */
export function validateConfig(cfg) {
  const errors = [];
  const url = (cfg.mcpServerUrl || "").trim();
  if (!url) {
    errors.push("MCP server URL is required.");
  } else if (!/^https?:\/\//.test(url)) {
    errors.push("MCP server URL must start with http:// or https://.");
  }
  if (!(cfg.entraTenantId || "").trim()) {
    errors.push("Entra tenant ID is required.");
  }
  if (!(cfg.entraClientId || "").trim()) {
    errors.push("Entra client (application) ID is required.");
  }
  if (!(cfg.entraScopes || "").trim()) {
    errors.push("At least one Entra scope is required.");
  }
  return errors;
}

/**
 * Merge stored values over defaults. Pure function.
 * @param {Partial<ExtensionConfig>} stored
 * @returns {ExtensionConfig}
 */
export function withDefaults(stored) {
  return { ...DEFAULT_CONFIG, ...(stored || {}) };
}

/** Load configuration from storage.local, merged over defaults. */
export async function loadConfig() {
  const api = getApi();
  const result = await api.storage.local.get(STORAGE_KEY);
  return withDefaults(result[STORAGE_KEY]);
}

/**
 * Persist configuration to storage.local.
 * @param {ExtensionConfig} cfg
 */
export async function saveConfig(cfg) {
  const api = getApi();
  await api.storage.local.set({ [STORAGE_KEY]: cfg });
}
