# Buildium MCP Sidebar (Browser Extension)

A Manifest V3 browser extension (Chrome + Firefox) that renders a **full vertical-height
side panel chat** — like the Gemini side panel — backed by the
[Buildium MCP server](../README.md). The assistant is **LLM-driven**: a language model
holds the conversation and calls the server's Buildium tools as needed. Users sign in with
**Microsoft Entra ID** (Azure AD); the upstream Buildium API key never touches the browser.

```
┌──────────────┐   Entra access token    ┌──────────────────┐   Buildium API key
│  Extension   │ ───────────────────────▶│  MCP server      │ ─────────────────▶ Buildium
│  (side panel)│   Streamable HTTP/MCP    │  (HTTP transport)│   (server-side only)
│   + LLM      │◀─────────────────────── │  Entra JWT verify│
└──────────────┘                          └──────────────────┘
        │  Chat Completions (tools)
        ▼
   LLM provider (OpenAI-compatible)
```

## Architecture

| Module | Responsibility |
| ------ | -------------- |
| `src/pkce.js` | OAuth 2.0 PKCE verifier/challenge/state (Web Crypto). Pure. |
| `src/auth.js` | Entra Authorization Code + PKCE flow, token cache & silent refresh. |
| `src/mcpClient.js` | MCP Streamable HTTP client (`initialize`, `tools/list`, `tools/call`). |
| `src/llm.js` | LLM orchestration: advertises MCP tools, runs the tool-calling loop, streams replies. |
| `src/config.js` | Settings schema, validation, `storage.local` persistence. |
| `src/sidepanel.*` | Full-height chat UI (Chrome side panel / Firefox sidebar). |
| `src/options.*` | Settings page. |
| `src/background.js` | Opens the side panel on the toolbar action (Chrome). |

The pure logic modules are unit-tested with [Vitest](https://vitest.dev); browser APIs are
accessed lazily so the modules import cleanly in Node.

## Build

No bundler — the extension ships native ES modules. The build script only merges the shared
manifest with a per-browser manifest and copies files into `dist/<browser>/`.

```bash
npm install
npm run build            # builds dist/chrome and dist/firefox
npm run build:chrome     # single target
npm run build:firefox
```

### Load unpacked (development)

* **Chrome/Edge:** `chrome://extensions` → enable *Developer mode* → *Load unpacked* →
  select `dist/chrome`. Click the toolbar icon to open the side panel.
* **Firefox:** `about:debugging#/runtime/this-firefox` → *Load Temporary Add-on* →
  select `dist/firefox/manifest.json`. Open the sidebar from the toolbar.

## Configure

Open the extension's **Settings** (the ⚙ button in the panel, or the extension options page):

| Field | Description |
| ----- | ----------- |
| MCP server URL | The server's Streamable HTTP endpoint, e.g. `https://host/mcp`. |
| Entra tenant ID | Your Azure AD tenant GUID (or `common`/`organizations`). |
| Entra client ID | The **public-client/SPA** app registration ID for *this extension*. |
| Entra scopes | e.g. `api://<mcp-api-app-id>/MCP.Access`. |
| LLM API base / model / key | An OpenAI-compatible Chat Completions endpoint. |
| System prompt | Steers the assistant. |

### Entra app registrations

1. **MCP server API app** — register an app, *Expose an API*, add an Application ID URI
   (`api://<guid>`) and a scope (e.g. `MCP.Access`). Configure the server with
   `BUILDIUM_ENTRA_TENANT_ID`, `BUILDIUM_ENTRA_AUDIENCE=api://<guid>` (see the
   [server README](../README.md#transport--remote-access-http--microsoft-entra-id)).
2. **Extension app** — register a second app as a **Single-page application (SPA)**. Add the
   redirect URI shown on the extension's Settings page (`https://<id>.chromiumapp.org/` in
   Chrome; the value from `identity.getRedirectURL()` in Firefox). Under *API permissions*,
   add the MCP API's `MCP.Access` scope and grant consent.
3. Put the extension app's client ID and the `api://…/MCP.Access` scope into Settings.

The server must allow the extension's origin via CORS
(`BUILDIUM_CORS_ALLOW_ORIGINS=chrome-extension://<id>,moz-extension://<id>`). Entra's token
endpoint supports CORS for SPA-registered redirect URIs, so token exchange works from the
extension without extra host permissions.

## Test

```bash
npm test                 # unit tests (Vitest)
```

An **optional** live integration test runs only when `MCP_TEST_URL` is set (so CI is
unaffected). Point it at a running server using the dev static-token path:

```bash
# from the repo root
docker compose up --build mockapi mcp-server-http
# then, with BUILDIUM_MCP_AUTH_TOKEN=dev-token configured on the server:
MCP_TEST_URL=http://localhost:8000/mcp MCP_TEST_TOKEN=dev-token npm test -- integration
```

## Security notes

* The **Buildium API key stays on the server**; the extension only ever holds a short-lived
  Entra access token (cached in `storage.session`, cleared on browser close / sign-out).
* PKCE (S256) + a random `state` protect the authorization code exchange.
* Strict MV3 CSP (`script-src 'self'`); no remote code, no `eval`.
* Permissions are minimal: `storage`, `identity`, and (Chrome) `sidePanel`.

## Packaging

Zip the built directory for distribution:

```bash
npm run build
(cd dist/chrome && zip -r ../buildium-mcp-sidebar-chrome.zip .)
(cd dist/firefox && zip -r ../buildium-mcp-sidebar-firefox.xpi .)
```
