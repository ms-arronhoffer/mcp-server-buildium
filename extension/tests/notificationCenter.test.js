import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationCenter } from "../src/notificationCenter.js";

describe("NotificationCenter", () => {
  beforeEach(() => {
    globalThis.chrome = {
      notifications: { create: vi.fn() },
    };
  });

  it("routes to configured channels", () => {
    const showBanner = vi.fn();
    const addSystemMessage = vi.fn();
    const center = new NotificationCenter({ showBanner, addSystemMessage }, { windowMs: 50_000 });
    center.notify({
      type: "digest",
      message: "Digest ready",
      channels: { banner: true, chat: true, browser: true },
    });
    expect(showBanner).toHaveBeenCalledWith("Digest ready", false);
    expect(addSystemMessage).toHaveBeenCalledWith("Digest ready");
    expect(globalThis.chrome.notifications.create).toHaveBeenCalledTimes(1);
  });

  it("dedupes repeated notifications in the suppression window", () => {
    const showBanner = vi.fn();
    const center = new NotificationCenter({ showBanner }, { windowMs: 60_000 });
    const event = { type: "system", message: "Duplicate", dedupeKey: "same-key", channels: { banner: true } };
    expect(center.notify(event)).toBe(true);
    expect(center.notify(event)).toBe(false);
    expect(showBanner).toHaveBeenCalledTimes(1);
  });
});
