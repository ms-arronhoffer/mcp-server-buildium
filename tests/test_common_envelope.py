"""Tests for the shared response envelope and tool classification."""

from __future__ import annotations

import pytest

from mcp_server_buildium.tools import _common as c


def test_success_envelope_has_meta_key() -> None:
    env = c.success([1, 2, 3])
    assert env["data"] == [1, 2, 3]
    assert env["count"] == 3
    assert env["error"] is None
    assert "meta" in env  # stable schema
    assert env["meta"] is None


def test_success_with_meta() -> None:
    env = c.success({"a": 1}, meta={"duration_ms": 5.0})
    assert env["meta"]["duration_ms"] == 5.0


def test_failure_envelope_shape() -> None:
    env = c.failure("boom", status=500, code="api_error", hint="retry")
    assert env["data"] is None
    assert env["error"]["code"] == "api_error"
    assert env["error"]["status"] == 500
    assert env["error"]["hint"] == "retry"
    assert env["error"]["message"] == "boom"


def test_classify_op_type() -> None:
    assert c.classify_op_type("list_leases") == "read"
    assert c.classify_op_type("get_rental") == "read"
    assert c.classify_op_type("create_lease") == "write"
    assert c.classify_op_type("update_bill") == "write"
    assert c.classify_op_type("delete_thing") == "write"
    # server-local defaults to read (never an accidental mutation)
    assert c.classify_op_type("health_check") == "read"


def test_classify_sensitive() -> None:
    assert c.classify_sensitive("create_bill")
    assert c.classify_sensitive("list_bank_accounts")
    assert c.classify_sensitive("get_gl_account")
    assert c.classify_sensitive("create_bill_payment")
    assert c.classify_sensitive("create_file_download_request")
    assert not c.classify_sensitive("list_leases")
    assert not c.classify_sensitive("create_rental")


def test_register_operation_populates_metadata() -> None:
    try:
        c.register_operation("create_widget", "SomeOp_CreateWidget")
        meta = c.TOOL_METADATA["create_widget"]
        assert meta["op_type"] == "write"
        assert meta["sensitive"] is False
    finally:
        # Avoid polluting the global registry consumed by spec-coverage tests.
        c.TOOL_OPERATIONS.pop("create_widget", None)
        c.TOOL_METADATA.pop("create_widget", None)


@pytest.mark.asyncio
async def test_execute_attaches_timing_meta() -> None:
    async def call():
        return {"ok": True}

    env = await c.execute("get_thing", call)
    assert env["error"] is None
    assert env["meta"]["attempts"] == 1
    assert "duration_ms" in env["meta"]


@pytest.mark.asyncio
async def test_execute_validation_error_has_meta() -> None:
    async def call():
        raise ValueError("bad enum")

    env = await c.execute("list_thing", call)
    assert env["error"]["code"] == "validation_error"
    assert env["meta"] is not None


def test_clamp_pagination_bounds() -> None:
    assert c.clamp_pagination(100000, -5) == (c.MAX_LIMIT, 0)
    assert c.clamp_pagination(0, 10) == (c.MIN_LIMIT, 10)
    assert c.clamp_pagination(None, None) == (c.DEFAULT_LIMIT, 0)
