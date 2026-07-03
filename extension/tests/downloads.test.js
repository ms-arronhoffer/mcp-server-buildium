import { describe, expect, it, vi } from "vitest";
import { artifactToBlobUrl, base64ToBytes, formatBytes } from "../src/downloads.js";

describe("base64ToBytes", () => {
  it("decodes base64 into raw bytes", () => {
    // "ABC" -> bytes 65, 66, 67
    const bytes = base64ToBytes("QUJD");
    expect(Array.from(bytes)).toEqual([65, 66, 67]);
  });

  it("tolerates surrounding whitespace and empty input", () => {
    expect(Array.from(base64ToBytes("  QUJD \n"))).toEqual([65, 66, 67]);
    expect(Array.from(base64ToBytes(""))).toEqual([]);
  });
});

describe("formatBytes", () => {
  it("formats bytes, KB, and MB", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("defaults invalid input to 0 B", () => {
    expect(formatBytes(undefined)).toBe("0 B");
  });
});

describe("artifactToBlobUrl", () => {
  it("builds an object URL with the artifact name and media type", () => {
    const created = [];
    vi.stubGlobal("URL", {
      createObjectURL: (blob) => {
        created.push(blob);
        return "blob:mock";
      },
    });

    const result = artifactToBlobUrl({
      name: "leases.csv",
      media_type: "text/csv",
      data: "QUJD",
    });

    expect(result.url).toBe("blob:mock");
    expect(result.name).toBe("leases.csv");
    expect(result.mediaType).toBe("text/csv");
    expect(created[0].type).toBe("text/csv");
    vi.unstubAllGlobals();
  });

  it("falls back to a generic media type and name", () => {
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x" });
    const result = artifactToBlobUrl({ data: "QUJD" });
    expect(result.name).toBe("download");
    expect(result.mediaType).toBe("application/octet-stream");
    vi.unstubAllGlobals();
  });
});
