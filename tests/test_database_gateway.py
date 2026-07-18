from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.db.gateway import RepositoryGateway
from app.db.records import TrafficRequestRecord
from app.db.settings import DatabaseSettings


def test_gateway_reuses_one_async_runtime_across_sync_calls(tmp_path: Path) -> None:
    gateway = RepositoryGateway(DatabaseSettings.sqlite(tmp_path / "gateway.sqlite3"))
    record = TrafficRequestRecord(
        ts=datetime.now(timezone.utc),
        prompt_hash="a" * 64,
        model="fixture",
        tokens_in=2,
        tokens_out=1,
        latency_ms=4.0,
        cost_usd=0.0,
        route_taken="default",
    )

    saved = gateway.call(lambda repositories: repositories.traffic.append(record))
    assert saved.id == 1
    assert gateway.call(lambda repositories: repositories.traffic.count()) == 1
    assert gateway.call(
        lambda repositories: repositories.traffic.list_for_prompt_hash("a" * 64)
    ) == [saved]
    gateway.close()
