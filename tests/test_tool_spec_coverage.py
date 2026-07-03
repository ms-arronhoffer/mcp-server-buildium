"""Spec-driven validation of MCP tools against the Buildium OpenAPI document.

These tests assert that every registered tool maps to a real Buildium OpenAPI
operation and that the generated coverage report is up to date. They run fully
offline (no credentials, no network) by introspecting the FastMCP server.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_PATH = REPO_ROOT / "openapi.json"
REPORT_PATH = REPO_ROOT / "docs" / "tool-coverage.md"


@pytest.fixture(scope="module")
def spec_operation_ids() -> set[str]:
    spec = json.loads(OPENAPI_PATH.read_text())
    ids: set[str] = set()
    for methods in spec.get("paths", {}).values():
        for op in methods.values():
            if isinstance(op, dict) and op.get("operationId"):
                ids.add(op["operationId"])
    return ids


@pytest.fixture(scope="module")
def server_module(monkeypatch_env):  # noqa: ANN001
    from mcp_server_buildium import server

    return server


@pytest.fixture(scope="module")
def monkeypatch_env():
    import os

    os.environ.setdefault("BUILDIUM_CLIENT_ID", "test-id")
    os.environ.setdefault("BUILDIUM_CLIENT_SECRET", "test-secret")
    return os.environ


@pytest.fixture(scope="module")
def tool_operations(server_module) -> dict[str, str]:  # noqa: ANN001
    from mcp_server_buildium.tools import _common

    return dict(_common.TOOL_OPERATIONS)


@pytest.fixture(scope="module")
def registered_tool_names(server_module) -> list[str]:  # noqa: ANN001
    tools = asyncio.run(server_module.mcp.get_tools())
    return [t.name for t in tools.values()]


def test_every_mapped_tool_targets_a_real_operation(
    tool_operations: dict[str, str], spec_operation_ids: set[str]
) -> None:
    """Each tool's declared operationId must exist in openapi.json."""
    invalid = {t: op for t, op in tool_operations.items() if op not in spec_operation_ids}
    assert not invalid, f"Tools mapping to non-existent operations: {invalid}"


def test_every_registered_tool_is_mapped_or_local(
    registered_tool_names: list[str], tool_operations: dict[str, str]
) -> None:
    """Every tool either maps to a spec operation or is an allowed server-local tool."""
    server_local = {"health_check"}
    unmapped = set(registered_tool_names) - set(tool_operations) - server_local
    assert not unmapped, f"Tools with no operation mapping: {sorted(unmapped)}"


def test_operation_mappings_are_unique(tool_operations: dict[str, str]) -> None:
    """No two tools should silently target the same operation via a copy/paste error."""
    seen: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    for tool, op in tool_operations.items():
        if op in seen:
            duplicates.setdefault(op, [seen[op]]).append(tool)
        else:
            seen[op] = tool
    # Duplicates are allowed only where genuinely intended; today there are none.
    assert not duplicates, f"Multiple tools map to the same operation: {duplicates}"


def test_coverage_report_is_current() -> None:
    """docs/tool-coverage.md must match freshly generated output."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_tool_coverage.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Coverage report is stale or mappings invalid. "
        f"stdout={result.stdout} stderr={result.stderr}"
    )
