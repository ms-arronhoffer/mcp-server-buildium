import { getApi } from "./browser.js";
import { getAccessToken, isSignedIn, signIn, signOut } from "./auth.js";
import { CONFIG_STORAGE_KEY, loadConfig, validateConfig } from "./config.js";
import { ChatClient } from "./llm.js";
import { normalizeError } from "./errors.js";
import { NotificationCenter } from "./notificationCenter.js";
import { ChatStateMachine, CHAT_STATES } from "./chatState.js";
import { ArtifactUrlStore } from "./artifactUrls.js";
import {
  addMessage,
  hideBanner,
  renderAssistantMarkdown,
  setMessageKind,
  setConnection,
  showLoadingMessage,
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
  retry: document.getElementById("retry-btn"),
  clear: document.getElementById("clear-btn"),
  signin: document.getElementById("signin-btn"),
  settings: document.getElementById("settings-btn"),
  connDot: document.getElementById("conn-dot"),
  connText: document.getElementById("conn-text"),
  banner: document.getElementById("status-banner"),
  toast: document.getElementById("toast"),
  onboarding: document.getElementById("onboarding"),
  dismissOnboarding: document.getElementById("dismiss-onboarding-btn"),
  insightsPanel: document.getElementById("insights-panel"),
  insightsList: document.getElementById("insights-list"),
  lastUpdated: document.getElementById("last-updated"),
  toggleInsights: document.getElementById("toggle-insights-btn"),
  quickActions: document.getElementById("quick-actions"),
  viewControls: document.getElementById("view-controls"),
  recentPrompts: document.getElementById("recent-prompts"),
  composerError: document.getElementById("composer-error"),
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
const PANEL_PREFS_KEY = "buildium_sidepanel_prefs";
const MAX_RECENT_PROMPTS = 5;
const MAX_INSIGHTS = 4;
// UX cap to keep requests concise and avoid overwhelming the assistant context.
const MAX_PROMPT_CHARS = 2000;
const QUICK_PROMPTS = [
  { label: "Priority alerts", prompt: "Show me today's highest-priority alerts." },
  { label: "Expiring leases", prompt: "Summarize leases with upcoming expirations in the next 60 days." },
  { label: "Tenant follow-up", prompt: "Draft tenant follow-up messages for overdue balances." },
];

const panelPrefs = {
  view: "all",
  onboardingDismissed: false,
  insightsCollapsed: false,
  recentPrompts: [],
  draft: "",
  composerHeight: null,
};

let lastAttempt = "";
let clearedSnapshot = null;
let prefsSaveTimer = null;

const attachmentController = new AttachmentController(els.attachments, (text, isError) =>
  showBanner(els.banner, text, isError),
);

function addSystemMessage(text) {
  const el = addMessage(els.messages, "assistant", `ℹ ${text}`, "system");
  setMessageKind(el, "alerts");
  applyViewFilter();
}

const notifications = new NotificationCenter({
  showBanner: (text, isError) => showBanner(els.banner, text, isError),
  addSystemMessage,
});

function setBusy(isBusy) {
  els.send.disabled = isBusy;
  els.retry.disabled = isBusy;
}

function nowLabel() {
  return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function updateLastUpdated(label = `Updated ${nowLabel()}`) {
  els.lastUpdated.textContent = label;
}

async function loadPanelPrefs() {
  const saved = await api.storage.local.get(PANEL_PREFS_KEY);
  Object.assign(panelPrefs, saved[PANEL_PREFS_KEY] || {});
}

async function savePanelPrefs() {
  await api.storage.local.set({ [PANEL_PREFS_KEY]: panelPrefs });
}

function scheduleSavePanelPrefs(delay = 250) {
  if (prefsSaveTimer) window.clearTimeout(prefsSaveTimer);
  prefsSaveTimer = window.setTimeout(() => {
    savePanelPrefs().catch(() => undefined);
  }, delay);
}

function renderOnboarding() {
  els.onboarding.classList.toggle("hidden", panelPrefs.onboardingDismissed);
}

const FONT_SIZES = new Set(["small", "medium", "large", "xlarge"]);

/** Apply the overall font-size preference from config to the panel. */
function applyFontSize(size) {
  const value = FONT_SIZES.has(size) ? size : "medium";
  document.body.dataset.fontSize = value;
}

// Tracks the last height we set programmatically so the ResizeObserver can
// distinguish our own auto-grow updates from a user dragging the resize handle.
let lastAutoComposerHeight = 0;

/** Grow the composer to fit its content, honoring any user-chosen height. */
function autoGrowInput() {
  if (panelPrefs.composerHeight) {
    els.input.style.height = `${panelPrefs.composerHeight}px`;
    lastAutoComposerHeight = els.input.offsetHeight;
    return;
  }
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 160)}px`;
  lastAutoComposerHeight = els.input.offsetHeight;
}

/** Persist a manual resize of the composer performed via the drag handle. */
function watchComposerResize() {
  if (typeof ResizeObserver === "undefined") return;
  const observer = new ResizeObserver(() => {
    const height = els.input.offsetHeight;
    // Ignore height changes we caused ourselves via autoGrowInput(). The 1px
    // tolerance absorbs sub-pixel rounding differences between the height we
    // set and the value the browser reports back to the observer.
    if (Math.abs(height - lastAutoComposerHeight) <= 1) return;
    lastAutoComposerHeight = height;
    panelPrefs.composerHeight = height;
    scheduleSavePanelPrefs();
  });
  observer.observe(els.input);
}

function renderInsightsCollapsed() {
  els.insightsPanel.classList.toggle("collapsed", !!panelPrefs.insightsCollapsed);
  els.toggleInsights.textContent = panelPrefs.insightsCollapsed ? "Expand" : "Collapse";
}

function renderRecentPrompts() {
  const prompts = panelPrefs.recentPrompts || [];
  els.recentPrompts.innerHTML = "";
  els.recentPrompts.classList.toggle("hidden", prompts.length === 0);
  prompts.forEach((prompt) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "recent-prompt";
    button.textContent = prompt;
    button.addEventListener("click", () => {
      els.input.value = prompt;
      els.input.dispatchEvent(new Event("input"));
      els.input.focus();
    });
    els.recentPrompts.appendChild(button);
  });
}

function renderViewControls() {
  const view = panelPrefs.view || "all";
  for (const btn of els.viewControls.querySelectorAll(".view-btn")) {
    btn.classList.toggle("active", btn.dataset.view === view);
  }
}

function renderQuickActions() {
  els.quickActions.innerHTML = "";
  QUICK_PROMPTS.forEach((item, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quick-action";
    btn.dataset.prompt = item.prompt;
    btn.textContent = item.label;
    btn.setAttribute("aria-keyshortcuts", `Alt+${idx + 1}`);
    btn.title = `Alt+${idx + 1}`;
    els.quickActions.appendChild(btn);
  });
}

function applyViewFilter() {
  const view = panelPrefs.view || "all";
  const items = els.messages.querySelectorAll(".msg");
  for (const item of items) {
    if (view === "all") {
      item.classList.remove("hidden-by-filter");
      continue;
    }
    const kind = item.dataset.kind || "chat";
    const shouldShow = (view === "alerts" && kind === "alerts") || (view === "files" && kind === "files");
    item.classList.toggle("hidden-by-filter", !shouldShow);
  }
}

function setComposerError(message = "") {
  els.composerError.textContent = message;
  els.composerError.classList.toggle("hidden", !message);
  els.input.classList.toggle("invalid", !!message);
}

function validateDraft(text, attachmentsCount = 0) {
  const trimmed = text.trim();
  if (!trimmed && attachmentsCount === 0) return "";
  if (trimmed && trimmed.length < 3) return "Add a little more detail for better results.";
  if (trimmed.length > MAX_PROMPT_CHARS) return `Message must be under ${MAX_PROMPT_CHARS} characters.`;
  return "";
}

async function updateComposerValidation() {
  const error = validateDraft(els.input.value, attachmentController.pending.length);
  setComposerError(error);
  els.send.disabled = !!error || chatState.state !== CHAT_STATES.IDLE;
  panelPrefs.draft = els.input.value;
  scheduleSavePanelPrefs();
}

function addInsight(text, tone = "neutral") {
  if (!text) return;
  const li = document.createElement("li");
  li.className = `insight ${tone}`;
  li.textContent = text;
  els.insightsList.prepend(li);
  while (els.insightsList.children.length > MAX_INSIGHTS) {
    els.insightsList.lastElementChild?.remove();
  }
  updateLastUpdated();
}

function showToast(message, actionLabel, action) {
  els.toast.innerHTML = "";
  const text = document.createElement("span");
  text.textContent = message;
  els.toast.appendChild(text);
  if (actionLabel && action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link";
    btn.textContent = actionLabel;
    btn.addEventListener("click", action);
    els.toast.appendChild(btn);
  }
  els.toast.classList.remove("hidden");
  window.setTimeout(() => els.toast.classList.add("hidden"), 7000);
}

function clearChatWithUndo() {
  const nodes = Array.from(els.messages.childNodes);
  clearedSnapshot = { nodes, history: [...history] };
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = "Conversation cleared. Use quick actions above to start again.";
  els.messages.replaceChildren(empty);
  history.splice(0, history.length);
  applyViewFilter();
  showToast("Conversation cleared.", "Undo", () => {
    if (!clearedSnapshot) return;
    els.messages.replaceChildren(...clearedSnapshot.nodes);
    history.splice(0, history.length, ...clearedSnapshot.history);
    clearedSnapshot = null;
    applyViewFilter();
    showToast("Conversation restored.");
  });
  addInsight("Conversation was cleared.", "neutral");
}

function rememberPrompt(text) {
  const trimmed = text.trim();
  if (!trimmed) return;
  panelPrefs.recentPrompts = [trimmed, ...(panelPrefs.recentPrompts || []).filter((v) => v !== trimmed)].slice(
    0,
    MAX_RECENT_PROMPTS,
  );
  renderRecentPrompts();
  savePanelPrefs();
}

function notify(event) {
  const channels = {
    banner: event.channels?.banner ?? true,
    chat: !!(config?.notificationChat && event.channels?.chat),
    browser: !!(config?.notificationBrowser && event.channels?.browser),
  };
  notifications.notify({ ...event, channels });
  if (event?.severity === "error") addInsight(event.message, "error");
  if (event?.type === "digest" || event?.type === "artifact_ready") addInsight(event.message, "info");
}

async function refreshSignInState() {
  const signedIn = await isSignedIn();
  els.signin.textContent = signedIn ? "Sign out" : "Sign in";
  setConnection(els.connDot, signedIn ? "ok" : null, els.connText);
  return signedIn;
}

async function ensureReady() {
  config = await loadConfig();
  applyFontSize(config.fontSize);
  const errors = validateConfig(config);
  if (errors.length > 0) {
    notify({
      type: "system",
      severity: "error",
      message: "Configuration required — open settings (⚙). " + errors.join(" "),
      dedupeKey: "config-required",
      channels: { banner: true, chat: false, browser: false },
    });
    addInsight("Configuration is required before sending messages.", "error");
    return false;
  }
  hideBanner(els.banner);
  chat = new ChatClient(config, (opts) => getAccessToken(config, opts));
  await refreshSignInState();
  updateLastUpdated();
  return true;
}

async function handleSend(text) {
  if (![CHAT_STATES.IDLE, CHAT_STATES.COMPLETE, CHAT_STATES.ERROR].includes(chatState.state)) {
    return;
  }
  const attachments = attachmentController.pending;
  const validationError = validateDraft(text, attachments.length);
  if (validationError) {
    setComposerError(validationError);
    return;
  }
  if (!text.trim() && attachments.length === 0) return;
  if (!(await ensureReady())) return;

  els.input.value = "";
  autoGrowInput();
  chatState.transition(CHAT_STATES.SENDING);
  setBusy(true);
  setComposerError("");
  lastAttempt = text;

  const consumedAttachments = attachmentController.consume();
  const userLabel =
    consumedAttachments.length > 0
      ? `${text}${text.trim() ? "\n" : ""}📎 ${consumedAttachments.map((a) => a.name).join(", ")}`
      : text;
  const userEl = addMessage(els.messages, "user", userLabel);
  setMessageKind(userEl, "chat");

  const userMessage = { role: "user", content: text };
  if (consumedAttachments.length > 0) userMessage.attachments = consumedAttachments;
  history.push(userMessage);

  const assistantEl = showLoadingMessage(els.messages);
  setMessageKind(assistantEl, "chat");
  let streamed = "";

  try {
    const { content, artifacts } = await chat.run(history, {
      onToken: (token) => {
        if (chatState.state === CHAT_STATES.SENDING) chatState.transition(CHAT_STATES.STREAMING);
        streamed += token;
        if (assistantEl.classList.contains("loading")) {
          assistantEl.classList.remove("loading");
          assistantEl.textContent = "";
        }
        assistantEl.textContent = streamed;
        els.messages.scrollTop = els.messages.scrollHeight;
      },
    });

    const finalText = content || streamed;
    if (finalText) {
      renderAssistantMarkdown(els.messages, assistantEl, finalText, (prompt) => handleSend(prompt));
    } else if (artifacts && artifacts.length > 0) {
      assistantEl.textContent = artifacts.length > 1 ? "Here are your files:" : "Here is your file:";
      setMessageKind(assistantEl, "files");
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
    if (artifacts?.length) setMessageKind(assistantEl, "files");

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
    setConnection(els.connDot, "ok", els.connText);
    chatState.transition(CHAT_STATES.COMPLETE);
    rememberPrompt(text);
    addInsight("Response received successfully.", "neutral");
    els.retry.classList.add("hidden");
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
      assistantEl.classList.remove("loading");
      assistantEl.textContent = `⚠ ${normalized.message}`;
      setMessageKind(assistantEl, "alerts");
      setConnection(els.connDot, "err", els.connText);
      notify({
        type: normalized.type,
        severity: normalized.severity,
        message: normalized.message,
        dedupeKey: `chat-error-${normalized.type}`,
        channels: { banner: true, chat: false, browser: false },
      });
      els.retry.classList.remove("hidden");
    }
  } finally {
    setBusy(false);
    els.input.focus();
    applyViewFilter();
    updateLastUpdated();
    if (chatState.state === CHAT_STATES.COMPLETE || chatState.state === CHAT_STATES.ERROR) {
      chatState.transition(CHAT_STATES.IDLE);
    }
    await updateComposerValidation();
  }
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.input.value;
  handleSend(text);
});

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

els.input.addEventListener("input", () => {
  autoGrowInput();
  updateComposerValidation();
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
els.retry.addEventListener("click", () => {
  if (!lastAttempt) return;
  els.input.value = lastAttempt;
  els.form.requestSubmit();
});
els.clear.addEventListener("click", clearChatWithUndo);
els.attachBtn.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", async () => {
  await attachmentController.addFiles(els.fileInput.files);
  els.fileInput.value = "";
  await updateComposerValidation();
});

els.quickActions.addEventListener("click", (event) => {
  const target = event.target.closest(".quick-action");
  if (!target) return;
  const prompt = target.dataset.prompt || "";
  els.input.value = prompt;
  els.input.dispatchEvent(new Event("input"));
  els.form.requestSubmit();
});

els.viewControls.addEventListener("click", async (event) => {
  const target = event.target.closest(".view-btn");
  if (!target) return;
  panelPrefs.view = target.dataset.view || "all";
  renderViewControls();
  applyViewFilter();
  await savePanelPrefs();
});

els.dismissOnboarding.addEventListener("click", async () => {
  panelPrefs.onboardingDismissed = true;
  renderOnboarding();
  await savePanelPrefs();
});

els.toggleInsights.addEventListener("click", async () => {
  panelPrefs.insightsCollapsed = !panelPrefs.insightsCollapsed;
  renderInsightsCollapsed();
  await savePanelPrefs();
});

window.addEventListener("keydown", (event) => {
  if (!event.altKey) return;
  const idx = Number(event.key) - 1;
  if (idx < 0 || idx >= QUICK_PROMPTS.length) return;
  event.preventDefault();
  const prompt = QUICK_PROMPTS[idx].prompt;
  els.input.value = prompt;
  els.input.dispatchEvent(new Event("input"));
  els.form.requestSubmit();
});

api.runtime.onMessage.addListener((message) => {
  if (!message || message.type !== "digest_notification") return;
  const text = message.text || "New portfolio digest available.";
  notify({
    type: "digest",
    severity: "info",
    message: text,
    dedupeKey: message.dedupeKey || text,
    channels: {
      banner: !!config?.notificationInPanel,
      chat: !!config?.notificationChat,
      browser: !!config?.notificationBrowser,
    },
  });
});

function cleanupPanelLifecycle() {
  artifactUrls.revokeAll();
  // Panel-close notifications are best-effort; ignore failures when runtime is unavailable.
  api.runtime.sendMessage({ type: "panel_closed" }).catch(() => undefined);
}

window.addEventListener("beforeunload", cleanupPanelLifecycle);
window.addEventListener("pagehide", cleanupPanelLifecycle);

// Apply configuration changes saved from the options page without requiring
// the user to close and reopen the side panel.
api.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes[CONFIG_STORAGE_KEY]) return;
  ensureReady().catch((err) => {
    // Best-effort refresh; surface the reason so a failed reload is diagnosable.
    console.error("Failed to apply updated configuration:", err);
  });
});

async function init() {
  await loadPanelPrefs();
  renderOnboarding();
  renderInsightsCollapsed();
  renderRecentPrompts();
  renderQuickActions();
  renderViewControls();
  els.input.value = panelPrefs.draft || "";
  els.input.dispatchEvent(new Event("input"));
  watchComposerResize();
  els.retry.classList.add("hidden");
  els.messages.innerHTML =
    '<div class="empty">Ask a question to get started, or use a quick action above for common workflows.</div>';
  applyViewFilter();
  updateLastUpdated("Waiting for first update");
  await updateComposerValidation();
  await ensureReady();
}

api.runtime.sendMessage({ type: "panel_open" }).catch(() => undefined);
init();
