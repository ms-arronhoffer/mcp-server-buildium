import { describe, expect, it, vi } from "vitest";
import { ArtifactUrlStore } from "../src/artifactUrls.js";

describe("ArtifactUrlStore", () => {
  it("tracks created URLs and revokes all", () => {
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:test"),
      revokeObjectURL,
    });
    vi.stubGlobal("atob", vi.fn(() => "ABC"));

    const store = new ArtifactUrlStore();
    const mapped = store.add({ name: "x.csv", media_type: "text/csv", data: "QUJD" });
    expect(mapped.url).toBe("blob:test");
    store.revokeAll();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:test");
    vi.unstubAllGlobals();
  });
});
