"""Async engine and session lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .settings import DatabaseBackend, DatabaseSettings


@dataclass
class DatabaseRuntime:
    """One engine and session factory shared by application repositories."""

    settings: DatabaseSettings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def close(self) -> None:
        await self.engine.dispose()


def create_database_runtime(settings: DatabaseSettings | None = None) -> DatabaseRuntime:
    resolved = settings or DatabaseSettings.from_env()
    connect_args: dict[str, object] = {}
    if resolved.backend is DatabaseBackend.SQLITE:
        connect_args["timeout"] = 5
    engine = create_async_engine(
        resolved.url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if resolved.backend is DatabaseBackend.SQLITE:
        _enable_sqlite_foreign_keys(engine)
    return DatabaseRuntime(
        settings=resolved,
        engine=engine,
        sessions=async_sessionmaker(engine, expire_on_commit=False),
    )


def _enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
