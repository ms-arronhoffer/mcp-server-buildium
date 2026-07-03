/** Options page controller: load/save configuration and show the redirect URI. */

import { getApi } from "./browser.js";
import { DEFAULT_CONFIG, loadConfig, saveConfig, validateConfig } from "./config.js";

const api = getApi();

const FIELDS = ["mcpServerUrl", "entraTenantId", "entraClientId", "entraScopes", "llmModel"];

function el(id) {
  return document.getElementById(id);
}

async function populate() {
  const cfg = await loadConfig();
  for (const key of FIELDS) {
    el(key).value = cfg[key] ?? DEFAULT_CONFIG[key] ?? "";
  }
  try {
    el("redirect-uri").textContent = api.identity.getRedirectURL();
  } catch {
    el("redirect-uri").textContent = "(available inside the extension)";
  }
}

function showErrors(errors) {
  const list = el("errors");
  list.innerHTML = "";
  for (const e of errors) {
    const li = document.createElement("li");
    li.textContent = e;
    list.appendChild(li);
  }
}

el("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const cfg = {};
  for (const key of FIELDS) {
    cfg[key] = el(key).value.trim();
  }
  const errors = validateConfig(cfg);
  showErrors(errors);
  if (errors.length > 0) return;

  await saveConfig(cfg);
  const status = el("status");
  status.textContent = "Saved ✓";
  setTimeout(() => {
    status.textContent = "";
  }, 2000);
});

populate();
