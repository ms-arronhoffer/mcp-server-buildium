"""Thin Microsoft Graph client for the admin management routes.

Used server-side only by ``management_endpoint.py`` to:

* invite Microsoft Entra **B2B guests** (``POST /invitations``), and
* read/create/delete **app-role assignments** on the API app's service
  principal, binding a user to one of the API app's Entra App Roles.

The client authenticates with the **client-credentials** (app-only) grant using
a dedicated Entra app registration whose credentials
(``BUILDIUM_GRAPH_CLIENT_ID`` / ``BUILDIUM_GRAPH_CLIENT_SECRET``) never leave the
server. The app registration must hold the admin-consented application
permissions ``User.Invite.All``, ``AppRoleAssignment.ReadWrite.All`` and
``Application.Read.All``.

Coarse role names (``admin``/``operator``/``readonly``) are translated to Entra
**App Role IDs** through ``BUILDIUM_ENTRA_APP_ROLE_ID_MAP`` so the same role
vocabulary flows end to end and matches ``BUILDIUM_ENTRA_ROLE_POLICY_MAP``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx

    from .config import BuildiumConfig

logger = get_logger("mcp_server_buildium.graph")

# Default landing page for redeemed B2B invitations.
_DEFAULT_INVITE_REDIRECT_URL = "https://myapps.microsoft.com"

# Refresh app-only tokens this many seconds before their stated expiry.
_TOKEN_REFRESH_MARGIN_SECONDS = 60


class GraphError(Exception):
    """A Microsoft Graph call failed.

    Carries an HTTP ``status`` (0 for transport/auth errors) and a
    machine-readable ``code`` so callers can translate it into a friendly
    response envelope without leaking Graph internals.
    """

    def __init__(self, message: str, *, status: int = 0, code: str = "graph_error") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class GraphClient:
    """Minimal async Microsoft Graph client (app-only credentials)."""

    def __init__(self, config: BuildiumConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # -- auth ---------------------------------------------------------------
    async def _access_token(self) -> str:
        """Return a cached app-only Graph token, minting a new one when stale."""
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        tenant = self._config.get_graph_tenant_id()
        if not tenant:
            raise GraphError("Graph tenant is not configured", code="graph_not_configured")
        url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        body = {
            "client_id": self._config.graph_client_id or "",
            "client_secret": self._config.graph_client_secret or "",
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        }
        try:
            resp = await self._client.post(
                url,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as exc:  # pragma: no cover - transport failure
            raise GraphError(
                "Could not reach the Microsoft identity platform", code="graph_unreachable"
            ) from exc
        data = _safe_json(resp)
        if resp.status_code != 200 or not data.get("access_token"):
            desc = data.get("error_description") or data.get("error") or "token request failed"
            raise GraphError(
                f"Graph token request failed: {desc}",
                status=resp.status_code,
                code="graph_auth_failed",
            )
        self._token = str(data["access_token"])
        expires_in = int(data.get("expires_in") or 3600)
        self._token_expires_at = now + max(0, expires_in - _TOKEN_REFRESH_MARGIN_SECONDS)
        return self._token

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> Any:
        """Issue an authenticated Graph request and return parsed JSON (or None)."""
        token = await self._access_token()
        url = f"{self._config.graph_base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            resp = await self._client.request(
                method,
                url,
                json=json_body,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json",
                },
            )
        except Exception as exc:  # pragma: no cover - transport failure
            raise GraphError(
                "Could not reach Microsoft Graph", code="graph_unreachable"
            ) from exc
        if resp.status_code == 204 or not resp.content:
            return None
        data = _safe_json(resp)
        if resp.status_code >= 400:
            err = data.get("error") if isinstance(data, dict) else None
            message = (err or {}).get("message") if isinstance(err, dict) else None
            code = (err or {}).get("code") if isinstance(err, dict) else None
            raise GraphError(
                message or f"Graph request failed ({resp.status_code})",
                status=resp.status_code,
                code=str(code or "graph_error"),
            )
        return data

    # -- role mapping -------------------------------------------------------
    def _app_role_id(self, role: str) -> str:
        role_ids = self._config.get_entra_app_role_id_map()
        app_role_id = role_ids.get((role or "").strip().lower())
        if not app_role_id:
            raise GraphError(
                f"No Entra App Role ID configured for role {role!r}",
                code="unknown_role",
            )
        return app_role_id

    # -- high-level operations ---------------------------------------------
    async def invite_guest(self, email: str) -> dict[str, Any]:
        """Send an Entra B2B invitation and return the invited user's info.

        Returns a dict with at least ``id`` (the invited user's object ID) and
        ``email``.
        """
        redirect = (
            self._config.management_invite_redirect_url
            or _DEFAULT_INVITE_REDIRECT_URL
        )
        payload = {
            "invitedUserEmailAddress": email,
            "inviteRedirectUrl": redirect,
            "sendInvitationMessage": bool(self._config.management_send_invitation_message),
        }
        data = await self._request("POST", "/invitations", json_body=payload)
        invited = (data or {}).get("invitedUser") or {}
        user_id = invited.get("id")
        if not user_id:
            raise GraphError(
                "Graph did not return an invited user ID", code="invite_failed"
            )
        return {
            "id": str(user_id),
            "email": email,
            "status": (data or {}).get("status"),
            "invite_redeem_url": (data or {}).get("inviteRedeemUrl"),
        }

    async def assign_app_role(self, user_object_id: str, role: str) -> dict[str, Any]:
        """Create an app-role assignment binding a user to a role on the API SP."""
        sp_id = self._config.entra_api_service_principal_id
        payload = {
            "principalId": user_object_id,
            "resourceId": sp_id,
            "appRoleId": self._app_role_id(role),
        }
        data = await self._request(
            "POST", f"/servicePrincipals/{sp_id}/appRoleAssignedTo", json_body=payload
        )
        return data or {}

    async def remove_app_role_assignment(self, assignment_id: str) -> None:
        """Delete an app-role assignment from the API service principal."""
        sp_id = self._config.entra_api_service_principal_id
        await self._request(
            "DELETE", f"/servicePrincipals/{sp_id}/appRoleAssignedTo/{assignment_id}"
        )

    async def list_app_role_assignments(self) -> list[dict[str, Any]]:
        """List raw app-role assignments on the API service principal."""
        sp_id = self._config.entra_api_service_principal_id
        data = await self._request(
            "GET", f"/servicePrincipals/{sp_id}/appRoleAssignedTo"
        )
        value = (data or {}).get("value") if isinstance(data, dict) else None
        return list(value or [])

    async def list_users_with_roles(self) -> list[dict[str, Any]]:
        """Return users assigned to the API app with their resolved coarse role."""
        reverse = self._config.get_app_role_id_to_role()
        assignments = await self.list_app_role_assignments()
        users: list[dict[str, Any]] = []
        for a in assignments:
            # Only surface user (not group/servicePrincipal) assignments.
            if (a.get("principalType") or "User") != "User":
                continue
            users.append(
                {
                    "assignment_id": a.get("id"),
                    "user_id": a.get("principalId"),
                    "display_name": a.get("principalDisplayName"),
                    "app_role_id": a.get("appRoleId"),
                    "role": reverse.get(str(a.get("appRoleId")), "unknown"),
                }
            )
        return users

    async def set_user_role(self, user_object_id: str, role: str) -> dict[str, Any]:
        """Assign ``role`` to a user, removing their prior role assignments.

        Adds the new app-role assignment first, then removes any other existing
        assignments for that user on the API service principal so the user ends
        up with exactly one role.
        """
        new_role_id = self._app_role_id(role)
        existing = [
            a
            for a in await self.list_app_role_assignments()
            if str(a.get("principalId")) == str(user_object_id)
        ]
        created = await self.assign_app_role(user_object_id, role)
        for a in existing:
            if str(a.get("appRoleId")) == str(new_role_id):
                # Already had the target role; leave it (created is the canonical one).
                continue
            assignment_id = a.get("id")
            if assignment_id:
                await self.remove_app_role_assignment(str(assignment_id))
        return created


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    """Parse a response body as JSON, returning ``{}`` on failure."""
    try:
        parsed = resp.json()
    except Exception:  # pragma: no cover - non-JSON body
        return {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}
