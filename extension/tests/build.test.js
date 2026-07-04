import { describe, it, expect } from "vitest";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { mergeManifest } from "../scripts/build.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const srcDir = join(__dirname, "..", "src");

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

describe("mergeManifest", () => {
  it("unions permissions so platform manifests extend the base ones", () => {
    const base = { permissions: ["storage", "identity", "alarms", "notifications"] };
    const overrides = { permissions: ["storage", "identity", "sidePanel"] };
    const merged = mergeManifest(base, overrides);
    expect(merged.permissions).toEqual([
      "storage",
      "identity",
      "alarms",
      "notifications",
      "sidePanel",
    ]);
  });

  it("keeps base permissions when a platform manifest omits the key", () => {
    const base = { permissions: ["storage", "alarms"] };
    const merged = mergeManifest(base, {});
    expect(merged.permissions).toEqual(["storage", "alarms"]);
  });

  it("replaces non-permission keys with the platform value (shallow)", () => {
    const base = { background: { scripts: ["a.js"] }, name: "base" };
    const overrides = { background: { service_worker: "b.js" }, name: "chrome" };
    const merged = mergeManifest(base, overrides);
    expect(merged.background).toEqual({ service_worker: "b.js" });
    expect(merged.name).toBe("chrome");
  });

  it("gives the real Chrome build the alarms and notifications permissions the service worker needs", async () => {
    const base = await readJson(join(srcDir, "manifest.base.json"));
    const chrome = await readJson(join(srcDir, "manifest.chrome.json"));
    const merged = mergeManifest(base, chrome);
    expect(merged.permissions).toContain("alarms");
    expect(merged.permissions).toContain("notifications");
    expect(merged.permissions).toContain("sidePanel");
  });
});
