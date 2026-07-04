/**
 * Minimal, dependency-free Markdown renderer for assistant messages.
 *
 * Supports the subset the assistant emits: headings, paragraphs, bold, inline
 * code, bullet/numbered lists, GitHub-style tables, and links. Two link flavours
 * are recognised:
 *
 *  - External links `[text](https://…)` render as safe anchors.
 *  - Action links `[text](action:<lookup request>)` render as clickable controls
 *    that invoke `onAction(prompt)`. When a list item or table row contains an
 *    action link, the whole row becomes clickable and triggers that lookup, so a
 *    user can drill into a specific record (e.g. a single lease) from a list.
 *
 * Parsing (`parseMarkdown`, `parseInline`) is pure and unit-tested; DOM building
 * (`renderMarkdown`) is a thin layer over it and is XSS-safe because every value
 * is inserted via `textContent`/DOM APIs rather than `innerHTML`.
 */

const ACTION_SCHEME = "action:";
// URL schemes we are willing to turn into real anchors. Anything else (notably
// `javascript:`) is rendered as inert text to avoid script injection.
const SAFE_LINK_RE = /^(https?:|mailto:)/i;

/** Decode the prompt carried by an `action:` link. */
function decodeAction(raw) {
  const value = raw.trim();
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/**
 * Parse a single line of inline Markdown into tokens.
 * @param {string} text
 * @returns {Array<object>} inline tokens
 */
export function parseInline(text) {
  const tokens = [];
  let buf = "";
  let i = 0;

  const flush = () => {
    if (buf) {
      tokens.push({ type: "text", value: buf });
      buf = "";
    }
  };

  const rules = [
    {
      re: /^\[([^\]]+)\]\(([^)]+)\)/,
      make: (m) => {
        const href = m[2].trim();
        if (href.toLowerCase().startsWith(ACTION_SCHEME)) {
          return {
            type: "action",
            label: m[1],
            prompt: decodeAction(href.slice(ACTION_SCHEME.length)),
          };
        }
        if (SAFE_LINK_RE.test(href)) return { type: "link", label: m[1], href };
        // Unsupported/unsafe scheme: keep the label as plain text.
        return { type: "text", value: m[1] };
      },
    },
    // Bold content is parsed recursively so nested links/action links keep
    // working (the assistant often emits `**[Label](action:…)**`).
    { re: /^\*\*([^*]+)\*\*/, make: (m) => ({ type: "bold", children: parseInline(m[1]) }) },
    { re: /^`([^`]+)`/, make: (m) => ({ type: "code", value: m[1] }) },
  ];

  while (i < text.length) {
    const rest = text.slice(i);
    let hit = null;
    for (const rule of rules) {
      const m = rule.re.exec(rest);
      if (m) {
        hit = { len: m[0].length, token: rule.make(m) };
        break;
      }
    }
    if (hit) {
      flush();
      tokens.push(hit.token);
      i += hit.len;
    } else {
      buf += text[i];
      i += 1;
    }
  }
  flush();
  return tokens;
}

/** Return the first action prompt found among inline tokens, or null. */
function firstAction(tokens) {
  for (const t of tokens) {
    if (t.type === "action") return t.prompt;
    // Action links may be nested inside bold, e.g. `**[Label](action:…)**`.
    if (t.children) {
      const nested = firstAction(t.children);
      if (nested) return nested;
    }
  }
  return null;
}

/** True when a table separator row like `| --- | :--: |` is present. */
function isTableSeparator(line) {
  return /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(line) && line.includes("-");
}

/** Split a Markdown table row into trimmed cell strings. */
function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

/**
 * Parse Markdown text into an array of block tokens. Pure.
 * @param {string} md
 * @returns {Array<object>} block tokens
 */
export function parseMarkdown(md) {
  const lines = String(md ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;

  const isUl = (l) => /^\s*[-*]\s+/.test(l);
  const isOl = (l) => /^\s*\d+\.\s+/.test(l);

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") {
      i += 1;
      continue;
    }

    // Fenced code block.
    const fence = /^\s*```(.*)$/.exec(line);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // consume closing fence (if any)
      blocks.push({ type: "code", value: body.join("\n") });
      continue;
    }

    // Heading.
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({
        type: "heading",
        level: heading[1].length,
        inline: parseInline(heading[2].trim()),
      });
      i += 1;
      continue;
    }

    // Table: a `|` row immediately followed by a separator row.
    if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const header = splitRow(line).map(parseInline);
      i += 2; // skip header + separator
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        const cells = splitRow(lines[i]).map(parseInline);
        rows.push({ cells, action: firstAction(cells.flat()) });
        i += 1;
      }
      blocks.push({ type: "table", header, rows });
      continue;
    }

    // Lists.
    if (isUl(line) || isOl(line)) {
      const ordered = isOl(line);
      const items = [];
      while (i < lines.length && (ordered ? isOl(lines[i]) : isUl(lines[i]))) {
        const text = lines[i].replace(ordered ? /^\s*\d+\.\s+/ : /^\s*[-*]\s+/, "");
        const inline = parseInline(text);
        items.push({ inline, action: firstAction(inline) });
        i += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    // Paragraph: gather consecutive plain lines.
    const para = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !isUl(lines[i]) &&
      !isOl(lines[i]) &&
      !/^\s*```/.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push({ type: "paragraph", inline: parseInline(para.join("\n")) });
  }

  return blocks;
}

// --- DOM rendering ---------------------------------------------------------

/** Append inline tokens to `parent`, wiring action controls to `onAction`. */
function renderInline(tokens, parent, onAction) {
  for (const t of tokens) {
    if (t.type === "text") {
      parent.appendChild(document.createTextNode(t.value));
    } else if (t.type === "bold") {
      const el = document.createElement("strong");
      renderInline(t.children, el, onAction);
      parent.appendChild(el);
    } else if (t.type === "code") {
      const el = document.createElement("code");
      el.textContent = t.value;
      parent.appendChild(el);
    } else if (t.type === "link") {
      const a = document.createElement("a");
      a.href = t.href;
      a.textContent = t.label;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      parent.appendChild(a);
    } else if (t.type === "action") {
      const a = document.createElement("a");
      a.className = "action-link";
      a.setAttribute("role", "button");
      a.tabIndex = 0;
      a.textContent = t.label;
      const fire = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (onAction) onAction(t.prompt);
      };
      a.addEventListener("click", fire);
      a.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") fire(e);
      });
      parent.appendChild(a);
    }
  }
}

/** Mark an element as a clickable row that triggers `prompt` via `onAction`. */
function makeRowClickable(el, prompt, onAction) {
  el.classList.add("clickable");
  el.setAttribute("role", "button");
  el.tabIndex = 0;
  const fire = (e) => {
    e.preventDefault();
    if (onAction) onAction(prompt);
  };
  el.addEventListener("click", fire);
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") fire(e);
  });
}

/**
 * Render Markdown text into a DocumentFragment.
 * @param {string} text
 * @param {(prompt:string)=>void} [onAction] invoked when an action link/row is clicked
 * @returns {DocumentFragment}
 */
export function renderMarkdown(text, onAction) {
  const frag = document.createDocumentFragment();
  for (const block of parseMarkdown(text)) {
    if (block.type === "heading") {
      const el = document.createElement(`h${Math.min(block.level, 6)}`);
      renderInline(block.inline, el, onAction);
      frag.appendChild(el);
    } else if (block.type === "paragraph") {
      const el = document.createElement("p");
      renderInline(block.inline, el, onAction);
      frag.appendChild(el);
    } else if (block.type === "code") {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = block.value;
      pre.appendChild(code);
      frag.appendChild(pre);
    } else if (block.type === "list") {
      const list = document.createElement(block.ordered ? "ol" : "ul");
      for (const item of block.items) {
        const li = document.createElement("li");
        renderInline(item.inline, li, onAction);
        if (item.action) makeRowClickable(li, item.action, onAction);
        list.appendChild(li);
      }
      frag.appendChild(list);
    } else if (block.type === "table") {
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const htr = document.createElement("tr");
      for (const cell of block.header) {
        const th = document.createElement("th");
        renderInline(cell, th, onAction);
        htr.appendChild(th);
      }
      thead.appendChild(htr);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      for (const row of block.rows) {
        const tr = document.createElement("tr");
        for (const cell of row.cells) {
          const td = document.createElement("td");
          renderInline(cell, td, onAction);
          tr.appendChild(td);
        }
        if (row.action) makeRowClickable(tr, row.action, onAction);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      frag.appendChild(table);
    }
  }
  return frag;
}
