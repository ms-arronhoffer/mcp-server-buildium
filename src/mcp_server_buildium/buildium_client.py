"""Buildium API client using the generated SDK."""

import logging

from .config import BuildiumConfig
from .logging_config import get_logger
from .sdk_imports import (  # noqa: E402
    ApiClient,
    ApplicantsApi,
    AssociationOwnersApi,
    AssociationsApi,
    AssociationTenantsApi,
    AssociationUnitsApi,
    BankAccountsApi,
    BillsApi,
    BoardMembersApi,
    BudgetsApi,
    CommunicationsApi,
    Configuration,
    ContactRequestsApi,
    FilesApi,
    GeneralLedgerApi,
    LeasesApi,
    LeaseTransactionsApi,
    ListingsApi,
    OwnershipAccountsApi,
    OwnershipAccountTransactionsApi,
    RentalOwnerRequestsApi,
    RentalOwnersApi,
    RentalPropertiesApi,
    RentalTenantsApi,
    RentalUnitsApi,
    ResidentRequestsApi,
    TasksApi,
    ToDoRequestsApi,
    VendorsApi,
    WorkOrdersApi,
)

logger = get_logger(__name__)


class BuildiumClient:
    """Buildium API client using the generated SDK with API key header authentication.

    Buildium authenticates server-to-server requests using two request headers,
    ``x-buildium-client-id`` and ``x-buildium-client-secret`` (an API key pair,
    not OAuth 2.0).
    """

    def __init__(self, config: BuildiumConfig | None = None):
        """Initialize the Buildium client.

        Args:
            config: Buildium configuration. If None, loads from environment.
        """
        self.config = config or BuildiumConfig.from_env()
        self._sdk_config: Configuration | None = None
        self._api_client: ApiClient | None = None

        # Initialize SDK configuration and API client
        self._initialize_sdk()

        # Initialize API clients
        self.associations_api = AssociationsApi(self._api_client)
        self.board_members_api = BoardMembersApi(self._api_client)
        self.ownership_accounts_api = OwnershipAccountsApi(self._api_client)
        self.leases_api = LeasesApi(self._api_client)
        self.lease_transactions_api = LeaseTransactionsApi(self._api_client)
        self.ownership_account_transactions_api = OwnershipAccountTransactionsApi(self._api_client)
        self.rentals_api = RentalPropertiesApi(self._api_client)
        self.listings_api = ListingsApi(self._api_client)
        self.applicants_api = ApplicantsApi(self._api_client)
        self.rental_tenants_api = RentalTenantsApi(self._api_client)
        self.association_tenants_api = AssociationTenantsApi(self._api_client)
        self.rental_owners_api = RentalOwnersApi(self._api_client)
        self.association_owners_api = AssociationOwnersApi(self._api_client)
        self.rental_units_api = RentalUnitsApi(self._api_client)
        self.association_units_api = AssociationUnitsApi(self._api_client)
        self.vendors_api = VendorsApi(self._api_client)
        self.tasks_api = TasksApi(self._api_client)
        self.bills_api = BillsApi(self._api_client)
        self.files_api = FilesApi(self._api_client)
        self.bank_accounts_api = BankAccountsApi(self._api_client)
        self.general_ledger_api = GeneralLedgerApi(self._api_client)
        self.work_orders_api = WorkOrdersApi(self._api_client)
        self.communications_api = CommunicationsApi(self._api_client)
        self.budgets_api = BudgetsApi(self._api_client)
        self.contact_requests_api = ContactRequestsApi(self._api_client)
        self.to_do_requests_api = ToDoRequestsApi(self._api_client)
        self.resident_requests_api = ResidentRequestsApi(self._api_client)
        self.rental_owner_requests_api = RentalOwnerRequestsApi(self._api_client)

    def _initialize_sdk(self) -> None:
        """Initialize the SDK configuration and API client with API key headers."""
        # The SDK's generated paths already include /v1, so base_url should NOT include /v1
        # e.g., base_url should be https://apisandbox.buildium.com (not .../v1)
        base_url = self.config.base_url.rstrip("/")
        # Remove /v1 if it's in the base_url (the SDK paths already include it)
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]  # Remove /v1

        self._sdk_config = Configuration(host=base_url)
        self._api_client = ApiClient(self._sdk_config)
        # Set API key headers as default headers (Buildium uses headers, not OAuth)
        self._api_client.set_default_header("x-buildium-client-id", self.config.client_id)
        self._api_client.set_default_header("x-buildium-client-secret", self.config.client_secret)

        # Log initialization details (never log secret values)
        logger.log(logging.DEBUG, "Initialized Buildium client base_url=%s", base_url)

    async def close(self) -> None:
        """Close the API client and release network resources."""
        if self._api_client is not None:
            close = getattr(self._api_client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    async def __aenter__(self) -> "BuildiumClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
