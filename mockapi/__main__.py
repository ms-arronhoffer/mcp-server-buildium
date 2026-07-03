"""Run the mock Buildium API with uvicorn.

Seeds the database on startup (unless ``MOCKAPI_SKIP_SEED`` is set) so the API
is immediately useful, then serves the FastAPI app.

Usage::

    python -m mockapi              # seed + serve on 0.0.0.0:8080
    MOCKAPI_SKIP_SEED=1 python -m mockapi
"""

from __future__ import annotations

import os

import uvicorn

from .app import create_app
from .db import SessionLocal, reset_db
from .seed import seed_all


def _seed_if_needed() -> None:
    if os.environ.get("MOCKAPI_SKIP_SEED"):
        return
    reset_db()
    session = SessionLocal()
    try:
        counts = seed_all(session)
    finally:
        session.close()
    total = sum(counts.values())
    print(f"[mockapi] Seeded {total} records across {len(counts)} resources.")


def main() -> None:
    _seed_if_needed()
    app = create_app()
    host = os.environ.get("MOCKAPI_HOST", "0.0.0.0")  # noqa: S104 - container bind
    port = int(os.environ.get("MOCKAPI_PORT", "8080"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
