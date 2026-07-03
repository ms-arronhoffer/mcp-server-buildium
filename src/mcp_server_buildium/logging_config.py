"""Structured logging configuration for the Buildium MCP server.

Logs are emitted to ``stderr`` so they never corrupt the MCP ``stdio`` protocol
which uses ``stdout`` for JSON-RPC messages.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

_CONFIGURED = False

# Header/field names that must never be written to logs.
_SENSITIVE_KEYS = {
    "x-buildium-client-id",
    "x-buildium-client-secret",
    "client_id",
    "client_secret",
    "authorization",
    "password",
    "secret",
    "token",
}


def scrub(value: object) -> object:
    """Recursively redact sensitive values from a structure before logging."""
    if isinstance(value, dict):
        return {
            k: ("***REDACTED***" if str(k).lower() in _SENSITIVE_KEYS else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter with no external dependencies."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Attach structured extras stored under ``record.extra_fields``.
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(scrub(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None, *, json_format: bool | None = None) -> None:
    """Configure root logging once for the process.

    Args:
        level: Log level name (defaults to ``BUILDIUM_LOG_LEVEL`` or ``INFO``).
        json_format: Emit JSON logs (defaults to ``BUILDIUM_LOG_JSON`` truthy).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = level or os.getenv("BUILDIUM_LOG_LEVEL", "INFO")
    if json_format is None:
        json_format = os.getenv("BUILDIUM_LOG_JSON", "false").lower() in {"1", "true", "yes"}

    handler = logging.StreamHandler(stream=sys.stderr)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger, ensuring logging is configured first."""
    configure_logging()
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **fields: object) -> None:
    """Emit a log record with structured, secret-scrubbed extra fields."""
    logger.log(level, message, extra={"extra_fields": fields})
