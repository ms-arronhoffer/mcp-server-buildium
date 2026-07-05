"""Self-contained HTML admin page for LLM configuration.

Served at ``GET /manage/`` when management is enabled. The page authenticates
against the ``/manage/llm`` REST endpoints using a ****** that the admin
provides in the UI. No external CDN resources are used.
"""

from __future__ import annotations

_ADMIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Buildium AI Configuration</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  font-size:14px;color:#1a1a2e;background:#f0f2f5;min-height:100vh}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px}
h1{font-size:1.4rem;font-weight:700;margin-bottom:4px}
.subtitle{color:#6b7280;margin-bottom:24px;font-size:.9rem}
.card{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.1);
  padding:20px;margin-bottom:20px}
.card h2{font-size:1rem;font-weight:600;margin-bottom:14px;
  color:#1a1a2e;border-bottom:1px solid #e5e7eb;padding-bottom:8px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:600px){.grid-2{grid-template-columns:1fr}}
label{display:block;font-size:.8rem;font-weight:500;color:#374151;margin-bottom:3px}
input,select{width:100%;padding:7px 10px;border:1px solid #d1d5db;
  border-radius:6px;font-size:.875rem;outline:none;transition:border .2s}
input:focus,select:focus{border-color:#4f46e5}
input[type=password]{letter-spacing:.1em}
.provider-block{padding:14px;border:1px solid #e5e7eb;border-radius:8px;
  margin-bottom:12px;background:#fafafa}
.provider-block h3{font-size:.875rem;font-weight:600;margin-bottom:10px;color:#374151}
.tier-row{display:grid;grid-template-columns:90px 1fr 1fr auto;
  gap:8px;align-items:end;margin-bottom:8px}
@media(max-width:600px){.tier-row{grid-template-columns:1fr 1fr}}
.tier-label{font-size:.75rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.05em;color:#6b7280;padding-top:4px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:14px}
button{padding:8px 16px;border:none;border-radius:6px;font-size:.875rem;
  font-weight:500;cursor:pointer;transition:opacity .15s}
.btn-primary{background:#4f46e5;color:#fff}
.btn-primary:hover{opacity:.9}
.btn-secondary{background:#e5e7eb;color:#374151}
.btn-secondary:hover{background:#d1d5db}
.btn-danger{background:#dc2626;color:#fff}
.btn-danger:hover{opacity:.9}
.btn-sm{padding:5px 10px;font-size:.8rem}
.status{font-size:.85rem;padding:6px 10px;border-radius:5px;
  display:none;margin-top:8px}
.status.ok{background:#d1fae5;color:#065f46;display:block}
.status.err{background:#fee2e2;color:#991b1b;display:block}
.routing-table{width:100%;border-collapse:collapse;margin-top:8px}
.routing-table th,.routing-table td{text-align:left;padding:6px 10px;
  font-size:.825rem;border-bottom:1px solid #e5e7eb}
.routing-table th{font-weight:600;color:#374151;background:#f9fafb}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;
  font-size:.75rem;font-weight:500}
.badge-ok{background:#d1fae5;color:#065f46}
.badge-warn{background:#fef3c7;color:#92400e}
.badge-off{background:#f3f4f6;color:#6b7280}
#token-section{margin-bottom:20px}
#token-section input{font-family:monospace}
#global-status{margin-bottom:10px}
.hint{font-size:.78rem;color:#6b7280;margin-top:3px}
</style>
</head>
<body>
<div class="wrap">
  <h1>&#x1F916; Buildium AI Configuration</h1>
  <p class="subtitle">Configure LLM providers and model tiers. Changes take effect on the next request.</p>

  <div class="card" id="token-section">
    <h2>Authentication</h2>
    <label for="bearer-token">Admin ******
    <input type="password" id="bearer-token" placeholder="Paste your Entra access token here"/>
    <p class="hint">Sign in via the browser extension and copy the token from the extension settings, or use your Entra access token.</p>
    <div class="row">
      <button class="btn-primary" onclick="loadConfig()">Load configuration</button>
    </div>
    <div id="auth-status" class="status"></div>
  </div>

  <div id="config-area" style="display:none">
    <div id="global-status" class="status"></div>

    <!-- Providers -->
    <div class="card">
      <h2>Providers</h2>
      <div id="providers-area"></div>
    </div>

    <!-- Tiers -->
    <div class="card">
      <h2>Model Tiers</h2>
      <p class="hint" style="margin-bottom:12px">
        Assign a provider and model to each tier. The router classifies each
        request and selects the configured tier automatically.
      </p>
      <div id="tiers-area"></div>
    </div>

    <!-- Routing overview -->
    <div class="card">
      <h2>Routing Overview</h2>
      <table class="routing-table" id="routing-table">
        <thead><tr>
          <th>Tier</th><th>Use case</th><th>Provider</th><th>Model</th><th>Status</th>
        </tr></thead>
        <tbody id="routing-body"></tbody>
      </table>
    </div>

    <div class="row">
      <button class="btn-primary" onclick="saveConfig()">Save all changes</button>
      <button class="btn-secondary" onclick="loadConfig()">Reload from server</button>
    </div>
    <div id="save-status" class="status" style="margin-top:10px"></div>
  </div>
</div>

<script>
const TIER_META = {
  simple:   {label:"Simple",   desc:"Short queries, drafting, conversational"},
  thinking: {label:"Thinking", desc:"Analysis, compliance, financial reasoning"},
  agentic:  {label:"Agentic",  desc:"Portfolio-wide, multi-tool operations"},
  artifact: {label:"Artifact", desc:"PDF/DOCX extraction, file export"},
};
const PROVIDERS = ["openai","anthropic","gemini"];
const PROVIDER_LABELS = {openai:"OpenAI",anthropic:"Anthropic",gemini:"Google Gemini"};

let _cfg = null;

function token(){
  return document.getElementById("bearer-token").value.trim();
}

function setStatus(id, msg, ok){
  const el = document.getElementById(id);
  el.textContent = msg;
  el.className = "status " + (ok ? "ok" : "err");
}

function clearStatus(id){
  const el = document.getElementById(id);
  el.className = "status";
  el.textContent = "";
}

function manageUrl(suffix){
  const base = location.href.replace(/\\/manage\\/?.*$/, "");
  return base + "/manage/" + suffix;
}

async function apiFetch(method, suffix, body){
  const t = token();
  if(!t) throw new Error("No bearer token provided.");
  const opts = {
    method,
    headers:{"Authorization":"Bearer " + t,"Accept":"application/json"},
  };
  if(body !== undefined){
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(manageUrl(suffix), opts);
  let data = null;
  try{ data = await resp.json(); }catch{}
  if(!resp.ok){
    throw new Error((data && (data.error || data.detail)) || "Request failed (" + resp.status + ")");
  }
  return data;
}

async function loadConfig(){
  clearStatus("auth-status");
  clearStatus("global-status");
  try{
    _cfg = await apiFetch("GET","llm");
    renderConfig(_cfg);
    document.getElementById("config-area").style.display = "";
    setStatus("auth-status","Connected \u2713",true);
  }catch(e){
    setStatus("auth-status",e.message,false);
    document.getElementById("config-area").style.display = "none";
  }
}

function renderConfig(cfg){
  renderProviders(cfg.providers || {});
  renderTiers(cfg.tiers || {});
  renderRouting(cfg.tiers || {});
}

function renderProviders(providers){
  const area = document.getElementById("providers-area");
  area.innerHTML = "";
  for(const p of PROVIDERS){
    const pdata = providers[p] || {};
    const enabled = pdata.enabled !== false;
    const masked = pdata.api_key_masked || "";
    const block = document.createElement("div");
    block.className = "provider-block";
    block.innerHTML = `
      <h3>${PROVIDER_LABELS[p]}</h3>
      <div class="grid-2">
        <div>
          <label for="key-${p}">API Key</label>
          <input type="password" id="key-${p}" data-provider="${p}"
            placeholder="${masked ? "leave blank to keep existing" : "sk-..."}"
            autocomplete="off"/>
          ${masked ? '<p class="hint">Stored key: ' + masked + '</p>' : ""}
        </div>
        <div>
          <label for="url-${p}">Base URL (optional)</label>
          <input type="text" id="url-${p}" data-provider="${p}"
            value="${pdata.base_url || ""}" placeholder="default"/>
        </div>
      </div>
      <div class="row">
        <button class="btn-secondary btn-sm" onclick="testProvider('${p}')">Test connection</button>
        <span id="test-status-${p}" class="status btn-sm" style="margin-top:0"></span>
      </div>`;
    area.appendChild(block);
  }
}

function renderTiers(tiers){
  const area = document.getElementById("tiers-area");
  area.innerHTML = "";
  const header = document.createElement("div");
  header.className = "tier-row";
  header.innerHTML = "<div></div><div><label>Provider</label></div><div><label>Model</label></div><div></div>";
  area.appendChild(header);
  for(const [t, meta] of Object.entries(TIER_META)){
    const td = tiers[t] || {};
    const row = document.createElement("div");
    row.className = "tier-row";
    const provOpts = PROVIDERS.map(p =>
      `<option value="${p}" ${td.provider===p?"selected":""}>${PROVIDER_LABELS[p]}</option>`
    ).join("");
    row.innerHTML = `
      <div class="tier-label" title="${meta.desc}">${meta.label}</div>
      <div>
        <select id="tier-prov-${t}">
          <option value="">-- none --</option>
          ${provOpts}
        </select>
      </div>
      <div>
        <input type="text" id="tier-model-${t}" value="${td.model||""}"
          placeholder="e.g. gpt-4o-mini"/>
      </div>
      <div>
        <button class="btn-secondary btn-sm" onclick="saveTier('${t}')">Save</button>
      </div>`;
    area.appendChild(row);
  }
}

function renderRouting(tiers){
  const body = document.getElementById("routing-body");
  body.innerHTML = "";
  for(const [t, meta] of Object.entries(TIER_META)){
    const td = tiers[t] || {};
    const configured = td.provider && td.model;
    const badge = configured
      ? `<span class="badge badge-ok">\u2713 configured</span>`
      : `<span class="badge badge-warn">not set</span>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${meta.label}</strong></td>
      <td style="color:#6b7280">${meta.desc}</td>
      <td>${PROVIDER_LABELS[td.provider] || "\u2014"}</td>
      <td><code>${td.model || "\u2014"}</code></td>
      <td>${badge}</td>`;
    body.appendChild(tr);
  }
}

async function testProvider(pname){
  clearStatus("test-status-" + pname);
  const keyEl = document.getElementById("key-" + pname);
  const urlEl = document.getElementById("url-" + pname);
  const apiKey = keyEl.value.trim();
  const baseUrl = urlEl.value.trim();
  try{
    await apiFetch("POST","llm/test",{provider:pname,api_key:apiKey,base_url:baseUrl});
    setStatus("test-status-" + pname,"Connected \u2713",true);
  }catch(e){
    setStatus("test-status-" + pname,e.message,false);
  }
}

async function saveTier(tierName){
  const prov = document.getElementById("tier-prov-" + tierName).value;
  const model = document.getElementById("tier-model-" + tierName).value.trim();
  try{
    _cfg = await apiFetch("PATCH","llm/tier/" + tierName,{provider:prov,model});
    renderRouting(_cfg.tiers || {});
    setStatus("global-status","Tier \u2018" + TIER_META[tierName].label + "\u2019 saved \u2713",true);
  }catch(e){
    setStatus("global-status",e.message,false);
  }
}

async function saveConfig(){
  clearStatus("save-status");
  const providers = {};
  for(const p of PROVIDERS){
    const keyEl = document.getElementById("key-" + p);
    const urlEl = document.getElementById("url-" + p);
    providers[p] = {
      api_key: keyEl ? keyEl.value.trim() : "",
      base_url: urlEl ? urlEl.value.trim() : "",
    };
  }
  const tiers = {};
  for(const t of Object.keys(TIER_META)){
    tiers[t] = {
      provider: document.getElementById("tier-prov-" + t).value,
      model: document.getElementById("tier-model-" + t).value.trim(),
    };
  }
  try{
    _cfg = await apiFetch("PUT","llm",{providers,tiers});
    renderConfig(_cfg);
    setStatus("save-status","Configuration saved \u2713",true);
  }catch(e){
    setStatus("save-status",e.message,false);
  }
}
</script>
</body>
</html>
"""


def get_admin_html() -> str:
    """Return the self-contained admin UI HTML page."""
    return _ADMIN_HTML
