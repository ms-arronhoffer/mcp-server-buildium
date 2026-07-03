"""Spec-accurate mock of the Buildium API for offline testing.

A small FastAPI + SQLAlchemy (SQLite) application that mimics the Buildium v1
endpoints exercised by the MCP server's tools. Responses use Buildium's
PascalCase message shapes so the generated SDK deserializes them exactly as it
would against the real API. Seed data is referentially consistent so tools
return meaningful, related results.
"""

__all__ = ["create_app"]

from .app import create_app
