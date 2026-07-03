"""Tenant management tools for Buildium (rental and association)."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

# Maps a Buildium phone-number ``Type`` (as returned by GET endpoints, which
# expose phone numbers as a list of ``{Number, Type}`` entries) onto the keyed
# ``PhoneNumbers`` object shape (``Home``/``Work``/``Mobile``/``Fax``) that the
# create/update ``PhoneNumbers`` message expects. Unmapped types are dropped
# rather than guessed at, so we never place a number under the wrong label.
_PHONE_TYPE_TO_KEY = {
    "home": "home",
    "office": "work",
    "work": "work",
    "cell": "mobile",
    "mobile": "mobile",
    "fax": "fax",
}


def _phone_list_to_object(phones: Any) -> dict[str, str]:
    """Convert a GET-style phone-number list into the PUT ``PhoneNumbers`` object."""
    result: dict[str, str] = {}
    if not isinstance(phones, list):
        return result
    for entry in phones:
        if not isinstance(entry, dict):
            continue
        number = entry.get("number")
        key = _PHONE_TYPE_TO_KEY.get(str(entry.get("type") or "").lower())
        if number and key and key not in result:
            result[key] = number
    return result


def _tenant_get_to_put_base(current: Any) -> dict[str, Any]:
    """Build a snake_case PUT-shaped dict from a fetched tenant record.

    The generated PUT models require ``first_name``/``last_name`` and an address,
    so a naive partial update (e.g. only a phone number) fails validation. We
    seed those required fields from the existing record and reshape the phone
    numbers from the GET list form into the keyed object form the PUT expects.
    Extra read-only fields (``id``, timestamps, ...) are ignored by the model.
    """
    raw = current.to_dict() if hasattr(current, "to_dict") else dict(current or {})
    base = c.normalize_keys(raw)
    if isinstance(base, dict):
        phones = _phone_list_to_object(base.get("phone_numbers"))
        if phones:
            base["phone_numbers"] = phones
        else:
            base.pop("phone_numbers", None)
        return base
    return {}


def register_tenant_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register tenant-related tools with the MCP server."""

    c.register_operation("list_rental_tenants", "ExternalApiRentalTenants_GetAllTenants")
    c.register_operation("get_rental_tenant", "ExternalApiRentalTenants_GetTenantById")
    c.register_operation("create_rental_tenant", "ExternalApiRentalTenants_CreateRentalTenant")
    c.register_operation("update_rental_tenant", "ExternalApiRentalTenants_UpdateRentalTenant")
    c.register_operation(
        "list_association_tenants", "ExternalApiAssociationTenants_GetAssociationTenants"
    )
    c.register_operation(
        "create_association_tenant", "ExternalApiAssociationTenants_CreateAssociationTenant"
    )
    c.register_operation(
        "update_association_tenant", "ExternalApiAssociationTenants_UpdateAssociationTenant"
    )

    # Rental Tenants
    @mcp.tool()
    async def list_rental_tenants(
        property_id: int | None = None,
        unit_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List rental tenants from Buildium, optionally filtered by property/unit."""
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if property_id is not None:
            kwargs["propertyids"] = [property_id]
        if unit_id is not None:
            kwargs["unitids"] = [unit_id]
        return await c.execute(
            "list_rental_tenants",
            lambda: client.rental_tenants_api.external_api_rental_tenants_get_all_tenants(**kwargs),
        )

    @mcp.tool()
    async def get_rental_tenant(tenant_id: int) -> dict[str, Any]:
        """Get a specific rental tenant by ID."""
        return await c.execute(
            "get_rental_tenant",
            lambda: client.rental_tenants_api.external_api_rental_tenants_get_tenant_by_id(
                tenant_id=tenant_id
            ),
        )

    @mcp.tool()
    async def create_rental_tenant(tenant_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new rental tenant."""
        message = c.build_model(
            "rental_tenant_post_message", "RentalTenantPostMessage", tenant_data
        )
        return await c.execute(
            "create_rental_tenant",
            lambda: client.rental_tenants_api.external_api_rental_tenants_create_rental_tenant(
                rental_tenant_post_message=message
            ),
        )

    @mcp.tool()
    async def update_rental_tenant(tenant_id: int, tenant_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing rental tenant, merging changes onto the current record.

        ``tenant_data`` only needs to contain the fields you want to change; the
        current tenant is fetched first and used to supply required fields
        (``FirstName``, ``LastName``, ``Address``) so partial edits succeed
        without a full schema. Keys may use either the JSON aliases
        (``PhoneNumbers``) or field names (``phone_numbers``). Phone numbers use
        the keyed object form, e.g. ``{"phone_numbers": {"mobile": "555-555-5555"}}``.
        """

        async def _do_update() -> Any:
            current = await client.rental_tenants_api.external_api_rental_tenants_get_tenant_by_id(
                tenant_id=tenant_id
            )
            base = _tenant_get_to_put_base(current)
            merged = c.deep_merge(base, c.normalize_keys(tenant_data))
            message = c.build_model("rental_tenant_put_message", "RentalTenantPutMessage", merged)
            return await client.rental_tenants_api.external_api_rental_tenants_update_rental_tenant(
                tenant_id=tenant_id, rental_tenant_put_message=message
            )

        return await c.execute("update_rental_tenant", _do_update)

    # Association Tenants
    @mcp.tool()
    async def list_association_tenants(
        association_id: int | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List association tenants from Buildium, optionally filtered by association."""
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if association_id is not None:
            kwargs["associationids"] = [association_id]
        return await c.execute(
            "list_association_tenants",
            lambda: (
                client.association_tenants_api.external_api_association_tenants_get_association_tenants(
                    **kwargs
                )
            ),
        )

    @mcp.tool()
    async def create_association_tenant(tenant_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new association tenant."""
        message = c.build_model(
            "association_tenant_post_message", "AssociationTenantPostMessage", tenant_data
        )
        return await c.execute(
            "create_association_tenant",
            lambda: (
                client.association_tenants_api.external_api_association_tenants_create_association_tenant(
                    association_tenant_post_message=message
                )
            ),
        )

    @mcp.tool()
    async def update_association_tenant(
        tenant_id: int, tenant_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing association tenant, merging changes onto the current record.

        ``tenant_data`` only needs the fields you want to change; the current
        tenant is fetched first to supply required fields (``FirstName``,
        ``LastName``, ``PrimaryAddress``) so partial edits succeed without a
        full schema. Keys may use JSON aliases or field names, and phone numbers
        use the keyed object form, e.g.
        ``{"phone_numbers": {"mobile": "555-555-5555"}}``.
        """

        async def _do_update() -> Any:
            api = client.association_tenants_api
            current = await api.external_api_association_tenants_get_association_tenant_by_id(
                tenant_id=tenant_id
            )
            base = _tenant_get_to_put_base(current)
            merged = c.deep_merge(base, c.normalize_keys(tenant_data))
            message = c.build_model(
                "association_tenant_put_message", "AssociationTenantPutMessage", merged
            )
            return await api.external_api_association_tenants_update_association_tenant(
                tenant_id=tenant_id, association_tenant_put_message=message
            )

        return await c.execute("update_association_tenant", _do_update)
