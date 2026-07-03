"""Tests for the shared fetch-then-merge partial-update pattern.

These verify that every ``update_*`` tool fetches the current record first and
deep-merges the caller's partial patch on top, so single-field edits succeed
without resupplying the full strict schema. Entities that carry phone numbers
also have the GET-style list reshaped into the keyed PUT object.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.tools import _common as c
from mcp_server_buildium.tools.applicants import register_applicant_tools
from mcp_server_buildium.tools.associations import register_association_tools
from mcp_server_buildium.tools.bank_accounts import register_bank_account_tools
from mcp_server_buildium.tools.bills import register_bill_tools
from mcp_server_buildium.tools.files import register_file_tools
from mcp_server_buildium.tools.leases import register_lease_tools
from mcp_server_buildium.tools.owners import register_owner_tools
from mcp_server_buildium.tools.tasks import register_task_tools
from mcp_server_buildium.tools.units import register_unit_tools
from mcp_server_buildium.tools.vendors import register_vendor_tools
from mcp_server_buildium.tools.work_orders import register_work_order_tools


# ---------------------------------------------------------------------------
# Shared merge helpers
# ---------------------------------------------------------------------------
class _FakeModel:
    """Minimal stand-in for an SDK model exposing ``to_dict``."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    def to_dict(self) -> dict[str, Any]:
        return self._doc


def test_get_to_put_base_preserves_alias_keys() -> None:
    current = _FakeModel({"Id": 1, "Name": "x", "Nested": {"AddressLine1": "a"}})
    base = c.get_to_put_base(current)
    assert base == {"Id": 1, "Name": "x", "Nested": {"AddressLine1": "a"}}


def test_get_to_put_base_reshapes_phone_list() -> None:
    current = _FakeModel(
        {"PhoneNumbers": [{"Number": "1", "Type": "Home"}, {"Number": "2", "Type": "Cell"}]}
    )
    base = c.get_to_put_base(current, reshape_phones=True)
    assert base["PhoneNumbers"] == {"home": "1", "mobile": "2"}


def test_get_to_put_base_drops_empty_phone_list_when_reshaping() -> None:
    base = c.get_to_put_base(_FakeModel({"PhoneNumbers": []}), reshape_phones=True)
    assert "PhoneNumbers" not in base
    assert "phone_numbers" not in base


def test_merge_update_overlays_patch_on_current() -> None:
    current = _FakeModel({"FirstName": "Ada", "Email": "old@example.com"})
    # Patch uses snake_case; the base alias spelling is preserved on match.
    merged = c.merge_update(current, {"email": "new@example.com"})
    assert merged == {"FirstName": "Ada", "Email": "new@example.com"}


# ---------------------------------------------------------------------------
# Generic fake API plumbing
# ---------------------------------------------------------------------------
class _FakeApi:
    """Dispatches arbitrary SDK method names to a canned GET and a capturing PUT.

    ``get_method`` returns the current record; ``update_method`` records the
    ``*_put_message``/``*_save_message`` keyword it receives so assertions can
    inspect the payload that would be sent to Buildium.
    """

    def __init__(self, current: dict[str, Any], get_method: str, update_method: str) -> None:
        self._current = current
        self._get_method = get_method
        self._update_method = update_method
        self.received: Any = None

    def __getattr__(self, name: str) -> Any:
        if name == self._get_method:

            async def _get(**_kwargs: Any) -> _FakeModel:
                return _FakeModel(self._current)

            return _get
        if name == self._update_method:

            async def _update(**kwargs: Any) -> Any:
                # The message keyword is the only non-id kwarg.
                for key, value in kwargs.items():
                    if key.endswith("_message"):
                        self.received = value
                return kwargs

            return _update
        raise AttributeError(name)


class _FakeClient:
    def __init__(self, **apis: _FakeApi) -> None:
        for attr, api in apis.items():
            setattr(self, attr, api)


async def _get_tool(register: Any, client: Any, name: str) -> Any:
    mcp = FastMCP("test")
    register(mcp, client)
    tools = await mcp.get_tools()
    return tools[name]


# ---------------------------------------------------------------------------
# Phone-carrying entities: partial phone update preserves other fields
# ---------------------------------------------------------------------------
_OWNER_CURRENT = {
    "Id": 5,
    "FirstName": "Ada",
    "LastName": "Lovelace",
    "IsCompany": False,
    "PropertyIds": [1],
    "Address": {"AddressLine1": "1 Way", "PostalCode": "12345", "Country": "UnitedStates"},
    "PhoneNumbers": [{"Number": "555-000-1111", "Type": "Home"}],
}


@pytest.mark.asyncio
async def test_update_rental_owner_partial_phone() -> None:
    api = _FakeApi(
        _OWNER_CURRENT,
        "external_api_rental_owners_get_rental_owner_by_id",
        "external_api_rental_owners_update_rental_owner",
    )
    client = _FakeClient(rental_owners_api=api)
    tool = await _get_tool(register_owner_tools, client, "update_rental_owner")

    result = await tool.fn(owner_id=5, owner_data={"phone_numbers": {"mobile": "555-999-8888"}})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["FirstName"] == "Ada"
    assert sent["PhoneNumbers"] == {"Home": "555-000-1111", "Mobile": "555-999-8888"}
    assert "Id" not in sent


@pytest.mark.asyncio
async def test_update_vendor_partial_field_preserves_required() -> None:
    api = _FakeApi(
        {"Id": 2, "FirstName": "Bob", "LastName": "Smith", "IsCompany": False, "CategoryId": 3},
        "external_api_vendors_get_vendor_by_id",
        "external_api_vendors_update_vendor",
    )
    client = _FakeClient(vendors_api=api)
    tool = await _get_tool(register_vendor_tools, client, "update_vendor")

    result = await tool.fn(vendor_id=2, vendor_data={"FirstName": "Robert"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["FirstName"] == "Robert"
    assert sent["LastName"] == "Smith"
    assert sent["CategoryId"] == 3


@pytest.mark.asyncio
async def test_update_applicant_partial_field() -> None:
    api = _FakeApi(
        {"Id": 9, "FirstName": "Grace", "LastName": "Hopper", "Email": "old@example.com"},
        "external_api_applicants_get_applicant_by_id",
        "external_api_applicants_update_applicant",
    )
    client = _FakeClient(applicants_api=api)
    tool = await _get_tool(register_applicant_tools, client, "update_applicant")

    result = await tool.fn(applicant_id=9, applicant_data={"Email": "grace@example.com"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Email"] == "grace@example.com"
    assert sent["FirstName"] == "Grace"


# ---------------------------------------------------------------------------
# Non-phone entities: partial edits preserve untouched required fields
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_work_order_partial_field() -> None:
    api = _FakeApi(
        {"Id": 3, "EntryAllowed": "Yes", "VendorId": 7, "InvoiceNumber": "INV-1"},
        "external_api_work_orders_get_work_order_by_id",
        "external_api_work_orders_update_work_order",
    )
    client = _FakeClient(work_orders_api=api)
    tool = await _get_tool(register_work_order_tools, client, "update_work_order")

    result = await tool.fn(work_order_id=3, work_order_data={"InvoiceNumber": "INV-2"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["InvoiceNumber"] == "INV-2"
    assert sent["VendorId"] == 7


@pytest.mark.asyncio
async def test_update_bill_partial_field() -> None:
    api = _FakeApi(
        {"Id": 8, "Date": "2020-01-01", "DueDate": "2020-02-01", "VendorId": 1, "Memo": "old"},
        "external_api_bills_get_bill_by_id",
        "external_api_bills_update_bill",
    )
    client = _FakeClient(bills_api=api)
    tool = await _get_tool(register_bill_tools, client, "update_bill")

    result = await tool.fn(bill_id=8, bill_data={"Memo": "new"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Memo"] == "new"
    assert sent["VendorId"] == 1


@pytest.mark.asyncio
async def test_update_bank_account_partial_field() -> None:
    api = _FakeApi(
        {
            "Id": 6,
            "Name": "Operating",
            "Description": "old",
            "BankAccountType": "Checking",
            "Country": "UnitedStates",
            "CheckPrintingInfo": {
                "EnableRemoteCheckPrinting": False,
                "EnableLocalCheckPrinting": False,
                "CheckLayoutType": "Voucher2StubTopMemo",
            },
        },
        "external_api_bank_accounts_get_bank_account",
        "external_api_bank_accounts_update_bank_account",
    )
    client = _FakeClient(bank_accounts_api=api)
    tool = await _get_tool(register_bank_account_tools, client, "update_bank_account")

    result = await tool.fn(bank_account_id=6, bank_account_data={"Description": "new"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Description"] == "new"
    assert sent["Name"] == "Operating"


@pytest.mark.asyncio
async def test_update_task_category_partial_field() -> None:
    api = _FakeApi(
        {"Id": 4, "Name": "Plumbing"},
        "external_api_task_categories_get_task_category_by_id",
        "external_api_task_categories_update_task_category",
    )
    client = _FakeClient(tasks_api=api)
    tool = await _get_tool(register_task_tools, client, "update_task_category")

    result = await tool.fn(category_id=4, category_data={"Name": "HVAC"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Name"] == "HVAC"


@pytest.mark.asyncio
async def test_update_file_partial_field() -> None:
    api = _FakeApi(
        {"Id": 11, "Title": "doc", "CategoryId": 2, "Description": "old"},
        "external_api_files_get_file_by_id",
        "external_api_files_update_file",
    )
    client = _FakeClient(files_api=api)
    tool = await _get_tool(register_file_tools, client, "update_file")

    result = await tool.fn(file_id=11, file_data={"Description": "new"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Description"] == "new"
    assert sent["Title"] == "doc"


@pytest.mark.asyncio
async def test_update_rental_unit_partial_field() -> None:
    api = _FakeApi(
        {
            "Id": 12,
            "UnitNumber": "1A",
            "Description": "old",
            "Address": {"AddressLine1": "1 Way", "PostalCode": "12345", "Country": "UnitedStates"},
        },
        "external_api_rental_units_get_rental_unit_by_id",
        "external_api_rental_units_update_rental_unit",
    )
    client = _FakeClient(rental_units_api=api)
    tool = await _get_tool(register_unit_tools, client, "update_rental_unit")

    result = await tool.fn(unit_id=12, unit_data={"Description": "new"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Description"] == "new"
    assert sent["UnitNumber"] == "1A"


@pytest.mark.asyncio
async def test_update_association_partial_field() -> None:
    api = _FakeApi(
        {
            "Id": 13,
            "Name": "HOA",
            "OperatingBankAccountId": 1,
            "FiscalYearEndDay": 31,
            "FiscalYearEndMonth": 12,
            "Address": {"AddressLine1": "1 Way", "PostalCode": "12345", "Country": "UnitedStates"},
        },
        "external_api_associations_get_association_by_id",
        "external_api_associations_update_association",
    )
    client = _FakeClient(associations_api=api)
    tool = await _get_tool(register_association_tools, client, "update_association")

    result = await tool.fn(association_id=13, association_data={"Name": "New HOA"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Name"] == "New HOA"
    assert sent["OperatingBankAccountId"] == 1


@pytest.mark.asyncio
async def test_update_lease_partial_field() -> None:
    api = _FakeApi(
        {
            "Id": 14,
            "UnitId": 1,
            "LeaseType": "Fixed",
            "LeaseFromDate": "2020-01-01",
            "IsEvictionPending": False,
        },
        "external_api_leases_get_lease_by_id",
        "external_api_leases_update_lease",
    )
    client = _FakeClient(leases_api=api)
    tool = await _get_tool(register_lease_tools, client, "update_lease")

    result = await tool.fn(lease_id=14, lease_data={"LeaseType": "AtWill"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["LeaseType"] == "AtWill"
    assert sent["UnitId"] == 1
