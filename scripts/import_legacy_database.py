"""DB-2 one-shot importer from legacy SQLite/runs facts into DB-1 storage."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import create_database_runtime
from app.db.models import (
    AgentDecisionRow,
    ApprovalRow,
    ClusterRow,
    GuardianScoreRow,
    JobRow,
    LivePassRateRow,
    RoutingStateRow,
    TrafficRequestRow,
)
from app.db.records import (
    AgentDecisionRecord,
    ApprovalRecord,
    ClusterRecord,
    GuardianScoreRecord,
    JobRecord,
    LivePassRateRecord,
    RoutingRecord,
    TrafficRequestRecord,
)
from app.db.settings import DatabaseBackend, DatabaseConfigurationError, DatabaseSettings
from app.proxy.clusters import CLUSTER_CATALOG


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = REPOSITORY_ROOT / "app" / "proxy" / "traffic.db"
DEFAULT_RUNS_DIR = REPOSITORY_ROOT / "runs"
MIGRATION_TIMESTAMP = datetime(2026, 7, 18, tzinfo=timezone.utc)
PRODUCT_CLUSTER_IDS = {cluster.cluster_id for cluster in CLUSTER_CATALOG}
TABLE_NAMES = (
    "traffic_requests",
    "clusters",
    "routing_state",
    "guardian_scores",
    "live_pass_rate",
    "jobs",
    "agent_decisions",
    "approvals",
)

T = TypeVar("T")


class LegacyImportError(RuntimeError):
    """Stable importer failure that must not include driver URLs or secrets."""


@dataclass(frozen=True)
class ImportPlan:
    source_sha256: str
    source_id: str
    traffic_requests: tuple[TrafficRequestRecord, ...] = ()
    clusters: tuple[ClusterRecord, ...] = ()
    routing_state: tuple[RoutingRecord, ...] = ()
    guardian_scores: tuple[GuardianScoreRecord, ...] = ()
    live_pass_rate: tuple[LivePassRateRecord, ...] = ()
    jobs: tuple[JobRecord, ...] = ()
    agent_decisions: tuple[AgentDecisionRecord, ...] = ()
    approvals: tuple[ApprovalRecord, ...] = ()
    skipped: Mapping[str, int] = field(default_factory=dict)

    def records_by_table(self) -> Mapping[str, tuple[Any, ...]]:
        return {
            "traffic_requests": self.traffic_requests,
            "clusters": self.clusters,
            "routing_state": self.routing_state,
            "guardian_scores": self.guardian_scores,
            "live_pass_rate": self.live_pass_rate,
            "jobs": self.jobs,
            "agent_decisions": self.agent_decisions,
            "approvals": self.approvals,
        }

    def counts(self) -> dict[str, int]:
        return {name: len(records) for name, records in self.records_by_table().items()}

    def digests(self) -> dict[str, str]:
        return {
            name: _digest_rows(_record_rows(name, records))
            for name, records in self.records_by_table().items()
        }

    def summary(self) -> dict[str, Any]:
        return {
            "source_sha256": self.source_sha256,
            "source_id": self.source_id,
            "counts": self.counts(),
            "digests": self.digests(),
            "skipped": dict(sorted(self.skipped.items())),
        }


@dataclass(frozen=True)
class ApplyResult:
    inserted: Mapping[str, int]
    verification: "VerificationResult"

    def summary(self) -> dict[str, Any]:
        return {
            "inserted": dict(self.inserted),
            "verification": self.verification.summary(),
        }


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    expected_counts: Mapping[str, int]
    actual_counts: Mapping[str, int]
    expected_digests: Mapping[str, str]
    actual_digests: Mapping[str, str]

    def summary(self) -> dict[str, Any]:
        mismatched = [
            table
            for table in TABLE_NAMES
            if self.expected_counts.get(table) != self.actual_counts.get(table)
            or self.expected_digests.get(table) != self.actual_digests.get(table)
        ]
        return {
            "ok": self.ok,
            "mismatched_tables": mismatched,
            "expected_counts": dict(self.expected_counts),
            "actual_counts": dict(self.actual_counts),
            "expected_digests": dict(self.expected_digests),
            "actual_digests": dict(self.actual_digests),
        }


def build_import_plan(source_db: Path, runs_dir: Path | None = None) -> ImportPlan:
    """Parse legacy facts without mutating the source SQLite file."""

    source_path = Path(source_db).expanduser()
    if not source_path.is_file():
        raise LegacyImportError("source sqlite database is unavailable")
    source_sha = _sha256_file(source_path)
    with _connect_readonly(source_path) as connection:
        connection.row_factory = sqlite3.Row
        traffic_requests = _read_traffic(connection)
        routing_state, skipped_routing = _read_routing(connection)
        guardian_scores, skipped_scores = _read_guardian_scores(connection)
        live_pass_rate, skipped_live = _read_live_pass_rate(connection)
        agent_decisions = _read_agent_decisions(connection)
        approvals = _read_approvals(connection, {record.id for record in agent_decisions})

    jobs = _read_jobs(runs_dir) if runs_dir is not None else ()
    skipped = {
        "routing_state_non_product": skipped_routing,
        "guardian_scores_non_product": skipped_scores,
        "live_pass_rate_non_product": skipped_live,
    }
    return ImportPlan(
        source_sha256=source_sha,
        source_id=source_sha[:16],
        traffic_requests=traffic_requests,
        clusters=_seed_clusters(),
        routing_state=routing_state,
        guardian_scores=guardian_scores,
        live_pass_rate=live_pass_rate,
        jobs=jobs,
        agent_decisions=agent_decisions,
        approvals=approvals,
        skipped={name: count for name, count in skipped.items() if count},
    )


async def apply_import_plan(plan: ImportPlan, settings: DatabaseSettings) -> ApplyResult:
    runtime = create_database_runtime(settings)
    try:
        inserted = await _apply_with_runtime(plan, runtime)
        verification = await _verify_with_runtime(plan, runtime)
        return ApplyResult(inserted=inserted, verification=verification)
    finally:
        await runtime.close()


async def verify_import_plan(plan: ImportPlan, settings: DatabaseSettings) -> VerificationResult:
    runtime = create_database_runtime(settings)
    try:
        return await _verify_with_runtime(plan, runtime)
    finally:
        await runtime.close()


def resolve_target_settings(
    *, target_sqlite_path: Path | None = None, target_env: str = "SUPABASE_DB_URL"
) -> DatabaseSettings:
    """Resolve the destination without accepting a database URL on argv."""

    if target_sqlite_path is not None:
        return DatabaseSettings.sqlite(target_sqlite_path)
    raw_url = os.environ.get(target_env, "").strip()
    if not raw_url:
        raise DatabaseConfigurationError(
            f"{target_env} is required unless --target-sqlite-path is used"
        )
    return DatabaseSettings.from_env(
        {"VF_DB_BACKEND": DatabaseBackend.POSTGRES.value, "SUPABASE_DB_URL": raw_url}
    )


def sanitize_error(message: object) -> str:
    """Return a bounded error string with URLs and password-like values removed."""

    text_value = str(message)
    text_value = re.sub(
        r"\b(?:postgres(?:ql)?|sqlite(?:\+aiosqlite)?|mysql)://\S+",
        "[redacted-db-url]",
        text_value,
        flags=re.IGNORECASE,
    )
    text_value = re.sub(
        r"(?i)(password|passwd|pwd|SUPABASE_DB_URL|SUPABASE_DB_DIRECT_CONN_STRING)"
        r"\s*=\s*[^,\s;]+",
        r"\1=[redacted]",
        text_value,
    )
    text_value = re.sub(r"://([^:/@\s]+):([^@\s]+)@", r"://\1:[redacted]@", text_value)
    return text_value[:800]


def classify_owner_action(message: object) -> bool:
    text_value = str(message).lower()
    return any(
        marker in text_value
        for marker in ("403", "accessdenied", "access denied", "permission denied", "not authorized")
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import legacy VerifierForge SQLite/runs relational facts."
    )
    parser.add_argument("mode", choices=("dry-run", "apply", "verify"))
    parser.add_argument("--source-db", type=Path, default=Path(os.environ.get("VF_PROXY_DB_PATH", DEFAULT_SOURCE_DB)))
    parser.add_argument("--runs-dir", type=Path, default=Path(os.environ.get("VF_RUNS_DIR", DEFAULT_RUNS_DIR)))
    parser.add_argument(
        "--target-sqlite-path",
        type=Path,
        help="Local test destination. Production imports read SUPABASE_DB_URL from the environment.",
    )
    parser.add_argument(
        "--target-env",
        default="SUPABASE_DB_URL",
        help="Environment variable containing the Postgres URL; never printed.",
    )
    args = parser.parse_args(argv)

    try:
        plan = build_import_plan(args.source_db, args.runs_dir)
        if args.mode == "dry-run":
            _print_json({"status": "planned", **plan.summary()})
            return 0

        settings = resolve_target_settings(
            target_sqlite_path=args.target_sqlite_path,
            target_env=args.target_env,
        )
        if args.mode == "apply":
            result = asyncio.run(apply_import_plan(plan, settings))
            _print_json({"status": "applied", **result.summary()})
            return 0 if result.verification.ok else 1

        verification = asyncio.run(verify_import_plan(plan, settings))
        _print_json({"status": "verified" if verification.ok else "mismatch", **verification.summary()})
        return 0 if verification.ok else 1
    except (DatabaseConfigurationError, LegacyImportError, sqlite3.Error, OSError, ValueError) as error:
        payload: dict[str, Any] = {"status": "failed", "error": sanitize_error(error)}
        if classify_owner_action(error):
            payload["owner_action"] = True
        _print_json(payload, stream=sys.stderr)
        return 1


async def _apply_with_runtime(plan: ImportPlan, runtime) -> dict[str, int]:
    try:
        async with runtime.sessions.begin() as session:
            inserted = {
                "clusters": await _apply_table(
                    session,
                    "clusters",
                    ClusterRow,
                    plan.clusters,
                    lambda record: record.cluster_id,
                    _cluster_row,
                ),
                "traffic_requests": await _apply_table(
                    session,
                    "traffic_requests",
                    TrafficRequestRow,
                    plan.traffic_requests,
                    lambda record: record.id,
                    _traffic_row,
                ),
                "routing_state": await _apply_table(
                    session,
                    "routing_state",
                    RoutingStateRow,
                    plan.routing_state,
                    lambda record: record.cluster_id,
                    _routing_row,
                ),
                "guardian_scores": await _apply_table(
                    session,
                    "guardian_scores",
                    GuardianScoreRow,
                    plan.guardian_scores,
                    lambda record: record.id,
                    _guardian_row,
                ),
                "live_pass_rate": await _apply_table(
                    session,
                    "live_pass_rate",
                    LivePassRateRow,
                    plan.live_pass_rate,
                    lambda record: record.id,
                    _live_row,
                ),
                "jobs": await _apply_table(
                    session,
                    "jobs",
                    JobRow,
                    plan.jobs,
                    lambda record: record.job_id,
                    _job_row,
                ),
                "agent_decisions": await _apply_table(
                    session,
                    "agent_decisions",
                    AgentDecisionRow,
                    plan.agent_decisions,
                    lambda record: record.id,
                    _decision_row,
                ),
                "approvals": await _apply_approvals(session, plan.approvals),
            }
            await _reset_sequences(session, runtime.settings.backend)
            return inserted
    except LegacyImportError:
        raise
    except SQLAlchemyError:
        raise LegacyImportError("target database import failed") from None


async def _apply_table(
    session: AsyncSession,
    table_name: str,
    row_model: type[Any],
    records: Iterable[T],
    primary_key: Callable[[T], object],
    make_row: Callable[[T], Any],
) -> int:
    inserted = 0
    for record in records:
        key = primary_key(record)
        existing = await session.get(row_model, key)
        expected = _record_row(table_name, record)
        if existing is not None:
            actual = _target_row(table_name, existing)
            if actual != expected:
                raise LegacyImportError(f"target {table_name} contains conflicting imported row")
            continue
        session.add(make_row(record))
        inserted += 1
    return inserted


async def _apply_approvals(session: AsyncSession, records: Iterable[ApprovalRecord]) -> int:
    inserted = 0
    for record in records:
        existing = await session.get(ApprovalRow, record.id)
        expected = _record_row("approvals", record)
        if existing is not None:
            actual = _target_row("approvals", existing)
            if actual != expected:
                raise LegacyImportError("target approvals contains conflicting imported row")
            continue
        duplicate = await session.scalar(
            select(ApprovalRow).where(ApprovalRow.decision_id == record.decision_id)
        )
        if duplicate is not None:
            raise LegacyImportError("target approvals contains conflicting imported decision")
        session.add(_approval_row(record))
        inserted += 1
    return inserted


async def _verify_with_runtime(plan: ImportPlan, runtime) -> VerificationResult:
    try:
        async with runtime.sessions.begin() as session:
            actual = await _target_rows(session)
    except SQLAlchemyError:
        raise LegacyImportError("target database verification failed") from None
    expected = {
        table_name: _record_rows(table_name, records)
        for table_name, records in plan.records_by_table().items()
    }
    expected_counts = {table_name: len(rows) for table_name, rows in expected.items()}
    actual_counts = {table_name: len(rows) for table_name, rows in actual.items()}
    expected_digests = {table_name: _digest_rows(rows) for table_name, rows in expected.items()}
    actual_digests = {table_name: _digest_rows(rows) for table_name, rows in actual.items()}
    return VerificationResult(
        ok=expected_counts == actual_counts and expected_digests == actual_digests,
        expected_counts=expected_counts,
        actual_counts=actual_counts,
        expected_digests=expected_digests,
        actual_digests=actual_digests,
    )


async def _target_rows(session: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    return {
        "traffic_requests": [
            _target_row("traffic_requests", row)
            for row in (
                await session.scalars(select(TrafficRequestRow).order_by(TrafficRequestRow.id))
            ).all()
        ],
        "clusters": [
            _target_row("clusters", row)
            for row in (await session.scalars(select(ClusterRow).order_by(ClusterRow.cluster_id))).all()
        ],
        "routing_state": [
            _target_row("routing_state", row)
            for row in (
                await session.scalars(select(RoutingStateRow).order_by(RoutingStateRow.cluster_id))
            ).all()
        ],
        "guardian_scores": [
            _target_row("guardian_scores", row)
            for row in (
                await session.scalars(select(GuardianScoreRow).order_by(GuardianScoreRow.id))
            ).all()
        ],
        "live_pass_rate": [
            _target_row("live_pass_rate", row)
            for row in (
                await session.scalars(select(LivePassRateRow).order_by(LivePassRateRow.id))
            ).all()
        ],
        "jobs": [
            _target_row("jobs", row)
            for row in (await session.scalars(select(JobRow).order_by(JobRow.job_id))).all()
        ],
        "agent_decisions": [
            _target_row("agent_decisions", row)
            for row in (
                await session.scalars(select(AgentDecisionRow).order_by(AgentDecisionRow.id))
            ).all()
        ],
        "approvals": [
            _target_row("approvals", row)
            for row in (await session.scalars(select(ApprovalRow).order_by(ApprovalRow.id))).all()
        ],
    }


async def _reset_sequences(session: AsyncSession, backend: DatabaseBackend) -> None:
    if backend is not DatabaseBackend.POSTGRES:
        return
    for table_name in ("traffic_requests", "guardian_scores", "live_pass_rate"):
        await session.execute(
            text(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table_name}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table_name}), 1),
                    (SELECT COUNT(*) FROM {table_name}) > 0
                )
                """
            )
        )


def _read_traffic(connection: sqlite3.Connection) -> tuple[TrafficRequestRecord, ...]:
    if not _table_exists(connection, "traffic"):
        return ()
    required = {
        "id",
        "timestamp",
        "system_prompt_hash",
        "model",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "estimated_cost_usd",
    }
    columns = _require_columns(connection, "traffic", required)
    route_column = "route_path" if "route_path" in columns else None
    select_route = ", route_path" if route_column else ""
    rows = connection.execute(
        f"""
        SELECT id, timestamp, system_prompt_hash, model, input_tokens,
               output_tokens, latency_ms, estimated_cost_usd{select_route}
        FROM traffic
        ORDER BY id
        """
    ).fetchall()
    records: list[TrafficRequestRecord] = []
    for row in rows:
        records.append(
            TrafficRequestRecord(
                id=_positive_pk(row["id"], "traffic.id"),
                ts=_parse_datetime(row["timestamp"], "traffic.timestamp"),
                prompt_hash=str(row["system_prompt_hash"]),
                model=str(row["model"]),
                tokens_in=_nonnegative_int(row["input_tokens"], "traffic.input_tokens"),
                tokens_out=_nonnegative_int(row["output_tokens"], "traffic.output_tokens"),
                latency_ms=_nonnegative_float(row["latency_ms"], "traffic.latency_ms"),
                cost_usd=_nonnegative_float(row["estimated_cost_usd"], "traffic.estimated_cost_usd"),
                route_taken=str(row["route_path"] if route_column else "default"),
            )
        )
    return tuple(records)


def _read_routing(connection: sqlite3.Connection) -> tuple[tuple[RoutingRecord, ...], int]:
    if not _table_exists(connection, "routing"):
        return (), 0
    _require_columns(connection, "routing", {"cluster_id", "enabled", "canary_percent", "target_upstream"})
    rows = connection.execute(
        """
        SELECT cluster_id, enabled, canary_percent, target_upstream
        FROM routing
        ORDER BY cluster_id
        """
    ).fetchall()
    records: list[RoutingRecord] = []
    skipped = 0
    for row in rows:
        cluster_id = str(row["cluster_id"])
        if cluster_id not in PRODUCT_CLUSTER_IDS:
            skipped += 1
            continue
        records.append(
            RoutingRecord(
                cluster_id=cluster_id,
                enabled=bool(row["enabled"]),
                canary_percent=_bounded_percent(row["canary_percent"], "routing.canary_percent"),
                target_model=str(row["target_upstream"]),
                updated_at=MIGRATION_TIMESTAMP,
            )
        )
    return tuple(records), skipped


def _read_guardian_scores(
    connection: sqlite3.Connection,
) -> tuple[tuple[GuardianScoreRecord, ...], int]:
    if not _table_exists(connection, "guardian_scores"):
        return (), 0
    _require_columns(connection, "guardian_scores", {"id", "cluster_id", "timestamp", "score"})
    rows = connection.execute(
        """
        SELECT id, cluster_id, timestamp, score
        FROM guardian_scores
        ORDER BY id
        """
    ).fetchall()
    records: list[GuardianScoreRecord] = []
    skipped = 0
    for row in rows:
        cluster_id = str(row["cluster_id"])
        if cluster_id not in PRODUCT_CLUSTER_IDS:
            skipped += 1
            continue
        records.append(
            GuardianScoreRecord(
                id=_positive_pk(row["id"], "guardian_scores.id"),
                cluster_id=cluster_id,
                ts=_parse_datetime(row["timestamp"], "guardian_scores.timestamp"),
                score=_unit_float(row["score"], "guardian_scores.score"),
            )
        )
    return tuple(records), skipped


def _read_live_pass_rate(
    connection: sqlite3.Connection,
) -> tuple[tuple[LivePassRateRecord, ...], int]:
    if not _table_exists(connection, "live_pass_rate"):
        return (), 0
    _require_columns(connection, "live_pass_rate", {"id", "cluster_id", "timestamp", "pass_rate"})
    rows = connection.execute(
        """
        SELECT id, cluster_id, timestamp, pass_rate
        FROM live_pass_rate
        ORDER BY id
        """
    ).fetchall()
    records: list[LivePassRateRecord] = []
    skipped = 0
    for row in rows:
        cluster_id = str(row["cluster_id"])
        if cluster_id not in PRODUCT_CLUSTER_IDS:
            skipped += 1
            continue
        records.append(
            LivePassRateRecord(
                id=_positive_pk(row["id"], "live_pass_rate.id"),
                cluster_id=cluster_id,
                ts=_parse_datetime(row["timestamp"], "live_pass_rate.timestamp"),
                pass_rate=_unit_float(row["pass_rate"], "live_pass_rate.pass_rate"),
            )
        )
    return tuple(records), skipped


def _read_agent_decisions(connection: sqlite3.Connection) -> tuple[AgentDecisionRecord, ...]:
    if not _table_exists(connection, "agent_decisions"):
        return ()
    _require_columns(
        connection,
        "agent_decisions",
        {
            "id",
            "cluster_id",
            "evidence_fingerprint",
            "run_status",
            "decision_json",
            "trace_id",
            "trace_s3_key",
            "provider",
            "model_name",
            "created_at",
            "tokens_in",
            "tokens_out",
            "summary_json",
        },
    )
    rows = connection.execute(
        """
        SELECT id, cluster_id, evidence_fingerprint, run_status, decision_json,
               trace_id, trace_s3_key, provider, model_name, created_at,
               tokens_in, tokens_out, summary_json
        FROM agent_decisions
        ORDER BY id
        """
    ).fetchall()
    return tuple(_decision_from_row(row) for row in rows)


def _read_approvals(
    connection: sqlite3.Connection, decision_ids: set[str]
) -> tuple[ApprovalRecord, ...]:
    if not _table_exists(connection, "approvals"):
        return ()
    _require_columns(
        connection,
        "approvals",
        {"id", "decision_id", "approved_by", "approved_at", "approval_json"},
    )
    rows = connection.execute(
        """
        SELECT id, decision_id, approved_by, approved_at, approval_json
        FROM approvals
        ORDER BY id
        """
    ).fetchall()
    records: list[ApprovalRecord] = []
    for row in rows:
        decision_id = str(row["decision_id"])
        if decision_id not in decision_ids:
            raise LegacyImportError("approval references a missing agent decision")
        records.append(
            ApprovalRecord(
                id=_bounded_text(row["id"], "approvals.id", 128),
                decision_id=_bounded_text(decision_id, "approvals.decision_id", 128),
                approved_by=_bounded_text(row["approved_by"], "approvals.approved_by", 128),
                approved_at=_parse_datetime(row["approved_at"], "approvals.approved_at"),
                provision_handle=None,
            )
        )
    return tuple(records)


def _decision_from_row(row: sqlite3.Row) -> AgentDecisionRecord:
    summary = _json_object(row["summary_json"], "agent_decisions.summary_json")
    decision_payload = _json_object_or_none(row["decision_json"], "agent_decisions.decision_json")
    if decision_payload is None and isinstance(summary.get("decision"), dict):
        decision_payload = summary["decision"]

    decision: str | None = None
    rationale: str | None = None
    confidence: float | None = None
    config_json: dict[str, Any] | None = None
    if decision_payload is not None:
        decision = _optional_bounded_text(decision_payload.get("decision"), "decision.decision", 32)
        rationale = _optional_bounded_text(decision_payload.get("rationale"), "decision.rationale", 20_000)
        confidence_value = decision_payload.get("confidence")
        confidence = (
            None if confidence_value is None else _unit_float(confidence_value, "decision.confidence")
        )
        raw_config = decision_payload.get("config")
        if raw_config is not None and not isinstance(raw_config, dict):
            raise LegacyImportError("agent decision config is not a JSON object")
        config_json = raw_config

    return AgentDecisionRecord(
        id=_bounded_text(row["id"], "agent_decisions.id", 128),
        cluster_id=_bounded_text(row["cluster_id"], "agent_decisions.cluster_id", 128),
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        config_json=config_json,
        trace_s3_key=_optional_bounded_text(row["trace_s3_key"], "agent_decisions.trace_s3_key", 1024),
        model_name=_bounded_text(row["model_name"], "agent_decisions.model_name", 255),
        created_at=_parse_datetime(row["created_at"], "agent_decisions.created_at"),
        evidence_fingerprint=_optional_bounded_text(
            row["evidence_fingerprint"], "agent_decisions.evidence_fingerprint", 64
        ),
        run_status=_bounded_text(row["run_status"], "agent_decisions.run_status", 32),
        trace_id=_optional_bounded_text(row["trace_id"], "agent_decisions.trace_id", 128),
        provider=_optional_bounded_text(row["provider"], "agent_decisions.provider", 64),
        tokens_in=_nonnegative_int(row["tokens_in"], "agent_decisions.tokens_in"),
        tokens_out=_nonnegative_int(row["tokens_out"], "agent_decisions.tokens_out"),
        summary_json=summary,
    )


def _read_jobs(runs_dir: Path) -> tuple[JobRecord, ...]:
    root = Path(runs_dir).expanduser()
    if not root.is_dir():
        return ()
    records: list[JobRecord] = []
    for job_dir in sorted(root.iterdir(), key=lambda path: path.name):
        if not job_dir.is_dir() or job_dir.name.startswith("."):
            continue
        job_id = _bounded_text(job_dir.name, "jobs.job_id", 128)
        metrics = _metrics_summary(job_dir / "metrics.jsonl")
        config = _job_config(job_dir, metrics)
        records.append(
            JobRecord(
                job_id=job_id,
                template=_bounded_text(str(config.get("template", "unknown")), "jobs.template", 128),
                status=_job_status(job_dir),
                config_json=config,
                created_at=datetime.fromtimestamp(job_dir.stat().st_mtime, tz=timezone.utc),
                s3_prefix=None,
                summary_json={
                    "source": "legacy_runs",
                    "metrics_count": metrics["count"],
                    "first_metric_at": metrics.get("first_metric_at"),
                    "last_metric_at": metrics.get("last_metric_at"),
                    "last_metric": metrics.get("last_metric"),
                    "has_train_log": (job_dir / "train.log").is_file(),
                    "has_final_artifact": (job_dir / "artifacts" / "final" / "model.txt").is_file(),
                },
            )
        )
    return tuple(records)


def _metrics_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"count": 0}
    count = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    last_metric: dict[str, Any] | None = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise LegacyImportError("metrics row is not a JSON object")
            count += 1
            timestamp = payload.get("timestamp")
            if isinstance(timestamp, str):
                first_timestamp = first_timestamp or timestamp
                last_timestamp = timestamp
            last_metric = {
                key: payload[key]
                for key in ("step", "reward_mean", "pass_at_1", "entropy", "timestamp")
                if key in payload
            }
    return {
        "count": count,
        "first_metric_at": first_timestamp,
        "last_metric_at": last_timestamp,
        "last_metric": last_metric,
    }


def _job_config(job_dir: Path, metrics: Mapping[str, Any]) -> dict[str, Any]:
    config_path = job_dir / "config.json"
    if config_path.is_file():
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {"source": "legacy_runs", **payload}
    return {
        "source": "legacy_runs",
        "template": "unknown",
        "metrics_count": metrics["count"],
    }


def _job_status(job_dir: Path) -> str:
    if (job_dir / "failed").exists():
        return "failed"
    if (job_dir / "early_stopped").exists():
        return "early_stopped"
    if (job_dir / "artifacts" / "final" / "model.txt").is_file():
        return "done"
    if (job_dir / "metrics.jsonl").exists() or (job_dir / "train.log").exists():
        return "running"
    return "queued"


def _seed_clusters() -> tuple[ClusterRecord, ...]:
    return tuple(
        ClusterRecord(
            cluster_id=cluster.cluster_id,
            name=cluster.name,
            status=cluster.status.value,
            monthly_calls=cluster.monthly_calls,
            monthly_cost_usd=cluster.monthly_cost_usd,
            trainable=cluster.trainable,
            job_id=cluster.job_id,
            analyzer_summary=None,
            updated_at=MIGRATION_TIMESTAMP,
        )
        for cluster in CLUSTER_CATALOG
    )


def _traffic_row(record: TrafficRequestRecord) -> TrafficRequestRow:
    return TrafficRequestRow(
        id=record.id,
        ts=_utc(record.ts),
        prompt_hash=record.prompt_hash,
        model=record.model,
        tokens_in=record.tokens_in,
        tokens_out=record.tokens_out,
        latency_ms=record.latency_ms,
        cost_usd=record.cost_usd,
        route_taken=record.route_taken,
    )


def _cluster_row(record: ClusterRecord) -> ClusterRow:
    return ClusterRow(
        cluster_id=record.cluster_id,
        name=record.name,
        status=record.status,
        monthly_calls=record.monthly_calls,
        monthly_cost_usd=record.monthly_cost_usd,
        trainable=record.trainable,
        job_id=record.job_id,
        analyzer_summary=record.analyzer_summary,
        updated_at=_utc(record.updated_at),
    )


def _routing_row(record: RoutingRecord) -> RoutingStateRow:
    return RoutingStateRow(
        cluster_id=record.cluster_id,
        enabled=record.enabled,
        canary_percent=record.canary_percent,
        target_model=record.target_model,
        updated_at=_utc(record.updated_at),
    )


def _guardian_row(record: GuardianScoreRecord) -> GuardianScoreRow:
    return GuardianScoreRow(
        id=record.id,
        cluster_id=record.cluster_id,
        ts=_utc(record.ts),
        score=record.score,
    )


def _live_row(record: LivePassRateRecord) -> LivePassRateRow:
    return LivePassRateRow(
        id=record.id,
        cluster_id=record.cluster_id,
        ts=_utc(record.ts),
        pass_rate=record.pass_rate,
    )


def _job_row(record: JobRecord) -> JobRow:
    return JobRow(
        job_id=record.job_id,
        template=record.template,
        status=record.status,
        config_json=record.config_json,
        created_at=_utc(record.created_at),
        s3_prefix=record.s3_prefix,
        summary_json=record.summary_json,
    )


def _decision_row(record: AgentDecisionRecord) -> AgentDecisionRow:
    return AgentDecisionRow(
        id=record.id,
        cluster_id=record.cluster_id,
        decision=record.decision,
        rationale=record.rationale,
        confidence=record.confidence,
        config_json=record.config_json,
        trace_s3_key=record.trace_s3_key,
        model_name=record.model_name,
        created_at=_utc(record.created_at),
        evidence_fingerprint=record.evidence_fingerprint,
        run_status=record.run_status,
        trace_id=record.trace_id,
        provider=record.provider,
        tokens_in=record.tokens_in,
        tokens_out=record.tokens_out,
        summary_json=record.summary_json,
    )


def _approval_row(record: ApprovalRecord) -> ApprovalRow:
    return ApprovalRow(
        id=record.id,
        decision_id=record.decision_id,
        approved_by=record.approved_by,
        approved_at=_utc(record.approved_at),
        provision_handle=record.provision_handle,
    )


def _target_row(table_name: str, row: Any) -> dict[str, Any]:
    if table_name == "traffic_requests":
        record = TrafficRequestRecord(
            id=row.id,
            ts=_utc(row.ts),
            prompt_hash=row.prompt_hash,
            model=row.model,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            latency_ms=row.latency_ms,
            cost_usd=row.cost_usd,
            route_taken=row.route_taken,
        )
    elif table_name == "clusters":
        record = ClusterRecord(
            cluster_id=row.cluster_id,
            name=row.name,
            status=row.status,
            monthly_calls=row.monthly_calls,
            monthly_cost_usd=row.monthly_cost_usd,
            trainable=row.trainable,
            job_id=row.job_id,
            analyzer_summary=row.analyzer_summary,
            updated_at=_utc(row.updated_at),
        )
    elif table_name == "routing_state":
        record = RoutingRecord(
            cluster_id=row.cluster_id,
            enabled=row.enabled,
            canary_percent=row.canary_percent,
            target_model=row.target_model,
            updated_at=_utc(row.updated_at),
        )
    elif table_name == "guardian_scores":
        record = GuardianScoreRecord(
            id=row.id,
            cluster_id=row.cluster_id,
            ts=_utc(row.ts),
            score=row.score,
        )
    elif table_name == "live_pass_rate":
        record = LivePassRateRecord(
            id=row.id,
            cluster_id=row.cluster_id,
            ts=_utc(row.ts),
            pass_rate=row.pass_rate,
        )
    elif table_name == "jobs":
        record = JobRecord(
            job_id=row.job_id,
            template=row.template,
            status=row.status,
            config_json=row.config_json,
            created_at=_utc(row.created_at),
            s3_prefix=row.s3_prefix,
            summary_json=row.summary_json,
        )
    elif table_name == "agent_decisions":
        record = AgentDecisionRecord(
            id=row.id,
            cluster_id=row.cluster_id,
            decision=row.decision,
            rationale=row.rationale,
            confidence=row.confidence,
            config_json=row.config_json,
            trace_s3_key=row.trace_s3_key,
            model_name=row.model_name,
            created_at=_utc(row.created_at),
            evidence_fingerprint=row.evidence_fingerprint,
            run_status=row.run_status,
            trace_id=row.trace_id,
            provider=row.provider,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            summary_json=row.summary_json,
        )
    elif table_name == "approvals":
        record = ApprovalRecord(
            id=row.id,
            decision_id=row.decision_id,
            approved_by=row.approved_by,
            approved_at=_utc(row.approved_at),
            provision_handle=row.provision_handle,
        )
    else:
        raise KeyError(table_name)
    return _record_row(table_name, record)


def _record_rows(table_name: str, records: Iterable[Any]) -> list[dict[str, Any]]:
    rows = [_record_row(table_name, record) for record in records]
    return sorted(
        rows,
        key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False),
    )


def _record_row(table_name: str, record: Any) -> dict[str, Any]:
    values = dict(record.__dict__)
    return {key: _canonical_value(value) for key, value in sorted(values.items())}


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _canonical_value(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical_value(child) for child in value]
    return value


def _digest_rows(rows: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(
        rows,
        key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False),
    )
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _require_columns(
    connection: sqlite3.Connection, table_name: str, required: set[str]
) -> set[str]:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}
    missing = sorted(required - columns)
    if missing:
        raise LegacyImportError(f"legacy table {table_name} is missing required columns")
    return columns


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str) or not value.strip():
        raise LegacyImportError(f"{field_name} must be an ISO timestamp")
    text_value = value.strip()
    if text_value.endswith("Z"):
        text_value = f"{text_value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        raise LegacyImportError(f"{field_name} must be an ISO timestamp") from None
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_object(value: object, field_name: str) -> dict[str, Any]:
    payload = _json_object_or_none(value, field_name)
    if payload is None:
        raise LegacyImportError(f"{field_name} must be a JSON object")
    return payload


def _json_object_or_none(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        payload = value
    elif isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            raise LegacyImportError(f"{field_name} must be valid JSON") from None
    else:
        raise LegacyImportError(f"{field_name} must be valid JSON")
    if not isinstance(payload, dict):
        raise LegacyImportError(f"{field_name} must be a JSON object")
    return payload


def _positive_pk(value: object, field_name: str) -> int:
    parsed = _nonnegative_int(value, field_name)
    if parsed < 1:
        raise LegacyImportError(f"{field_name} must be positive")
    return parsed


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise LegacyImportError(f"{field_name} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise LegacyImportError(f"{field_name} must be a non-negative integer") from None
    if parsed < 0:
        raise LegacyImportError(f"{field_name} must be a non-negative integer")
    return parsed


def _nonnegative_float(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise LegacyImportError(f"{field_name} must be a non-negative number")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise LegacyImportError(f"{field_name} must be a non-negative number") from None
    if parsed < 0:
        raise LegacyImportError(f"{field_name} must be a non-negative number")
    return parsed


def _unit_float(value: object, field_name: str) -> float:
    parsed = _nonnegative_float(value, field_name)
    if parsed > 1:
        raise LegacyImportError(f"{field_name} must be in [0, 1]")
    return parsed


def _bounded_percent(value: object, field_name: str) -> int:
    parsed = _nonnegative_int(value, field_name)
    if parsed > 100:
        raise LegacyImportError(f"{field_name} must be in [0, 100]")
    return parsed


def _bounded_text(value: object, field_name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LegacyImportError(f"{field_name} must be non-empty")
    text_value = value.strip()
    if len(text_value) > limit:
        raise LegacyImportError(f"{field_name} is too long")
    return text_value


def _optional_bounded_text(value: object, field_name: str, limit: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, field_name, limit)


def _print_json(payload: Mapping[str, Any], *, stream=None) -> None:
    print(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
        file=stream or sys.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
