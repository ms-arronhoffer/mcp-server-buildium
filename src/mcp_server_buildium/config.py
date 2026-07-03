"""Configuration management for Buildium MCP Server."""

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

# Load .env file if it exists
load_dotenv()

# Tool categories supported by the server. Kept here so both the server and
# validation tooling share a single source of truth.
ALL_CATEGORIES = frozenset(
    {
        "associations",
        "leases",
        "rentals",
        "applicants",
        "tenants",
        "owners",
        "units",
        "vendors",
        "tasks",
        "bills",
        "files",
        "bank_accounts",
        "general_ledger",
        "work_orders",
    }
)


class BuildiumConfig(BaseSettings):
    """Buildium API key configuration (API key headers, not OAuth).

    Buildium authenticates using two request headers,
    ``x-buildium-client-id`` and ``x-buildium-client-secret``. This is an API
    key pair, *not* OAuth 2.0.
    """

    base_url: str = Field(
        default="https://api.buildium.com",
        description=(
            "Buildium API base URL without /v1 (prod: https://api.buildium.com, "
            "sandbox: https://apisandbox.buildium.com). The SDK adds /v1 to paths "
            "automatically."
        ),
    )
    client_id: str = Field(
        ..., description="Buildium API client ID (sent as the x-buildium-client-id header)"
    )
    client_secret: str = Field(
        ..., description="Buildium API client secret (sent as the x-buildium-client-secret header)"
    )
    categories: str | None = Field(
        default=None,
        description=(
            "Comma-separated list of tool categories to enable (e.g. "
            "'associations,leases,rentals'). If not specified, all categories are enabled."
        ),
    )
    mcp_auth_token: str | None = Field(
        default=None,
        description=(
            "Optional bearer token. When set, MCP clients must present this token via "
            "the Authorization header (header-auth passthrough) to use the server."
        ),
    )

    # --- Transport ---------------------------------------------------------
    transport: str = Field(
        default="stdio",
        description=(
            "MCP transport to serve. 'stdio' (default) embeds the server in a local "
            "MCP client (Claude Desktop, Cursor). 'http' serves the Streamable HTTP "
            "transport over the network so browser extensions and remote clients can "
            "connect."
        ),
    )
    host: str = Field(
        default="127.0.0.1",
        description="Host/interface to bind when transport='http'.",
    )
    port: int = Field(
        default=8000,
        description="TCP port to listen on when transport='http'.",
    )
    mcp_path: str = Field(
        default="/mcp",
        description="URL path the Streamable HTTP MCP endpoint is served at.",
    )

    # --- Microsoft Entra ID (Azure AD) JWT auth ---------------------------
    entra_tenant_id: str | None = Field(
        default=None,
        description=(
            "Microsoft Entra ID (Azure AD) tenant ID (GUID) or 'common'/'organizations'. "
            "When set together with entra_audience, incoming MCP requests must present a "
            "valid Entra-issued JWT access token."
        ),
    )
    entra_audience: str | None = Field(
        default=None,
        description=(
            "Expected audience ('aud') of the Entra access token. Typically the API app "
            "registration's Application ID URI (e.g. 'api://<app-id>') or its client ID."
        ),
    )
    entra_issuer: str | None = Field(
        default=None,
        description=(
            "Optional explicit token issuer. If not set, it is derived from the tenant ID "
            "as 'https://login.microsoftonline.com/<tenant>/v2.0'."
        ),
    )
    entra_jwks_uri: str | None = Field(
        default=None,
        description=(
            "Optional explicit JWKS URI for signing-key discovery. If not set, it is "
            "derived from the tenant ID as "
            "'https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys'."
        ),
    )
    entra_required_scopes: str | None = Field(
        default=None,
        description=(
            "Optional comma-separated list of scopes ('scp' claim) the token must contain "
            "(e.g. 'MCP.Access')."
        ),
    )

    # --- CORS --------------------------------------------------------------
    cors_allow_origins: str | None = Field(
        default=None,
        description=(
            "Comma-separated list of allowed CORS origins for the HTTP transport "
            "(e.g. 'chrome-extension://<id>,moz-extension://<id>'). Use '*' to allow any "
            "origin. Only applied when transport='http'."
        ),
    )

    model_config = {
        "env_prefix": "BUILDIUM_",
        "case_sensitive": False,
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Ignore legacy fields such as BUILDIUM_SCOPE / BUILDIUM_TOKEN_URL
    }

    @model_validator(mode="after")
    def _validate(self) -> "BuildiumConfig":
        """Fail fast on obviously invalid configuration."""
        if not self.client_id or not self.client_id.strip():
            raise ValueError("BUILDIUM_CLIENT_ID must be a non-empty value")
        if not self.client_secret or not self.client_secret.strip():
            raise ValueError("BUILDIUM_CLIENT_SECRET must be a non-empty value")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("BUILDIUM_BASE_URL must start with http:// or https://")
        if self.transport.lower() not in {"stdio", "http"}:
            raise ValueError("BUILDIUM_TRANSPORT must be one of: stdio, http")
        if self.entra_tenant_id and not self.entra_audience:
            raise ValueError(
                "BUILDIUM_ENTRA_AUDIENCE is required when BUILDIUM_ENTRA_TENANT_ID is set"
            )
        unknown = self.get_enabled_categories()
        if unknown is not None:
            invalid = unknown - ALL_CATEGORIES
            if invalid:
                raise ValueError(
                    f"Unknown BUILDIUM_CATEGORIES: {sorted(invalid)}. "
                    f"Valid categories: {sorted(ALL_CATEGORIES)}"
                )
        return self

    @classmethod
    def from_env(cls) -> "BuildiumConfig":
        """Load configuration from environment variables."""
        return cls()

    def get_enabled_categories(self) -> set[str] | None:
        """Get enabled categories as a set.

        Returns:
            Set of category names if categories are specified, None if all should be enabled.
        """
        if not self.categories:
            return None  # None means all categories enabled
        return {cat.strip().lower() for cat in self.categories.split(",") if cat.strip()}

    def is_category_enabled(self, category: str) -> bool:
        """Check if a category is enabled.

        Args:
            category: Category name to check.

        Returns:
            True if category is enabled, False otherwise.
        """
        enabled = self.get_enabled_categories()
        if enabled is None:
            return True  # All categories enabled
        return category.lower() in enabled

    def entra_enabled(self) -> bool:
        """Return True when Entra ID JWT verification is configured."""
        return bool(self.entra_tenant_id and self.entra_audience)

    def get_entra_issuer(self) -> str | None:
        """Return the expected token issuer, derived from the tenant if unset."""
        if self.entra_issuer:
            return self.entra_issuer
        if self.entra_tenant_id:
            return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"
        return None

    def get_entra_jwks_uri(self) -> str | None:
        """Return the JWKS URI for signing keys, derived from the tenant if unset."""
        if self.entra_jwks_uri:
            return self.entra_jwks_uri
        if self.entra_tenant_id:
            return f"https://login.microsoftonline.com/{self.entra_tenant_id}/discovery/v2.0/keys"
        return None

    def get_entra_scopes(self) -> list[str] | None:
        """Return required Entra scopes as a list, or None when unset."""
        if not self.entra_required_scopes:
            return None
        scopes = [s.strip() for s in self.entra_required_scopes.split(",") if s.strip()]
        return scopes or None

    def get_cors_origins(self) -> list[str] | None:
        """Return allowed CORS origins as a list, or None when unset."""
        if not self.cors_allow_origins:
            return None
        origins = [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]
        return origins or None
