"""Guarded FastMCP registration wrapper.

:class:`GuardedMCP` wraps a real ``FastMCP`` instance and intercepts tool
registration so the security policy, rate limiter, and audit trail are applied
centrally:

* **Registration time**: tools forbidden by the policy are never registered, so
  they are not even advertised to MCP clients (the strongest guardrail).
* **Runtime**: each registered tool is wrapped in a *signature-preserving*
  coroutine that re-checks the policy (defense-in-depth), enforces the rate
  limit, and emits an audit event capturing sanitized arguments and the outcome.

The wrapper copies ``__signature__`` and ``__annotations__`` from the original
function so FastMCP still derives the correct parameter schema.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any

from .. import audit as audit_mod
from ..logging_config import get_logger, log_event
from ..tools import _common as c
from .policy import RateLimiter, ToolPolicy

logger = get_logger("mcp_server_buildium.security")


def _classify(tool_name: str) -> tuple[str, bool]:
    meta = c.TOOL_METADATA.get(tool_name)
    if meta is not None:
        return meta["op_type"], bool(meta["sensitive"])
    return c.classify_op_type(tool_name), c.classify_sensitive(tool_name)


class GuardedMCP:
    """A policy-enforcing proxy around a ``FastMCP`` server."""

    def __init__(
        self,
        mcp: Any,
        policy: ToolPolicy,
        recorder: audit_mod.AuditRecorder,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._mcp = mcp
        self._policy = policy
        self._recorder = recorder
        self._limiter = limiter or RateLimiter(0)
        self.skipped: list[str] = []

    def __getattr__(self, item: str) -> Any:
        # Delegate everything we don't override to the wrapped server.
        return getattr(self._mcp, item)

    def tool(self, *dargs: Any, **dkwargs: Any) -> Callable[[Callable], Any]:
        """Drop-in replacement for ``FastMCP.tool`` that applies the policy."""
        real_decorator = self._mcp.tool(*dargs, **dkwargs)

        def decorator(fn: Callable) -> Any:
            name = getattr(fn, "__name__", "")
            decision = self._policy.decide(name)
            if not decision.allowed:
                self.skipped.append(name)
                log_event(
                    logger,
                    logging.INFO,
                    "tool.disabled",
                    tool=name,
                    reason=decision.reason,
                )
                # Return the original function unregistered so it is not exposed.
                return fn
            guarded = self._guard(fn, name)
            return real_decorator(guarded)

        return decorator

    def _guard(self, fn: Callable, name: str) -> Callable:
        policy = self._policy
        recorder = self._recorder
        limiter = self._limiter
        op_type, sensitive = _classify(name)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Defense-in-depth: re-check the policy at call time.
            decision = policy.decide(name)
            if not decision.allowed:
                recorder.record(
                    tool=name,
                    op_type=op_type,
                    sensitive=sensitive,
                    outcome="denied",
                    code="forbidden",
                    reason=decision.reason,
                    args=kwargs,
                )
                return c.failure(
                    f"Tool '{name}' is not permitted: {decision.reason}",
                    code="forbidden",
                    hint="Adjust BUILDIUM_ROLE or the allow/deny guardrails to enable this tool.",
                )

            if not limiter.allow():
                recorder.record(
                    tool=name,
                    op_type=op_type,
                    sensitive=sensitive,
                    outcome="rate_limited",
                    code="rate_limited",
                    reason=f"exceeded {limiter.per_minute}/min",
                    args=kwargs,
                )
                return c.failure(
                    "Rate limit exceeded. Please retry shortly.",
                    code="rate_limited",
                    hint="Increase BUILDIUM_RATE_LIMIT_PER_MINUTE or slow the request rate.",
                )

            started = time.monotonic()
            result = await fn(*args, **kwargs)
            duration_ms = round((time.monotonic() - started) * 1000, 2)

            outcome = "success"
            status: int | None = None
            code: int | str | None = None
            attempts: int | None = None
            if isinstance(result, dict):
                error = result.get("error")
                if error:
                    outcome = "error"
                    status = error.get("status")
                    code = error.get("code")
                meta = result.get("meta")
                if isinstance(meta, dict):
                    attempts = meta.get("attempts")

            recorder.record(
                tool=name,
                op_type=op_type,
                sensitive=sensitive,
                outcome=outcome,
                status=status,
                code=code if isinstance(code, str) else None,
                attempts=attempts,
                duration_ms=duration_ms,
                args=kwargs,
            )
            return result

        # Preserve the schema-bearing signature/annotations for FastMCP.
        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        wrapper.__annotations__ = dict(getattr(fn, "__annotations__", {}))
        return wrapper
