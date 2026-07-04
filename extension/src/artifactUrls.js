import { artifactToBlobUrl } from "./downloads.js";

/** Manage object URL lifecycles for assistant-generated artifacts. */
export class ArtifactUrlStore {
  constructor() {
    this.urls = new Set();
  }

  add(artifact) {
    const mapped = artifactToBlobUrl(artifact);
    this.urls.add(mapped.url);
    return mapped;
  }

  revoke(url) {
    if (!url || !this.urls.has(url)) return;
    URL.revokeObjectURL(url);
    this.urls.delete(url);
  }

  revokeAll() {
    for (const url of this.urls) URL.revokeObjectURL(url);
    this.urls.clear();
  }
}
