/**
 * Client-side helpers for attaching documents to a chat message.
 *
 * The side panel lets the user attach a document (a lease PDF, an image, a
 * DOCX, …) which the server-side assistant reads to extract fields and create
 * Buildium records. These helpers validate a picked file and read it into the
 * `{ name, media_type, data }` shape (base64 `data`) the server expects.
 *
 * The pure helpers (`guessMediaType`, `validateFile`, `dataUrlToBase64`) are
 * unit-tested; `fileToAttachment` wraps the browser `FileReader`.
 */

/** Media types the server accepts (must match the server allow-list). */
export const ALLOWED_MEDIA_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "image/png",
  "image/jpeg",
  "image/webp",
  "text/plain",
  "text/csv",
  "text/markdown",
];

/** Default maximum size of a single attachment (10 MB), mirroring the server. */
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

/** Map a lower-case file extension to a media type when the browser omits one. */
const EXTENSION_MEDIA_TYPES = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  txt: "text/plain",
  csv: "text/csv",
  md: "text/markdown",
};

/**
 * Determine a file's media type, falling back to its extension when the browser
 * does not provide one (common for DOCX). Returns "" when unknown.
 * @param {{name?:string, type?:string}} file
 * @returns {string}
 */
export function guessMediaType(file) {
  const type = (file.type || "").split(";")[0].trim().toLowerCase();
  if (type) return type;
  const name = file.name || "";
  const dot = name.lastIndexOf(".");
  if (dot === -1) return "";
  const ext = name.slice(dot + 1).toLowerCase();
  return EXTENSION_MEDIA_TYPES[ext] || "";
}

/**
 * Validate a picked file against the media-type allow-list and size cap.
 * @param {{name?:string, type?:string, size?:number}} file
 * @param {number} [maxBytes]
 * @returns {string|null} an error message, or null when the file is acceptable
 */
export function validateFile(file, maxBytes = MAX_ATTACHMENT_BYTES) {
  const mediaType = guessMediaType(file);
  if (!ALLOWED_MEDIA_TYPES.includes(mediaType)) {
    return `${file.name || "File"}: unsupported type. Attach a PDF, image, text, or DOCX.`;
  }
  if (typeof file.size === "number" && file.size > maxBytes) {
    const mb = Math.round(maxBytes / (1024 * 1024));
    return `${file.name || "File"} is too large (max ${mb} MB).`;
  }
  return null;
}

/**
 * Extract the base64 payload from a `data:` URL produced by FileReader.
 * @param {string} dataUrl
 * @returns {string}
 */
export function dataUrlToBase64(dataUrl) {
  const comma = String(dataUrl).indexOf(",");
  return comma === -1 ? String(dataUrl) : dataUrl.slice(comma + 1);
}

/**
 * Read a File into the `{ name, media_type, data }` attachment shape.
 * @param {File} file
 * @returns {Promise<{name:string, media_type:string, data:string}>}
 */
export function fileToAttachment(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
    reader.onload = () => {
      resolve({
        name: file.name,
        media_type: guessMediaType(file),
        data: dataUrlToBase64(reader.result),
      });
    };
    reader.readAsDataURL(file);
  });
}
