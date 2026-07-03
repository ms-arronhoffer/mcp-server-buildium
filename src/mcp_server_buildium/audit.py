"""Pluggable audit trail for the Buildium MCP server.

Every tool invocation (and every policy denial or rate-limit rejection) emits a
structured *audit event* that is independent of operational logging. Events are
written to a configurable **sink**:

* ``log`` (default): emit through the existing stderr JSON logger under the
  ``audit`` event name. Preserves today's behavior with richer detail.
* ``file``: append newline-delimited JSON to ``BUILDIUM_AUDIT_FILE`` so the
  trail is durable and can be summarized by
  ``scripts/generate_audit_report.py``.
* ``none``: disable auditing.

Arguments are scrubbed of secrets/PII and truncated before being recorded, so
the trail never leaks credentials or unbounded payloads.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

from .logging_config import get_logger, log_event, scrub

logger = get_logger("mcp_server_buildium.audit")

# Cap on the serialized size of audited arguments to keep the trail bounded.
MAX_ARG_CHARS = 2000

AUDIT_EVENT = "audit"


def sanitize_args(args: dict[str, Any] | None) -> Any:
    """Redact secrets/PII from tool arguments and truncate large payloads."""
    if not args:
        return None
    scrubbed = scrub(args)
    try:
        encoded = json.dumps(scrubbed, default=str)
    except (TypeError, ValueError):
        return {"_unserializable": True}
    if len(encoded) > MAX_ARG_CHARS:
        return {"_truncated": True, "preview": encoded[:MAX_ARG_CHARS]}
    return scrubbed


class AuditSink:
    """Base class for audit sinks."""

    def emit(self, event: dict[str, Any]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - optional
        """Release any resources held by the sink."""


class NullSink(AuditSink):
    """Discards all audit events."""

    def emit(self, event: dict[str, Any]) -> None:
        return None


class LogSink(AuditSink):
    """Routes audit events through the structured stderr logger."""

    def emit(self, event: dict[str, Any]) -> None:
        log_event(logger, logging.INFO, AUDIT_EVENT, **event)


class FileSink(AuditSink):
    """Appends newline-delimited JSON audit records to a file (thread-safe)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)

    def emit(self, event: dict[str, Any]) -> None:
        line = json.dumps(scrub(event), default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class AuditRecorder:
    """Builds and dispatches audit events to a sink."""

    def __init__(self, sink: AuditSink, *, role: str | None = None) -> None:
        self.sink = sink
        self.role = role

    @classmethod
    def from_config(cls, config) -> AuditRecorder:  # noqa: ANN001 - avoid cycle
        """Construct a recorder from a :class:`BuildiumConfig`-like object."""
        sink_name = (getattr(config, "audit_sink", None) or "log").strip().lower()
        role = getattr(config, "role", None)
        if sink_name in ("none", "off", "disabled"):
            return cls(NullSink(), role=role)
        if sink_name == "file":
            path = getattr(config, "audit_file", None)
            if not path:
                raise ValueError("BUILDIUM_AUDIT_SINK=file requires BUILDIUM_AUDIT_FILE to be set")
            return cls(FileSink(path), role=role)
        if sink_name == "log":
            return cls(LogSink(), role=role)
        raise ValueError(
            f"Unknown BUILDIUM_AUDIT_SINK {sink_name!r}. Valid values: log, file, none"
        )

    def record(
        self,
        *,
        tool: str,
        op_type: str,
        outcome: str,
        sensitive: bool = False,
        status: int | None = None,
        code: str | None = None,
        attempts: int | None = None,
        duration_ms: float | None = None,
        args: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        """Emit a single audit event.

        Args:
            tool: The tool name.
            op_type: ``"read"`` or ``"write"``.
            outcome: One of ``success``, ``error``, ``denied``, ``rate_limited``.
            sensitive: Whether the tool touches financially sensitive resources.
            status: Upstream HTTP status, if any.
            code: Machine-readable error code, if any.
            attempts: Number of upstream attempts made.
            duration_ms: Wall-clock duration in milliseconds.
            args: Tool arguments (sanitized before recording).
            reason: Human-readable reason (e.g. policy denial explanation).
        """
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "tool": tool,
            "op_type": op_type,
            "sensitive": sensitive,
            "role": self.role,
            "outcome": outcome,
        }
        if status is not None:
            event["status"] = status
        if code is not None:
            event["code"] = code
        if attempts is not None:
            event["attempts"] = attempts
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if reason is not None:
            event["reason"] = reason
        sanitized = sanitize_args(args)
        if sanitized is not None:
            event["args"] = sanitized
        self.sink.emit(event)


# ---------------------------------------------------------------------------
# Reporting helpers (consumed by audit_summary and generate_audit_report.py)
# ---------------------------------------------------------------------------
def read_events(path: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Read audit records from a newline-delimited JSON file.

    Malformed lines are skipped. When ``limit`` is given, only the most recent
    ``limit`` records are returned.
    """
    if not os.path.exists(path):
        return []
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (TypeError, ValueError):
                continue
    if limit is not None and len(events) > limit:
        events = events[-limit:]
    return events


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate audit ``events`` into counts and recent security-relevant items."""
    by_tool: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    by_op_type: dict[str, int] = {}
    mutations: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    errors = 0

    for event in events:
        tool = str(event.get("tool", "unknown"))
        outcome = str(event.get("outcome", "unknown"))
        op_type = str(event.get("op_type", "unknown"))
        by_tool[tool] = by_tool.get(tool, 0) + 1
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        by_op_type[op_type] = by_op_type.get(op_type, 0) + 1
        if outcome == "error":
            errors += 1
        if op_type == "write" and outcome in ("success", "error"):
            mutations.append(event)
        if outcome in ("denied", "rate_limited"):
            denied.append(event)

    total = len(events)
    return {
        "total_events": total,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_outcome": by_outcome,
        "by_op_type": by_op_type,
        "mutation_count": len(mutations),
        "recent_mutations": mutations[-20:],
        "denied_count": len(denied),
        "recent_denied": denied[-20:],
    }


def summarize_file(path: str, *, limit: int | None = None) -> dict[str, Any]:
    """Convenience wrapper: read a file audit sink and summarize it."""
    return summarize_events(read_events(path, limit=limit))
