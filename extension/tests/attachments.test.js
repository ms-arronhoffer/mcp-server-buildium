import { describe, expect, it, vi } from "vitest";
import {
  ALLOWED_MEDIA_TYPES,
  dataUrlToBase64,
  fileToAttachment,
  guessMediaType,
  validateFile,
} from "../src/attachments.js";

describe("guessMediaType", () => {
  it("uses the browser-provided type when present", () => {
    expect(guessMediaType({ name: "x.pdf", type: "application/pdf" })).toBe("application/pdf");
  });

  it("strips media-type parameters", () => {
    expect(guessMediaType({ name: "x.txt", type: "text/plain; charset=utf-8" })).toBe(
      "text/plain",
    );
  });

  it("falls back to the file extension (e.g. DOCX)", () => {
    expect(guessMediaType({ name: "lease.docx", type: "" })).toBe(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    );
    expect(guessMediaType({ name: "photo.JPG", type: "" })).toBe("image/jpeg");
  });

  it("returns empty string for unknown files", () => {
    expect(guessMediaType({ name: "mystery", type: "" })).toBe("");
  });
});

describe("validateFile", () => {
  it("accepts supported types within the size cap", () => {
    expect(validateFile({ name: "a.pdf", type: "application/pdf", size: 100 })).toBeNull();
  });

  it("rejects unsupported types", () => {
    const err = validateFile({ name: "a.exe", type: "application/octet-stream", size: 1 });
    expect(err).toMatch(/unsupported/i);
  });

  it("rejects files over the size cap", () => {
    const err = validateFile({ name: "a.pdf", type: "application/pdf", size: 100 }, 10);
    expect(err).toMatch(/too large/i);
  });
});

describe("dataUrlToBase64", () => {
  it("strips the data-url prefix", () => {
    expect(dataUrlToBase64("data:text/plain;base64,aGk=")).toBe("aGk=");
  });

  it("returns the input unchanged when there is no comma", () => {
    expect(dataUrlToBase64("aGk=")).toBe("aGk=");
  });
});

describe("fileToAttachment", () => {
  it("reads a file into { name, media_type, data }", async () => {
    // Stub a minimal FileReader that resolves a data URL.
    class FakeReader {
      readAsDataURL() {
        this.result = "data:text/plain;base64,aGVsbG8=";
        this.onload();
      }
    }
    vi.stubGlobal("FileReader", FakeReader);
    const att = await fileToAttachment({ name: "note.txt", type: "text/plain" });
    expect(att).toEqual({ name: "note.txt", media_type: "text/plain", data: "aGVsbG8=" });
    vi.unstubAllGlobals();
  });
});

describe("ALLOWED_MEDIA_TYPES", () => {
  it("includes DOCX and PDF", () => {
    expect(ALLOWED_MEDIA_TYPES).toContain("application/pdf");
    expect(ALLOWED_MEDIA_TYPES).toContain(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    );
  });
});
