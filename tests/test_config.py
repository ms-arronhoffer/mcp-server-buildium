"""Unit tests for the transport, Entra, and CORS configuration fields."""

from __future__ import annotations

import pytest

from mcp_server_buildium.config import BuildiumConfig

_BASE = {"client_id": "cid", "client_secret": "secret"}

# Environment variables that must not leak into these unit tests (other test
# modules set some of these at import time to configure the server).
_LEAKY_ENV = [
    "BUILDIUM_TRANSPORT",
    "BUILDIUM_HOST",
    "BUILDIUM_PORT",
    "BUILDIUM_MCP_PATH",
    "BUILDIUM_MCP_AUTH_TOKEN",
    "BUILDIUM_ENTRA_TENANT_ID",
    "BUILDIUM_ENTRA_AUDIENCE",
    "BUILDIUM_ENTRA_ISSUER",
    "BUILDIUM_ENTRA_JWKS_URI",
    "BUILDIUM_ENTRA_REQUIRED_SCOPES",
    "BUILDIUM_CORS_ALLOW_ORIGINS",
    "BUILDIUM_CATEGORIES",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LEAKY_ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_stdio_and_no_auth() -> None:
    cfg = BuildiumConfig(**_BASE)
    assert cfg.transport == "stdio"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8000
    assert cfg.mcp_path == "/mcp"
    assert cfg.entra_enabled() is False
    assert cfg.get_entra_issuer() is None
    assert cfg.get_entra_jwks_uri() is None
    assert cfg.get_entra_scopes() is None
    assert cfg.get_cors_origins() is None


def test_invalid_transport_rejected() -> None:
    with pytest.raises(ValueError, match="BUILDIUM_TRANSPORT"):
        BuildiumConfig(**_BASE, transport="grpc")


def test_entra_requires_audience() -> None:
    with pytest.raises(ValueError, match="BUILDIUM_ENTRA_AUDIENCE"):
        BuildiumConfig(**_BASE, entra_tenant_id="tid")


def test_entra_issuer_and_jwks_derived_from_tenant() -> None:
    cfg = BuildiumConfig(**_BASE, entra_tenant_id="tid-123", entra_audience="api://app")
    assert cfg.entra_enabled() is True
    assert cfg.get_entra_issuer() == "https://login.microsoftonline.com/tid-123/v2.0"
    assert cfg.get_entra_jwks_uri() == (
        "https://login.microsoftonline.com/tid-123/discovery/v2.0/keys"
    )


def test_entra_issuer_and_jwks_overrides_win() -> None:
    cfg = BuildiumConfig(
        **_BASE,
        entra_tenant_id="tid",
        entra_audience="api://app",
        entra_issuer="https://custom/issuer",
        entra_jwks_uri="https://custom/keys",
    )
    assert cfg.get_entra_issuer() == "https://custom/issuer"
    assert cfg.get_entra_jwks_uri() == "https://custom/keys"


def test_entra_scopes_parsing() -> None:
    cfg = BuildiumConfig(
        **_BASE,
        entra_tenant_id="tid",
        entra_audience="api://app",
        entra_required_scopes=" MCP.Access , User.Read ,",
    )
    assert cfg.get_entra_scopes() == ["MCP.Access", "User.Read"]


def test_cors_origins_parsing() -> None:
    cfg = BuildiumConfig(
        **_BASE,
        cors_allow_origins="chrome-extension://abc, moz-extension://def ,",
    )
    assert cfg.get_cors_origins() == ["chrome-extension://abc", "moz-extension://def"]
