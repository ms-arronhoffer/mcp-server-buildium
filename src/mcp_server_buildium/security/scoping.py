"""Per-identity tool scoping middleware for the Streamable HTTP transport.

The process-wide :class:`ToolPolicy` (see :mod:`.policy`) is the *ceiling*: it
decides which tools are registered at all. This middleware adds a *per-request*
inner bound derived from the caller's Microsoft Entra **App Role** (the token
``roles`` claim; group object IDs from ``groups`` also match), configured via
``BUILDIUM_ENTRA_ROLE_POLICY_MAP``.

It enforces the caller's effective policy at both surfaces the plan requires:

* **Visibility** — ``tools/list`` only advertises tools the caller may use.
* **Call-time denial** — ``tools/call`` rejects tools outside the caller's grant
  (defense-in-depth, so a client cannot invoke an unadvertised tool).

When no role map is configured, or Entra auth is not in use, the middleware is a
no-op and today's behavior is preserved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware

from ..logging_config import get_logger
from .policy import DENY_ALL, effective_policy_for_claims

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import BuildiumConfig
    from .policy import CombinedPolicy, ToolPolicy

logger = get_logger("mcp_server_buildium.security.scoping")


class EntraScopingMiddleware(Middleware):
    """Narrow the advertised/callable tools to the caller's Entra App Role."""

    def __init__(self, config: BuildiumConfig, base_policy: ToolPolicy) -> None:
        self._config = config
        self._base_policy = base_policy
        self._role_map = config.get_entra_role_policy_map()
        # Only active when a role map is configured *and* Entra auth verifies
        # tokens (so a trusted ``roles`` claim is actually present).
        self._active = bool(self._role_map) and config.entra_enabled()

    @property
    def active(self) -> bool:
        """True when per-identity scoping is configured and enforced."""
        return self._active

    def _effective_policy(self) -> ToolPolicy | CombinedPolicy:
        """Resolve the effective policy for the current request's identity."""
        token = get_access_token()
        if token is None:
            # Scoping is active but no verified identity is available: deny all.
            return DENY_ALL
        claims = getattr(token, "claims", None) or {}
        return effective_policy_for_claims(self._base_policy, self._role_map, claims)

    async def on_list_tools(self, context: Any, call_next: Any) -> Any:
        tools = await call_next(context)
        if not self._active:
            return tools
        policy = self._effective_policy()
        return [tool for tool in tools if policy.is_allowed(tool.name)]

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        if self._active:
            policy = self._effective_policy()
            name = context.message.name
            if not policy.is_allowed(name):
                logger.info("Tool %s denied for caller by Entra role scoping", name)
                raise ToolError(f"Tool '{name}' is not permitted for your role.")
        return await call_next(context)
