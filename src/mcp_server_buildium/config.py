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
