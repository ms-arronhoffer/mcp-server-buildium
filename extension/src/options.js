/** Options page controller: load/save configuration and show the redirect URI. */

import { getApi } from "./browser.js";
import { DEFAULT_CONFIG, bakedFields, loadConfig, saveConfig, validateConfig } from "./config.js";

const api = getApi();

const FIELD_CONFIG = [
  { key: "mcpServerUrl", type: "text" },
  { key: "entraTenantId", type: "text" },
  { key: "entraClientId", type: "text" },
  { key: "entraScopes", type: "text" },
  { key: "llmModel", type: "text" },
  { key: "notificationFeatureEnabled", type: "checkbox" },
  { key: "notificationRole", type: "text" },
  { key: "notificationPollMinutes", type: "number" },
  { key: "notificationInPanel", type: "checkbox" },
  { key: "notificationBrowser", type: "checkbox" },
  { key: "notificationChat", type: "checkbox" },
  { key: "fontSize", type: "select" },
];

function el(id) {
  return document.getElementById(id);
}

async function populate() {
  const cfg = await loadConfig();
  const preconfigured = new Set(bakedFields());
  for (const field of FIELD_CONFIG) {
    const node = el(field.key);
    if (field.type === "checkbox") {
      node.checked = Boolean(cfg[field.key] ?? DEFAULT_CONFIG[field.key]);
    } else {
      node.value = cfg[field.key] ?? DEFAULT_CONFIG[field.key] ?? "";
    }
    // Baked defaults are prepopulated but remain user-editable.
    if (preconfigured.has(field.key)) {
      node.title = "Prepopulated by this build — you can override it.";
      node.classList.add("prefilled");
    }
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
  for (const field of FIELD_CONFIG) {
    const node = el(field.key);
    if (field.type === "checkbox") {
      cfg[field.key] = node.checked;
    } else if (field.type === "number") {
      cfg[field.key] = Number(node.value || DEFAULT_CONFIG[field.key]);
    } else if (field.type === "select") {
      cfg[field.key] = node.value;
    } else {
      cfg[field.key] = node.value.trim();
    }
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
