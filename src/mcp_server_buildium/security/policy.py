"""Role-based, config-driven tool access policy and rate limiting.

The policy answers a single question: *may this MCP client invoke this tool?*
It combines several independent, composable guardrails, evaluated so that the
most restrictive rule wins:

* **Role** (``BUILDIUM_ROLE``): ``readonly``, ``operator``, ``admin`` (default),
  or ``custom``. This is the base grant.
* **Read-only kill switch** (``BUILDIUM_READONLY``): blocks every mutating tool.
* **Sensitive block** (``BUILDIUM_BLOCK_SENSITIVE``): blocks financially
  sensitive tools (bills, bank accounts, general ledger, payments, file URLs).
* **Allow list** (``BUILDIUM_ALLOW_TOOLS``): when set, grants exactly the listed
  tools (a whitelist) on top of the role.
* **Deny list** (``BUILDIUM_DENY_TOOLS``): always removes the listed tools.

Everything defaults to today's behavior: an unset configuration yields the
``admin`` role with no extra guardrails, so all tools are permitted.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from ..tools import _common as c

# Built-in roles. ``custom`` behaves like ``admin`` as a base and is intended to
# be shaped entirely by the allow/deny/readonly/sensitive guardrails.
ROLES = frozenset({"readonly", "operator", "admin", "custom"})
DEFAULT_ROLE = "admin"

# Relative permissiveness of the coarse roles an Entra identity may map to. Used
# to pick the most permissive role when a caller matches several App Roles/groups.
ROLE_RANK = {"readonly": 1, "operator": 2, "admin": 3}

# Tools that only the ``admin`` role (or an explicit allow-list entry) may use,
# regardless of their read/write classification.
ADMIN_ONLY_TOOLS = frozenset({"audit_summary"})


def _op_type(tool_name: str) -> str:
    meta = c.TOOL_METADATA.get(tool_name)
    if meta is not None:
        return meta["op_type"]
    return c.classify_op_type(tool_name)


def _sensitive(tool_name: str) -> bool:
    meta = c.TOOL_METADATA.get(tool_name)
    if meta is not None:
        return bool(meta["sensitive"])
    return c.classify_sensitive(tool_name)


@dataclass(frozen=True)
class PolicyDecision:
    """Result of evaluating the policy for a single tool."""

    allowed: bool
    reason: str


@dataclass
class ToolPolicy:
    """A resolved access policy for the server process."""

    role: str = DEFAULT_ROLE
    readonly: bool = False
    block_sensitive: bool = False
    allow_tools: frozenset[str] = field(default_factory=frozenset)
    deny_tools: frozenset[str] = field(default_factory=frozenset)
    deny_all: bool = False

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Unknown role {self.role!r}. Valid roles: {sorted(ROLES)}")

    # -- construction -------------------------------------------------------
    @classmethod
    def from_config(cls, config) -> ToolPolicy:  # noqa: ANN001 - avoids import cycle
        """Build a policy from a :class:`BuildiumConfig`-like object."""
        return cls(
            role=(config.role or DEFAULT_ROLE).strip().lower(),
            readonly=bool(config.readonly),
            block_sensitive=bool(config.block_sensitive),
            allow_tools=frozenset(_split_csv(config.allow_tools)),
            deny_tools=frozenset(_split_csv(config.deny_tools)),
        )

    # -- evaluation ---------------------------------------------------------
    def _role_allows(self, tool_name: str, op_type: str, sensitive: bool) -> bool:
        if tool_name in ADMIN_ONLY_TOOLS:
            return self.role == "admin"
        if self.role in ("admin", "custom"):
            return True
        if self.role == "readonly":
            return op_type == "read"
        if self.role == "operator":
            # Reads plus non-sensitive writes.
            return op_type == "read" or not sensitive
        return False

    def decide(self, tool_name: str) -> PolicyDecision:
        """Return a :class:`PolicyDecision` for ``tool_name``."""
        if self.deny_all:
            return PolicyDecision(False, "no permitted role for this identity")

        op_type = _op_type(tool_name)
        sensitive = _sensitive(tool_name)

        # Deny list always wins.
        if tool_name in self.deny_tools:
            return PolicyDecision(False, "denied by BUILDIUM_DENY_TOOLS")

        # Base grant from role, or a strict whitelist when an allow list is set.
        if self.allow_tools:
            if tool_name not in self.allow_tools:
                return PolicyDecision(False, "not in BUILDIUM_ALLOW_TOOLS")
            # An allow-list entry grants the tool (subject to the guardrails
            # below), overriding the role — except admin-only tools still
            # require the admin role.
            allowed = tool_name not in ADMIN_ONLY_TOOLS or self.role == "admin"
            reason = "granted by BUILDIUM_ALLOW_TOOLS"
        else:
            allowed = self._role_allows(tool_name, op_type, sensitive)
            reason = f"role={self.role}"

        # Additional guardrails trim the grant.
        if self.readonly and op_type == "write":
            return PolicyDecision(False, "blocked by BUILDIUM_READONLY")
        if self.block_sensitive and sensitive:
            return PolicyDecision(False, "blocked by BUILDIUM_BLOCK_SENSITIVE")

        if not allowed:
            return PolicyDecision(False, reason)
        return PolicyDecision(True, reason)

    def is_allowed(self, tool_name: str) -> bool:
        """Convenience wrapper returning just the boolean decision."""
        return self.decide(tool_name).allowed

    def describe(self) -> dict[str, object]:
        """Return a JSON-serializable summary of the effective policy."""
        return {
            "role": self.role,
            "readonly": self.readonly,
            "block_sensitive": self.block_sensitive,
            "allow_tools": sorted(self.allow_tools),
            "deny_tools": sorted(self.deny_tools),
        }


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


# A shared deny-everything policy for identities that match no configured role.
DENY_ALL = ToolPolicy(role="custom", deny_all=True)


@dataclass
class CombinedPolicy:
    """Intersect several policies: a tool is allowed only if *all* allow it.

    Used to narrow the server-wide (process) policy by a per-request policy
    derived from the caller's Entra App Role, without collapsing the two grants
    into a single role. The first policy to deny wins, mirroring the
    most-restrictive-rule-wins semantics of :class:`ToolPolicy`.
    """

    policies: tuple[ToolPolicy, ...]

    def decide(self, tool_name: str) -> PolicyDecision:
        for policy in self.policies:
            decision = policy.decide(tool_name)
            if not decision.allowed:
                return decision
        return PolicyDecision(True, "allowed by all policies")

    def is_allowed(self, tool_name: str) -> bool:
        return self.decide(tool_name).allowed


def _claim_values(claims: dict) -> set[str]:
    """Collect App Role and group identifiers from verified token claims."""
    values: set[str] = set()
    for key in ("roles", "groups"):
        raw = claims.get(key)
        if isinstance(raw, str):
            values.add(raw)
        elif isinstance(raw, (list, tuple)):
            values.update(str(item) for item in raw)
    return values


def _most_permissive(roles: list[str]) -> str:
    """Return the most permissive coarse role from a non-empty list."""
    return max(roles, key=lambda r: ROLE_RANK.get(r, 0))


def effective_policy_for_claims(
    base: ToolPolicy,
    role_map: dict[str, str] | None,
    claims: dict,
) -> ToolPolicy | CombinedPolicy:
    """Resolve the per-request policy for a caller from their token claims.

    When ``role_map`` is empty/None the base (server-wide) policy is returned
    unchanged, preserving today's behavior. Otherwise the caller's ``roles``/
    ``groups`` claims are mapped to a coarse role and intersected with the base
    policy (server ceiling ∩ user grant). Callers matching no mapped role/group
    are denied every tool.
    """
    if not role_map:
        return base
    values = _claim_values(claims or {})
    matched = [role_map[v] for v in values if v in role_map]
    if not matched:
        return DENY_ALL
    return CombinedPolicy((base, ToolPolicy(role=_most_permissive(matched))))


def is_admin_claims(config, claims: dict) -> bool:  # noqa: ANN001 - avoid import cycle
    """Return True when the caller resolves to the coarse ``admin`` role.

    This reuses the exact admin notion that governs :data:`ADMIN_ONLY_TOOLS`:
    the caller's effective policy (server-wide ceiling intersected with their
    Entra App Role, via :func:`effective_policy_for_claims`) must permit every
    admin-only tool. So a caller is "admin" iff their token maps — through
    ``BUILDIUM_ENTRA_ROLE_POLICY_MAP`` — to the ``admin`` role (and the server
    role ceiling is also ``admin``). When no role map is configured the decision
    falls back to the server-wide role, matching non-scoped behavior.

    Args:
        config: A :class:`~mcp_server_buildium.config.BuildiumConfig`-like object.
        claims: Verified JWT claims (empty dict when no token was validated).

    Returns:
        Whether the caller is authorized for admin-only management actions.
    """
    base = ToolPolicy.from_config(config)
    role_map = config.get_entra_role_policy_map()
    effective = effective_policy_for_claims(base, role_map, claims or {})
    probes = ADMIN_ONLY_TOOLS or frozenset({"audit_summary"})
    return all(effective.is_allowed(tool) for tool in probes)


class RateLimiter:
    """A simple in-process sliding-window rate limiter.

    Caps the number of tool invocations per rolling 60-second window. A limit of
    ``0`` (the default) disables limiting entirely. Intended as a guardrail
    against runaway automated loops, not as a distributed quota system.
    """

    def __init__(self, per_minute: int = 0) -> None:
        self.per_minute = max(0, int(per_minute))
        self._events: deque[float] = deque()

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0

    def allow(self, *, now: float | None = None) -> bool:
        """Record an attempt and return whether it is within the limit."""
        if not self.enabled:
            return True
        current = time.monotonic() if now is None else now
        window_start = current - 60.0
        events = self._events
        while events and events[0] < window_start:
            events.popleft()
        if len(events) >= self.per_minute:
            return False
        events.append(current)
        return True
