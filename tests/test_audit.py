"""Tests for the audit trail (sinks, redaction, summarization)."""

from __future__ import annotations

import json
import os

from mcp_server_buildium import audit


def test_sanitize_args_redacts_secrets() -> None:
    sanitized = audit.sanitize_args({"client_secret": "topsecret", "lease_id": 5, "password": "p"})
    assert sanitized["client_secret"] == "***REDACTED***"
    assert sanitized["password"] == "***REDACTED***"
    assert sanitized["lease_id"] == 5


def test_sanitize_args_truncates_large_payloads() -> None:
    big = {"blob": "x" * (audit.MAX_ARG_CHARS + 100)}
    sanitized = audit.sanitize_args(big)
    assert sanitized["_truncated"] is True
    assert len(sanitized["preview"]) == audit.MAX_ARG_CHARS


def test_sanitize_args_none() -> None:
    assert audit.sanitize_args(None) is None
    assert audit.sanitize_args({}) is None


def test_file_sink_writes_jsonl(tmp_path) -> None:
    path = os.path.join(tmp_path, "audit.log")
    recorder = audit.AuditRecorder(audit.FileSink(path), role="admin")
    recorder.record(tool="create_lease", op_type="write", outcome="success", duration_ms=1.2)
    recorder.record(
        tool="create_bill",
        op_type="write",
        outcome="denied",
        code="forbidden",
        reason="role=readonly",
        args={"client_secret": "s"},
    )
    lines = [json.loads(line) for line in open(path)]
    assert len(lines) == 2
    assert lines[0]["tool"] == "create_lease"
    assert lines[0]["role"] == "admin"
    # secret redacted in persisted args
    assert lines[1]["args"]["client_secret"] == "***REDACTED***"


def test_null_sink_discards() -> None:
    recorder = audit.AuditRecorder(audit.NullSink())
    recorder.record(tool="list_leases", op_type="read", outcome="success")  # no error


def test_summarize_events() -> None:
    events = [
        {"tool": "list_leases", "op_type": "read", "outcome": "success"},
        {"tool": "create_lease", "op_type": "write", "outcome": "success"},
        {"tool": "create_bill", "op_type": "write", "outcome": "error", "status": 500},
        {"tool": "create_bill", "op_type": "write", "outcome": "denied", "reason": "role"},
    ]
    summary = audit.summarize_events(events)
    assert summary["total_events"] == 4
    assert summary["by_outcome"]["success"] == 2
    assert summary["mutation_count"] == 2  # success + error writes
    assert summary["denied_count"] == 1
    assert summary["error_rate"] == 0.25
    assert summary["by_tool"]["create_bill"] == 2


def test_read_events_skips_malformed(tmp_path) -> None:
    path = os.path.join(tmp_path, "audit.log")
    with open(path, "w") as handle:
        handle.write('{"tool": "a", "outcome": "success", "op_type": "read"}\n')
        handle.write("not json\n")
        handle.write('{"tool": "b", "outcome": "error", "op_type": "write"}\n')
    events = audit.read_events(path)
    assert len(events) == 2


def test_read_events_missing_file() -> None:
    assert audit.read_events("/nonexistent/path/audit.log") == []


def test_from_config_file_requires_path() -> None:
    class Cfg:
        audit_sink = "file"
        audit_file = None
        role = "admin"

    try:
        audit.AuditRecorder.from_config(Cfg())
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
