/**
 * Microsoft Entra ID (Azure AD) OAuth 2.0 Authorization Code + PKCE flow for a
 * browser extension.
 *
 * The extension is registered in Entra as a **public client / SPA** (no client
 * secret). It requests an access token scoped to the MCP server API and sends it
 * as an `Authorization` bearer header on every MCP request. Tokens are cached in
 * `storage.session` (cleared when the browser closes) and refreshed silently.
 *
 * Pure URL/body builders are exported separately so they can be unit-tested
 * without a browser.
 */

import { getApi } from "./browser.js";
import { createPkcePair } from "./pkce.js";

const TOKEN_STORAGE_KEY = "buildium_mcp_tokens";

/** @param {string} tenantId */
export function authorizeEndpoint(tenantId) {
  return `https://login.microsoftonline.com/${encodeURIComponent(tenantId)}/oauth2/v2.0/authorize`;
}

/** @param {string} tenantId */
export function tokenEndpoint(tenantId) {
  return `https://login.microsoftonline.com/${encodeURIComponent(tenantId)}/oauth2/v2.0/token`;
}

/**
 * Build the Entra /authorize URL for the Authorization Code + PKCE flow.
 * Pure function.
 * @param {{tenantId:string, clientId:string, redirectUri:string, scopes:string,
 *   challenge:string, state:string}} p
 * @returns {string}
 */
export function buildAuthorizeUrl(p) {
  const params = new URLSearchParams({
    client_id: p.clientId,
    response_type: "code",
    redirect_uri: p.redirectUri,
    response_mode: "query",
    // `offline_access` yields a refresh token; `openid`/`profile` for identity.
    scope: `openid profile offline_access ${p.scopes}`.trim(),
    state: p.state,
    code_challenge: p.challenge,
    code_challenge_method: "S256",
  });
  return `${authorizeEndpoint(p.tenantId)}?${params.toString()}`;
}

/**
 * Parse the query parameters Entra appends to the redirect URI.
 * Pure function.
 * @param {string} redirectUrl
 * @returns {{code?:string, state?:string, error?:string, error_description?:string}}
 */
export function parseRedirectUrl(redirectUrl) {
  const url = new URL(redirectUrl);
  const q = url.searchParams;
  return {
    code: q.get("code") ?? undefined,
    state: q.get("state") ?? undefined,
    error: q.get("error") ?? undefined,
    error_description: q.get("error_description") ?? undefined,
  };
}

/**
 * Build the token-endpoint request body for the authorization_code grant.
 * Pure function.
 * @param {{clientId:string, code:string, redirectUri:string, verifier:string, scopes:string}} p
 * @returns {URLSearchParams}
 */
export function buildCodeExchangeBody(p) {
  return new URLSearchParams({
    client_id: p.clientId,
    grant_type: "authorization_code",
    code: p.code,
    redirect_uri: p.redirectUri,
    code_verifier: p.verifier,
    scope: `openid profile offline_access ${p.scopes}`.trim(),
  });
}

/**
 * Build the token-endpoint request body for the refresh_token grant.
 * Pure function.
 * @param {{clientId:string, refreshToken:string, scopes:string}} p
 * @returns {URLSearchParams}
 */
export function buildRefreshBody(p) {
  return new URLSearchParams({
    client_id: p.clientId,
    grant_type: "refresh_token",
    refresh_token: p.refreshToken,
    scope: `openid profile offline_access ${p.scopes}`.trim(),
  });
}

/**
 * Normalize a token response into our stored shape, computing an absolute
 * expiry timestamp. Pure function.
 * @param {any} tokenResponse
 * @param {number} [now=Date.now()]
 * @returns {{accessToken:string, refreshToken?:string, expiresAt:number, scope?:string}}
 */
export function normalizeTokenResponse(tokenResponse, now = Date.now()) {
  const expiresInMs = (Number(tokenResponse.expires_in) || 3600) * 1000;
  return {
    accessToken: tokenResponse.access_token,
    refreshToken: tokenResponse.refresh_token,
    // Refresh 60s early to avoid using a token that expires mid-request.
    expiresAt: now + expiresInMs - 60_000,
    scope: tokenResponse.scope,
  };
}

/** @returns {string} the extension's OAuth redirect URI. */
export function getRedirectUri() {
  return getApi().identity.getRedirectURL();
}

async function readStoredTokens() {
  const api = getApi();
  const store = api.storage.session ?? api.storage.local;
  const result = await store.get(TOKEN_STORAGE_KEY);
  return result[TOKEN_STORAGE_KEY] || null;
}

async function writeStoredTokens(tokens) {
  const api = getApi();
  const store = api.storage.session ?? api.storage.local;
  await store.set({ [TOKEN_STORAGE_KEY]: tokens });
}

/** Clear cached tokens (sign out). */
export async function signOut() {
  const api = getApi();
  const store = api.storage.session ?? api.storage.local;
  await store.remove(TOKEN_STORAGE_KEY);
}

async function postToken(config, body) {
  const resp = await fetch(tokenEndpoint(config.entraTenantId), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(
      `Entra token request failed (${resp.status}): ${data.error_description || data.error || "unknown error"}`,
    );
  }
  return data;
}

/**
 * Run the interactive sign-in flow and cache the resulting tokens.
 * @param {import('./config.js').ExtensionConfig} config
 * @returns {Promise<string>} the access token
 */
export async function signIn(config) {
  const api = getApi();
  const redirectUri = getRedirectUri();
  const { verifier, challenge, state } = await createPkcePair();

  const authUrl = buildAuthorizeUrl({
    tenantId: config.entraTenantId,
    clientId: config.entraClientId,
    redirectUri,
    scopes: config.entraScopes,
    challenge,
    state,
  });

  const responseUrl = await api.identity.launchWebAuthFlow({
    url: authUrl,
    interactive: true,
  });

  const parsed = parseRedirectUrl(responseUrl);
  if (parsed.error) {
    throw new Error(`Entra sign-in error: ${parsed.error_description || parsed.error}`);
  }
  if (parsed.state !== state) {
    throw new Error("Entra sign-in failed: state mismatch (possible CSRF).");
  }
  if (!parsed.code) {
    throw new Error("Entra sign-in failed: no authorization code returned.");
  }

  const tokenResponse = await postToken(
    config,
    buildCodeExchangeBody({
      clientId: config.entraClientId,
      code: parsed.code,
      redirectUri,
      verifier,
      scopes: config.entraScopes,
    }),
  );
  const tokens = normalizeTokenResponse(tokenResponse);
  await writeStoredTokens(tokens);
  return tokens.accessToken;
}

/**
 * Return a valid access token, refreshing or prompting for sign-in as needed.
 * @param {import('./config.js').ExtensionConfig} config
 * @param {{interactive?:boolean, forceRefresh?:boolean}} [opts]
 *   `forceRefresh` bypasses the cached access token and mints a new one (via the
 *   refresh token, else interactive sign-in). Use it to recover from a token the
 *   server rejects — e.g. after a key rotation or server restart — without
 *   forcing the user to sign in again.
 * @returns {Promise<string>}
 */
export async function getAccessToken(config, opts = {}) {
  const tokens = await readStoredTokens();
  if (!opts.forceRefresh && tokens && tokens.expiresAt > Date.now()) {
    return tokens.accessToken;
  }
  if (tokens && tokens.refreshToken) {
    try {
      const refreshed = await postToken(
        config,
        buildRefreshBody({
          clientId: config.entraClientId,
          refreshToken: tokens.refreshToken,
          scopes: config.entraScopes,
        }),
      );
      const next = normalizeTokenResponse(refreshed);
      // Entra may not return a new refresh token; keep the previous one.
      if (!next.refreshToken) next.refreshToken = tokens.refreshToken;
      await writeStoredTokens(next);
      return next.accessToken;
    } catch {
      // Fall through to interactive sign-in.
    }
  }
  if (opts.interactive === false) {
    throw new Error("Not signed in.");
  }
  return signIn(config);
}

/** @returns {Promise<boolean>} whether a (non-expired) token is cached. */
export async function isSignedIn() {
  const tokens = await readStoredTokens();
  return !!(tokens && tokens.expiresAt > Date.now());
}
