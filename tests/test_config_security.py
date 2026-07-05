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


# --- Entra App Role → coarse role mapping (BUILDIUM_ENTRA_ROLE_POLICY_MAP) ----


def test_role_policy_map_unset_returns_none() -> None:
    cfg = _cfg()
    assert cfg.get_entra_role_policy_map() is None


def test_role_policy_map_parsed_and_normalized() -> None:
    cfg = _cfg(entra_role_policy_map='{"Buildium.Admin":"Admin","Buildium.ReadOnly":"readonly"}')
    assert cfg.get_entra_role_policy_map() == {
        "Buildium.Admin": "admin",
        "Buildium.ReadOnly": "readonly",
    }


def test_role_policy_map_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="valid JSON object"):
        _cfg(entra_role_policy_map="not-json")


def test_role_policy_map_rejects_unknown_coarse_role() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        _cfg(entra_role_policy_map='{"Buildium.Root":"root"}')


def test_role_policy_map_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="non-empty JSON object"):
        _cfg(entra_role_policy_map="[]")
