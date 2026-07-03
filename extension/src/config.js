/**
 * Extension configuration: defaults, validation, and persistence.
 *
 * Settings are stored in `storage.local` (they are not secrets — tokens live in
 * `storage.session`). The upstream Buildium API key never lives in the browser;
 * it stays server-side behind the MCP server.
 */

import { getApi } from "./browser.js";

/** @typedef {Object} ExtensionConfig
 * @property {string} mcpServerUrl   Base URL of the MCP Streamable HTTP endpoint (e.g. https://host/mcp)
 * @property {string} entraTenantId  Entra ID tenant GUID (or 'common'/'organizations')
 * @property {string} entraClientId  App registration (public client) ID for THIS extension
 * @property {string} entraScopes    Space-separated scopes to request (e.g. 'api://<api-id>/MCP.Access')
 * @property {string} llmApiBase     Base URL of the OpenAI-compatible chat completions API
 * @property {string} llmModel       Model name to use
 * @property {string} llmApiKey      API key/token for the LLM endpoint (optional if using Entra-protected proxy)
 * @property {string} systemPrompt   System prompt steering the assistant
 */

/** @type {ExtensionConfig} */
export const DEFAULT_CONFIG = {
  mcpServerUrl: "http://localhost:8000/mcp",
  entraTenantId: "",
  entraClientId: "",
  entraScopes: "",
  llmApiBase: "https://api.openai.com/v1",
  llmModel: "gpt-4o-mini",
  llmApiKey: "",
  systemPrompt:
    "You are a helpful property-management assistant for Buildium. " +
    "Use the available tools to answer questions and perform actions. " +
    "Prefer read-only tools unless the user explicitly asks to create or modify data. " +
    "Always confirm destructive or write operations before calling them.",
};

const STORAGE_KEY = "buildium_mcp_config";

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
  if (!(cfg.llmModel || "").trim()) {
    errors.push("LLM model is required.");
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
