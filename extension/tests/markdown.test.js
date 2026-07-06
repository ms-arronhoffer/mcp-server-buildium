import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "../src/markdown.js";

describe("parseInline", () => {
  it("returns plain text as a single token", () => {
    expect(parseInline("hello world")).toEqual([{ type: "text", value: "hello world" }]);
  });

  it("parses bold and inline code", () => {
    expect(parseInline("a **bold** and `code` bit")).toEqual([
      { type: "text", value: "a " },
      { type: "bold", children: [{ type: "text", value: "bold" }] },
      { type: "text", value: " and " },
      { type: "code", value: "code" },
      { type: "text", value: " bit" },
    ]);
  });

  it("parses an action link nested inside bold", () => {
    // The assistant commonly emits `**[Label](action:…)**`; the link inside the
    // bold must still be tokenized as an action rather than shown as literal text.
    const tokens = parseInline("**[Task 11](action:Show full details for task 11)**");
    expect(tokens).toEqual([
      {
        type: "bold",
        children: [
          { type: "action", label: "Task 11", prompt: "Show full details for task 11" },
        ],
      },
    ]);
  });

  it("repairs bold markers that split an action link", () => {
    // The assistant sometimes emits the bold markers *inside* the link boundary,
    // e.g. `**[Label]**(action:…)` or `**[Label] (action:…)**`, which breaks the
    // `[label](target)` grammar and would otherwise leak the raw `action:` text.
    const expected = [
      {
        type: "bold",
        children: [
          { type: "action", label: "open work orders", prompt: "List work orders" },
        ],
      },
    ];
    expect(parseInline("**[open work orders]**(action:List work orders)")).toEqual(expected);
    expect(parseInline("**[open work orders]** (action:List work orders)")).toEqual(expected);
    expect(parseInline("**[open work orders] (action:List work orders)**")).toEqual(expected);
  });

  it("repairs a stray space between an action label and its target", () => {
    expect(parseInline("[Work Order 2] (action:Get details for work order 2)")).toEqual([
      { type: "action", label: "Work Order 2", prompt: "Get details for work order 2" },
    ]);
  });

  it("does not turn a split unsafe scheme into a link", () => {
    // Repairing malformed markup must never resurrect a `javascript:` target.
    const tokens = parseInline("**[x]** (javascript:alert(1))");
    expect(tokens.some((t) => t.type === "action" || t.type === "link")).toBe(false);
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

  it("parses an action link whose label contains parentheses", () => {
    // The assistant emits task rows like `[Service disposal (Due: 2026-01-20)]
    // (action:Show full details for task 5)`; parentheses in the visible label
    // must not break the `[label](target)` grammar.
    const tokens = parseInline(
      "**[Service garbage disposal (Due: 2026-01-20)](action:Show full details for task 5)**",
    );
    expect(tokens).toEqual([
      {
        type: "bold",
        children: [
          {
            type: "action",
            label: "Service garbage disposal (Due: 2026-01-20)",
            prompt: "Show full details for task 5",
          },
        ],
      },
    ]);
  });

  it("keeps parentheses inside an action prompt instead of truncating", () => {
    // A natural-language action prompt may itself contain parentheses; the whole
    // prompt must be captured rather than cut off at the first `)`.
    const tokens = parseInline("[Lease 1](action:Show details for lease 1 (Unit 101))");
    expect(tokens).toEqual([
      { type: "action", label: "Lease 1", prompt: "Show details for lease 1 (Unit 101)" },
    ]);
  });

  it("keeps two levels of nested parentheses inside an action prompt", () => {
    // Property names may themselves be parenthesised (e.g. injected PropertyName
    // like "Riverside Commons (Bldg A)"), producing a doubly-nested prompt that
    // must still be captured whole instead of leaking raw `action:` text.
    const tokens = parseInline(
      "[Lease 12](action:Show lease 12 (Riverside Commons (Bldg A)))",
    );
    expect(tokens).toEqual([
      {
        type: "action",
        label: "Lease 12",
        prompt: "Show lease 12 (Riverside Commons (Bldg A))",
      },
    ]);
  });

  it("parses an action link whose label contains a bracketed qualifier", () => {
    // The assistant sometimes qualifies a label with a bracketed property or
    // building, e.g. `[Unit 101 [Riverside Commons]](action:…)`. Nested square
    // brackets must not break the `[label](target)` grammar and leak raw text.
    const tokens = parseInline(
      "[Unit 101 [Riverside Commons]](action:Show details for unit 5)",
    );
    expect(tokens).toEqual([
      {
        type: "action",
        label: "Unit 101 [Riverside Commons]",
        prompt: "Show details for unit 5",
      },
    ]);
  });

  it("parses a bold action link whose label contains a bracketed qualifier", () => {
    // The clickable-table repro: `**[Maplewood Apartments [Bldg A]](action:…)**`
    // rendered the whole thing as literal `[label](action:…)` text before the
    // label grammar allowed balanced nested brackets.
    const tokens = parseInline(
      "**[Maplewood Apartments [Bldg A]](action:Show property 1)**",
    );
    expect(tokens).toEqual([
      {
        type: "bold",
        children: [
          {
            type: "action",
            label: "Maplewood Apartments [Bldg A]",
            prompt: "Show property 1",
          },
        ],
      },
    ]);
  });

  it("keeps unsafe link schemes as inert text", () => {
    // A javascript: URL must never become a clickable anchor/action.
    const tokens = parseInline("[x](javascript:alert(1))");
    expect(tokens.some((t) => t.type === "link" || t.type === "action")).toBe(false);
    expect(tokens.map((t) => t.value).join("")).toContain("x");
  });

  it("tolerates an unbalanced parenthesis in an action target", () => {
    // A natural-language action prompt sometimes carries a stray '(' with no
    // closing ')', e.g. a property qualifier the model forgot to close. The link
    // must still render as a control instead of leaking the raw `[label](action:…)`
    // markup to the user.
    expect(parseInline("[X](action:Show details for property 2 (Bldg A)")).toEqual([
      { type: "action", label: "X", prompt: "Show details for property 2 (Bldg A)" },
    ]);
    expect(parseInline("[Owner](action:Email owner (a@b.com)")).toEqual([
      { type: "action", label: "Owner", prompt: "Email owner (a@b.com)" },
    ]);
  });

  it("does not tolerate an unbalanced parenthesis for a non-renderable target", () => {
    // Only recognised schemes justify swallowing an unbalanced target; ordinary
    // prose such as `see [x](note` must be left as literal text.
    expect(parseInline("see [x](note")).toEqual([{ type: "text", value: "see [x](note" }]);
  });

  it("captures a label nested more than two bracket levels deep", () => {
    // The previous depth-limited grammar leaked labels with three or more levels
    // of balanced brackets; the scanner has no fixed nesting limit.
    expect(parseInline("[A [B [C [D]]]](action:Show A)")).toEqual([
      { type: "action", label: "A [B [C [D]]]", prompt: "Show A" },
    ]);
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

  it("detects action links nested inside bold list items", () => {
    const blocks = parseMarkdown(
      "- **[Task 11](action:Show full details for task 11)** — high priority",
    );
    expect(blocks[0].type).toBe("list");
    expect(blocks[0].items[0].action).toBe("Show full details for task 11");
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
