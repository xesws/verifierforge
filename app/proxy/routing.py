"""Repository-backed route state and rolling guardian aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.db import RepositoryGateway, repository_gateway
from app.db.records import (
    ClusterRecord,
    GuardianScoreRecord,
    RoutingRecord,
)
from app.db.settings import DatabaseSettings
from app.proxy.clusters import cluster_profile
from app.proxy.traffic import DEFAULT_DB_PATH


DEFAULT_TARGET_UPSTREAM = "tuned"
DEFAULT_ROLLING_WINDOW = 20


@dataclass(frozen=True)
class RouteRecord:
    cluster_id: str
    enabled: bool
    canary_percent: int
    target_upstream: str


@dataclass(frozen=True)
class LivePassRateRecord:
    cluster_id: str
    timestamp: str
    pass_rate: float


def get_route(
    cluster_id: str,
    *,
    db_path: Path | None = None,
    gateway: RepositoryGateway | None = None,
) -> RouteRecord:
    """Read one route, treating an absent row as safely disabled."""
    _validate_cluster_id(cluster_id)
    saved = _gateway(db_path, gateway).call(
        lambda repositories: repositories.routing.get(cluster_id)
    )
    if saved is None:
        return RouteRecord(cluster_id, False, 0, DEFAULT_TARGET_UPSTREAM)
    return RouteRecord(
        saved.cluster_id, saved.enabled, saved.canary_percent, saved.target_model
    )


def put_route(
    route: RouteRecord,
    *,
    db_path: Path | None = None,
    gateway: RepositoryGateway | None = None,
) -> RouteRecord:
    """Upsert validated control-plane route state."""
    _validate_route(route)
    observed_at = datetime.now(timezone.utc)
    resolved = _gateway(db_path, gateway)

    async def write(repositories):
        await _seed_cluster(repositories, route.cluster_id, observed_at)
        return await repositories.routing.put(
            RoutingRecord(
                cluster_id=route.cluster_id,
                enabled=route.enabled,
                canary_percent=route.canary_percent,
                target_model=route.target_upstream,
                updated_at=observed_at,
            )
        )

    saved = resolved.call(write)
    return RouteRecord(
        saved.cluster_id, saved.enabled, saved.canary_percent, saved.target_model
    )


def record_guardian_score(
    cluster_id: str,
    score: float,
    *,
    db_path: Path | None = None,
    gateway: RepositoryGateway | None = None,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
    timestamp: str | None = None,
) -> LivePassRateRecord:
    """Persist one score and its rolling exact-pass fraction as a live point."""
    _validate_cluster_id(cluster_id)
    if not 0.0 <= score <= 1.0:
        raise ValueError("guardian score must be in [0, 1]")
    if rolling_window < 1:
        raise ValueError("rolling window must be positive")
    observed_at = _parse_timestamp(timestamp)
    resolved = _gateway(db_path, gateway)

    async def write(repositories):
        await _seed_cluster(repositories, cluster_id, observed_at)
        return await repositories.live_pass_rate.record_score(
            GuardianScoreRecord(
                cluster_id=cluster_id,
                ts=observed_at,
                score=score,
            ),
            rolling_window=rolling_window,
        )

    saved = resolved.call(write)
    return LivePassRateRecord(
        cluster_id=saved.cluster_id,
        timestamp=saved.ts.isoformat(),
        pass_rate=saved.pass_rate,
    )


def list_live_pass_rate(
    cluster_id: str,
    *,
    db_path: Path | None = None,
    gateway: RepositoryGateway | None = None,
) -> list[LivePassRateRecord]:
    """Return recorded rolling points in insertion order."""
    _validate_cluster_id(cluster_id)
    rows = _gateway(db_path, gateway).call(
        lambda repositories: repositories.live_pass_rate.list_points(cluster_id)
    )
    return [
        LivePassRateRecord(row.cluster_id, row.ts.isoformat(), row.pass_rate)
        for row in rows
    ]


def _gateway(
    db_path: Path | None, gateway: RepositoryGateway | None
) -> RepositoryGateway:
    return gateway or repository_gateway(
        DatabaseSettings.sqlite(db_path or DEFAULT_DB_PATH)
    )


async def _seed_cluster(repositories, cluster_id: str, timestamp: datetime) -> None:
    if await repositories.clusters.get(cluster_id) is not None:
        return
    try:
        profile = cluster_profile(cluster_id)
    except KeyError:
        raise ValueError("cluster_id is not in the product catalog") from None
    await repositories.clusters.put(
        ClusterRecord(
            cluster_id=profile.cluster_id,
            name=profile.name,
            status=profile.status.value,
            monthly_calls=profile.monthly_calls,
            monthly_cost_usd=profile.monthly_cost_usd,
            trainable=profile.trainable,
            job_id=profile.job_id,
            analyzer_summary=None,
            updated_at=timestamp,
        )
    )


def _parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("guardian timestamp must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise ValueError("guardian timestamp must include a timezone")
    return parsed


def _validate_route(route: RouteRecord) -> None:
    _validate_cluster_id(route.cluster_id)
    if not isinstance(route.enabled, bool):
        raise ValueError("route enabled must be a bool")
    if isinstance(route.canary_percent, bool) or not isinstance(route.canary_percent, int):
        raise ValueError("route canary percent must be an int")
    if not 0 <= route.canary_percent <= 100:
        raise ValueError("route canary percent must be in [0, 100]")
    if not route.target_upstream.strip():
        raise ValueError("route target upstream must be non-empty")


def _validate_cluster_id(cluster_id: str) -> None:
    if not isinstance(cluster_id, str) or not cluster_id.strip():
        raise ValueError("cluster_id must be non-empty")
