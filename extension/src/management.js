/**
 * Admin management client: talks to the server-side `/manage/*` routes.
 *
 * These routes are gated by the same Entra auth as `/chat` and additionally
 * require the caller to be an **admin**. This client attaches the user's Entra
 * access token and exposes small helpers the options page uses to render the
 * admin panel: list/invite/edit users and download the preconfigured extension.
 *
 * `buildManageUrl` is pure and unit-tested.
 */

import { deriveEndpoint } from "./config.js";

/**
 * Derive a `/manage/...` endpoint URL from the configured MCP endpoint.
 * Pure function. E.g. `https://host/mcp` + `users` → `https://host/manage/users`.
 * @param {string} mcpServerUrl
 * @param {string} [suffix]  path after `manage/` (e.g. 'users', 'capabilities')
 * @returns {string}
 */
export function buildManageUrl(mcpServerUrl, suffix = "") {
  const path = suffix ? `manage/${suffix}` : "manage";
  return deriveEndpoint(mcpServerUrl, path);
}

export class ManagementClient {
  /**
   * @param {import('./config.js').ExtensionConfig} config
   * @param {(opts?:{forceRefresh?:boolean}) => Promise<string>} getToken
   */
  constructor(config, getToken) {
    this.config = config;
    this.getToken = getToken;
  }

  /** Issue an authenticated JSON request to a `/manage/...` route. */
  async _request(method, suffix, body) {
    const token = await this.getToken();
    const url = buildManageUrl(this.config.mcpServerUrl, suffix);
    const init = {
      method,
      headers: {
        Accept: "application/json",
        Authorization: "Bearer " + token,
      },
    };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const resp = await fetch(url, init);
    let data = null;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (!resp.ok) {
      const message = (data && (data.error || data.code)) || `Request failed (${resp.status})`;
      const err = new Error(message);
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  /** Report whether management is enabled and whether the caller is an admin. */
  async capabilities() {
    return this._request("GET", "capabilities");
  }

  /** List users assigned to the API app with their coarse roles. */
  async listUsers() {
    const data = await this._request("GET", "users");
    return (data && data.users) || [];
  }

  /**
   * Invite an Entra B2B guest and assign them a role.
   * @param {string} email
   * @param {string} role  one of 'admin'|'operator'|'readonly'
   */
  async inviteUser(email, role) {
    const data = await this._request("POST", "users", { email, role });
    return data && data.user;
  }

  /**
   * Change an existing user's role.
   * @param {string} userId  the user's Entra object ID
   * @param {string} role
   */
  async setUserRole(userId, role) {
    const suffix = `users/${encodeURIComponent(userId)}/role`;
    const data = await this._request("PATCH", suffix, { role });
    return data && data.user;
  }

  /**
   * Download the prebuilt, preconfigured extension archive for a browser.
   * Returns a Blob the caller can save. Uses the bearer token like other routes.
   * @param {string} browser  'chrome' | 'firefox'
   * @returns {Promise<Blob>}
   */
  async downloadExtension(browser) {
    const token = await this.getToken();
    const url = buildManageUrl(this.config.mcpServerUrl, "extension") +
      `?browser=${encodeURIComponent(browser)}`;
    const resp = await fetch(url, {
      method: "GET",
      headers: { Authorization: "Bearer " + token },
    });
    if (!resp.ok) {
      let message = `Download failed (${resp.status})`;
      try {
        const data = await resp.json();
        if (data && data.error) message = data.error;
      } catch {
        // non-JSON error body; keep the default message.
      }
      const err = new Error(message);
      err.status = resp.status;
      throw err;
    }
    return resp.blob();
  }
}
