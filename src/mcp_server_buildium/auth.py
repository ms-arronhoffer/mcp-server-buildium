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

from fastmcp.server.auth.providers.jwt import JWTVerifier

from .logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from fastmcp.server.auth.auth import AccessToken, TokenVerifier

    from .config import BuildiumConfig

logger = get_logger(__name__)


class MultiIssuerJWTVerifier(JWTVerifier):
    """A :class:`JWTVerifier` that accepts tokens from any of several issuers.

    Microsoft Entra ID mints either a v1.0 or a v2.0 access token for the same
    app registration depending on the API app's ``accessTokenAcceptedVersion``
    setting. The tokens differ only in their ``iss`` claim
    (``https://sts.windows.net/<tenant>/`` for v1 vs.
    ``https://login.microsoftonline.com/<tenant>/v2.0`` for v2). The stock
    ``JWTVerifier`` only compares against a single issuer, so a mismatch there is
    a common cause of persistent 401s. This subclass validates the issuer
    against a set of accepted values instead, accepting the token when *any*
    matches.
    """

    def __init__(self, *, accepted_issuers: Sequence[str], **kwargs: object) -> None:
        # Disable the parent's single-issuer check; we validate the issuer
        # ourselves against ``accepted_issuers`` below.
        super().__init__(issuer=None, **kwargs)  # type: ignore[arg-type]
        self.accepted_issuers = list(accepted_issuers)

    async def load_access_token(self, token: str) -> AccessToken | None:
        result = await super().load_access_token(token)
        if result is None:
            return None
        if self.accepted_issuers:
            iss = result.claims.get("iss")
            if iss not in self.accepted_issuers:
                self.logger.debug(
                    "Token validation failed: issuer %r not in accepted issuers %s",
                    iss,
                    self.accepted_issuers,
                )
                self.logger.info("Rejecting request: issuer not in accepted set")
                return None
        return result


def build_entra_verifier(config: BuildiumConfig) -> TokenVerifier:
    """Build a JWKS-based JWT verifier for Microsoft Entra ID access tokens.

    Args:
        config: Server configuration with Entra settings populated.

    Returns:
        A verifier validating signature, issuer, audience, expiry, and any
        required scopes. Both the v1.0 and v2.0 Entra issuers are accepted by
        default (unless ``entra_issuer`` is set), so the server works regardless
        of the API app's ``accessTokenAcceptedVersion`` setting.
    """
    issuers = config.get_entra_accepted_issuers() or []
    jwks_uri = config.get_entra_jwks_uri()
    scopes = config.get_entra_scopes()

    logger.info(
        "Configuring Entra ID JWT auth (issuers=%s, audience=%s, required_scopes=%s)",
        issuers,
        config.entra_audience,
        scopes,
    )

    return MultiIssuerJWTVerifier(
        accepted_issuers=issuers,
        jwks_uri=jwks_uri,
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
    if config.dev_auth_bypass:
        logger.warning(
            "DEV auth bypass enabled (BUILDIUM_DEV_AUTH_BYPASS): all MCP/HTTP "
            "authentication is DISABLED. Never enable this in production."
        )
        return None

    if config.entra_enabled():
        return build_entra_verifier(config)

    if config.mcp_auth_token:
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

        logger.info("MCP static-token auth passthrough enabled")
        return StaticTokenVerifier(
            tokens={config.mcp_auth_token: {"client_id": "buildium-mcp", "scopes": []}}
        )

    return None
