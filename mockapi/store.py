"""CRUD + filtering helpers over the generic :class:`Entity` store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Entity

# Buildium caps page size at 1000; list endpoints default to a smaller page.
MAX_LIMIT = 1000
DEFAULT_LIMIT = 50


def _clamp(limit: int | None, offset: int | None) -> tuple[int, int]:
    safe_limit = DEFAULT_LIMIT if limit is None else max(1, min(MAX_LIMIT, int(limit)))
    safe_offset = 0 if offset is None else max(0, int(offset))
    return safe_limit, safe_offset


def list_docs(
    session: Session,
    resource: str,
    *,
    limit: int | None = None,
    offset: int | None = None,
    parent_type: str | None = None,
    parent_id: int | None = None,
    property_ids: list[int] | None = None,
    unit_ids: list[int] | None = None,
    association_ids: list[int] | None = None,
    vendor_id: int | None = None,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return spec-shaped documents for a resource, filtered and paginated."""
    stmt = select(Entity).where(Entity.resource == resource)
    if parent_type is not None:
        stmt = stmt.where(Entity.parent_type == parent_type)
    if parent_id is not None:
        stmt = stmt.where(Entity.parent_id == parent_id)
    if property_ids:
        stmt = stmt.where(Entity.property_id.in_(property_ids))
    if unit_ids:
        stmt = stmt.where(Entity.unit_id.in_(unit_ids))
    if association_ids:
        stmt = stmt.where(Entity.association_id.in_(association_ids))
    if vendor_id is not None:
        stmt = stmt.where(Entity.vendor_id == vendor_id)
    if statuses:
        stmt = stmt.where(Entity.status.in_(statuses))

    stmt = stmt.order_by(Entity.entity_id)
    limit, offset = _clamp(limit, offset)
    stmt = stmt.offset(offset).limit(limit)
    return [row.doc for row in session.scalars(stmt).all()]


def get_doc(
    session: Session,
    resource: str,
    entity_id: int,
    *,
    parent_type: str | None = None,
    parent_id: int | None = None,
) -> dict[str, Any] | None:
    """Return a single document by id (optionally scoped to a parent)."""
    stmt = select(Entity).where(Entity.resource == resource, Entity.entity_id == entity_id)
    if parent_type is not None:
        stmt = stmt.where(Entity.parent_type == parent_type)
    if parent_id is not None:
        stmt = stmt.where(Entity.parent_id == parent_id)
    row = session.scalars(stmt).first()
    return row.doc if row else None


def next_id(session: Session, resource: str) -> int:
    """Return the next business id for a resource (max + 1)."""
    stmt = select(Entity.entity_id).where(Entity.resource == resource)
    ids = list(session.scalars(stmt).all())
    return (max(ids) + 1) if ids else 1


def create_doc(
    session: Session,
    resource: str,
    doc: dict[str, Any],
    *,
    entity_id: int | None = None,
    parent_type: str | None = None,
    parent_id: int | None = None,
    property_id: int | None = None,
    unit_id: int | None = None,
    association_id: int | None = None,
    vendor_id: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Insert a new document, assigning an id if not provided."""
    if entity_id is None:
        entity_id = next_id(session, resource)
    doc = {**doc, "Id": entity_id}
    entity = Entity(
        resource=resource,
        entity_id=entity_id,
        parent_type=parent_type,
        parent_id=parent_id,
        property_id=property_id,
        unit_id=unit_id,
        association_id=association_id,
        vendor_id=vendor_id,
        status=status,
        doc=doc,
    )
    session.add(entity)
    session.commit()
    return doc


def update_doc(
    session: Session,
    resource: str,
    entity_id: int,
    changes: dict[str, Any],
    *,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Merge ``changes`` into an existing document; return the updated doc."""
    stmt = select(Entity).where(Entity.resource == resource, Entity.entity_id == entity_id)
    entity = session.scalars(stmt).first()
    if entity is None:
        return None
    merged = {**entity.doc, **changes, "Id": entity_id}
    entity.doc = merged
    if status is not None:
        entity.status = status
    session.commit()
    return merged


def count(session: Session, resource: str) -> int:
    """Return the number of records for a resource."""
    stmt = select(Entity).where(Entity.resource == resource)
    return len(list(session.scalars(stmt).all()))
