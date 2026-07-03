/**
 * PKCE (Proof Key for Code Exchange, RFC 7636) helpers for OAuth 2.0
 * Authorization Code flow. Pure functions built on the Web Crypto API, so they
 * run identically in the extension and in Node (vitest) test environments.
 */

/**
 * Base64url-encode an ArrayBuffer / Uint8Array without padding.
 * @param {ArrayBuffer|Uint8Array} buffer
 * @returns {string}
 */
export function base64UrlEncode(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  let binary = "";
  for (const b of bytes) {
    binary += String.fromCharCode(b);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Generate a high-entropy code verifier (43-128 chars, URL-safe).
 * @param {number} [byteLength=32] entropy bytes (32 -> 43-char verifier)
 * @returns {string}
 */
export function generateCodeVerifier(byteLength = 32) {
  const random = new Uint8Array(byteLength);
  globalThis.crypto.getRandomValues(random);
  return base64UrlEncode(random);
}

/**
 * Derive the S256 code challenge from a verifier.
 * @param {string} verifier
 * @returns {Promise<string>}
 */
export async function challengeFromVerifier(verifier) {
  const data = new TextEncoder().encode(verifier);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", data);
  return base64UrlEncode(digest);
}

/**
 * Generate a random opaque state value for CSRF protection.
 * @param {number} [byteLength=16]
 * @returns {string}
 */
export function generateState(byteLength = 16) {
  const random = new Uint8Array(byteLength);
  globalThis.crypto.getRandomValues(random);
  return base64UrlEncode(random);
}

/**
 * Create a complete PKCE pair (verifier + S256 challenge + state).
 * @returns {Promise<{verifier: string, challenge: string, state: string}>}
 */
export async function createPkcePair() {
  const verifier = generateCodeVerifier();
  const challenge = await challengeFromVerifier(verifier);
  const state = generateState();
  return { verifier, challenge, state };
}
