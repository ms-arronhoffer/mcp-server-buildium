import { getApi } from "./browser.js";
import { getAccessToken, isSignedIn, signIn, signOut } from "./auth.js";
import { loadConfig, validateConfig } from "./config.js";
import { ChatClient } from "./llm.js";
import { normalizeError } from "./errors.js";
import { NotificationCenter } from "./notificationCenter.js";
import { ChatStateMachine, CHAT_STATES } from "./chatState.js";
import { ArtifactUrlStore } from "./artifactUrls.js";
import {
  addMessage,
  hideBanner,
  renderAssistantMarkdown,
  setConnection,
  showBanner,
} from "./sidepanel_ui.js";
import { AttachmentController } from "./sidepanel_attachments.js";
import { renderDownloads } from "./sidepanel_downloads.js";

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
const history = [];
const chatState = new ChatStateMachine();
const artifactUrls = new ArtifactUrlStore();

const attachmentController = new AttachmentController(els.attachments, (text, isError) =>
  showBanner(els.banner, text, isError),
);

function addSystemMessage(text) {
  addMessage(els.messages, "assistant", `ℹ ${text}`, "system");
}

const notifications = new NotificationCenter({
  showBanner: (text, isError) => showBanner(els.banner, text, isError),
  addSystemMessage,
});

function setBusy(isBusy) {
  els.send.disabled = isBusy;
}

function notify(event) {
  const channels = {
    banner: event.channels?.banner ?? true,
    chat: !!(config?.notificationChat && event.channels?.chat),
    browser: !!(config?.notificationBrowser && event.channels?.browser),
  };
  notifications.notify({ ...event, channels });
}

async function refreshSignInState() {
  const signedIn = await isSignedIn();
  els.signin.textContent = signedIn ? "Sign out" : "Sign in";
  setConnection(els.connDot, signedIn ? "ok" : null);
  return signedIn;
}

async function ensureReady() {
  config = await loadConfig();
  const errors = validateConfig(config);
  if (errors.length > 0) {
    notify({
      type: "system",
      severity: "error",
      message: "Configuration required — open settings (⚙). " + errors.join(" "),
      dedupeKey: "config-required",
      channels: { banner: true, chat: false, browser: false },
    });
    return false;
  }
  hideBanner(els.banner);
  chat = new ChatClient(config, (opts) => getAccessToken(config, opts));
  await refreshSignInState();
  return true;
}

async function handleSend(text) {
  if (![CHAT_STATES.IDLE, CHAT_STATES.COMPLETE, CHAT_STATES.ERROR].includes(chatState.state)) {
    return;
  }
  const attachments = attachmentController.pending;
  if (!text.trim() && attachments.length === 0) return;
  if (!(await ensureReady())) return;

  chatState.transition(CHAT_STATES.SENDING);
  setBusy(true);

  const consumedAttachments = attachmentController.consume();
  const userLabel =
    consumedAttachments.length > 0
      ? `${text}${text.trim() ? "\n" : ""}📎 ${consumedAttachments.map((a) => a.name).join(", ")}`
      : text;
  addMessage(els.messages, "user", userLabel);

  const userMessage = { role: "user", content: text };
  if (consumedAttachments.length > 0) userMessage.attachments = consumedAttachments;
  history.push(userMessage);

  const assistantEl = addMessage(els.messages, "assistant", "");
  let streamed = "";

  try {
    const { content, artifacts } = await chat.run(history, {
      onToken: (token) => {
        if (chatState.state === CHAT_STATES.SENDING) chatState.transition(CHAT_STATES.STREAMING);
        streamed += token;
        assistantEl.textContent = streamed;
        els.messages.scrollTop = els.messages.scrollHeight;
      },
    });

    const finalText = content || streamed;
    if (finalText) {
      renderAssistantMarkdown(els.messages, assistantEl, finalText, (prompt) => handleSend(prompt));
    } else if (artifacts && artifacts.length > 0) {
      assistantEl.textContent = artifacts.length > 1 ? "Here are your files:" : "Here is your file:";
    } else {
      assistantEl.textContent = "(no response)";
    }

    renderDownloads(els.messages, assistantEl, artifacts, artifactUrls, (msg, isError) =>
      notify({
        type: "artifact_ready",
        severity: isError ? "error" : "info",
        message: msg,
        dedupeKey: `artifact-${msg}`,
        channels: { banner: true, chat: false, browser: false },
      }),
    );

    if (artifacts?.length) {
      notify({
        type: "artifact_ready",
        severity: "info",
        message: artifacts.length > 1 ? "Files are ready for download." : "File is ready for download.",
        dedupeKey: `artifact-ready-${artifacts.length}`,
        channels: { banner: true, chat: true, browser: false },
      });
    }

    history.push({ role: "assistant", content: finalText });
    setConnection(els.connDot, "ok");
    chatState.transition(CHAT_STATES.COMPLETE);
  } catch (err) {
    const normalized = normalizeError(err, "Chat request failed.");
    chatState.transition(CHAT_STATES.ERROR);
    if (normalized.type === "auth") {
      assistantEl.remove();
      notify({
        type: "auth",
        severity: "error",
        message: "Session expired. Please sign in again.",
        dedupeKey: "auth-expired",
        channels: { banner: true, chat: true, browser: false },
      });
      await signOut();
      await refreshSignInState();
    } else {
      assistantEl.textContent = `⚠ ${normalized.message}`;
      setConnection(els.connDot, "err");
      notify({
        type: normalized.type,
        severity: normalized.severity,
        message: normalized.message,
        dedupeKey: `chat-error-${normalized.type}`,
        channels: { banner: true, chat: false, browser: false },
      });
    }
  } finally {
    setBusy(false);
    els.input.focus();
    if (chatState.state === CHAT_STATES.COMPLETE || chatState.state === CHAT_STATES.ERROR) {
      chatState.transition(CHAT_STATES.IDLE);
    }
  }
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.input.value;
  els.input.value = "";
  els.input.style.height = "auto";
  handleSend(text);
});

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
    notify({
      type: "system",
      severity: "error",
      message: "Configuration required — open settings (⚙).",
      dedupeKey: "signin-config-required",
      channels: { banner: true, chat: false, browser: false },
    });
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
    const normalized = normalizeError(err, "Sign-in failed.");
    notify({
      type: normalized.type,
      severity: "error",
      message: `Sign-in failed: ${normalized.message}`,
      dedupeKey: "signin-failed",
      channels: { banner: true, chat: false, browser: false },
    });
  }
});

els.settings.addEventListener("click", () => api.runtime.openOptionsPage());
els.attachBtn.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", async () => {
  await attachmentController.addFiles(els.fileInput.files);
  els.fileInput.value = "";
});

api.runtime.onMessage.addListener((message) => {
  if (!message || message.type !== "digest_notification") return;
  const text = message.text || "New portfolio digest available.";
  notify({
    type: "digest",
    severity: "info",
    message: text,
    dedupeKey: message.dedupeKey || text,
    channels: { banner: !!config?.notificationInPanel, chat: !!config?.notificationChat, browser: false },
  });
});

window.addEventListener("beforeunload", () => {
  artifactUrls.revokeAll();
  api.runtime.sendMessage({ type: "panel_closed" }).catch(() => undefined);
});

els.messages.innerHTML = '<div class="empty">Ask a question to get started.</div>';
api.runtime.sendMessage({ type: "panel_open" }).catch(() => undefined);
ensureReady();
