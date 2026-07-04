import { getApi } from "./browser.js";

const DEFAULT_WINDOW_MS = 30_000;
const MAX_KEY_LEN = 200;

/**
 * Multi-channel notifications with lightweight dedupe/rate-limiting.
 * Channels: banner, chat system message, and optional browser notifications.
 */
export class NotificationCenter {
  /**
   * @param {{showBanner?:(text:string,isError?:boolean)=>void,addSystemMessage?:(text:string)=>void}} handlers
   * @param {{windowMs?:number}} [opts]
   */
  constructor(handlers, opts = {}) {
    this.handlers = handlers || {};
    this.windowMs = opts.windowMs ?? DEFAULT_WINDOW_MS;
    this.seen = new Map();
    this.api = getApi();
  }

  notify(event) {
    const now = Date.now();
    const fallback = `${event?.type || "system"}:${event?.message || ""}`.slice(0, MAX_KEY_LEN);
    const key = String(event?.dedupeKey || fallback);
    const prev = this.seen.get(key) || 0;
    if (now - prev < this.windowMs) return false;
    this.seen.set(key, now);

    const message = event?.message || "Notification";
    const severity = event?.severity || "info";
    const channels = event?.channels || {};

    if (channels.banner !== false && this.handlers.showBanner) {
      this.handlers.showBanner(message, severity === "error");
    }
    if (channels.chat && this.handlers.addSystemMessage) {
      this.handlers.addSystemMessage(message);
    }
    if (channels.browser) {
      this._notifyBrowser(event?.title || "Portfolio Desk AI Assistant", message);
    }
    return true;
  }

  clearExpired() {
    const cutoff = Date.now() - this.windowMs;
    for (const [key, ts] of this.seen.entries()) {
      if (ts < cutoff) this.seen.delete(key);
    }
  }

  _notifyBrowser(title, message) {
    try {
      if (!this.api.notifications?.create) return;
      const id = `buildium-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      this.api.notifications.create(id, {
        type: "basic",
        iconUrl: "icons/icon-48.png",
        title,
        message,
      });
    } catch {
      // Non-fatal.
    }
  }
}
