"""Database engine, session, and schema for the mock Buildium API."""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import JSON, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# SQLite file path (overridable for tests/containers). ``:memory:`` is avoided so
# the seeded data is visible across connections/processes.
DATABASE_URL = os.environ.get("MOCKAPI_DATABASE_URL", "sqlite:///./mockapi.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Declarative base for mock API tables."""


class Entity(Base):
    """A single Buildium resource stored as a spec-shaped JSON document.

    Using a generic document store keeps the mock spec-accurate (responses are
    authored to match Buildium's PascalCase message schemas) while still being a
    real relational database with indexed columns for the fields tools filter on.
    """

    __tablename__ = "entities"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Resource collection, e.g. "rentals", "units", "leases".
    resource: Mapped[str] = mapped_column(String, index=True)
    # Business identifier exposed as ``Id`` in responses.
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    # Optional parent linkage for nested collections (e.g. a lease's transactions).
    parent_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Generic, indexed filter columns commonly used by Buildium list endpoints.
    property_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    unit_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    association_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    vendor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # The full, spec-shaped response document (PascalCase keys).
    doc: Mapped[dict] = mapped_column(JSON)


def init_db() -> None:
    """Create all tables."""
    Base.metadata.create_all(bind=engine)


def reset_db() -> None:
    """Drop and recreate all tables (used before seeding)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
