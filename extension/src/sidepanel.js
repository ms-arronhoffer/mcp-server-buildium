/**
 * Side panel controller: wires the chat UI to the LLM agent + MCP client.
 *
 * Responsibilities:
 *  - Load config; prompt to open settings when incomplete.
 *  - Manage Entra sign-in state and the connection indicator.
 *  - On send: append the user message, stream the assistant reply, and render
 *    any tool calls/results the agent performs.
 */

import { getApi } from "./browser.js";
import { getAccessToken, isSignedIn, signIn, signOut } from "./auth.js";
import { loadConfig, validateConfig } from "./config.js";
import { ChatClient } from "./llm.js";
import { renderMarkdown } from "./markdown.js";
import {
  MAX_ATTACHMENT_BYTES,
  fileToAttachment,
  validateFile,
} from "./attachments.js";
import { artifactToBlobUrl, formatBytes } from "./downloads.js";

const api = getApi();

const els = {
  messages: document.getElementById("messages"),
  form: document.getElementById("composer"),
  input: document.getElementById("input"),
  send: document.getElementById("send-btn"),
  signin: document.getElementById("signin-btn"),
  settings: document.getElementById("settings-btn"),
  connDot: document.getElementById("conn-dot"),
  banner: document.getElementById("status-banner"),
  attachBtn: document.getElementById("attach-btn"),
  fileInput: document.getElementById("file-input"),
  attachments: document.getElementById("attachments"),
};

/** @type {import('./config.js').ExtensionConfig|null} */
let config = null;
let chat = null;
let busy = false;
/** Chat history in OpenAI message format (user/assistant only). */
const history = [];
/**
 * Pending attachments picked for the next message.
 * @type {Array<{name:string, media_type:string, data:string}>}
 */
let pendingAttachments = [];

function showBanner(text, isError = false) {
  els.banner.textContent = text;
  els.banner.classList.toggle("error", isError);
  els.banner.classList.remove("hidden");
}

function hideBanner() {
  els.banner.classList.add("hidden");
}

function setConnection(state) {
  els.connDot.classList.remove("ok", "err");
  if (state === "ok") els.connDot.classList.add("ok");
  if (state === "err") els.connDot.classList.add("err");
}

function clearEmptyState() {
  const empty = els.messages.querySelector(".empty");
  if (empty) empty.remove();
}

function addMessage(role, text) {
  clearEmptyState();
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  els.messages.appendChild(div);
  els.messages.scrollTop = els.messages.scrollHeight;
  return div;
}

/** Render the pending-attachment chips (with remove buttons) below the input. */
function renderAttachmentChips() {
  els.attachments.textContent = "";
  els.attachments.classList.toggle("hidden", pendingAttachments.length === 0);
  pendingAttachments.forEach((att, index) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    const label = document.createElement("span");
    label.className = "chip-label";
    label.textContent = att.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "chip-remove";
    remove.title = "Remove attachment";
    remove.textContent = "✕";
    remove.addEventListener("click", () => {
      pendingAttachments.splice(index, 1);
      renderAttachmentChips();
    });
    chip.append(label, remove);
    els.attachments.appendChild(chip);
  });
}

/** Read and validate files picked via the attach button into pending attachments. */
async function addFiles(fileList) {
  const files = Array.from(fileList || []);
  for (const file of files) {
    const error = validateFile(file, MAX_ATTACHMENT_BYTES);
    if (error) {
      showBanner(error, true);
      continue;
    }
    try {
      pendingAttachments.push(await fileToAttachment(file));
    } catch {
      showBanner(`Could not read ${file.name}.`, true);
    }
  }
  renderAttachmentChips();
}

/**
 * Replace an assistant bubble's contents with rendered Markdown. Clicking an
 * action link/row sends a follow-up lookup for that specific record.
 */
function renderAssistantMarkdown(el, text) {
  el.textContent = "";
  el.classList.add("markdown");
  el.appendChild(renderMarkdown(text, (prompt) => handleSend(prompt)));
  els.messages.scrollTop = els.messages.scrollHeight;
}

/**
 * Append download links for assistant-generated files to a message element.
 * Each artifact is turned into an object URL and rendered as a download anchor.
 * @param {HTMLElement} el
 * @param {Array<{name?:string, media_type?:string, size?:number, data:string}>} artifacts
 */
function renderDownloads(el, artifacts) {
  if (!artifacts || artifacts.length === 0) return;
  const container = document.createElement("div");
  container.className = "downloads";
  for (const artifact of artifacts) {
    try {
      const { url, name } = artifactToBlobUrl(artifact);
      const link = document.createElement("a");
      link.className = "download-link";
      link.href = url;
      link.download = name;
      link.textContent = `⬇ ${name}`;
      if (typeof artifact.size === "number") {
        const size = document.createElement("span");
        size.className = "download-size";
        size.textContent = ` (${formatBytes(artifact.size)})`;
        link.appendChild(size);
      }
      container.appendChild(link);
    } catch {
      showBanner(`Could not prepare ${artifact.name || "file"} for download.`, true);
    }
  }
  el.appendChild(container);
  els.messages.scrollTop = els.messages.scrollHeight;
}

async function refreshSignInState() {
  const signedIn = await isSignedIn();
  els.signin.textContent = signedIn ? "Sign out" : "Sign in";
  setConnection(signedIn ? "ok" : null);
  return signedIn;
}

async function ensureReady() {
  config = await loadConfig();
  const errors = validateConfig(config);
  if (errors.length > 0) {
    showBanner("Configuration required — open settings (⚙). " + errors.join(" "), true);
    return false;
  }
  hideBanner();
  chat = new ChatClient(config, (opts) => getAccessToken(config, opts));
  await refreshSignInState();
  return true;
}

async function handleSend(text) {
  if (busy || (!text.trim() && pendingAttachments.length === 0)) return;
  if (!(await ensureReady())) return;

  busy = true;
  els.send.disabled = true;

  // Consume the pending attachments for this turn and clear the composer chips.
  const attachments = pendingAttachments;
  pendingAttachments = [];
  renderAttachmentChips();

  const userLabel =
    attachments.length > 0
      ? `${text}${text.trim() ? "\n" : ""}📎 ${attachments.map((a) => a.name).join(", ")}`
      : text;
  addMessage("user", userLabel);
  const userMessage = { role: "user", content: text };
  if (attachments.length > 0) userMessage.attachments = attachments;
  history.push(userMessage);

  const assistantEl = addMessage("assistant", "");
  let streamed = "";

  try {
    const { content, artifacts } = await chat.run(history, {
      onToken: (t) => {
        streamed += t;
        assistantEl.textContent = streamed;
        els.messages.scrollTop = els.messages.scrollHeight;
      },
    });
    const finalText = content || streamed;
    if (finalText) {
      renderAssistantMarkdown(assistantEl, finalText);
    } else if (artifacts && artifacts.length > 0) {
      assistantEl.textContent =
        artifacts.length > 1 ? "Here are your files:" : "Here is your file:";
    } else {
      assistantEl.textContent = "(no response)";
    }
    renderDownloads(assistantEl, artifacts);
    history.push({ role: "assistant", content: finalText });
    setConnection("ok");
  } catch (err) {
    if (err && err.code === 401) {
      showBanner("Session expired. Please sign in again.", true);
      assistantEl.remove();
      await signOut();
      await refreshSignInState();
    } else {
      assistantEl.textContent = `⚠ ${err.message}`;
      setConnection("err");
    }
  } finally {
    busy = false;
    els.send.disabled = false;
    els.input.focus();
  }
}

// --- Event wiring ---------------------------------------------------------

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.input.value;
  els.input.value = "";
  els.input.style.height = "auto";
  handleSend(text);
});

// Enter to send, Shift+Enter for newline; auto-grow the textarea.
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});
els.input.addEventListener("input", () => {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 160)}px`;
});

els.signin.addEventListener("click", async () => {
  if (!config) config = await loadConfig();
  const errors = validateConfig(config);
  if (errors.length > 0) {
    showBanner("Configuration required — open settings (⚙).", true);
    return;
  }
  try {
    if (await isSignedIn()) {
      await signOut();
    } else {
      await signIn(config);
    }
    await refreshSignInState();
  } catch (err) {
    showBanner(`Sign-in failed: ${err.message}`, true);
  }
});

els.settings.addEventListener("click", () => api.runtime.openOptionsPage());

// Attach documents: the button opens the hidden file picker; picked files are
// read into pending attachments and shown as chips until the message is sent.
els.attachBtn.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", async () => {
  await addFiles(els.fileInput.files);
  els.fileInput.value = "";
});

// --- Init -----------------------------------------------------------------

addMessage("assistant", "");
els.messages.innerHTML = '<div class="empty">Ask a question to get started.</div>';
ensureReady();
