# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# MCP server image (mcp-server-buildium)
# Multi-stage build using uv for fast, reproducible installs.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first (cached layer) using only the lockfile + metadata.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the project source and install the package itself.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as an unprivileged user.
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
COPY --chown=app:app pyproject.toml README.md ./

# Directory for persistent state (e.g. the admin-UI LLM config store and the
# optional file audit sink). Owned by `app` so that a fresh named/bind volume
# mounted here inherits app ownership and stays writable by the non-root user.
RUN mkdir -p /app/data && chown app:app /app/data

USER app

# STDIO transport MCP server.
ENTRYPOINT ["mcp-server-buildium"]
