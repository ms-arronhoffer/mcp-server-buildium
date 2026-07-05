/** Options page controller: load/save configuration and show the redirect URI. */

import { getAccessToken } from "./auth.js";
import { getApi } from "./browser.js";
import { DEFAULT_CONFIG, bakedFields, loadConfig, saveConfig, validateConfig } from "./config.js";
import { ManagementClient } from "./management.js";

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
  // Re-evaluate admin access with the newly saved settings.
  initAdminPanel();
});

// --- Admin panel -----------------------------------------------------------

/** Populate the role <select> elements from a list of role names. */
function fillRoleOptions(select, roles) {
  select.innerHTML = "";
  for (const role of roles) {
    const opt = document.createElement("option");
    opt.value = role;
    opt.textContent = role;
    select.appendChild(opt);
  }
}

function showAdminErrors(errors) {
  const list = el("admin-errors");
  list.innerHTML = "";
  for (const e of errors) {
    const li = document.createElement("li");
    li.textContent = e;
    list.appendChild(li);
  }
}

function adminStatus(message) {
  const node = el("admin-status");
  node.textContent = message;
  if (message) {
    setTimeout(() => {
      node.textContent = "";
    }, 3000);
  }
}

/** Render the users table with an inline role editor per row. */
function renderUsers(client, users, roles) {
  const body = el("users-body");
  body.innerHTML = "";
  if (!users.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 3;
    cell.textContent = "No users assigned yet.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  for (const user of users) {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    nameCell.textContent = user.display_name || user.user_id || "(unknown)";
    row.appendChild(nameCell);

    const roleCell = document.createElement("td");
    roleCell.textContent = user.role || "unknown";
    row.appendChild(roleCell);

    const actionCell = document.createElement("td");
    const select = document.createElement("select");
    fillRoleOptions(select, roles);
    if (roles.includes(user.role)) select.value = user.role;
    const apply = document.createElement("button");
    apply.type = "button";
    apply.textContent = "Apply";
    apply.addEventListener("click", async () => {
      try {
        await client.setUserRole(user.user_id, select.value);
        adminStatus("Role updated ✓");
        await refreshUsers(client, roles);
      } catch (err) {
        showAdminErrors([err.message]);
      }
    });
    actionCell.appendChild(select);
    actionCell.appendChild(apply);
    row.appendChild(actionCell);

    body.appendChild(row);
  }
}

async function refreshUsers(client, roles) {
  try {
    const users = await client.listUsers();
    renderUsers(client, users, roles);
    showAdminErrors([]);
  } catch (err) {
    showAdminErrors([err.message]);
  }
}

/** Save a downloaded blob to disk using the browser download API (or a link). */
async function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  try {
    if (api.downloads && api.downloads.download) {
      await api.downloads.download({ url, filename });
    } else {
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
    }
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}

function renderDownloadButtons(client, browsers) {
  const container = el("download-buttons");
  container.innerHTML = "";
  const filenames = {
    chrome: "buildium-mcp-sidebar-chrome.zip",
    firefox: "buildium-mcp-sidebar-firefox.xpi",
  };
  for (const browser of browsers) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `Download ${browser} build`;
    button.addEventListener("click", async () => {
      try {
        const blob = await client.downloadExtension(browser);
        await saveBlob(blob, filenames[browser] || `extension-${browser}`);
        adminStatus("Download started ✓");
      } catch (err) {
        showAdminErrors([err.message]);
      }
    });
    container.appendChild(button);
  }
  el("download-extension").hidden = browsers.length === 0;
}

/**
 * Query the server for management capabilities and, when the caller is an admin,
 * reveal and wire up the admin panel. Silently hides the panel otherwise (the
 * server remains the enforcement point).
 */
async function initAdminPanel() {
  const panel = el("admin-panel");
  panel.hidden = true;
  let cfg;
  try {
    cfg = await loadConfig();
  } catch {
    return;
  }
  if (validateConfig(cfg).length > 0) return;

  const client = new ManagementClient(cfg, (opts) => getAccessToken(cfg, opts));
  let caps;
  try {
    caps = await client.capabilities();
  } catch {
    return; // not signed in, not reachable, or not authorized — keep panel hidden.
  }
  if (!caps || !caps.enabled || !caps.isAdmin) return;

  const roles = Array.isArray(caps.roles) && caps.roles.length
    ? caps.roles
    : ["admin", "operator", "readonly"];
  fillRoleOptions(el("invite-role"), roles);
  renderDownloadButtons(client, caps.extensionBrowsers || []);
  panel.hidden = false;

  el("invite-button").onclick = async () => {
    showAdminErrors([]);
    const email = el("invite-email").value.trim();
    const role = el("invite-role").value;
    if (!email) {
      showAdminErrors(["An email address is required."]);
      return;
    }
    try {
      await client.inviteUser(email, role);
      adminStatus("Invitation sent ✓");
      el("invite-email").value = "";
      await refreshUsers(client, roles);
    } catch (err) {
      showAdminErrors([err.message]);
    }
  };
  el("refresh-users").onclick = () => refreshUsers(client, roles);

  await refreshUsers(client, roles);
}

populate();
initAdminPanel();
