"""Unit tests for authentication provider selection and Entra JWT verification."""

from __future__ import annotations

import pytest
from fastmcp.server.auth.providers.jwt import (
    JWTVerifier,
    RSAKeyPair,
    StaticTokenVerifier,
)

from mcp_server_buildium.auth import (
    MultiIssuerJWTVerifier,
    build_auth,
    build_entra_verifier,
)
from mcp_server_buildium.config import BuildiumConfig

_BASE = {"client_id": "cid", "client_secret": "secret"}

_LEAKY_ENV = [
    "BUILDIUM_MCP_AUTH_TOKEN",
    "BUILDIUM_ENTRA_TENANT_ID",
    "BUILDIUM_ENTRA_AUDIENCE",
    "BUILDIUM_ENTRA_ISSUER",
    "BUILDIUM_ENTRA_JWKS_URI",
    "BUILDIUM_ENTRA_REQUIRED_SCOPES",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LEAKY_ENV:
        monkeypatch.delenv(name, raising=False)


def test_build_auth_returns_none_without_config() -> None:
    assert build_auth(BuildiumConfig(**_BASE)) is None


def test_build_auth_static_token() -> None:
    cfg = BuildiumConfig(**_BASE, mcp_auth_token="shared-secret")
    verifier = build_auth(cfg)
    assert isinstance(verifier, StaticTokenVerifier)


def test_build_auth_prefers_entra_over_static_token() -> None:
    cfg = BuildiumConfig(
        **_BASE,
        mcp_auth_token="shared-secret",
        entra_tenant_id="tid",
        entra_audience="api://app",
    )
    verifier = build_auth(cfg)
    assert isinstance(verifier, JWTVerifier)


def test_entra_verifier_configuration() -> None:
    cfg = BuildiumConfig(
        **_BASE,
        entra_tenant_id="tid-123",
        entra_audience="api://app-guid",
        entra_required_scopes="MCP.Access",
    )
    verifier = build_entra_verifier(cfg)
    assert verifier.audience == "api://app-guid"
    # No single issuer is set; both the v1 and v2 Entra issuers are accepted.
    assert verifier.issuer is None
    assert verifier.accepted_issuers == [
        "https://login.microsoftonline.com/tid-123/v2.0",
        "https://sts.windows.net/tid-123/",
    ]
    assert verifier.required_scopes == ["MCP.Access"]


def test_entra_verifier_explicit_issuer_is_sole_accepted() -> None:
    cfg = BuildiumConfig(
        **_BASE,
        entra_tenant_id="tid-123",
        entra_audience="api://app-guid",
        entra_issuer="https://custom.example.com/issuer",
    )
    verifier = build_entra_verifier(cfg)
    assert verifier.accepted_issuers == ["https://custom.example.com/issuer"]


@pytest.fixture(scope="module")
def key_pair() -> RSAKeyPair:
    return RSAKeyPair.generate()


def _verifier(key_pair: RSAKeyPair, **kwargs: object) -> JWTVerifier:
    """Build a JWTVerifier that trusts ``key_pair`` (mirrors Entra JWKS wiring)."""
    return JWTVerifier(
        public_key=key_pair.public_key,
        issuer="https://login.microsoftonline.com/tid/v2.0",
        audience="api://app",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_valid_token_accepted(key_pair: RSAKeyPair) -> None:
    verifier = _verifier(key_pair)
    token = key_pair.create_token(
        issuer="https://login.microsoftonline.com/tid/v2.0",
        audience="api://app",
        subject="user-1",
    )
    result = await verifier.verify_token(token)
    assert result is not None


@pytest.mark.asyncio
async def test_wrong_audience_rejected(key_pair: RSAKeyPair) -> None:
    verifier = _verifier(key_pair)
    token = key_pair.create_token(
        issuer="https://login.microsoftonline.com/tid/v2.0",
        audience="api://other",
        subject="user-1",
    )
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_wrong_issuer_rejected(key_pair: RSAKeyPair) -> None:
    verifier = _verifier(key_pair)
    token = key_pair.create_token(
        issuer="https://evil.example.com/v2.0",
        audience="api://app",
        subject="user-1",
    )
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_expired_token_rejected(key_pair: RSAKeyPair) -> None:
    verifier = _verifier(key_pair)
    token = key_pair.create_token(
        issuer="https://login.microsoftonline.com/tid/v2.0",
        audience="api://app",
        subject="user-1",
        expires_in_seconds=-10,
    )
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_missing_required_scope_rejected(key_pair: RSAKeyPair) -> None:
    verifier = _verifier(key_pair, required_scopes=["MCP.Access"])
    token = key_pair.create_token(
        issuer="https://login.microsoftonline.com/tid/v2.0",
        audience="api://app",
        subject="user-1",
        scopes=["Other.Scope"],
    )
    assert await verifier.verify_token(token) is None


def _multi_issuer_verifier(key_pair: RSAKeyPair, **kwargs: object) -> MultiIssuerJWTVerifier:
    """Build a MultiIssuerJWTVerifier trusting ``key_pair`` for both Entra issuers."""
    return MultiIssuerJWTVerifier(
        accepted_issuers=[
            "https://login.microsoftonline.com/tid/v2.0",
            "https://sts.windows.net/tid/",
        ],
        public_key=key_pair.public_key,
        audience="api://app",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_multi_issuer_accepts_v2_token(key_pair: RSAKeyPair) -> None:
    verifier = _multi_issuer_verifier(key_pair)
    token = key_pair.create_token(
        issuer="https://login.microsoftonline.com/tid/v2.0",
        audience="api://app",
        subject="user-1",
    )
    assert await verifier.verify_token(token) is not None


@pytest.mark.asyncio
async def test_multi_issuer_accepts_v1_token(key_pair: RSAKeyPair) -> None:
    verifier = _multi_issuer_verifier(key_pair)
    token = key_pair.create_token(
        issuer="https://sts.windows.net/tid/",
        audience="api://app",
        subject="user-1",
    )
    assert await verifier.verify_token(token) is not None


@pytest.mark.asyncio
async def test_multi_issuer_rejects_unknown_issuer(key_pair: RSAKeyPair) -> None:
    verifier = _multi_issuer_verifier(key_pair)
    token = key_pair.create_token(
        issuer="https://evil.example.com/v2.0",
        audience="api://app",
        subject="user-1",
    )
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_multi_issuer_empty_accepted_set_rejects_all(key_pair: RSAKeyPair) -> None:
    """Fail secure: an empty accepted-issuer set must reject every token."""
    verifier = MultiIssuerJWTVerifier(
        accepted_issuers=[],
        public_key=key_pair.public_key,
        audience="api://app",
    )
    token = key_pair.create_token(
        issuer="https://login.microsoftonline.com/tid/v2.0",
        audience="api://app",
        subject="user-1",
    )
    assert await verifier.verify_token(token) is None
