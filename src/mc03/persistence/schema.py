"""Schema helpers shared by runtime initialization and Alembic migrations."""

from __future__ import annotations

from sqlalchemy import Connection

from mc03.persistence.immutability import install_database_guards
from mc03.persistence.models import Base


def create_foundation_schema(connection: Connection) -> None:
    """Create the current foundation tables and append-only database guards."""
    Base.metadata.create_all(connection)
    install_database_guards(connection)


def drop_foundation_schema(connection: Connection) -> None:
    """Drop the foundation schema for a development/test migration downgrade."""
    Base.metadata.drop_all(connection)


__all__ = ["create_foundation_schema", "drop_foundation_schema"]
