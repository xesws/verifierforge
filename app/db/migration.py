"""Alembic entry points, including automatic local SQLite initialization."""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, inspect

from .settings import DatabaseBackend, DatabaseSettings


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = REPOSITORY_ROOT / "alembic.ini"
_MIGRATION_LOCK = threading.Lock()
_MANAGED_TABLES = {
    "traffic_requests",
    "clusters",
    "routing_state",
    "guardian_scores",
    "live_pass_rate",
    "jobs",
    "agent_decisions",
    "provider_credentials",
    "approvals",
    "provision_events",
    "serving_endpoints",
    "serving_events",
}


def run_migrations(
    settings: DatabaseSettings | None = None, *, revision: str = "head"
) -> None:
    """Synchronously upgrade one configured database without rendering its URL."""

    resolved = settings or DatabaseSettings.from_env()
    _reject_unmanaged_legacy_sqlite(resolved)
    config = Config(str(ALEMBIC_CONFIG))
    config.attributes["database_url"] = resolved.url
    with _MIGRATION_LOCK:
        command.upgrade(config, revision)


def downgrade_database(
    settings: DatabaseSettings | None = None, *, revision: str = "base"
) -> None:
    """Synchronously downgrade a database, primarily for migration verification."""

    resolved = settings or DatabaseSettings.from_env()
    config = Config(str(ALEMBIC_CONFIG))
    config.attributes["database_url"] = resolved.url
    with _MIGRATION_LOCK:
        command.downgrade(config, revision)


async def migrate_sqlite(settings: DatabaseSettings | None = None) -> None:
    """Ensure the explicitly selected SQLite database is at Alembic head."""

    resolved = settings or DatabaseSettings.from_env()
    if resolved.backend is not DatabaseBackend.SQLITE:
        raise ValueError("migrate_sqlite requires the sqlite backend")
    database = resolved.url.database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(run_migrations, resolved)


def _reject_unmanaged_legacy_sqlite(settings: DatabaseSettings) -> None:
    """Refuse an in-place upgrade that could collide with legacy table names."""

    if settings.backend is not DatabaseBackend.SQLITE:
        return
    database = settings.url.database
    if not database or database == ":memory:" or not Path(database).is_file():
        return
    engine = create_engine(URL.create("sqlite", database=database))
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    if "alembic_version" not in tables and tables & _MANAGED_TABLES:
        raise RuntimeError(
            "legacy SQLite requires the idempotent DB-2 importer before DB-1 writes"
        )
