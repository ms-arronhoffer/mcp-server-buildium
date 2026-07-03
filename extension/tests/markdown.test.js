import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "../src/markdown.js";

describe("parseInline", () => {
  it("returns plain text as a single token", () => {
    expect(parseInline("hello world")).toEqual([{ type: "text", value: "hello world" }]);
  });

  it("parses bold and inline code", () => {
    expect(parseInline("a **bold** and `code` bit")).toEqual([
      { type: "text", value: "a " },
      { type: "bold", value: "bold" },
      { type: "text", value: " and " },
      { type: "code", value: "code" },
      { type: "text", value: " bit" },
    ]);
  });

  it("parses external links as safe anchors", () => {
    expect(parseInline("see [docs](https://example.com)")).toEqual([
      { type: "text", value: "see " },
      { type: "link", label: "docs", href: "https://example.com" },
    ]);
  });

  it("parses action links and decodes the prompt", () => {
    const tokens = parseInline("[Lease 1](action:Show%20details%20for%20lease%201)");
    expect(tokens).toEqual([
      { type: "action", label: "Lease 1", prompt: "Show details for lease 1" },
    ]);
  });

  it("keeps unsafe link schemes as inert text", () => {
    // A javascript: URL must never become a clickable anchor/action.
    const tokens = parseInline("[x](javascript:alert(1))");
    expect(tokens.some((t) => t.type === "link" || t.type === "action")).toBe(false);
    expect(tokens.map((t) => t.value).join("")).toContain("x");
  });
});

describe("parseMarkdown", () => {
  it("parses headings and paragraphs", () => {
    const blocks = parseMarkdown("# Title\n\nSome text.");
    expect(blocks[0]).toMatchObject({ type: "heading", level: 1 });
    expect(blocks[1]).toMatchObject({ type: "paragraph" });
  });

  it("marks list items containing an action link as clickable rows", () => {
    const blocks = parseMarkdown(
      "- [Lease 1](action:Show details for lease 1)\n- plain item",
    );
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type).toBe("list");
    expect(blocks[0].items[0].action).toBe("Show details for lease 1");
    expect(blocks[0].items[1].action).toBeNull();
  });

  it("parses a GitHub-style table and extracts a per-row action", () => {
    const md = [
      "| Lease | Unit |",
      "| --- | --- |",
      "| [1](action:Show lease 1) | 101 |",
      "| 2 | 102 |",
    ].join("\n");
    const blocks = parseMarkdown(md);
    expect(blocks).toHaveLength(1);
    const table = blocks[0];
    expect(table.type).toBe("table");
    expect(table.header).toHaveLength(2);
    expect(table.rows[0].action).toBe("Show lease 1");
    expect(table.rows[1].action).toBeNull();
  });

  it("parses fenced code blocks verbatim", () => {
    const blocks = parseMarkdown("```\nline1\nline2\n```");
    expect(blocks[0]).toEqual({ type: "code", value: "line1\nline2" });
  });

  it("handles empty input", () => {
    expect(parseMarkdown("")).toEqual([]);
    expect(parseMarkdown(null)).toEqual([]);
  });
});
