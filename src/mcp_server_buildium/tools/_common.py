"""Shared helpers for Buildium MCP tools.

Provides a consistent response envelope, robust error handling, retry/backoff
for transient failures, pagination clamping, and an operation registry that maps
each MCP tool to the Buildium OpenAPI ``operationId`` it exercises. The registry
is consumed by the spec-coverage validator (see ``scripts/generate_tool_coverage.py``).
"""

from __future__ import annotations

import asyncio
import logging
import random
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

# Pagination guardrails (Buildium caps page size at 1000).
MAX_LIMIT = 1000
MIN_LIMIT = 1
DEFAULT_LIMIT = 100

# Retry configuration for transient upstream failures.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 8.0

# Per-request timeout (seconds) passed to the SDK.
REQUEST_TIMEOUT_SECONDS = 30.0

# ---------------------------------------------------------------------------
# Operation registry (tool name -> Buildium OpenAPI operationId)
# ---------------------------------------------------------------------------
#: Maps MCP tool name -> OpenAPI ``operationId`` it calls. Populated as tools
#: are registered so the spec-coverage validator can verify every tool maps to
#: a real Buildium endpoint.
TOOL_OPERATIONS: dict[str, str] = {}


def register_operation(tool_name: str, operation_id: str) -> None:
    """Record the OpenAPI operation a tool maps to (for spec validation)."""
    TOOL_OPERATIONS[tool_name] = operation_id


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


def success(data: Any, *, count: int | None = None) -> dict[str, Any]:
    """Build a successful ``{data, count, error}`` envelope.

    Args:
        data: The serialized payload.
        count: Item count. Auto-computed for lists when not provided.
    """
    serialized = _serialize(data)
    if count is None and isinstance(serialized, list):
        count = len(serialized)
    return {"data": serialized, "count": count, "error": None}


def failure(message: str, *, status: int | None = None, code: str | None = None) -> dict[str, Any]:
    """Build an error ``{data, count, error}`` envelope."""
    return {
        "data": None,
        "count": None,
        "error": {"message": message, "status": status, "code": code},
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
) -> dict[str, Any]:
    """Execute an SDK call, returning a consistent envelope.

    Adds bounded ret/backoff on transient (429/5xx) errors, maps
    ``ApiException`` into a clean error envelope without leaking credentials,
    and logs structured events.

    Args:
        tool_name: Name of the calling tool (for logging).
        call: Zero-arg coroutine factory performing the SDK call.
        count: Optional explicit item count for the envelope.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await call()
            log_event(logger, logging.INFO, "tool.success", tool=tool_name, attempt=attempt)
            return success(result, count=count)
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
            )
        except ValueError as exc:
            # Input validation errors (e.g. bad enum) are not retryable.
            log_event(
                logger, logging.WARNING, "tool.validation_error", tool=tool_name, reason=str(exc)
            )
            return failure(str(exc), code="validation_error")
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
            )
