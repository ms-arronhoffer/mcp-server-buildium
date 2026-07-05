"""Admin-only management HTTP routes: ``/manage/*``.

These custom routes let an **admin** (as determined by the existing Entra JWT
auth and the coarse ``admin`` role that governs :data:`ADMIN_ONLY_TOOLS`) manage
users, distribute the browser extension, and configure LLM providers/models:

* ``GET  /manage/`` — serve the self-contained admin web UI (HTML page).
* ``GET  /manage/capabilities`` — whether management is enabled and whether the
  caller is an admin (so the extension can show/hide the admin UI).
* ``GET  /manage/users`` — list users assigned to the API app and their roles.
* ``POST /manage/users`` — invite an Entra **B2B guest** and assign a role.
* ``PATCH /manage/users/{id}/role`` — change a user's assigned role.
* ``GET  /manage/extension?browser=chrome|firefox`` — download the prebuilt,
  preconfigured extension archive.
* ``GET  /manage/llm`` — return the current LLM config with keys masked.
* ``PUT  /manage/llm`` — replace the full LLM config (providers + tiers).
* ``PATCH /manage/llm/tier/{tier}`` — update a single model-tier assignment.
* ``POST /manage/llm/test`` — validate a provider API key with a live request.

Every mutating/download route authenticates with the same verifier as ``/mcp``
and ``/chat`` (401 on failure) and then requires the caller to be an admin (403
otherwise). Microsoft Graph credentials stay server-side and are never returned.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from .config import ENTRA_MAPPABLE_ROLES, MANAGEMENT_BROWSERS
from .entra_graph import GraphClient, GraphError
from .llm.config_store import PROVIDERS, TIERS, LLMConfig, get_store
from .logging_config import get_logger
from .security.policy import is_admin_claims

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from fastmcp.server.auth.auth import TokenVerifier

    from .audit import AuditRecorder
    from .config import BuildiumConfig

logger = get_logger("mcp_server_buildium.manage")

MANAGE_ADMIN_UI_PATH = "/manage"
MANAGE_CAPABILITIES_PATH = "/manage/capabilities"
MANAGE_USERS_PATH = "/manage/users"
MANAGE_USER_ROLE_PATH = "/manage/users/{user_id}/role"
MANAGE_EXTENSION_PATH = "/manage/extension"
MANAGE_LLM_PATH = "/manage/llm"
MANAGE_LLM_TIER_PATH = "/manage/llm/tier/{tier}"
MANAGE_LLM_TEST_PATH = "/manage/llm/test"

# Basic email sanity check for B2B invitations (defense in depth). Intentionally
# permissive: it only rejects obviously malformed input (missing @/domain) and
# does not attempt full RFC 5322 validation or catch edge cases like consecutive
# or leading/trailing dots. Microsoft Entra performs the authoritative
# validation when the invitation is created.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Content types and download filenames per browser for the prebuilt archives.
_EXTENSION_META = {
    "chrome": ("application/zip", "buildium-mcp-sidebar-chrome.zip"),
    "firefox": ("application/x-xpinstall", "buildium-mcp-sidebar-firefox.xpi"),
}


def register_management_routes(
    mcp: Any,
    config: BuildiumConfig,
    verifier: TokenVerifier | None,
    audit_recorder: AuditRecorder | None = None,
) -> None:
    """Register the ``/manage/*`` routes on the FastMCP HTTP app."""

    # Reuse a single shared HTTP client across requests for Graph calls to avoid
    # leaking sockets/file descriptors (mirrors the /chat endpoint).
    shared_client: httpx.AsyncClient | None = None

    def _get_shared_client() -> httpx.AsyncClient:
        import httpx

        nonlocal shared_client
        if shared_client is None:
            shared_client = httpx.AsyncClient(timeout=30.0)
        return shared_client

    def _audit(tool: str, op_type: str, outcome: str, **extra: Any) -> None:
        if audit_recorder is None:
            return
        try:
            audit_recorder.record(tool=tool, op_type=op_type, outcome=outcome, **extra)
        except Exception:  # pragma: no cover - auditing must never break a request
            logger.debug("audit emit failed for %s", tool, exc_info=True)

    async def _authenticate(request: Request) -> tuple[bool, dict]:
        """Verify the request, returning ``(authorized, claims)``.

        Mirrors ``chat_endpoint._authenticate``: dev bypass and unconfigured
        auth are open (claims empty); otherwise a valid bearer token is required.
        """
        if config.dev_auth_bypass:
            return True, {}
        if verifier is None:
            return True, {}
        header = request.headers.get("Authorization", "")
        if not header.lower().startswith("bearer "):
            return False, {}
        token = header[len("bearer ") :].strip()
        if not token:
            return False, {}
        try:
            result = await verifier.verify_token(token)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Management request rejected: token verification error", exc_info=True)
            return False, {}
        if result is None:
            return False, {}
        return True, getattr(result, "claims", None) or {}

    async def _require_admin(
        request: Request,
    ) -> tuple[dict | None, JSONResponse | None]:
        """Return ``(claims, None)`` for an admin, else ``(None, error_response)``.

        Emits 401 when unauthenticated, 403 when authenticated but not admin, and
        503 when management is disabled on the server.
        """
        if not config.management_active():
            return None, JSONResponse(
                {"error": "Management is not enabled on this server."}, status_code=503
            )
        authorized, claims = await _authenticate(request)
        if not authorized:
            return None, JSONResponse({"error": "unauthorized"}, status_code=401)
        if not is_admin_claims(config, claims):
            logger.info("Management request rejected: caller is not an admin")
            return None, JSONResponse({"error": "forbidden"}, status_code=403)
        return claims, None

    def _graph() -> GraphClient:
        return GraphClient(config, _get_shared_client())

    def _graph_error_response(exc: GraphError) -> JSONResponse:
        status = 400
        if exc.code in ("graph_not_configured", "graph_unreachable", "graph_auth_failed"):
            status = 502
        elif exc.code == "unknown_role":
            status = 400
        return JSONResponse({"error": exc.message, "code": exc.code}, status_code=status)

    # -----------------------------------------------------------------------
    # Admin UI (HTML page)
    # -----------------------------------------------------------------------

    @mcp.custom_route(MANAGE_ADMIN_UI_PATH, methods=["GET"])
    async def manage_admin_ui(request: Request) -> HTMLResponse:
        """Serve the self-contained LLM configuration admin page.

        The page itself requires no auth token to *load* (it is just HTML with
        no sensitive data). All subsequent API calls it makes use the admin's
        ****** which they enter in the UI. Management must be enabled.
        """
        if not config.management_active():
            return HTMLResponse(
                "<html><body><h1>503 Management Not Enabled</h1>"
                "<p>Set BUILDIUM_MANAGEMENT_ENABLED=true to use this UI.</p></body></html>",
                status_code=503,
            )
        from .llm.admin_page import get_admin_html

        return HTMLResponse(get_admin_html())

    @mcp.custom_route(MANAGE_CAPABILITIES_PATH, methods=["GET"])
    async def manage_capabilities(request: Request) -> JSONResponse:
        # Authenticate but do not hard-require admin: the extension calls this to
        # decide whether to render the admin UI. Non-admins get isAdmin=false.
        authorized, claims = await _authenticate(request)
        if not authorized:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        enabled = config.management_active()
        is_admin = bool(enabled and is_admin_claims(config, claims))
        available = sorted(
            b for b in MANAGEMENT_BROWSERS if config.get_management_extension_path(b)
        )
        store = get_store(config)
        llm_configured = False
        if store is not None:
            llm_cfg = store.load()
            llm_configured = llm_cfg is not None and llm_cfg.is_configured()
        return JSONResponse(
            {
                "enabled": enabled,
                "isAdmin": is_admin,
                "roles": sorted(ENTRA_MAPPABLE_ROLES),
                "extensionBrowsers": available,
                "llmConfigured": llm_configured,
                "userManagementConfigured": config.graph_management_configured(),
            }
        )

    @mcp.custom_route(MANAGE_USERS_PATH, methods=["GET"])
    async def manage_list_users(request: Request) -> JSONResponse:
        claims, error = await _require_admin(request)
        if error is not None:
            return error
        try:
            users = await _graph().list_users_with_roles()
        except GraphError as exc:
            _audit("manage_list_users", "read", "error", code=exc.code, status=exc.status)
            return _graph_error_response(exc)
        _audit("manage_list_users", "read", "success")
        return JSONResponse({"users": users, "count": len(users)})

    @mcp.custom_route(MANAGE_USERS_PATH, methods=["POST"])
    async def manage_invite_user(request: Request) -> JSONResponse:
        claims, error = await _require_admin(request)
        if error is not None:
            return error
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
        email = str((body or {}).get("email") or "").strip()
        role = str((body or {}).get("role") or "").strip().lower()
        if not _EMAIL_RE.match(email):
            return JSONResponse({"error": "A valid 'email' is required."}, status_code=400)
        if role not in ENTRA_MAPPABLE_ROLES:
            return JSONResponse(
                {"error": f"'role' must be one of {sorted(ENTRA_MAPPABLE_ROLES)}."},
                status_code=400,
            )
        client = _graph()
        try:
            invited = await client.invite_guest(email)
            await client.assign_app_role(invited["id"], role)
        except GraphError as exc:
            _audit(
                "manage_invite_user",
                "write",
                "error",
                code=exc.code,
                status=exc.status,
                args={"email": email, "role": role},
            )
            return _graph_error_response(exc)
        _audit(
            "manage_invite_user",
            "write",
            "success",
            args={"email": email, "role": role},
        )
        return JSONResponse(
            {
                "user": {
                    "id": invited["id"],
                    "email": email,
                    "role": role,
                    "status": invited.get("status"),
                }
            },
            status_code=201,
        )

    @mcp.custom_route(MANAGE_USER_ROLE_PATH, methods=["PATCH"])
    async def manage_edit_role(request: Request) -> JSONResponse:
        claims, error = await _require_admin(request)
        if error is not None:
            return error
        user_id = str(request.path_params.get("user_id") or "").strip()
        if not user_id:
            return JSONResponse({"error": "A user id is required."}, status_code=400)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
        role = str((body or {}).get("role") or "").strip().lower()
        if role not in ENTRA_MAPPABLE_ROLES:
            return JSONResponse(
                {"error": f"'role' must be one of {sorted(ENTRA_MAPPABLE_ROLES)}."},
                status_code=400,
            )
        try:
            await _graph().set_user_role(user_id, role)
        except GraphError as exc:
            _audit(
                "manage_edit_role",
                "write",
                "error",
                code=exc.code,
                status=exc.status,
                args={"user_id": user_id, "role": role},
            )
            return _graph_error_response(exc)
        _audit(
            "manage_edit_role",
            "write",
            "success",
            args={"user_id": user_id, "role": role},
        )
        return JSONResponse({"user": {"id": user_id, "role": role}})

    @mcp.custom_route(MANAGE_EXTENSION_PATH, methods=["GET"])
    async def manage_download_extension(request: Request) -> Any:
        claims, error = await _require_admin(request)
        if error is not None:
            return error
        browser = (request.query_params.get("browser") or "").strip().lower()
        if browser not in MANAGEMENT_BROWSERS:
            return JSONResponse(
                {"error": f"'browser' must be one of {sorted(MANAGEMENT_BROWSERS)}."},
                status_code=400,
            )
        path = config.get_management_extension_path(browser)
        if not path or not os.path.isfile(path):
            _audit("manage_download_extension", "read", "error", args={"browser": browser})
            return JSONResponse(
                {"error": f"No prebuilt {browser} extension is configured on this server."},
                status_code=503,
            )
        content_type, filename = _EXTENSION_META[browser]
        _audit("manage_download_extension", "read", "success", args={"browser": browser})
        return FileResponse(
            path,
            media_type=content_type,
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -----------------------------------------------------------------------
    # LLM configuration routes
    # -----------------------------------------------------------------------

    def _get_llm_store():
        store = get_store(config)
        if store is None:
            raise ValueError(
                "The LLM config store is not configured. "
                "Set BUILDIUM_LLM_CONFIG_PATH to a writable file path."
            )
        return store

    @mcp.custom_route(MANAGE_LLM_PATH, methods=["GET"])
    async def manage_llm_get(request: Request) -> JSONResponse:
        """Return the current LLM config with API keys masked."""
        claims, error = await _require_admin(request)
        if error is not None:
            return error
        try:
            store = _get_llm_store()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        llm_cfg = store.load() or LLMConfig()
        _audit("manage_llm_get", "read", "success")
        return JSONResponse(store.to_display(llm_cfg))

    @mcp.custom_route(MANAGE_LLM_PATH, methods=["PUT"])
    async def manage_llm_put(request: Request) -> JSONResponse:
        """Replace the full LLM config (providers + tiers)."""
        claims, error = await _require_admin(request)
        if error is not None:
            return error
        try:
            store = _get_llm_store()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object."}, status_code=400)

        existing = store.load()
        try:
            updated = await store.update_from_body(body, existing)
        except Exception as exc:
            logger.error("LLM config PUT failed: %s", exc)
            _audit("manage_llm_put", "write", "error")
            return JSONResponse({"error": str(exc)}, status_code=500)

        _audit("manage_llm_put", "write", "success")
        return JSONResponse(store.to_display(updated))

    @mcp.custom_route(MANAGE_LLM_TIER_PATH, methods=["PATCH"])
    async def manage_llm_tier_patch(request: Request) -> JSONResponse:
        """Update a single model-tier assignment."""
        claims, error = await _require_admin(request)
        if error is not None:
            return error
        tier = str(request.path_params.get("tier") or "").strip().lower()
        if tier not in TIERS:
            return JSONResponse(
                {"error": f"'tier' must be one of {sorted(TIERS)}."},
                status_code=400,
            )
        try:
            store = _get_llm_store()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be a JSON object."}, status_code=400)
        provider = str((body or {}).get("provider") or "").strip().lower()
        model = str((body or {}).get("model") or "").strip()
        if provider and provider not in PROVIDERS:
            return JSONResponse(
                {"error": f"'provider' must be one of {sorted(PROVIDERS)} or empty."},
                status_code=400,
            )
        try:
            updated = await store.update_tier(tier, provider, model)
        except Exception as exc:
            logger.error("LLM tier PATCH failed: %s", exc)
            _audit("manage_llm_tier_patch", "write", "error", args={"tier": tier})
            return JSONResponse({"error": str(exc)}, status_code=500)
        _audit("manage_llm_tier_patch", "write", "success", args={"tier": tier})
        return JSONResponse(store.to_display(updated))

    @mcp.custom_route(MANAGE_LLM_TEST_PATH, methods=["POST"])
    async def manage_llm_test(request: Request) -> JSONResponse:
        """Validate a provider API key by making a minimal live request."""
        claims, error = await _require_admin(request)
        if error is not None:
            return error
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
        provider = str((body or {}).get("provider") or "").strip().lower()
        api_key = str((body or {}).get("api_key") or "").strip()
        base_url = str((body or {}).get("base_url") or "").strip()
        if provider not in PROVIDERS:
            return JSONResponse(
                {"error": f"'provider' must be one of {sorted(PROVIDERS)}."},
                status_code=400,
            )
        if not api_key:
            # If no key is provided, try to use the stored key for this provider.
            store = get_store(config)
            if store is not None:
                llm_cfg = store.load()
                if llm_cfg is not None:
                    pentry = llm_cfg.providers.get(provider)
                    if pentry:
                        api_key = pentry.api_key or ""
        if not api_key:
            return JSONResponse(
                {"error": f"No API key provided or stored for provider '{provider}'."},
                status_code=400,
            )

        ok, message = await _test_provider(provider, api_key, base_url)
        if ok:
            _audit("manage_llm_test", "read", "success", args={"provider": provider})
            return JSONResponse({"ok": True, "message": message})
        _audit("manage_llm_test", "read", "error", args={"provider": provider})
        return JSONResponse({"ok": False, "message": message}, status_code=502)


async def _test_provider(provider: str, api_key: str, base_url: str) -> tuple[bool, str]:
    """Make a minimal API call to check that the key is valid.

    Returns ``(success, message)`` — never raises.
    """
    import httpx

    from .llm.config_store import DEFAULT_BASE_URLS

    url_base = base_url.strip().rstrip("/") or DEFAULT_BASE_URLS.get(provider, "")
    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if provider == "openai":
                headers["Authorization"] = "Bearer " + api_key
                resp = await client.get(f"{url_base}/models", headers=headers)
                if resp.status_code in (200, 206):
                    return True, "OpenAI connection successful."
                return False, f"OpenAI returned HTTP {resp.status_code}."

            elif provider == "anthropic":
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
                # Use the models endpoint for a cheap, key-validating request.
                resp = await client.get(f"{url_base}/models", headers=headers)
                if resp.status_code in (200, 206):
                    return True, "Anthropic connection successful."
                return False, f"Anthropic returned HTTP {resp.status_code}."

            elif provider == "gemini":
                # List models via REST; the API key goes in the query string.
                resp = await client.get(
                    f"{url_base}/models",
                    params={"key": api_key},
                )
                if resp.status_code in (200, 206):
                    return True, "Gemini connection successful."
                return False, f"Gemini returned HTTP {resp.status_code}."

            return False, f"Unknown provider: {provider}"
    except Exception as exc:
        return False, f"Connection error: {exc}"
