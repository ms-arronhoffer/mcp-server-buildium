import { renderMarkdown } from "./markdown.js";

export function clearEmptyState(messagesEl) {
  const empty = messagesEl.querySelector(".empty");
  if (empty) empty.remove();
}

export function addMessage(messagesEl, role, text, extraClass = "") {
  clearEmptyState(messagesEl);
  const div = document.createElement("div");
  div.className = `msg ${role}${extraClass ? ` ${extraClass}` : ""}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

export function setMessageKind(messageEl, kind) {
  if (!messageEl) return;
  if (kind) messageEl.dataset.kind = kind;
}

export function showLoadingMessage(messagesEl) {
  const div = addMessage(messagesEl, "assistant", "", "loading");
  div.innerHTML = '<span class="skeleton"></span><span class="skeleton"></span><span class="skeleton"></span>';
  return div;
}

export function renderAssistantMarkdown(messagesEl, el, text, onAction) {
  el.textContent = "";
  el.classList.remove("loading");
  el.classList.add("markdown");
  el.appendChild(renderMarkdown(text, onAction));
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

export function setConnection(connDotEl, state, connTextEl = null) {
  connDotEl.classList.remove("ok", "err");
  if (state === "ok") connDotEl.classList.add("ok");
  if (state === "err") connDotEl.classList.add("err");
  if (connTextEl) {
    connTextEl.textContent = state === "ok" ? "Connected" : state === "err" ? "Issue detected" : "Offline";
  }
}

export function showBanner(bannerEl, text, isError = false) {
  bannerEl.textContent = text;
  bannerEl.classList.toggle("error", isError);
  bannerEl.classList.remove("hidden");
}

export function hideBanner(bannerEl) {
  bannerEl.classList.add("hidden");
}
