import { formatBytes } from "./downloads.js";

export function renderDownloads(messagesEl, el, artifacts, artifactUrls, onError) {
  if (!artifacts || artifacts.length === 0) return;
  const container = document.createElement("div");
  container.className = "downloads";
  for (const artifact of artifacts) {
    try {
      const { url, name } = artifactUrls.add(artifact);
      const link = document.createElement("a");
      link.className = "download-link";
      link.href = url;
      link.download = name;
      link.textContent = `⬇ ${name}`;
      link.addEventListener("click", () => {
        window.setTimeout(() => artifactUrls.revoke(url), 60_000);
      });
      if (typeof artifact.size === "number") {
        const size = document.createElement("span");
        size.className = "download-size";
        size.textContent = ` (${formatBytes(artifact.size)})`;
        link.appendChild(size);
      }
      container.appendChild(link);
    } catch {
      onError(`Could not prepare ${artifact.name || "file"} for download.`, true);
    }
  }
  el.appendChild(container);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
