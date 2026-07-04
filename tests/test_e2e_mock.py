"""End-to-end tests running the MCP tools against the seeded mock Buildium API.

The mock API is started in-process (uvicorn on a background thread) with a fresh
seeded SQLite database, and the real MCP tools are invoked against it via the
generated SDK. This validates the full path: tool → SDK → HTTP → mock → SDK
deserialization → ``{data, count, error}`` envelope.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from mcp_server_buildium.tools._common import list_tools_map
from mockapi.app import create_app
from mockapi.db import SessionLocal, reset_db
from mockapi.seed import seed_all


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server() -> Iterator[str]:
    """Start the seeded mock API on a background thread; yield its base URL."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "e2e.db")
    os.environ["MOCKAPI_DATABASE_URL"] = f"sqlite:///{db_path}"

    # Re-bind the db module to the test database and seed it.
    import mockapi.db as db_module

    db_module.engine.dispose()
    db_module.engine = db_module.create_engine(
        os.environ["MOCKAPI_DATABASE_URL"], connect_args={"check_same_thread": False}
    )
    db_module.SessionLocal.configure(bind=db_module.engine)
    reset_db()
    session = SessionLocal()
    try:
        seed_all(session)
    finally:
        session.close()

    port = _free_port()
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    # Wait for startup.
    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Mock server failed to start")

    yield base_url

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def event_loop():
    """A single event loop shared across the module.

    The generated SDK's httpx AsyncClient binds its connection pool to the loop
    that first uses it. Reusing one loop mirrors the real server (which runs in a
    single loop) and avoids cross-loop ``RuntimeError``s in the test harness.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def run(event_loop):
    """Return a helper that runs a coroutine on the shared loop."""

    def _run(coro):
        return event_loop.run_until_complete(coro)

    return _run


@pytest.fixture(scope="module")
def tools(mock_server: str, event_loop):
    """Register all MCP tools pointed at the mock server; return a name→tool map."""
    os.environ["BUILDIUM_CLIENT_ID"] = "e2e-id"
    os.environ["BUILDIUM_CLIENT_SECRET"] = "e2e-secret"
    os.environ["BUILDIUM_BASE_URL"] = mock_server

    from fastmcp import FastMCP

    from mcp_server_buildium.buildium_client import BuildiumClient
    from mcp_server_buildium.config import BuildiumConfig
    from mcp_server_buildium.server import _CATEGORY_REGISTRARS

    client = BuildiumClient(config=BuildiumConfig(base_url=mock_server))
    mcp = FastMCP("e2e")
    for register in _CATEGORY_REGISTRARS.values():
        register(mcp, client)
    tool_map = {t.name: t for t in event_loop.run_until_complete(list_tools_map(mcp)).values()}
    return tool_map


def _ok(result: dict) -> dict:
    assert result["error"] is None, f"tool returned error: {result['error']}"
    return result


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "min_count"),
    [
        ("list_rentals", {"limit": 5}, 5),
        ("list_rental_units", {"property_id": 1}, 1),
        ("list_leases", {"limit": 5}, 5),
        ("list_vendors", {}, 1),
        ("list_tasks", {}, 1),
        ("list_bills", {}, 1),
        ("list_bank_accounts", {}, 3),
        ("list_applicants", {}, 1),
        ("list_associations", {}, 4),
        ("list_gl_accounts", {}, 1),
        ("list_work_orders", {}, 1),
        ("list_files", {}, 1),
    ],
)
def test_list_tools_return_seeded_data(tools, run, tool_name, kwargs, min_count):
    tool = tools.get(tool_name)
    assert tool is not None, f"missing tool {tool_name} (have {sorted(tools)[:5]}...)"
    result = _ok(run(tool.fn(**kwargs)))
    assert result["count"] is not None and result["count"] >= min_count


def test_get_single_rental(tools, run):
    result = _ok(run(tools["get_rental"].fn(property_id=1)))
    assert result["data"]["Id"] == 1


def test_lease_transactions_nested(tools, run):
    result = _ok(run(tools["list_lease_transactions"].fn(lease_id=1)))
    assert result["count"] >= 1


def test_pagination_clamped(tools, run):
    # Requesting an oversized limit should be clamped, not error.
    result = _ok(run(tools["list_leases"].fn(limit=100000)))
    assert result["error"] is None


def test_invalid_enum_returns_validation_error(tools, run):
    result = run(tools["list_work_orders"].fn(status="Bogus"))
    assert result["error"] is not None
    assert result["error"]["code"] == "validation_error"


def test_list_result_has_timing_meta(tools, run):
    result = _ok(run(tools["list_rentals"].fn(limit=5)))
    assert result["meta"] is not None
    assert "duration_ms" in result["meta"]
    assert result["meta"]["attempts"] == 1


def test_partial_rental_tenant_phone_update(tools, run):
    """Updating only a phone number must not require the full tenant schema."""
    before = _ok(run(tools["get_rental_tenant"].fn(tenant_id=1)))
    assert before["data"]["FirstName"] == "Tenant1"

    result = _ok(
        run(
            tools["update_rental_tenant"].fn(
                tenant_id=1, tenant_data={"phone_numbers": {"mobile": "555-867-5309"}}
            )
        )
    )
    # Required fields were preserved from the existing record.
    assert result["data"]["FirstName"] == "Tenant1"
    assert result["data"]["LastName"] == "Doe"
    # The mobile number is present after the update (list form in the response).
    numbers = {p["Number"] for p in result["data"]["PhoneNumbers"]}
    assert "555-867-5309" in numbers


def test_partial_association_tenant_phone_update(tools, run):
    """Association tenants support the same partial-update behavior."""
    result = _ok(
        run(
            tools["update_association_tenant"].fn(
                tenant_id=1, tenant_data={"phone_numbers": {"mobile": "555-101-2020"}}
            )
        )
    )
    assert result["data"]["FirstName"] == "AssocTenant1"
    numbers = {p["Number"] for p in result["data"]["PhoneNumbers"]}
    assert "555-101-2020" in numbers


def test_partial_vendor_phone_update(tools, run):
    """Adding a phone number to a vendor must round-trip as a list (see #issue).

    Vendors expose ``PhoneNumbers`` as a list on read but accept the keyed object
    form on write; the mock normalizes the object back into the list shape so the
    update response deserializes as ``VendorMessage`` instead of erroring.
    """
    result = _ok(
        run(
            tools["update_vendor"].fn(
                vendor_id=2, vendor_data={"phone_numbers": {"mobile": "6144445511"}}
            )
        )
    )
    assert result["data"]["CompanyName"] == "Vendor Co 2"
    numbers = {p["Number"] for p in result["data"]["PhoneNumbers"]}
    assert "6144445511" in numbers


def test_partial_rental_owner_phone_update(tools, run):
    """Rental owners accept the same partial phone update."""
    result = _ok(
        run(
            tools["update_rental_owner"].fn(
                owner_id=1, owner_data={"phone_numbers": {"mobile": "555-111-2222"}}
            )
        )
    )
    assert result["data"]["FirstName"] == "Owner1"
    numbers = {p["Number"] for p in result["data"]["PhoneNumbers"]}
    assert "555-111-2222" in numbers


def test_partial_association_owner_phone_update(tools, run):
    """Association owners require a PrimaryAddress on write, preserved from read."""
    result = _ok(
        run(
            tools["update_association_owner"].fn(
                owner_id=1, owner_data={"phone_numbers": {"mobile": "555-111-3333"}}
            )
        )
    )
    assert result["data"]["FirstName"] == "AssocOwner1"
    numbers = {p["Number"] for p in result["data"]["PhoneNumbers"]}
    assert "555-111-3333" in numbers


def test_partial_work_order_update(tools, run):
    """Work order partial edits preserve the required EntryAllowed/VendorId."""
    result = _ok(
        run(tools["update_work_order"].fn(work_order_id=1, work_order_data={"VendorNotes": "hi"}))
    )
    assert result["error"] is None


def test_partial_bill_update(tools, run):
    """Bill partial edits reshape each line's GLAccount lookup into GlAccountId."""
    result = _ok(run(tools["update_bill"].fn(bill_id=1, bill_data={"Memo": "updated"})))
    assert result["data"]["Memo"] == "updated"


def test_partial_bank_account_update(tools, run):
    """Bank account partial edits preserve the required CheckPrintingInfo/Country."""
    result = _ok(
        run(tools["update_bank_account"].fn(bank_account_id=1, bank_account_data={"Description": "new"}))
    )
    assert result["data"]["Description"] == "new"


@pytest.mark.parametrize(
    ("tool_name", "payload", "field_name"),
    [
        (
            "create_rental_unit",
            {"PropertyId": 1, "Address": {"AddressLine1": "100 Main", "City": "X", "State": "IL"}},
            "UnitNumber",
        ),
        (
            "create_association_unit",
            {
                "AssociationId": 1,
                "Address": {"AddressLine1": "100 Main", "City": "X", "State": "IL"},
            },
            "UnitNumber",
        ),
        ("create_task_category", {}, "Name"),
    ],
)
def test_create_tools_enforce_openapi_required_fields(tools, run, tool_name, payload, field_name):
    result = run(tools[tool_name].fn(**{"unit_data": payload} if "unit" in tool_name else {"category_data": payload}))
    assert result["error"] is not None
    assert result["error"]["code"] == "validation_error"
    assert field_name in result["error"]["message"]


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "key", "expected"),
    [
        (
            "create_rental_unit",
            {
                "unit_data": {
                    "UnitNumber": "U-NEW-1",
                    "PropertyId": 1,
                    "Address": {
                        "AddressLine1": "500 Elm Street",
                        "City": "Springfield",
                        "State": "IL",
                    },
                }
            },
            "UnitNumber",
            "U-NEW-1",
        ),
        (
            "create_association_unit",
            {
                "unit_data": {
                    "UnitNumber": "A-NEW-1",
                    "AssociationId": 1,
                    "Address": {
                        "AddressLine1": "700 Oak Avenue",
                        "City": "Springfield",
                        "State": "IL",
                    },
                }
            },
            "UnitNumber",
            "A-NEW-1",
        ),
        (
            "create_task_category",
            {"category_data": {"Name": "Urgent Repairs"}},
            "Name",
            "Urgent Repairs",
        ),
    ],
)
def test_create_tools_round_trip_through_mockapi(tools, run, tool_name, kwargs, key, expected):
    result = _ok(run(tools[tool_name].fn(**kwargs)))
    assert result["data"][key] == expected


def test_readonly_policy_blocks_mutations_e2e(mock_server, event_loop):
    """A readonly GuardedMCP must not expose mutating tools, but reads still work."""
    from fastmcp import FastMCP

    from mcp_server_buildium import audit
    from mcp_server_buildium.buildium_client import BuildiumClient
    from mcp_server_buildium.config import BuildiumConfig
    from mcp_server_buildium.security.policy import ToolPolicy
    from mcp_server_buildium.security.registration import GuardedMCP
    from mcp_server_buildium.server import _CATEGORY_REGISTRARS

    client = BuildiumClient(config=BuildiumConfig(base_url=mock_server))
    guarded = GuardedMCP(
        FastMCP("e2e-readonly"),
        ToolPolicy(role="readonly"),
        audit.AuditRecorder(audit.NullSink()),
    )
    for register in _CATEGORY_REGISTRARS.values():
        register(guarded, client)
    tool_map = {t.name: t for t in event_loop.run_until_complete(list_tools_map(guarded)).values()}

    # Reads remain available and functional against the mock.
    assert "list_leases" in tool_map
    result = event_loop.run_until_complete(tool_map["list_leases"].fn(limit=5))
    assert result["error"] is None

    # Mutations are not registered at all.
    assert "create_lease" not in tool_map
    assert "update_rental" not in tool_map
    assert "create_bill" not in tool_map
