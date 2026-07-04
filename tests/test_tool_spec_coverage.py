"""Spec-driven validation of MCP tools against the Buildium OpenAPI document.

These tests assert that every registered tool maps to a real Buildium OpenAPI
operation and that the generated coverage report is up to date. They run fully
offline (no credentials, no network) by introspecting the FastMCP server.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_PATH = REPO_ROOT / "openapi.json"
REPORT_PATH = REPO_ROOT / "docs" / "tool-coverage.md"
TOOLS_PATH = REPO_ROOT / "src" / "mcp_server_buildium" / "tools"


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
    if hasattr(server_module.mcp, "get_tools"):
        tools = asyncio.run(server_module.mcp.get_tools())
        return [t.name for t in tools.values()]
    from mcp_server_buildium.tools import _common

    return sorted(set(_common.TOOL_OPERATIONS) | set(_common.TOOL_METADATA) | {"health_check", "audit_summary"})


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
    server_local = {
        "health_check",
        "audit_summary",
        "describe_create_schema",
        "list_uploaded_documents",
        "create_download_file",
        "save_uploaded_document",
        "get_reference_data",
        "lease_receivables_summary",
        "rent_roll_report",
        "aged_receivables_report",
        "income_statement_report",
        "run_month_end_close",
        "portfolio_alerts",
        "budget_variance_report",
        "vacancy_analysis",
        "rent_trend_report",
        "vendor_spend_report",
        "cash_flow_projection",
        "maintenance_roi_report",
        "owner_distribution_report",
        "delinquency_trend",
        "missing_charge_detector",
        "concession_drift_analyzer",
        "security_deposit_exposure_report",
        "occupancy_turnover_latency_report",
        "lease_renewal_likelihood_scorecard",
        "owner_risk_dashboard",
        "work_order_sla_bottleneck_report",
        "vendor_concentration_variance_report",
        "morning_portfolio_digest",
        "end_of_day_exception_digest",
        "role_notification_feed",
        "rent_payment_behavior_shift_anomaly",
        "delinquency_cluster_anomaly",
        "expense_anomaly_detection",
        "work_order_cycle_time_anomaly",
        "vacancy_duration_anomaly",
        "data_quality_anomaly_scan",
    }
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


def _request_body_schema_name(operation: dict) -> str | None:
    """Return the request-body schema class name for an OpenAPI operation, if any."""
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return None
    content = request_body.get("content")
    if not isinstance(content, dict):
        return None
    app_json = content.get("application/json")
    if not isinstance(app_json, dict):
        return None
    return _schema_name(app_json.get("schema"))


def _schema_name(schema: object) -> str | None:
    """Resolve the first component-schema class name referenced by ``schema``."""
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            name = _schema_name(item)
            if name:
                return name
    return None


def _tool_request_models() -> dict[str, tuple[str, str]]:
    """Collect ``tool_name -> (sdk_module, sdk_class)`` request-model references.

    The registry is built by AST-parsing the tool modules and recording the SDK
    model referenced by each write tool via ``c.create(...)``,
    ``c.build_model(...)``, or a direct generated-model constructor call.
    """
    request_models: dict[str, tuple[str, str]] = {}
    for path in TOOLS_PATH.glob("*.py"):
        tree = ast.parse(path.read_text())
        imported_models: dict[str, tuple[str, str]] = {}

        for node in tree.body:
            if isinstance(node, ast.Try):
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.ImportFrom)
                        and stmt.module
                        and stmt.module.startswith("mcp_server_buildium.buildium_sdk.models.")
                    ):
                        module = stmt.module.rsplit(".", 1)[-1]
                        for alias in stmt.names:
                            imported_models[alias.asname or alias.name] = (module, alias.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            model: tuple[str, str] | None = None
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                if (
                    isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "c"
                    and sub.func.attr == "create"
                    and len(sub.args) >= 3
                    and all(
                        isinstance(sub.args[i], ast.Constant) and isinstance(sub.args[i].value, str)
                        for i in (1, 2)
                    )
                ):
                    model = (sub.args[1].value, sub.args[2].value)
                elif (
                    isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "c"
                    and sub.func.attr == "build_model"
                    and len(sub.args) >= 2
                    and all(
                        isinstance(sub.args[i], ast.Constant) and isinstance(sub.args[i].value, str)
                        for i in (0, 1)
                    )
                ):
                    model = (sub.args[0].value, sub.args[1].value)
                elif isinstance(sub.func, ast.Name) and sub.func.id in imported_models:
                    model = imported_models[sub.func.id]
            if model is not None:
                request_models[node.name] = model
    return request_models


def test_every_request_body_tool_uses_the_openapi_request_model(
    tool_operations: dict[str, str],
) -> None:
    spec = json.loads(OPENAPI_PATH.read_text())
    operations = {
        op["operationId"]: op
        for methods in spec.get("paths", {}).values()
        for op in methods.values()
        if isinstance(op, dict) and op.get("operationId")
    }
    request_models = _tool_request_models()

    missing: list[str] = []
    mismatched: list[str] = []
    unresolved: list[str] = []

    for tool_name, operation_id in sorted(tool_operations.items()):
        expected_class = _request_body_schema_name(operations[operation_id])
        if expected_class is None:
            continue
        actual = request_models.get(tool_name)
        if actual is None:
            missing.append(tool_name)
            continue
        module_name, class_name = actual
        try:
            mod = importlib.import_module(f"mcp_server_buildium.buildium_sdk.models.{module_name}")
            getattr(mod, class_name)
        except (ImportError, AttributeError) as exc:
            unresolved.append(f"{tool_name}: {module_name}.{class_name} ({exc})")
            continue
        if class_name != expected_class:
            mismatched.append(f"{tool_name}: expected {expected_class}, got {class_name}")

    assert not missing, f"Request-body tools missing request model construction: {missing}"
    assert not unresolved, f"Request models must resolve from generated SDK: {unresolved}"
    assert not mismatched, f"Request models must match OpenAPI request schemas: {mismatched}"


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
