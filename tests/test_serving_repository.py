from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db.engine import create_database_runtime
from app.db.migration import migrate_sqlite
from app.db.records import ServingEndpointRecord
from app.db.repositories import create_repositories
from app.db.settings import DatabaseSettings


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def serving_store(tmp_path: Path):
    settings = DatabaseSettings.sqlite(tmp_path / "serving.sqlite3")
    await migrate_sqlite(settings)
    runtime = create_database_runtime(settings)
    try:
        yield create_repositories(runtime).serving_endpoints
    finally:
        await runtime.close()


async def test_concurrent_same_model_wake_is_idempotent(serving_store) -> None:
    first, second = await asyncio.gather(
        serving_store.reserve("vf-demo", "session-a", NOW),
        serving_store.reserve("vf-demo", "session-b", NOW),
    )
    assert sorted([first[1], second[1]]) == [False, True]
    assert first[0].session_id == second[0].session_id


async def test_compare_and_set_rejects_stale_worker(serving_store) -> None:
    await serving_store.reserve("vf-demo", "session-a", NOW)
    loading = ServingEndpointRecord(
        model_id="vf-demo",
        session_id="session-a",
        state="loading",
        updated_at=NOW,
    )
    await serving_store.put(loading, expected_state="provisioning")
    with pytest.raises(ValueError, match="expected provisioning, got loading"):
        await serving_store.put(loading, expected_state="provisioning")


async def test_ready_requires_url(serving_store) -> None:
    with pytest.raises(ValueError, match="requires url"):
        await serving_store.put(
            ServingEndpointRecord(
                model_id="vf-demo",
                session_id="session-a",
                state="ready",
                updated_at=NOW,
            )
        )
