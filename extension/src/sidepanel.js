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
};

/** @type {import('./config.js').ExtensionConfig|null} */
let config = null;
let chat = null;
let busy = false;
/** Chat history in OpenAI message format (user/assistant only). */
const history = [];

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
  if (busy || !text.trim()) return;
  if (!(await ensureReady())) return;

  busy = true;
  els.send.disabled = true;
  addMessage("user", text);
  history.push({ role: "user", content: text });

  const assistantEl = addMessage("assistant", "");
  let streamed = "";

  try {
    const { content } = await chat.run(history, {
      onToken: (t) => {
        streamed += t;
        assistantEl.textContent = streamed;
        els.messages.scrollTop = els.messages.scrollHeight;
      },
    });
    const finalText = content || streamed;
    if (finalText) {
      renderAssistantMarkdown(assistantEl, finalText);
    } else {
      assistantEl.textContent = "(no response)";
    }
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

// --- Init -----------------------------------------------------------------

addMessage("assistant", "");
els.messages.innerHTML = '<div class="empty">Ask a question to get started.</div>';
ensureReady();
