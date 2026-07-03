"""Authentication providers for the Buildium MCP server.

The server supports three modes, selected by configuration:

* **Microsoft Entra ID (Azure AD)** — when ``BUILDIUM_ENTRA_TENANT_ID`` and
  ``BUILDIUM_ENTRA_AUDIENCE`` are set, incoming MCP requests must present a valid
  Entra-issued JWT access token. Signatures are verified against Entra's rotating
  JWKS, and the ``iss``/``aud``/``exp`` claims (plus optional required scopes) are
  checked. This is the mode a browser extension (or any remote client) uses.
* **Static bearer token** — when only ``BUILDIUM_MCP_AUTH_TOKEN`` is set, a shared
  secret is accepted via the ``Authorization`` header. Useful for local/dev and CI.
* **No auth** — when neither is configured (e.g. the default ``stdio`` transport
  embedded in a trusted local MCP client).

The upstream Buildium API key (client ID/secret) is *never* exposed to clients;
it stays server-side in :class:`~mcp_server_buildium.buildium_client.BuildiumClient`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastmcp.server.auth.auth import TokenVerifier

    from .config import BuildiumConfig

logger = get_logger(__name__)


def build_entra_verifier(config: BuildiumConfig) -> TokenVerifier:
    """Build a JWKS-based JWT verifier for Microsoft Entra ID access tokens.

    Args:
        config: Server configuration with Entra settings populated.

    Returns:
        A configured ``JWTVerifier`` validating signature, issuer, audience,
        expiry, and any required scopes.
    """
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    issuer = config.get_entra_issuer()
    jwks_uri = config.get_entra_jwks_uri()
    scopes = config.get_entra_scopes()

    logger.info(
        "Configuring Entra ID JWT auth (issuer=%s, audience=%s, required_scopes=%s)",
        issuer,
        config.entra_audience,
        scopes,
    )

    return JWTVerifier(
        jwks_uri=jwks_uri,
        issuer=issuer,
        audience=config.entra_audience,
        required_scopes=scopes,
    )


def build_auth(config: BuildiumConfig) -> TokenVerifier | None:
    """Return the token verifier to use, or ``None`` for no authentication.

    Precedence: Entra ID (if configured) → static bearer token → none.

    Args:
        config: Server configuration.

    Returns:
        A ``TokenVerifier`` instance, or ``None`` when no auth is configured.
    """
    if config.entra_enabled():
        return build_entra_verifier(config)

    if config.mcp_auth_token:
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

        logger.info("MCP static-token auth passthrough enabled")
        return StaticTokenVerifier(
            tokens={config.mcp_auth_token: {"client_id": "buildium-mcp", "scopes": []}}
        )

    return None
