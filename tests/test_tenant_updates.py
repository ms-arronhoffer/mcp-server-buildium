"""Tests for partial (merge) tenant updates and the supporting merge helpers.

These verify that updating a single field (e.g. a phone number) succeeds without
the caller having to resupply the whole strict schema: the current record is
fetched and used to fill required fields, and the phone-number list form is
reshaped into the keyed object form the PUT message expects.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.tools import _common as c
from mcp_server_buildium.tools.tenants import register_tenant_tools


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------
def test_to_snake_key_variants() -> None:
    assert c.to_snake_key("FirstName") == "first_name"
    assert c.to_snake_key("PhoneNumbers") == "phone_numbers"
    assert c.to_snake_key("AddressLine1") == "address_line1"
    assert c.to_snake_key("PostalCode") == "postal_code"
    # Already snake_case is unchanged.
    assert c.to_snake_key("first_name") == "first_name"


def test_normalize_keys_is_recursive() -> None:
    out = c.normalize_keys({"PhoneNumbers": {"Mobile": "1"}, "Items": [{"AddressLine1": "x"}]})
    assert out == {"phone_numbers": {"mobile": "1"}, "items": [{"address_line1": "x"}]}


def test_deep_merge_only_overrides_specified_fields() -> None:
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    patch = {"nested": {"y": 3}}
    assert c.deep_merge(base, patch) == {"a": 1, "nested": {"x": 1, "y": 3}}


def test_deep_merge_does_not_mutate_inputs() -> None:
    base = {"nested": {"x": 1}}
    patch = {"nested": {"y": 2}}
    c.deep_merge(base, patch)
    assert base == {"nested": {"x": 1}}


# ---------------------------------------------------------------------------
# Fake SDK plumbing
# ---------------------------------------------------------------------------
class _FakeModel:
    """Minimal stand-in for an SDK model exposing ``to_dict``."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    def to_dict(self) -> dict[str, Any]:
        return self._doc


class _FakeRentalTenantsApi:
    def __init__(self, current: dict[str, Any]) -> None:
        self._current = current
        self.received: Any = None

    async def external_api_rental_tenants_get_tenant_by_id(self, tenant_id: int) -> _FakeModel:
        return _FakeModel(self._current)

    async def external_api_rental_tenants_update_rental_tenant(
        self, tenant_id: int, rental_tenant_put_message: Any
    ) -> Any:
        self.received = rental_tenant_put_message
        return rental_tenant_put_message


class _FakeAssociationTenantsApi:
    def __init__(self, current: dict[str, Any]) -> None:
        self._current = current
        self.received: Any = None

    async def external_api_association_tenants_get_association_tenant_by_id(
        self, tenant_id: int
    ) -> _FakeModel:
        return _FakeModel(self._current)

    async def external_api_association_tenants_update_association_tenant(
        self, tenant_id: int, association_tenant_put_message: Any
    ) -> Any:
        self.received = association_tenant_put_message
        return association_tenant_put_message


class _FakeClient:
    def __init__(self, rental_current=None, association_current=None) -> None:
        self.rental_tenants_api = _FakeRentalTenantsApi(rental_current or {})
        self.association_tenants_api = _FakeAssociationTenantsApi(association_current or {})


async def _get_tool(client: _FakeClient, name: str):
    mcp = FastMCP("test")
    register_tenant_tools(mcp, client)
    tools = await mcp.get_tools()
    return tools[name]


# ---------------------------------------------------------------------------
# Rental tenant partial update
# ---------------------------------------------------------------------------
_RENTAL_CURRENT = {
    "Id": 7,
    "FirstName": "Ada",
    "LastName": "Lovelace",
    "Email": "ada@example.com",
    "PhoneNumbers": [
        {"Number": "555-000-1111", "Type": "Home"},
        {"Number": "555-000-2222", "Type": "Cell"},
    ],
    "Address": {
        "AddressLine1": "1 Analytical Way",
        "City": "London",
        "PostalCode": "12345",
        "Country": "UnitedStates",
    },
    "CreatedDateTime": "2020-01-01T00:00:00Z",
}


@pytest.mark.asyncio
async def test_update_rental_tenant_partial_phone_number() -> None:
    client = _FakeClient(rental_current=_RENTAL_CURRENT)
    tool = await _get_tool(client, "update_rental_tenant")

    # Caller supplies ONLY the new mobile number.
    result = await tool.fn(tenant_id=7, tenant_data={"phone_numbers": {"mobile": "555-999-8888"}})

    assert result["error"] is None
    sent = client.rental_tenants_api.received.to_dict()
    # Required fields were filled in from the existing record.
    assert sent["FirstName"] == "Ada"
    assert sent["LastName"] == "Lovelace"
    assert sent["Address"]["AddressLine1"] == "1 Analytical Way"
    # Phone list was reshaped to the keyed object; the mobile was updated while
    # the existing home number was preserved.
    assert sent["PhoneNumbers"] == {"Home": "555-000-1111", "Mobile": "555-999-8888"}
    # Read-only fields are not sent back.
    assert "Id" not in sent
    assert "CreatedDateTime" not in sent


@pytest.mark.asyncio
async def test_update_rental_tenant_accepts_pascal_case_patch() -> None:
    client = _FakeClient(rental_current=_RENTAL_CURRENT)
    tool = await _get_tool(client, "update_rental_tenant")

    result = await tool.fn(tenant_id=7, tenant_data={"Email": "ada.new@example.com"})

    assert result["error"] is None
    sent = client.rental_tenants_api.received.to_dict()
    assert sent["Email"] == "ada.new@example.com"
    assert sent["FirstName"] == "Ada"


# ---------------------------------------------------------------------------
# Association tenant partial update
# ---------------------------------------------------------------------------
_ASSOCIATION_CURRENT = {
    "Id": 3,
    "FirstName": "Grace",
    "LastName": "Hopper",
    "PhoneNumbers": [{"Number": "555-111-0000", "Type": "Cell"}],
    "PrimaryAddress": {
        "AddressLine1": "2 Compiler Court",
        "City": "Arlington",
        "PostalCode": "22201",
        "Country": "UnitedStates",
    },
}


@pytest.mark.asyncio
async def test_update_association_tenant_partial_phone_number() -> None:
    client = _FakeClient(association_current=_ASSOCIATION_CURRENT)
    tool = await _get_tool(client, "update_association_tenant")

    result = await tool.fn(tenant_id=3, tenant_data={"phone_numbers": {"mobile": "555-222-3333"}})

    assert result["error"] is None
    sent = client.association_tenants_api.received.to_dict()
    assert sent["FirstName"] == "Grace"
    assert sent["LastName"] == "Hopper"
    assert sent["PrimaryAddress"]["AddressLine1"] == "2 Compiler Court"
    assert sent["PhoneNumbers"] == {"Mobile": "555-222-3333"}
