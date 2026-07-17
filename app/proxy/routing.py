"""SQLite route state and rolling guardian aggregates for the product proxy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


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


def get_route(cluster_id: str, *, db_path: Path) -> RouteRecord:
    """Read one route, treating an absent row as a safely disabled route."""
    _validate_cluster_id(cluster_id)
    with _connect(db_path) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT enabled, canary_percent, target_upstream FROM routing WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchone()
    if row is None:
        return RouteRecord(cluster_id, False, 0, DEFAULT_TARGET_UPSTREAM)
    return RouteRecord(cluster_id, bool(row[0]), int(row[1]), str(row[2]))


def put_route(route: RouteRecord, *, db_path: Path) -> RouteRecord:
    """Upsert validated control-plane route state."""
    _validate_route(route)
    with _connect(db_path) as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO routing (cluster_id, enabled, canary_percent, target_upstream)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cluster_id) DO UPDATE SET
                enabled = excluded.enabled,
                canary_percent = excluded.canary_percent,
                target_upstream = excluded.target_upstream
            """,
            (route.cluster_id, int(route.enabled), route.canary_percent, route.target_upstream),
        )
    return route


def record_guardian_score(
    cluster_id: str,
    score: float,
    *,
    db_path: Path,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
    timestamp: str | None = None,
) -> LivePassRateRecord:
    """Persist one score and its rolling exact-pass fraction as a live point."""
    _validate_cluster_id(cluster_id)
    if not 0.0 <= score <= 1.0:
        raise ValueError("guardian score must be in [0, 1]")
    if rolling_window < 1:
        raise ValueError("rolling window must be positive")
    observed_at = timestamp or datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as connection:
        _ensure_schema(connection)
        connection.execute(
            "INSERT INTO guardian_scores (cluster_id, timestamp, score) VALUES (?, ?, ?)",
            (cluster_id, observed_at, score),
        )
        scores = connection.execute(
            """
            SELECT score FROM guardian_scores
            WHERE cluster_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (cluster_id, rolling_window),
        ).fetchall()
        pass_rate = sum(1 for (value,) in scores if float(value) == 1.0) / len(scores)
        connection.execute(
            "INSERT INTO live_pass_rate (cluster_id, timestamp, pass_rate) VALUES (?, ?, ?)",
            (cluster_id, observed_at, pass_rate),
        )
    return LivePassRateRecord(cluster_id, observed_at, pass_rate)


def list_live_pass_rate(cluster_id: str, *, db_path: Path) -> list[LivePassRateRecord]:
    """Return recorded rolling points in insertion order."""
    _validate_cluster_id(cluster_id)
    with _connect(db_path) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT timestamp, pass_rate FROM live_pass_rate
            WHERE cluster_id = ?
            ORDER BY id ASC
            """,
            (cluster_id,),
        ).fetchall()
    return [LivePassRateRecord(cluster_id, str(timestamp), float(pass_rate)) for timestamp, pass_rate in rows]


def _connect(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS routing (
            cluster_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            canary_percent INTEGER NOT NULL CHECK (canary_percent >= 0 AND canary_percent <= 100),
            target_upstream TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS guardian_scores (
            id INTEGER PRIMARY KEY,
            cluster_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            score REAL NOT NULL CHECK (score >= 0 AND score <= 1)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS live_pass_rate (
            id INTEGER PRIMARY KEY,
            cluster_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            pass_rate REAL NOT NULL CHECK (pass_rate >= 0 AND pass_rate <= 1)
        )
        """
    )


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
