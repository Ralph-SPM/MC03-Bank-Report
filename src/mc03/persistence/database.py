"""SQLite engine, WAL initialization, and transaction boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.orm import Session, sessionmaker

from mc03.persistence.immutability import install_database_guards
from mc03.persistence.models import Base
from mc03.settings import RuntimeSettings
from mc03.storage import (
    ProtectedStoragePaths,
    build_protected_storage_paths,
    prepare_protected_storage,
)

SessionFactory = sessionmaker[Session]


def _as_storage_paths(
    storage: ProtectedStoragePaths | RuntimeSettings | Path | str,
) -> ProtectedStoragePaths:
    if isinstance(storage, ProtectedStoragePaths):
        paths = storage
    elif isinstance(storage, Path | str):
        database = Path(storage).expanduser().resolve(strict=False)
        paths = build_protected_storage_paths(database.parent, database=database)
    else:
        paths = storage.protected_storage
    prepare_protected_storage(paths)
    return paths


def _configure_sqlite_connection(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
    finally:
        cursor.close()


def create_database_engine(
    storage: ProtectedStoragePaths | RuntimeSettings | Path | str,
    *,
    echo: bool = False,
) -> Engine:
    """Create a protected SQLite engine with foreign keys and WAL on every connection."""
    paths = _as_storage_paths(storage)
    database_url = f"sqlite:///{paths.database.as_posix()}"
    engine = sqlalchemy_create_engine(
        database_url,
        echo=echo,
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


def initialize_database(engine: Engine) -> None:
    """Create the foundation schema, guards, and verify required SQLite modes."""
    if engine.dialect.name != "sqlite":
        raise ValueError("MC03 persistence requires SQLite")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        install_database_guards(connection)
    with engine.connect() as connection:
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        journal_mode = str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()).lower()
    if foreign_keys != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
    if journal_mode != "wal":
        raise RuntimeError(f"SQLite WAL could not be enabled; current mode is {journal_mode}")


def create_session_factory(engine: Engine) -> SessionFactory:
    """Create non-expiring sessions for one service transaction boundary."""
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Commit one transaction or roll it back completely on failure."""
    session = create_session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "SessionFactory",
    "create_database_engine",
    "create_session_factory",
    "initialize_database",
    "session_scope",
]
