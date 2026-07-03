/**
 * Cross-browser WebExtension API accessor.
 *
 * Firefox exposes the promise-based `browser.*` namespace; Chrome exposes
 * `chrome.*` (promise-based for the APIs used here under MV3). This module
 * returns whichever is available so the rest of the code is browser-agnostic.
 *
 * It is intentionally lazy (a function, not a top-level constant) so pure-logic
 * modules can be imported and unit-tested in Node without a WebExtension global.
 */

/** @returns {any} the WebExtension API namespace */
export function getApi() {
  const api = globalThis.browser ?? globalThis.chrome;
  if (!api) {
    throw new Error("WebExtension API unavailable (not running in a browser extension).");
  }
  return api;
}

/** @returns {boolean} true when running under Firefox (native `browser` global). */
export function isFirefox() {
  return typeof globalThis.browser !== "undefined" && !!globalThis.browser.runtime;
}
