"""Security primitives for the Buildium MCP server.

This package provides a configurable role/guardrail policy layer that decides
which tools an MCP client may invoke, plus a lightweight rate limiter. It is
consumed by the server at registration time (to avoid advertising forbidden
tools) and at runtime (defense-in-depth inside the tool wrapper).
"""

from .policy import PolicyDecision, RateLimiter, ToolPolicy

__all__ = ["PolicyDecision", "RateLimiter", "ToolPolicy"]
