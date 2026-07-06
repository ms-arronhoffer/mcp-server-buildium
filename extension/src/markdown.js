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
 *
 * `parseInline` also repairs a few malformed action/link shapes the assistant
 * occasionally emits (e.g. `**[label]**(action:…)`) so they render as clickable
 * controls instead of leaking the raw `action:` markup to the user. Links and
 * action links are matched with a linear balanced-delimiter scanner (`scanLink`)
 * rather than a regex, so labels/targets with arbitrarily deep nesting — and
 * targets with an unbalanced trailing `(` — are captured whole instead of
 * leaking, with no risk of catastrophic regex backtracking.
 */

const ACTION_SCHEME = "action:";
// URL schemes we are willing to turn into real anchors. Anything else (notably
// `javascript:`) is rendered as inert text to avoid script injection.
const SAFE_LINK_RE = /^(https?:|mailto:)/i;
// Link target schemes we know how to render (action controls or safe anchors).
// Used only to *repair* malformed markup, so it must not be broadened to unsafe
// schemes such as `javascript:`.
const LINK_TARGET_SCHEME = "action:|https?:|mailto:";
// Grammar for the visible `[label]` of a link/action, allowing up to two levels
// of *balanced* square brackets so labels that themselves carry a bracketed
// qualifier — e.g. `[Maplewood Apartments [Bldg A]]` or `[Unit 101 [Riverside
// Commons]]` — are captured whole instead of truncating at the first `]` and
// leaking the raw `[label](action:…)` markup to the user. Mirrors the balanced
// parenthesis handling used for the link target.
const LABEL_INNER = "(?:[^\\[\\]]|\\[(?:[^\\[\\]]|\\[[^\\[\\]]*\\])*\\])+";

/**
 * Repair link/action markup that the assistant sometimes emits in a shape that
 * breaks the `[label](target)` grammar, which would otherwise leak the raw
 * `[label](action:…)` text to the user instead of a clickable control.
 *
 * Three shapes are normalised:
 *  - `**[label]**(target)`   → `**[label](target)**`  (bold closed before target)
 *  - `**[label]** (target)`  → `**[label](target)**`  (as above, with a space)
 *  - `[label] (target)`      → `[label](target)`        (stray space before target)
 *
 * Only targets using a scheme we already render (`action:`, `http(s):`, `mailto:`)
 * are touched, so ordinary prose like `see item] (foo)` is left untouched.
 */
function repairLinkMarkup(text) {
  const boldSplit = new RegExp(
    `\\*\\*(\\[${LABEL_INNER}\\])\\*\\*\\s*(\\((?:${LINK_TARGET_SCHEME})[^)]*\\))`,
    "gi",
  );
  const spacedTarget = new RegExp(`\\]\\s+(\\((?:${LINK_TARGET_SCHEME}))`, "gi");
  return text.replace(boldSplit, "**$1$2**").replace(spacedTarget, "]$1");
}

/** Decode the prompt carried by an `action:` link. */
function decodeAction(raw) {
  const value = raw.trim();
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

// Recognised link-target schemes, used by the scanner to decide whether an
// *unbalanced* target (a stray `(` with no matching `)`) is worth tolerating.
// Mirrors LINK_TARGET_SCHEME but as a real anchored regex.
const RENDERABLE_SCHEME_RE = new RegExp(`^\\s*(?:${LINK_TARGET_SCHEME})`, "i");

/**
 * Scan a delimited, possibly-nested span starting at `open` and return the index
 * just past its matching `close`, or -1 when the span is never closed. Depth is
 * tracked so nested pairs (e.g. `[a [b] c]` or `(a (b) c)`) are matched whole,
 * with no fixed nesting limit. Linear in the length scanned, so it cannot exhibit
 * the catastrophic backtracking a nested-quantifier regex would on unbalanced
 * input.
 * @param {string} text
 * @param {number} start index of the opening delimiter (`text[start] === open`)
 * @param {string} open opening delimiter character
 * @param {string} close closing delimiter character
 * @returns {number} index just past the matching close, or -1 if unbalanced
 */
function scanBalanced(text, start, open, close) {
  let depth = 0;
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === open) depth += 1;
    else if (ch === close) {
      depth -= 1;
      if (depth === 0) return i + 1;
    }
  }
  return -1;
}

/**
 * Attempt to parse a `[label](target)` link/action starting at `text[start]`
 * (which must be `[`). Returns `{ len, token }` on success or `null` when the
 * text at `start` is not a link.
 *
 * Unlike a regex, this scanner matches labels and targets with *unbounded*
 * balanced nesting, and — for targets that use a scheme we render (`action:`,
 * `http(s):`, `mailto:`) — tolerates an *unbalanced* trailing `(` by consuming
 * to the end of the segment. That keeps a stray parenthesis in a natural-language
 * action prompt (e.g. `action:Show property 2 (Bldg A`) from leaking the raw
 * `[label](action:…)` markup to the user. Unsafe/unknown schemes are still
 * rendered as inert text.
 */
function scanLink(text, start) {
  if (text[start] !== "[") return null;
  const labelEnd = scanBalanced(text, start, "[", "]"); // index past matching ']'
  if (labelEnd < 0 || text[labelEnd] !== "(") return null;
  const label = text.slice(start + 1, labelEnd - 1);

  let target;
  let end;
  const targetOpen = labelEnd + 1;
  const targetClose = scanBalanced(text, labelEnd, "(", ")");
  if (targetClose >= 0) {
    target = text.slice(targetOpen, targetClose - 1);
    end = targetClose;
  } else if (RENDERABLE_SCHEME_RE.test(text.slice(targetOpen))) {
    // Unbalanced target, but it clearly opens a renderable scheme: consume the
    // rest so the control still renders instead of leaking raw markup. Gated on
    // the scheme so ordinary prose like `see [x](note` is left untouched.
    target = text.slice(targetOpen);
    end = text.length;
  } else {
    return null;
  }

  const href = target.trim();
  let token;
  if (href.toLowerCase().startsWith(ACTION_SCHEME)) {
    token = { type: "action", label, prompt: decodeAction(href.slice(ACTION_SCHEME.length)) };
  } else if (SAFE_LINK_RE.test(href)) {
    token = { type: "link", label, href };
  } else {
    // Unsupported/unsafe scheme: keep the label as plain text (never a link).
    token = { type: "text", value: label };
  }
  return { len: end - start, token };
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
  text = repairLinkMarkup(String(text ?? ""));

  const flush = () => {
    if (buf) {
      tokens.push({ type: "text", value: buf });
      buf = "";
    }
  };

  const rules = [
    // Bold content is parsed recursively so nested links/action links keep
    // working (the assistant often emits `**[Label](action:…)**`).
    { re: /^\*\*([^*]+)\*\*/, make: (m) => ({ type: "bold", children: parseInline(m[1]) }) },
    { re: /^`([^`]+)`/, make: (m) => ({ type: "code", value: m[1] }) },
  ];

  while (i < text.length) {
    const rest = text.slice(i);
    let hit = null;
    // Links/actions are scanned (not regex-matched) so labels and targets with
    // arbitrarily deep balanced brackets/parens — and targets with an unbalanced
    // trailing `(` — are captured whole instead of leaking the raw markup.
    if (text[i] === "[") {
      const link = scanLink(text, i);
      if (link) hit = { len: link.len, token: link.token };
    }
    if (!hit) {
      for (const rule of rules) {
        const m = rule.re.exec(rest);
        if (m) {
          hit = { len: m[0].length, token: rule.make(m) };
          break;
        }
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
