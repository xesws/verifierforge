from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import URL, create_engine, inspect, text

from app.db.engine import create_database_runtime
from app.db.migration import downgrade_database, migrate_sqlite, run_migrations
from app.db.settings import DatabaseSettings


EXPECTED_TABLES = {
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
}


async def _tables(settings: DatabaseSettings) -> set[str]:
    runtime = create_database_runtime(settings)
    try:
        async with runtime.engine.connect() as connection:
            return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
    finally:
        await runtime.close()


async def _columns(settings: DatabaseSettings) -> dict[str, set[str]]:
    runtime = create_database_runtime(settings)
    try:
        async with runtime.engine.connect() as connection:
            def inspect_columns(sync):
                inspector = inspect(sync)
                return {
                    table: {column["name"] for column in inspector.get_columns(table)}
                    for table in EXPECTED_TABLES
                }

            return await connection.run_sync(inspect_columns)
    finally:
        await runtime.close()


async def test_alembic_upgrade_downgrade_upgrade_cycle(tmp_path: Path) -> None:
    settings = DatabaseSettings.sqlite(tmp_path / "nested" / "db.sqlite3")

    await migrate_sqlite(settings)
    assert EXPECTED_TABLES <= await _tables(settings)
    columns = await _columns(settings)
    assert columns["traffic_requests"] == {
        "id", "ts", "prompt_hash", "model", "tokens_in", "tokens_out",
        "latency_ms", "cost_usd", "route_taken",
    }
    assert columns["agent_decisions"] >= {
        "id", "cluster_id", "decision", "trace_s3_key", "summary_json",
    }
    assert columns["provision_events"] >= {
        "id", "approval_id", "action", "status", "occurred_at",
    }

    await asyncio.to_thread(downgrade_database, settings)
    assert not (EXPECTED_TABLES & await _tables(settings))

    await asyncio.to_thread(run_migrations, settings)
    assert EXPECTED_TABLES <= await _tables(settings)


async def test_sqlite_migration_helper_rejects_postgres_without_connecting() -> None:
    settings = DatabaseSettings.from_env(
        {
            "VF_DB_BACKEND": "postgres",
            "SUPABASE_DB_URL": URL.create(
                "postgresql",
                username="vf",
                password="fixture",
                host="db.example.test",
                database="verifierforge",
            ).render_as_string(hide_password=False),
        }
    )

    try:
        await migrate_sqlite(settings)
    except ValueError as error:
        assert str(error) == "migrate_sqlite requires the sqlite backend"
    else:
        raise AssertionError("postgres settings must not enter the SQLite migration helper")


def test_unmanaged_legacy_sqlite_is_rejected_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE agent_decisions (id TEXT PRIMARY KEY)"))
    engine.dispose()
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="DB-2 importer"):
        run_migrations(DatabaseSettings.sqlite(path))

    assert path.read_bytes() == before
