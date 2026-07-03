/**
 * Build-time "baked" configuration defaults.
 *
 * This checked-in file is an **empty placeholder**. When an organisation
 * distributes its own build, `scripts/build.mjs` overwrites this file inside
 * `dist/<target>/src/` with the correct public values (MCP server URL, Entra
 * tenant/client IDs, scopes, default model) so the shipped extension is
 * preconfigured and users do not have to type anything after install.
 *
 * Precedence at runtime (see `config.js`):
 *   DEFAULT_CONFIG  <  BAKED_CONFIG (this file)  <  user settings
 *
 * These are **defaults the user can still override** on the Settings page.
 *
 * Never bake secrets here — only public identifiers and URLs. The
 * property-management API key and the LLM provider key always stay server-side.
 */

/** @type {Partial<import('./config.js').ExtensionConfig>} */
export const BAKED_CONFIG = {};
