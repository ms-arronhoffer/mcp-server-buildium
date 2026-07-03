"""Shared helpers for Buildium MCP tools.

Provides a consistent response envelope, robust error handling, retry/backoff
for transient failures, pagination clamping, and an operation registry that maps
each MCP tool to the Buildium OpenAPI ``operationId`` it exercises. The registry
is consumed by the spec-coverage validator (see ``scripts/generate_tool_coverage.py``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..logging_config import get_logger, log_event

try:  # pragma: no cover - exercised indirectly
    from mcp_server_buildium.buildium_sdk.exceptions import ApiException
except Exception:  # pragma: no cover - SDK always present in practice

    class ApiException(Exception):  # type: ignore[no-redef]  # noqa: N818
        """Fallback used only if the generated SDK is unavailable."""

        status: int | None = None
        reason: str | None = None
        body: str | None = None


logger = get_logger("mcp_server_buildium.tools")


def _env_int(name: str, default: int) -> int:
    """Read a positive integer from the environment, falling back to ``default``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to ``default``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Pagination guardrails (Buildium caps page size at 1000). Overridable via env so
# operators can tighten limits without code changes.
MAX_LIMIT = _env_int("BUILDIUM_MAX_PAGE_LIMIT", 1000)
MIN_LIMIT = 1
DEFAULT_LIMIT = _env_int("BUILDIUM_DEFAULT_PAGE_LIMIT", 100)

# Retry configuration for transient upstream failures.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = _env_int("BUILDIUM_MAX_RETRIES", 3)
BASE_BACKOFF_SECONDS = _env_float("BUILDIUM_BASE_BACKOFF_SECONDS", 0.5)
MAX_BACKOFF_SECONDS = _env_float("BUILDIUM_MAX_BACKOFF_SECONDS", 8.0)

# ---------------------------------------------------------------------------
# Operation registry (tool name -> Buildium OpenAPI operationId)
# ---------------------------------------------------------------------------
#: Maps MCP tool name -> OpenAPI ``operationId`` it calls. Populated as tools
#: are registered so the spec-coverage validator can verify every tool maps to
#: a real Buildium endpoint.
TOOL_OPERATIONS: dict[str, str] = {}

#: Maps MCP tool name -> classification metadata used by the security policy and
#: audit layers. Populated by :func:`register_operation` so there is a single
#: source of truth for whether a tool reads or mutates data and whether it
#: touches financially sensitive resources.
TOOL_METADATA: dict[str, dict[str, Any]] = {}

# Tool-name prefixes that identify read vs. mutating operations.
_READ_PREFIXES = ("list_", "get_")
_WRITE_PREFIXES = ("create_", "update_", "delete_")

# Substrings that mark a tool as financially sensitive (bills, bank accounts,
# general ledger, payments, and file up/download URL issuance).
_SENSITIVE_MARKERS = (
    "bill",
    "bank_account",
    "gl_",
    "payment",
    "file_download",
    "file_upload",
)


def classify_op_type(tool_name: str) -> str:
    """Classify a tool as ``"read"`` or ``"write"`` from its name.

    Defaults to ``"read"`` for server-local tools (e.g. ``health_check``) so an
    unknown tool never accidentally counts as a mutation.
    """
    if tool_name.startswith(_WRITE_PREFIXES):
        return "write"
    return "read"


def classify_sensitive(tool_name: str) -> bool:
    """Return True if a tool touches financially sensitive resources."""
    return any(marker in tool_name for marker in _SENSITIVE_MARKERS)


def register_operation(tool_name: str, operation_id: str) -> None:
    """Record the OpenAPI operation a tool maps to (for spec validation).

    Also records read/write and sensitivity classification in
    :data:`TOOL_METADATA` so the security policy and audit layers share one
    source of truth.
    """
    TOOL_OPERATIONS[tool_name] = operation_id
    TOOL_METADATA[tool_name] = {
        "op_type": classify_op_type(tool_name),
        "sensitive": classify_sensitive(tool_name),
    }


def build_model(module: str, name: str, data: dict[str, Any]) -> Any:
    """Instantiate an SDK model, falling back to the raw dict if unavailable.

    Args:
        module: SDK model module name (e.g. ``rental_unit_post_message``).
        name: Model class name (e.g. ``RentalUnitPostMessage``).
        data: Keyword arguments for the model.
    """
    try:
        mod = __import__(f"mcp_server_buildium.buildium_sdk.models.{module}", fromlist=[name])
        return getattr(mod, name)(**data)
    except ImportError:  # pragma: no cover - SDK always present in practice
        return data


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------
def _serialize(value: Any) -> Any:
    """Convert SDK model objects into JSON-serializable structures."""
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


def success(
    data: Any, *, count: int | None = None, meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a successful ``{data, count, error, meta}`` envelope.

    Args:
        data: The serialized payload.
        count: Item count. Auto-computed for lists when not provided.
        meta: Optional metadata block (pagination, timing, applied filters). The
            ``meta`` key is always present for a stable schema; it is ``None``
            when no metadata is supplied.
    """
    serialized = _serialize(data)
    if count is None and isinstance(serialized, list):
        count = len(serialized)
    return {"data": serialized, "count": count, "error": None, "meta": meta}


def failure(
    message: str,
    *,
    status: int | None = None,
    code: str | None = None,
    hint: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an error ``{data, count, error, meta}`` envelope.

    Args:
        message: Human-friendly error message.
        status: Upstream HTTP status code, if applicable.
        code: Stable machine-readable error code (e.g. ``validation_error``,
            ``api_error``, ``forbidden``, ``rate_limited``, ``internal_error``).
        hint: Optional actionable hint for resolving the error.
        meta: Optional metadata block (timing, etc.).
    """
    return {
        "data": None,
        "count": None,
        "error": {"message": message, "status": status, "code": code, "hint": hint},
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
def clamp_pagination(limit: int | None, offset: int | None) -> tuple[int, int]:
    """Clamp ``limit``/``offset`` into Buildium's accepted ranges."""
    safe_limit = DEFAULT_LIMIT if limit is None else int(limit)
    safe_limit = max(MIN_LIMIT, min(MAX_LIMIT, safe_limit))
    safe_offset = 0 if offset is None else max(0, int(offset))
    return safe_limit, safe_offset


def validate_enum(value: str | None, allowed: set[str], *, field: str) -> str | None:
    """Validate an optional enum value, raising ``ValueError`` if invalid."""
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"Invalid {field}: {value!r}. Allowed values: {sorted(allowed)}")
    return value


# ---------------------------------------------------------------------------
# Execution wrapper with retries and error mapping
# ---------------------------------------------------------------------------
def _status_of(exc: ApiException) -> int | None:
    return getattr(exc, "status", None)


async def execute(
    tool_name: str,
    call: Callable[[], Awaitable[Any]],
    *,
    count: int | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute an SDK call, returning a consistent envelope.

    Adds bounded retry/backoff on transient (429/5xx) errors, maps
    ``ApiException`` into a clean error envelope without leaking credentials,
    logs structured events, and attaches a ``meta`` block with timing and
    attempt information.

    Args:
        tool_name: Name of the calling tool (for logging).
        call: Zero-arg coroutine factory performing the SDK call.
        count: Optional explicit item count for the envelope.
        meta: Optional metadata (e.g. pagination) merged into the envelope's
            ``meta`` block alongside timing information.
    """
    started = time.monotonic()

    def _meta(attempt: int) -> dict[str, Any]:
        block: dict[str, Any] = {
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "attempts": attempt,
        }
        if meta:
            block.update(meta)
        return block

    attempt = 0
    while True:
        attempt += 1
        try:
            result = await call()
            log_event(logger, logging.INFO, "tool.success", tool=tool_name, attempt=attempt)
            return success(result, count=count, meta=_meta(attempt))
        except ApiException as exc:
            status = _status_of(exc)
            reason = getattr(exc, "reason", None) or str(exc)
            if status in RETRYABLE_STATUS and attempt <= MAX_RETRIES:
                backoff = min(
                    MAX_BACKOFF_SECONDS,
                    BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)),
                )
                backoff += random.uniform(0, BASE_BACKOFF_SECONDS)
                log_event(
                    logger,
                    logging.WARNING,
                    "tool.retry",
                    tool=tool_name,
                    attempt=attempt,
                    status=status,
                    backoff=round(backoff, 3),
                )
                await asyncio.sleep(backoff)
                continue
            log_event(
                logger,
                logging.ERROR,
                "tool.api_error",
                tool=tool_name,
                status=status,
                reason=reason,
            )
            return failure(
                f"Buildium API error: {reason}",
                status=status,
                code="api_error",
                meta=_meta(attempt),
            )
        except ValueError as exc:
            # Input validation errors (e.g. bad enum) are not retryable.
            log_event(
                logger, logging.WARNING, "tool.validation_error", tool=tool_name, reason=str(exc)
            )
            return failure(str(exc), code="validation_error", meta=_meta(attempt))
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors cleanly
            log_event(
                logger,
                logging.ERROR,
                "tool.unexpected_error",
                tool=tool_name,
                error=type(exc).__name__,
            )
            return failure(
                f"Unexpected error calling Buildium: {type(exc).__name__}",
                code="internal_error",
                meta=_meta(attempt),
            )
