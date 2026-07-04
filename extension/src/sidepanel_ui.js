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

export function renderAssistantMarkdown(messagesEl, el, text, onAction) {
  el.textContent = "";
  el.classList.add("markdown");
  el.appendChild(renderMarkdown(text, onAction));
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

export function setConnection(connDotEl, state) {
  connDotEl.classList.remove("ok", "err");
  if (state === "ok") connDotEl.classList.add("ok");
  if (state === "err") connDotEl.classList.add("err");
}

export function showBanner(bannerEl, text, isError = false) {
  bannerEl.textContent = text;
  bannerEl.classList.toggle("error", isError);
  bannerEl.classList.remove("hidden");
}

export function hideBanner(bannerEl) {
  bannerEl.classList.add("hidden");
}
