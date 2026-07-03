# Buildium OpenAPI specification

The single source of truth for the Buildium API specification lives at the
repository root: [`../openapi.json`](../openapi.json).

The previous duplicate (`specs/buildium-openapi.json`) has been removed to avoid
drift. Tooling that consumes the spec — the SDK generator (`make generate-sdk`),
the tool-coverage validator (`scripts/generate_tool_coverage.py`), and the
spec-coverage tests (`tests/test_tool_spec_coverage.py`) — all reference the
root `openapi.json`.
