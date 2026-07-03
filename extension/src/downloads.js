/**
 * Client-side helpers for presenting assistant-generated files as downloads.
 *
 * The server may stream `artifact` events — files the assistant generated for
 * the user (a CSV of leases, a slide deck of top properties, …) — each carrying
 * `{ name, media_type, data }` where `data` is base64. These helpers turn an
 * artifact into a downloadable object URL the side panel renders as a link.
 *
 * `base64ToBytes` and `formatBytes` are pure and unit-tested; `artifactToBlobUrl`
 * wraps the browser `Blob`/`URL` APIs.
 */

/**
 * Decode a base64 string into a `Uint8Array` of raw bytes.
 * @param {string} b64
 * @returns {Uint8Array}
 */
export function base64ToBytes(b64) {
  const clean = String(b64 || "").trim();
  const binary = atob(clean);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/**
 * Format a byte count as a short human-readable size (e.g. "12.3 KB").
 * @param {number} bytes
 * @returns {string}
 */
export function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

/**
 * Build an object URL for an artifact so it can be offered as a download.
 * The caller is responsible for revoking the returned URL when done.
 * @param {{name?:string, media_type?:string, data:string}} artifact
 * @returns {{url:string, name:string, mediaType:string}}
 */
export function artifactToBlobUrl(artifact) {
  const bytes = base64ToBytes(artifact.data);
  const mediaType = artifact.media_type || "application/octet-stream";
  const blob = new Blob([bytes], { type: mediaType });
  return {
    url: URL.createObjectURL(blob),
    name: artifact.name || "download",
    mediaType,
  };
}
