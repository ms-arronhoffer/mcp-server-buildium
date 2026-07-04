import { getApi } from "./browser.js";
import { getAccessToken, isSignedIn } from "./auth.js";
import { loadConfig } from "./config.js";
import { McpClient } from "./mcpClient.js";

const api = getApi();
const POLL_ALARM = "buildium-role-notification-poll";
const DEDUPE_KEY_STORAGE = "buildium_last_role_digest_dedupe";

let panelOpen = false;

function pollPeriodMinutes(cfg) {
  const n = Number(cfg.notificationPollMinutes);
  if (!Number.isFinite(n) || n < 1) return 15;
  return Math.min(1440, Math.floor(n));
}

async function notifyPanel(payload) {
  try {
    await api.runtime.sendMessage(payload);
  } catch {
    // Side panel may not be open.
  }
}

async function notifyBrowser(text) {
  if (!api.notifications?.create) return;
  try {
    await api.notifications.create(`digest-${Date.now()}`, {
      type: "basic",
      iconUrl: "icons/icon-48.png",
      title: "Portfolio Digest",
      message: text,
    });
  } catch {
    // Non-fatal.
  }
}

async function readLastDedupeKey() {
  const data = await api.storage.local.get(DEDUPE_KEY_STORAGE);
  return data[DEDUPE_KEY_STORAGE] || null;
}

async function writeLastDedupeKey(key) {
  await api.storage.local.set({ [DEDUPE_KEY_STORAGE]: key });
}

export async function configureRoleNotificationPolling() {
  const cfg = await loadConfig();
  await api.alarms.clear(POLL_ALARM);
  if (!cfg.notificationFeatureEnabled) return;
  await api.alarms.create(POLL_ALARM, { periodInMinutes: pollPeriodMinutes(cfg) });
}

export async function runRoleNotificationPoll() {
  const cfg = await loadConfig();
  if (!cfg.notificationFeatureEnabled) return;
  if (!(await isSignedIn())) return;

  const client = new McpClient(cfg.mcpServerUrl, () => getAccessToken(cfg, { interactive: false }));
  const result = await client.callTool("role_notification_feed", {
    role: cfg.notificationRole || "pm",
  });
  const payload = result?.structuredContent || result;
  const dedupeKey = payload?.machine_payload?.dedupe_key;
  const text = payload?.feed?.headline
    ? `${payload.feed.headline} (${payload.role || cfg.notificationRole})`
    : "New portfolio digest available.";
  if (!dedupeKey) return;

  const last = await readLastDedupeKey();
  if (last === dedupeKey) return;
  await writeLastDedupeKey(dedupeKey);

  if (cfg.notificationInPanel && panelOpen) {
    await notifyPanel({ type: "digest_notification", text, dedupeKey });
  }
  if (cfg.notificationBrowser && (!panelOpen || !cfg.notificationInPanel)) {
    await notifyBrowser(text);
  }
}

export function bindRoleNotificationMessages() {
  api.runtime.onMessage.addListener((message) => {
    if (!message || typeof message !== "object") return;
    if (message.type === "panel_open") panelOpen = true;
    if (message.type === "panel_closed") panelOpen = false;
  });
}

export function bindRoleNotificationAlarms() {
  api.alarms.onAlarm.addListener((alarm) => {
    if (alarm?.name !== POLL_ALARM) return;
    runRoleNotificationPoll().catch(() => undefined);
  });
}

export function bindRoleNotificationConfigChanges() {
  api.storage.onChanged?.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (!changes.buildium_mcp_config) return;
    configureRoleNotificationPolling().catch(() => undefined);
  });
}
