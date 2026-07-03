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
    tool_map = {t.name: t for t in event_loop.run_until_complete(mcp.get_tools()).values()}
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
    tool_map = {t.name: t for t in event_loop.run_until_complete(guarded.get_tools()).values()}

    # Reads remain available and functional against the mock.
    assert "list_leases" in tool_map
    result = event_loop.run_until_complete(tool_map["list_leases"].fn(limit=5))
    assert result["error"] is None

    # Mutations are not registered at all.
    assert "create_lease" not in tool_map
    assert "update_rental" not in tool_map
    assert "create_bill" not in tool_map
