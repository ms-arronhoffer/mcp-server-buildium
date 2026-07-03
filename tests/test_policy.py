"""Tests for the security policy layer (roles, guardrails, rate limiting)."""

from __future__ import annotations

import pytest

from mcp_server_buildium.security.policy import RateLimiter, ToolPolicy


def test_admin_role_allows_everything() -> None:
    p = ToolPolicy(role="admin")
    assert p.is_allowed("list_leases")
    assert p.is_allowed("create_lease")
    assert p.is_allowed("create_bill")
    # admin-only tool is allowed for admin
    assert p.is_allowed("audit_summary")


def test_readonly_role_blocks_writes() -> None:
    p = ToolPolicy(role="readonly")
    assert p.is_allowed("list_leases")
    assert p.is_allowed("get_rental")
    assert not p.is_allowed("create_lease")
    assert not p.is_allowed("update_rental")
    # server-local read tool still allowed
    assert p.is_allowed("health_check")


def test_operator_role_blocks_sensitive_writes_only() -> None:
    p = ToolPolicy(role="operator")
    assert p.is_allowed("create_rental")  # non-sensitive write
    assert p.is_allowed("list_bills")  # sensitive read still ok
    assert not p.is_allowed("create_bill")  # sensitive write blocked
    assert not p.is_allowed("create_bank_account")


def test_readonly_flag_overrides_admin_for_writes() -> None:
    p = ToolPolicy(role="admin", readonly=True)
    assert p.is_allowed("list_leases")
    assert not p.is_allowed("create_lease")


def test_block_sensitive_flag() -> None:
    p = ToolPolicy(role="admin", block_sensitive=True)
    assert p.is_allowed("list_leases")
    assert not p.is_allowed("create_bill")
    assert not p.is_allowed("get_bank_account")


def test_deny_list_always_wins() -> None:
    p = ToolPolicy(role="admin", deny_tools=frozenset({"create_lease"}))
    assert not p.is_allowed("create_lease")
    assert p.is_allowed("update_lease")


def test_allow_list_acts_as_whitelist() -> None:
    p = ToolPolicy(role="readonly", allow_tools=frozenset({"list_leases", "create_lease"}))
    # allow list grants create_lease despite readonly role base
    assert p.is_allowed("create_lease")
    assert p.is_allowed("list_leases")
    # a read tool not in the allow list is now excluded
    assert not p.is_allowed("get_rental")


def test_deny_beats_allow() -> None:
    p = ToolPolicy(
        role="admin",
        allow_tools=frozenset({"create_lease"}),
        deny_tools=frozenset({"create_lease"}),
    )
    assert not p.is_allowed("create_lease")


def test_audit_summary_requires_admin() -> None:
    assert not ToolPolicy(role="operator").is_allowed("audit_summary")
    assert not ToolPolicy(role="readonly").is_allowed("audit_summary")
    assert ToolPolicy(role="admin").is_allowed("audit_summary")


def test_unknown_role_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown role"):
        ToolPolicy(role="superuser")


def test_describe_is_serializable() -> None:
    p = ToolPolicy(role="operator", deny_tools=frozenset({"create_bill"}))
    described = p.describe()
    assert described["role"] == "operator"
    assert described["deny_tools"] == ["create_bill"]


def test_rate_limiter_disabled_by_default() -> None:
    limiter = RateLimiter(0)
    assert not limiter.enabled
    for _ in range(1000):
        assert limiter.allow()


def test_rate_limiter_enforces_window() -> None:
    limiter = RateLimiter(3)
    now = 100.0
    assert limiter.allow(now=now)
    assert limiter.allow(now=now)
    assert limiter.allow(now=now)
    # fourth within the same window is rejected
    assert not limiter.allow(now=now)
    # after the window slides, allowed again
    assert limiter.allow(now=now + 61)


# --- Per-identity (Entra App Role) scoping -------------------------------------

from mcp_server_buildium.security.policy import (  # noqa: E402
    DENY_ALL,
    CombinedPolicy,
    effective_policy_for_claims,
)


def test_deny_all_policy_blocks_every_tool() -> None:
    assert not DENY_ALL.is_allowed("list_leases")
    assert not DENY_ALL.is_allowed("health_check")


def test_combined_policy_is_the_intersection() -> None:
    # Server ceiling: operator (reads + non-sensitive writes).
    base = ToolPolicy(role="operator")
    # User grant: readonly. The intersection must be readonly.
    combined = CombinedPolicy((base, ToolPolicy(role="readonly")))
    assert combined.is_allowed("list_leases")
    assert not combined.is_allowed("create_lease")  # denied by the readonly grant


def test_effective_policy_no_map_returns_base() -> None:
    base = ToolPolicy(role="admin")
    assert effective_policy_for_claims(base, None, {"roles": ["Anything"]}) is base
    assert effective_policy_for_claims(base, {}, {}) is base


def test_effective_policy_unmapped_identity_is_denied() -> None:
    base = ToolPolicy(role="admin")
    role_map = {"Buildium.Admin": "admin"}
    eff = effective_policy_for_claims(base, role_map, {"roles": ["Other.Role"]})
    assert eff is DENY_ALL
    # Missing roles claim entirely -> also denied.
    assert effective_policy_for_claims(base, role_map, {}) is DENY_ALL


def test_effective_policy_maps_role_and_intersects_with_base() -> None:
    base = ToolPolicy(role="admin")  # permissive server ceiling
    role_map = {"Buildium.ReadOnly": "readonly", "Buildium.Operator": "operator"}
    eff = effective_policy_for_claims(base, role_map, {"roles": ["Buildium.ReadOnly"]})
    assert eff.is_allowed("list_leases")
    assert not eff.is_allowed("create_lease")


def test_effective_policy_picks_most_permissive_matched_role() -> None:
    base = ToolPolicy(role="admin")
    role_map = {"R1": "readonly", "R2": "operator"}
    eff = effective_policy_for_claims(base, role_map, {"roles": ["R1", "R2"]})
    # operator > readonly, so non-sensitive writes are allowed.
    assert eff.is_allowed("create_lease")
    # ...but sensitive writes remain blocked at operator level.
    assert not eff.is_allowed("create_bill")


def test_effective_policy_matches_group_claim_ids() -> None:
    base = ToolPolicy(role="admin")
    role_map = {"00000000-group-id": "readonly"}
    eff = effective_policy_for_claims(base, role_map, {"groups": ["00000000-group-id"]})
    assert eff.is_allowed("list_leases")
    assert not eff.is_allowed("create_lease")


def test_base_ceiling_constrains_user_role() -> None:
    # Server ceiling is readonly; a user mapped to admin still cannot write.
    base = ToolPolicy(role="readonly")
    role_map = {"Buildium.Admin": "admin"}
    eff = effective_policy_for_claims(base, role_map, {"roles": ["Buildium.Admin"]})
    assert eff.is_allowed("list_leases")
    assert not eff.is_allowed("create_lease")
