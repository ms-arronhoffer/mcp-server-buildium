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
import re
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


try:  # pragma: no cover - pydantic ships with the generated SDK
    from pydantic import ValidationError
except Exception:  # pragma: no cover - only hit if pydantic is unavailable

    class ValidationError(ValueError):  # type: ignore[no-redef]
        """Fallback so ``except ValidationError`` is always valid."""


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


def _humanize_tool_name(tool_name: str | None) -> str:
    """Turn a tool name like ``create_rental`` into ``create rental`` for messages."""
    if not tool_name:
        return ""
    return tool_name.replace("_", " ").strip()


def _describe_validation_error(exc: ValidationError, resource: str | None) -> str:
    """Turn a pydantic ``ValidationError`` into a friendly, user-facing prompt.

    The message lists the missing required fields and any invalid fields so the
    assistant can ask the user for exactly the information Buildium needs to
    create the object, rather than surfacing a raw pydantic traceback.
    """
    missing: list[str] = []
    invalid: list[str] = []
    try:
        errors = exc.errors()
    except Exception:  # pragma: no cover - defensive; fall back to str(exc)
        errors = []
    for err in errors:
        loc = ".".join(str(part) for part in err.get("loc", ())) or "(root)"
        if err.get("type") == "missing":
            missing.append(loc)
        else:
            msg = err.get("msg") or "invalid value"
            invalid.append(f"{loc} ({msg})")
    details: list[str] = []
    if missing:
        details.append("missing required field(s): " + ", ".join(missing))
    if invalid:
        details.append("invalid field(s): " + "; ".join(invalid))
    detail = "; ".join(details) if details else str(exc)
    action = _humanize_tool_name(resource)
    prefix = f"Cannot {action}" if action else "Cannot complete the request"
    return (
        f"{prefix}: {detail}. "
        "Please provide the missing or corrected information and try again."
    )


def build_model(module: str, name: str, data: dict[str, Any], *, resource: str | None = None) -> Any:
    """Instantiate an SDK model, falling back to the raw dict if unavailable.

    Args:
        module: SDK model module name (e.g. ``rental_unit_post_message``).
        name: Model class name (e.g. ``RentalUnitPostMessage``).
        data: Keyword arguments for the model.
        resource: Optional tool name (e.g. ``create_rental``) used to phrase a
            friendly error if the supplied ``data`` fails schema validation.

    Raises:
        ValueError: If ``data`` is missing required fields or contains invalid
            values, with a message that prompts the caller for the missing
            information. :func:`execute` maps this to a ``validation_error``
            envelope so the assistant can ask the user for more details.
    """
    try:
        mod = __import__(f"mcp_server_buildium.buildium_sdk.models.{module}", fromlist=[name])
    except ImportError:  # pragma: no cover - SDK always present in practice
        return data
    model_cls = getattr(mod, name)
    try:
        return model_cls(**data)
    except ValidationError as exc:
        raise ValueError(_describe_validation_error(exc, resource)) from exc


# ---------------------------------------------------------------------------
# Partial-update helpers
# ---------------------------------------------------------------------------
# Generated SDK models use PascalCase JSON aliases (e.g. ``FirstName``) but also
# accept their snake_case field names (``first_name``) because ``populate_by_name``
# is enabled. To merge a caller-supplied partial patch onto an existing record we
# first normalize every key to snake_case so the two structures line up regardless
# of which casing the LLM emitted.
_SNAKE_STEP1 = re.compile(r"(.)([A-Z][a-z]+)")
_SNAKE_STEP2 = re.compile(r"([a-z0-9])([A-Z])")


def to_snake_key(key: str) -> str:
    """Convert a PascalCase/camelCase key to snake_case (snake_case is unchanged)."""
    interim = _SNAKE_STEP1.sub(r"\1_\2", key)
    return _SNAKE_STEP2.sub(r"\1_\2", interim).lower()


def normalize_keys(obj: Any) -> Any:
    """Recursively convert all mapping keys in ``obj`` to snake_case.

    Lists and scalars are returned structurally unchanged (their nested mappings
    are still normalized). This lets partial patches use either the PascalCase
    JSON aliases or the snake_case field names interchangeably.
    """
    if isinstance(obj, dict):
        return {to_snake_key(str(k)): normalize_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_keys(v) for v in obj]
    return obj


def deep_merge(base: Any, patch: Any) -> Any:
    """Recursively merge ``patch`` onto ``base``, returning a new structure.

    Nested dicts are merged key-by-key so a partial patch only overrides the
    fields it specifies; any other value (including lists and scalars) in
    ``patch`` replaces the corresponding value in ``base``.
    """
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for key, value in patch.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return patch


# Maps a Buildium phone-number ``Type`` (as returned by GET endpoints, which
# expose phone numbers as a list of ``{Number, Type}`` entries) onto the keyed
# ``PhoneNumbers`` object shape (``Home``/``Work``/``Mobile``/``Fax``) that the
# create/update ``PhoneNumbers`` message expects. Unmapped types are dropped
# rather than guessed at, so we never place a number under the wrong label.
_PHONE_TYPE_TO_KEY = {
    "home": "home",
    "office": "work",
    "work": "work",
    "cell": "mobile",
    "mobile": "mobile",
    "fax": "fax",
}


def phone_list_to_object(phones: Any) -> dict[str, str]:
    """Convert a GET-style phone-number list into the PUT ``PhoneNumbers`` object.

    GET endpoints return phone numbers as a list of ``{Number, Type}`` entries,
    but the ``PhoneNumbers`` PUT/POST message expects a keyed object
    (``Home``/``Work``/``Mobile``/``Fax``). Keys are normalized to snake_case so
    the result lines up with a snake_case ``get_to_put_base`` record.
    """
    result: dict[str, str] = {}
    if not isinstance(phones, list):
        return result
    for entry in phones:
        if not isinstance(entry, dict):
            continue
        # Entries may use either the PascalCase JSON aliases (``Number``/``Type``)
        # or the snake_case field names depending on how the record was fetched.
        lowered = {to_snake_key(str(k)): v for k, v in entry.items()}
        number = lowered.get("number")
        key = _PHONE_TYPE_TO_KEY.get(str(lowered.get("type") or "").lower())
        if number and key and key not in result:
            result[key] = number
    return result


def get_to_put_base(current: Any, *, reshape_phones: bool = False) -> dict[str, Any]:
    """Build a PUT-shaped dict from a fetched record, preserving alias keys.

    Generated PUT models frequently require fields (e.g. ``FirstName``,
    ``Address``) that a naive partial update would omit, so we seed those from
    the existing record. The record's own key spelling (the PascalCase JSON
    aliases emitted by ``to_dict``) is preserved so fields whose SDK attribute
    name differs from ``to_snake_key(alias)`` (e.g. ``var_date`` / ``Date``) are
    not dropped. Read-only fields (``id``, timestamps, ...) are carried along but
    ignored by the target model. When ``reshape_phones`` is set, the GET-style
    phone-number list is converted into the keyed object form the PUT message
    expects (see :func:`phone_list_to_object`).
    """
    raw = current.to_dict() if hasattr(current, "to_dict") else dict(current or {})
    if not isinstance(raw, dict):
        return {}
    base = dict(raw)
    if reshape_phones:
        for key in list(base):
            if to_snake_key(str(key)) == "phone_numbers":
                phones = phone_list_to_object(base.pop(key))
                if phones:
                    base["PhoneNumbers"] = phones
                break
    return base


def _merge_normalized(base: Any, patch: Any) -> Any:
    """Deep-merge ``patch`` onto ``base``, matching keys case-insensitively.

    Keys are aligned by their snake_case form so a patch may use either the
    PascalCase JSON aliases or the snake_case field names, but the ``base`` key
    spelling is preserved on a match so valid aliases from a fetched record are
    never lost. Patch keys with no counterpart in ``base`` are added as-is.
    """
    if isinstance(base, dict) and isinstance(patch, dict):
        norm_to_key = {to_snake_key(str(k)): k for k in base}
        merged = dict(base)
        for pkey, pvalue in patch.items():
            bkey = norm_to_key.get(to_snake_key(str(pkey)))
            if bkey is None:
                merged[pkey] = pvalue
            elif isinstance(merged[bkey], dict) and isinstance(pvalue, dict):
                merged[bkey] = _merge_normalized(merged[bkey], pvalue)
            else:
                merged[bkey] = pvalue
        return merged
    return patch


def merge_update(current: Any, patch: Any, *, reshape_phones: bool = False) -> dict[str, Any]:
    """Merge a caller-supplied partial ``patch`` onto a fetched record.

    The fetched ``current`` record is reshaped into a PUT base (see
    :func:`get_to_put_base`) and ``patch`` is deep-merged on top, so single-field
    edits succeed without resupplying the full strict schema. Patch keys may use
    either the PascalCase JSON aliases or the snake_case field names
    interchangeably (see :func:`_merge_normalized`).
    """
    base = get_to_put_base(current, reshape_phones=reshape_phones)
    return _merge_normalized(base, patch)


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


async def create(
    tool_name: str,
    module: str,
    name: str,
    data: dict[str, Any],
    call: Callable[[Any], Awaitable[Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a Buildium object from caller-supplied ``data``.

    Builds the strict SDK POST model from ``data`` *inside* :func:`execute` so a
    schema-validation failure (missing required fields, invalid enum values, ...)
    is turned into a friendly ``validation_error`` envelope that lists exactly
    what is missing. The assistant can then ask the user for the additional
    information instead of surfacing a raw error.

    Args:
        tool_name: Name of the calling tool (e.g. ``create_rental``); also used
            to phrase the validation message.
        module: SDK POST model module name (e.g. ``rental_property_post_message``).
        name: SDK POST model class name (e.g. ``RentalPropertyPostMessage``).
        data: Caller-supplied fields for the new object.
        call: Coroutine factory taking the built model and performing the SDK
            create request.
        meta: Optional metadata merged into the response envelope.
    """

    async def _run() -> Any:
        message = build_model(module, name, data, resource=tool_name)
        return await call(message)

    return await execute(tool_name, _run, meta=meta)
