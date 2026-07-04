/** Normalize extension/runtime errors into one shape for consistent UX. */
export function normalizeError(err, fallbackMessage = "Unexpected error.") {
  const message =
    err && typeof err.message === "string" && err.message.trim()
      ? err.message
      : fallbackMessage;
  const code = Number(err && err.code);
  if (code === 401 || /unauthorized|expired|sign-?in/i.test(message)) {
    return { type: "auth", severity: "error", message };
  }
  if (/network|fetch|failed|timeout|offline|connection/i.test(message)) {
    return { type: "connection", severity: "error", message };
  }
  return { type: "system", severity: "error", message };
}
