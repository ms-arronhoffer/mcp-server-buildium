"""Configuration management for Buildium MCP Server."""

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

# Load .env file if it exists
load_dotenv()

# Built-in security roles (see security.policy for semantics).
ROLES = frozenset({"readonly", "operator", "admin", "custom"})

# Valid audit sink names.
AUDIT_SINKS = frozenset({"log", "file", "none"})

# Supported server-side LLM providers for the /chat endpoint.
LLM_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})

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

    # --- Server-side LLM (chat endpoint) -----------------------------------
    # The optional /chat endpoint runs the assistant loop on the server so that
    # provider API keys never reach the browser. Keys are read from the
    # environment/secret store and are never returned by any endpoint.
    llm_provider: str | None = Field(
        default=None,
        description=(
            "Active LLM provider for the /chat endpoint: 'openai', 'anthropic', or "
            "'gemini'. When unset, the /chat and /capabilities endpoints report the "
            "assistant as disabled."
        ),
    )
    llm_model: str | None = Field(
        default=None,
        description="Default model name for the active provider (e.g. 'gpt-4o-mini').",
    )
    llm_allowed_models: str | None = Field(
        default=None,
        description=(
            "Optional comma-separated allow-list of models a client may request. When "
            "set, /chat rejects any model not in this list. The default model must be a "
            "member. When unset, only the default model is permitted."
        ),
    )
    llm_system_prompt: str | None = Field(
        default=None,
        description="Optional system prompt steering the assistant (a sensible default is used).",
    )
    llm_max_tool_rounds: int = Field(
        default=8,
        description="Maximum tool-call rounds per chat turn before the loop stops (>=1).",
    )
    llm_openai_api_key: str | None = Field(
        default=None, description="API key for OpenAI (used when llm_provider='openai')."
    )
    llm_anthropic_api_key: str | None = Field(
        default=None, description="API key for Anthropic (used when llm_provider='anthropic')."
    )
    llm_gemini_api_key: str | None = Field(
        default=None, description="API key for Google Gemini (used when llm_provider='gemini')."
    )
    llm_openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL for the OpenAI-compatible Chat Completions API.",
    )
    llm_anthropic_base_url: str = Field(
        default="https://api.anthropic.com/v1",
        description="Base URL for the Anthropic Messages API.",
    )
    llm_gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        description="Base URL for the Google Gemini generateContent API.",
    )

    # --- Development auth bypass -------------------------------------------
    dev_auth_bypass: bool = Field(
        default=False,
        description=(
            "DEV ONLY: when true, skip all MCP/HTTP authentication (Entra JWT and static "
            "token) so local/mock testing needs no tokens. NEVER enable in production."
        ),
    )

    # -- Security guardrails (all optional; defaults preserve prior behavior) --
    role: str = Field(
        default="admin",
        description=(
            "Security role controlling which tools are permitted: 'readonly' "
            "(reads only), 'operator' (reads + non-sensitive writes), 'admin' "
            "(all, default), or 'custom' (shaped by allow/deny lists)."
        ),
    )
    readonly: bool = Field(
        default=False,
        description="Global kill switch: when true, all mutating tools are disabled.",
    )
    block_sensitive: bool = Field(
        default=False,
        description=(
            "When true, disable financially sensitive tools (bills, bank accounts, "
            "general ledger, payments, file upload/download URLs)."
        ),
    )
    allow_tools: str | None = Field(
        default=None,
        description=(
            "Comma-separated whitelist of tool names to permit (on top of the role). "
            "When set, only these tools (minus any denied) are enabled."
        ),
    )
    deny_tools: str | None = Field(
        default=None,
        description="Comma-separated blacklist of tool names to always disable (deny wins).",
    )
    rate_limit_per_minute: int = Field(
        default=0,
        description=(
            "Maximum tool invocations per rolling 60-second window (0 disables rate limiting)."
        ),
    )

    # -- Audit trail ---------------------------------------------------------
    audit_sink: str = Field(
        default="log",
        description=(
            "Audit sink: 'log' (structured stderr JSON, default), 'file' "
            "(newline-delimited JSON at BUILDIUM_AUDIT_FILE), or 'none' (disabled)."
        ),
    )
    audit_file: str | None = Field(
        default=None,
        description="Path for the file audit sink (required when BUILDIUM_AUDIT_SINK=file).",
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
        role = (self.role or "").strip().lower()
        if role not in ROLES:
            raise ValueError(f"Unknown BUILDIUM_ROLE: {self.role!r}. Valid roles: {sorted(ROLES)}")
        sink = (self.audit_sink or "").strip().lower()
        if sink not in AUDIT_SINKS:
            raise ValueError(
                f"Unknown BUILDIUM_AUDIT_SINK: {self.audit_sink!r}. "
                f"Valid values: {sorted(AUDIT_SINKS)}"
            )
        if sink == "file" and not (self.audit_file and self.audit_file.strip()):
            raise ValueError("BUILDIUM_AUDIT_SINK=file requires BUILDIUM_AUDIT_FILE to be set")
        if self.rate_limit_per_minute < 0:
            raise ValueError("BUILDIUM_RATE_LIMIT_PER_MINUTE must be >= 0")
        self._validate_llm()
        return self

    def _validate_llm(self) -> None:
        """Validate the optional server-side LLM configuration."""
        if self.llm_provider is None:
            return
        provider = self.llm_provider.strip().lower()
        if provider not in LLM_PROVIDERS:
            raise ValueError(
                f"Unknown BUILDIUM_LLM_PROVIDER: {self.llm_provider!r}. "
                f"Valid providers: {sorted(LLM_PROVIDERS)}"
            )
        if not (self.llm_model and self.llm_model.strip()):
            raise ValueError("BUILDIUM_LLM_MODEL is required when BUILDIUM_LLM_PROVIDER is set")
        if not (self.get_active_llm_key() or "").strip():
            raise ValueError(
                f"An API key is required for provider {provider!r} "
                f"(set BUILDIUM_LLM_{provider.upper()}_API_KEY)"
            )
        allowed = self.get_llm_allowed_models()
        if allowed is not None and self.llm_model.strip() not in allowed:
            raise ValueError("BUILDIUM_LLM_MODEL must be a member of BUILDIUM_LLM_ALLOWED_MODELS")
        if self.llm_max_tool_rounds < 1:
            raise ValueError("BUILDIUM_LLM_MAX_TOOL_ROUNDS must be >= 1")

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

    # --- LLM helpers -------------------------------------------------------
    def llm_enabled(self) -> bool:
        """Return True when a server-side LLM provider is configured."""
        return bool(self.llm_provider and self.llm_provider.strip())

    def get_llm_provider(self) -> str | None:
        """Return the normalized (lower-case) active provider name, or None."""
        if not self.llm_enabled():
            return None
        return self.llm_provider.strip().lower()

    def get_active_llm_key(self) -> str | None:
        """Return the API key for the active provider (never logged/returned to clients)."""
        return {
            "openai": self.llm_openai_api_key,
            "anthropic": self.llm_anthropic_api_key,
            "gemini": self.llm_gemini_api_key,
        }.get(self.get_llm_provider() or "")

    def get_llm_base_url(self) -> str | None:
        """Return the base URL for the active provider."""
        return {
            "openai": self.llm_openai_base_url,
            "anthropic": self.llm_anthropic_base_url,
            "gemini": self.llm_gemini_base_url,
        }.get(self.get_llm_provider() or "")

    def get_llm_allowed_models(self) -> list[str] | None:
        """Return the model allow-list as a list, or None when unset."""
        if not self.llm_allowed_models:
            return None
        models = [m.strip() for m in self.llm_allowed_models.split(",") if m.strip()]
        return models or None

    def get_llm_models(self) -> list[str]:
        """Return the models a client may select (allow-list, else just the default)."""
        allowed = self.get_llm_allowed_models()
        if allowed is not None:
            return allowed
        return [self.llm_model] if self.llm_model else []

    def is_llm_model_allowed(self, model: str) -> bool:
        """Return True when ``model`` is permitted for client selection."""
        return model in self.get_llm_models()

    def get_llm_system_prompt(self) -> str:
        """Return the system prompt for the assistant (default when unset)."""
        if self.llm_system_prompt and self.llm_system_prompt.strip():
            return self.llm_system_prompt
        return (
            "You are a helpful property-management assistant for Buildium. "
            "Use the available tools to answer questions and perform actions. "
            "Prefer read-only tools unless the user explicitly asks to create or modify data. "
            "Always confirm destructive or write operations before calling them."
        )
