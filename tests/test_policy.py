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
