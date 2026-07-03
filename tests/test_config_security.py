"""Tests for security/audit configuration validation and policy construction."""

from __future__ import annotations

import pytest

from mcp_server_buildium.config import BuildiumConfig
from mcp_server_buildium.security.policy import ToolPolicy


def _cfg(**kwargs):
    base = {"client_id": "id", "client_secret": "secret"}
    base.update(kwargs)
    return BuildiumConfig(**base)


def test_defaults_preserve_admin_behavior() -> None:
    cfg = _cfg()
    assert cfg.role == "admin"
    assert cfg.readonly is False
    assert cfg.block_sensitive is False
    assert cfg.audit_sink == "log"
    policy = ToolPolicy.from_config(cfg)
    assert policy.is_allowed("create_bill")


def test_invalid_role_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown BUILDIUM_ROLE"):
        _cfg(role="root")


def test_invalid_audit_sink_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown BUILDIUM_AUDIT_SINK"):
        _cfg(audit_sink="syslog")


def test_file_sink_requires_file_path() -> None:
    with pytest.raises(ValueError, match="BUILDIUM_AUDIT_FILE"):
        _cfg(audit_sink="file")


def test_negative_rate_limit_rejected() -> None:
    with pytest.raises(ValueError, match="RATE_LIMIT"):
        _cfg(rate_limit_per_minute=-1)


def test_policy_from_config_readonly() -> None:
    policy = ToolPolicy.from_config(_cfg(role="readonly"))
    assert policy.is_allowed("list_leases")
    assert not policy.is_allowed("create_lease")


def test_policy_from_config_allow_deny_lists() -> None:
    policy = ToolPolicy.from_config(
        _cfg(allow_tools="list_leases, create_lease", deny_tools="create_lease")
    )
    assert policy.is_allowed("list_leases")
    assert not policy.is_allowed("create_lease")  # deny wins
