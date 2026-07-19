"""SQLAlchemy 2.0 async implementations of all relational stores."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from typing import AsyncIterator, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .contracts import (
    AgentDecisionStore,
    ApprovalStore,
    ClusterStore,
    CredentialStore,
    JobStore,
    LivePassRateStore,
    ProvisionAuditStore,
    RoutingStore,
    TrafficStore,
)
from .engine import DatabaseRuntime
from .models import (
    AgentDecisionRow,
    ApprovalRow,
    ClusterRow,
    GuardianScoreRow,
    JobRow,
    LivePassRateRow,
    ProviderCredentialRow,
    ProvisionEventRow,
    RoutingStateRow,
    TrafficRequestRow,
)
from .records import (
    AgentDecisionRecord,
    ApprovalRecord,
    ClusterRecord,
    CredentialRecord,
    GuardianScoreRecord,
    JobRecord,
    LivePassRateRecord,
    ProvisionEventRecord,
    RoutingRecord,
    TrafficRequestRecord,
    TrafficAggregateRecord,
)


MAX_JSON_BYTES = 64 * 1024


class DatabaseOperationError(RuntimeError):
    """Stable database failure that does not expose driver details or URLs."""


@asynccontextmanager
async def _transaction(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    try:
        async with sessions.begin() as session:
            yield session
    except DatabaseOperationError:
        raise
    except SQLAlchemyError:
        raise DatabaseOperationError("database operation failed") from None


class SQLAlchemyTrafficStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def append(self, record: TrafficRequestRecord) -> TrafficRequestRecord:
        _traffic_valid(record)
        row = TrafficRequestRow(
            ts=_utc(record.ts),
            prompt_hash=record.prompt_hash,
            model=record.model,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            latency_ms=record.latency_ms,
            cost_usd=record.cost_usd,
            route_taken=record.route_taken,
        )
        async with _transaction(self.sessions) as session:
            session.add(row)
            await session.flush()
            return _traffic_record(row)

    async def list_for_prompt_hash(
        self, prompt_hash: str, *, limit: int = 1000
    ) -> list[TrafficRequestRecord]:
        _text(prompt_hash, "prompt_hash")
        _limit(limit)
        statement = (
            select(TrafficRequestRow)
            .where(TrafficRequestRow.prompt_hash == prompt_hash)
            .order_by(TrafficRequestRow.id)
            .limit(limit)
        )
        async with _transaction(self.sessions) as session:
            rows = (await session.scalars(statement)).all()
            return [_traffic_record(row) for row in rows]

    async def count(self) -> int:
        async with _transaction(self.sessions) as session:
            return int(await session.scalar(select(func.count(TrafficRequestRow.id))) or 0)

    async def summarize_by_prompt_hash(self) -> list[TrafficAggregateRecord]:
        statement = (
            select(
                TrafficRequestRow.prompt_hash,
                func.count(TrafficRequestRow.id),
                func.sum(TrafficRequestRow.tokens_in + TrafficRequestRow.tokens_out),
                func.sum(TrafficRequestRow.cost_usd),
            )
            .group_by(TrafficRequestRow.prompt_hash)
            .order_by(TrafficRequestRow.prompt_hash)
        )
        async with _transaction(self.sessions) as session:
            rows = (await session.execute(statement)).all()
            return [
                TrafficAggregateRecord(
                    prompt_hash=str(prompt_hash),
                    request_count=int(request_count),
                    total_tokens=int(total_tokens or 0),
                    total_cost_usd=float(total_cost or 0.0),
                )
                for prompt_hash, request_count, total_tokens, total_cost in rows
            ]


class SQLAlchemyClusterStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def put(self, record: ClusterRecord) -> ClusterRecord:
        _cluster_valid(record)
        async with _transaction(self.sessions) as session:
            row = await session.get(ClusterRow, record.cluster_id)
            if row is None:
                row = ClusterRow(cluster_id=record.cluster_id)
                session.add(row)
            row.name = record.name
            row.status = record.status
            row.monthly_calls = record.monthly_calls
            row.monthly_cost_usd = record.monthly_cost_usd
            row.trainable = record.trainable
            row.job_id = record.job_id
            row.analyzer_summary = _bounded_json(record.analyzer_summary, "analyzer_summary")
            row.approved_sample_source = _bounded_json(
                record.approved_sample_source, "approved_sample_source"
            )
            row.updated_at = _utc(record.updated_at)
            await session.flush()
            return _cluster_record(row)

    async def get(self, cluster_id: str) -> ClusterRecord | None:
        _text(cluster_id, "cluster_id")
        async with _transaction(self.sessions) as session:
            row = await session.get(ClusterRow, cluster_id)
            return _cluster_record(row) if row else None

    async def list(self) -> list[ClusterRecord]:
        async with _transaction(self.sessions) as session:
            rows = (await session.scalars(select(ClusterRow).order_by(ClusterRow.cluster_id))).all()
            return [_cluster_record(row) for row in rows]


class SQLAlchemyRoutingStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def put(self, record: RoutingRecord) -> RoutingRecord:
        _routing_valid(record)
        async with _transaction(self.sessions) as session:
            row = await session.get(RoutingStateRow, record.cluster_id)
            if row is None:
                row = RoutingStateRow(cluster_id=record.cluster_id)
                session.add(row)
            row.enabled = record.enabled
            row.canary_percent = record.canary_percent
            row.target_model = record.target_model
            row.updated_at = _utc(record.updated_at)
            await session.flush()
            return _routing_record(row)

    async def get(self, cluster_id: str) -> RoutingRecord | None:
        _text(cluster_id, "cluster_id")
        async with _transaction(self.sessions) as session:
            row = await session.get(RoutingStateRow, cluster_id)
            return _routing_record(row) if row else None


class SQLAlchemyLivePassRateStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def record_score(
        self, record: GuardianScoreRecord, *, rolling_window: int = 20
    ) -> LivePassRateRecord:
        _guardian_valid(record)
        if isinstance(rolling_window, bool) or rolling_window < 1:
            raise ValueError("rolling_window must be a positive integer")
        async with _transaction(self.sessions) as session:
            score = GuardianScoreRow(
                cluster_id=record.cluster_id,
                ts=_utc(record.ts),
                score=record.score,
            )
            session.add(score)
            await session.flush()
            statement = (
                select(GuardianScoreRow.score)
                .where(GuardianScoreRow.cluster_id == record.cluster_id)
                .order_by(GuardianScoreRow.id.desc())
                .limit(rolling_window)
            )
            scores = list((await session.scalars(statement)).all())
            pass_rate = sum(float(value) == 1.0 for value in scores) / len(scores)
            point = LivePassRateRow(
                cluster_id=record.cluster_id,
                ts=_utc(record.ts),
                pass_rate=pass_rate,
            )
            session.add(point)
            await session.flush()
            return _live_record(point)

    async def list_points(self, cluster_id: str) -> list[LivePassRateRecord]:
        _text(cluster_id, "cluster_id")
        statement = (
            select(LivePassRateRow)
            .where(LivePassRateRow.cluster_id == cluster_id)
            .order_by(LivePassRateRow.id)
        )
        async with _transaction(self.sessions) as session:
            rows = (await session.scalars(statement)).all()
            return [_live_record(row) for row in rows]


class SQLAlchemyJobStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def put(self, record: JobRecord) -> JobRecord:
        _job_valid(record)
        config = _bounded_json(record.config_json, "config_json")
        summary = _bounded_json(record.summary_json, "summary_json")
        async with _transaction(self.sessions) as session:
            row = await session.get(JobRow, record.job_id)
            if row is None:
                row = JobRow(job_id=record.job_id)
                session.add(row)
            row.template = record.template
            row.status = record.status
            row.config_json = config
            row.created_at = _utc(record.created_at)
            row.s3_prefix = record.s3_prefix
            row.summary_json = summary
            await session.flush()
            return _job_record(row)

    async def get(self, job_id: str) -> JobRecord | None:
        _text(job_id, "job_id")
        async with _transaction(self.sessions) as session:
            row = await session.get(JobRow, job_id)
            return _job_record(row) if row else None

    async def list(self) -> list[JobRecord]:
        async with _transaction(self.sessions) as session:
            rows = (await session.scalars(select(JobRow).order_by(JobRow.created_at))).all()
            return [_job_record(row) for row in rows]


class SQLAlchemyAgentDecisionStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def put(self, record: AgentDecisionRecord) -> AgentDecisionRecord:
        _decision_valid(record)
        async with _transaction(self.sessions) as session:
            existing = await session.get(AgentDecisionRow, record.id)
            if existing is not None:
                saved = _decision_record(existing)
                if saved != _normalized_decision(record):
                    raise ValueError("decision id already exists with different content")
                return saved
            row = AgentDecisionRow(
                id=record.id,
                cluster_id=record.cluster_id,
                decision=record.decision,
                rationale=record.rationale,
                confidence=record.confidence,
                config_json=_bounded_json(record.config_json, "config_json"),
                trace_s3_key=record.trace_s3_key,
                model_name=record.model_name,
                created_at=_utc(record.created_at),
                evidence_fingerprint=record.evidence_fingerprint,
                run_status=record.run_status,
                trace_id=record.trace_id,
                provider=record.provider,
                tokens_in=record.tokens_in,
                tokens_out=record.tokens_out,
                summary_json=_bounded_json(record.summary_json, "summary_json"),
            )
            session.add(row)
            await session.flush()
            return _decision_record(row)

    async def get(self, decision_id: str) -> AgentDecisionRecord | None:
        _text(decision_id, "decision_id")
        async with _transaction(self.sessions) as session:
            row = await session.get(AgentDecisionRow, decision_id)
            return _decision_record(row) if row else None

    async def latest_for_cluster(
        self, cluster_id: str, evidence_fingerprint: str | None = None
    ) -> AgentDecisionRecord | None:
        _text(cluster_id, "cluster_id")
        statement = select(AgentDecisionRow).where(
            AgentDecisionRow.cluster_id == cluster_id,
            AgentDecisionRow.run_status == "completed",
        )
        if evidence_fingerprint is not None:
            statement = statement.where(
                AgentDecisionRow.evidence_fingerprint == evidence_fingerprint
            )
        statement = statement.order_by(
            AgentDecisionRow.created_at.desc(), AgentDecisionRow.id.desc()
        ).limit(1)
        async with _transaction(self.sessions) as session:
            row = await session.scalar(statement)
            return _decision_record(row) if row else None


class SQLAlchemyCredentialStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def put(self, record: CredentialRecord) -> CredentialRecord:
        _credential_valid(record)
        async with _transaction(self.sessions) as session:
            row = await session.get(ProviderCredentialRow, record.id)
            if row is None:
                query = select(ProviderCredentialRow).where(
                    ProviderCredentialRow.user_id == record.user_id,
                    ProviderCredentialRow.provider == record.provider,
                )
                row = await session.scalar(query)
            if row is None:
                row = ProviderCredentialRow(id=record.id)
                session.add(row)
            row.user_id = record.user_id
            row.provider = record.provider
            row.encrypted_key = record.encrypted_key
            row.created_at = _utc(record.created_at)
            await session.flush()
            return _credential_record(row)

    async def get(self, credential_id: str) -> CredentialRecord | None:
        _text(credential_id, "credential_id")
        async with _transaction(self.sessions) as session:
            row = await session.get(ProviderCredentialRow, credential_id)
            return _credential_record(row) if row else None

    async def get_for_user_provider(
        self, user_id: str, provider: str
    ) -> CredentialRecord | None:
        _text(user_id, "user_id")
        _text(provider, "provider")
        statement = select(ProviderCredentialRow).where(
            ProviderCredentialRow.user_id == user_id,
            ProviderCredentialRow.provider == provider,
        )
        async with _transaction(self.sessions) as session:
            row = await session.scalar(statement)
            return _credential_record(row) if row else None


class SQLAlchemyApprovalStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def put(self, record: ApprovalRecord) -> ApprovalRecord:
        _approval_valid(record)
        async with _transaction(self.sessions) as session:
            query = select(ApprovalRow).where(ApprovalRow.decision_id == record.decision_id)
            existing = await session.scalar(query)
            if existing is not None:
                return _approval_record(existing)
            row = ApprovalRow(
                id=record.id,
                decision_id=record.decision_id,
                approved_by=record.approved_by,
                approved_at=_utc(record.approved_at),
                provision_handle=record.provision_handle,
            )
            session.add(row)
            await session.flush()
            return _approval_record(row)

    async def get_by_decision(self, decision_id: str) -> ApprovalRecord | None:
        _text(decision_id, "decision_id")
        statement = select(ApprovalRow).where(ApprovalRow.decision_id == decision_id)
        async with _transaction(self.sessions) as session:
            row = await session.scalar(statement)
            return _approval_record(row) if row else None


class SQLAlchemyProvisionAuditStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def append(self, record: ProvisionEventRecord) -> ProvisionEventRecord:
        _provision_event_valid(record)
        async with _transaction(self.sessions) as session:
            existing = await session.get(ProvisionEventRow, record.id)
            if existing is not None:
                saved = _provision_event_record(existing)
                if saved != _normalized_provision_event(record):
                    raise ValueError("provision event id already exists with different content")
                return saved
            row = ProvisionEventRow(
                id=record.id,
                approval_id=record.approval_id,
                job_id=record.job_id,
                provider=record.provider,
                action=record.action,
                status=record.status,
                actor=record.actor,
                occurred_at=_utc(record.occurred_at),
                detail_json=_bounded_json(record.detail_json, "detail_json"),
            )
            session.add(row)
            await session.flush()
            return _provision_event_record(row)

    async def list_for_approval(self, approval_id: str) -> list[ProvisionEventRecord]:
        _text(approval_id, "approval_id")
        statement = (
            select(ProvisionEventRow)
            .where(ProvisionEventRow.approval_id == approval_id)
            .order_by(ProvisionEventRow.occurred_at, ProvisionEventRow.id)
        )
        async with _transaction(self.sessions) as session:
            rows = (await session.scalars(statement)).all()
            return [_provision_event_record(row) for row in rows]


@dataclass(frozen=True)
class RepositoryBundle:
    traffic: TrafficStore
    clusters: ClusterStore
    routing: RoutingStore
    live_pass_rate: LivePassRateStore
    jobs: JobStore
    agent_decisions: AgentDecisionStore
    credentials: CredentialStore
    approvals: ApprovalStore
    provision_audit: ProvisionAuditStore


def create_repositories(runtime: DatabaseRuntime) -> RepositoryBundle:
    sessions = runtime.sessions
    return RepositoryBundle(
        traffic=SQLAlchemyTrafficStore(sessions),
        clusters=SQLAlchemyClusterStore(sessions),
        routing=SQLAlchemyRoutingStore(sessions),
        live_pass_rate=SQLAlchemyLivePassRateStore(sessions),
        jobs=SQLAlchemyJobStore(sessions),
        agent_decisions=SQLAlchemyAgentDecisionStore(sessions),
        credentials=SQLAlchemyCredentialStore(sessions),
        approvals=SQLAlchemyApprovalStore(sessions),
        provision_audit=SQLAlchemyProvisionAuditStore(sessions),
    )


def _traffic_record(row: TrafficRequestRow) -> TrafficRequestRecord:
    return TrafficRequestRecord(
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


def _cluster_record(row: ClusterRow) -> ClusterRecord:
    return ClusterRecord(
        cluster_id=row.cluster_id,
        name=row.name,
        status=row.status,
        monthly_calls=row.monthly_calls,
        monthly_cost_usd=row.monthly_cost_usd,
        trainable=row.trainable,
        job_id=row.job_id,
        analyzer_summary=row.analyzer_summary,
        approved_sample_source=row.approved_sample_source,
        updated_at=_utc(row.updated_at),
    )


def _routing_record(row: RoutingStateRow) -> RoutingRecord:
    return RoutingRecord(
        cluster_id=row.cluster_id,
        enabled=row.enabled,
        canary_percent=row.canary_percent,
        target_model=row.target_model,
        updated_at=_utc(row.updated_at),
    )


def _live_record(row: LivePassRateRow) -> LivePassRateRecord:
    return LivePassRateRecord(
        id=row.id,
        cluster_id=row.cluster_id,
        ts=_utc(row.ts),
        pass_rate=row.pass_rate,
    )


def _job_record(row: JobRow) -> JobRecord:
    return JobRecord(
        job_id=row.job_id,
        template=row.template,
        status=row.status,
        config_json=row.config_json,
        created_at=_utc(row.created_at),
        s3_prefix=row.s3_prefix,
        summary_json=row.summary_json,
    )


def _decision_record(row: AgentDecisionRow) -> AgentDecisionRecord:
    return AgentDecisionRecord(
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


def _credential_record(row: ProviderCredentialRow) -> CredentialRecord:
    return CredentialRecord(
        id=row.id,
        user_id=row.user_id,
        provider=row.provider,
        encrypted_key=row.encrypted_key,
        created_at=_utc(row.created_at),
    )


def _approval_record(row: ApprovalRow) -> ApprovalRecord:
    return ApprovalRecord(
        id=row.id,
        decision_id=row.decision_id,
        approved_by=row.approved_by,
        approved_at=_utc(row.approved_at),
        provision_handle=row.provision_handle,
    )


def _provision_event_record(row: ProvisionEventRow) -> ProvisionEventRecord:
    return ProvisionEventRecord(
        id=row.id,
        approval_id=row.approval_id,
        job_id=row.job_id,
        provider=row.provider,
        action=row.action,
        status=row.status,
        actor=row.actor,
        occurred_at=_utc(row.occurred_at),
        detail_json=row.detail_json,
    )


def _normalized_decision(record: AgentDecisionRecord) -> AgentDecisionRecord:
    return AgentDecisionRecord(**{**record.__dict__, "created_at": _utc(record.created_at)})


def _normalized_provision_event(record: ProvisionEventRecord) -> ProvisionEventRecord:
    return ProvisionEventRecord(**{**record.__dict__, "occurred_at": _utc(record.occurred_at)})


def _traffic_valid(record: TrafficRequestRecord) -> None:
    _timestamp(record.ts, "ts")
    _text(record.prompt_hash, "prompt_hash")
    _text(record.model, "model")
    _text(record.route_taken, "route_taken")
    _nonnegative_int(record.tokens_in, "tokens_in")
    _nonnegative_int(record.tokens_out, "tokens_out")
    _nonnegative_number(record.latency_ms, "latency_ms")
    _nonnegative_number(record.cost_usd, "cost_usd")


def _cluster_valid(record: ClusterRecord) -> None:
    _text(record.cluster_id, "cluster_id")
    _text(record.name, "name")
    _text(record.status, "status")
    _nonnegative_int(record.monthly_calls, "monthly_calls")
    _nonnegative_number(record.monthly_cost_usd, "monthly_cost_usd")
    if not isinstance(record.trainable, bool):
        raise ValueError("trainable must be a bool")
    _timestamp(record.updated_at, "updated_at")


def _routing_valid(record: RoutingRecord) -> None:
    _text(record.cluster_id, "cluster_id")
    if not isinstance(record.enabled, bool):
        raise ValueError("enabled must be a bool")
    if isinstance(record.canary_percent, bool) or not isinstance(record.canary_percent, int):
        raise ValueError("canary_percent must be an integer")
    if not 0 <= record.canary_percent <= 100:
        raise ValueError("canary_percent must be in [0, 100]")
    _text(record.target_model, "target_model")
    _timestamp(record.updated_at, "updated_at")


def _guardian_valid(record: GuardianScoreRecord) -> None:
    _text(record.cluster_id, "cluster_id")
    _timestamp(record.ts, "ts")
    _unit_number(record.score, "score")


def _job_valid(record: JobRecord) -> None:
    _text(record.job_id, "job_id")
    _text(record.template, "template")
    _text(record.status, "status")
    _timestamp(record.created_at, "created_at")


def _decision_valid(record: AgentDecisionRecord) -> None:
    _text(record.id, "id")
    _text(record.cluster_id, "cluster_id")
    _text(record.model_name, "model_name")
    _text(record.run_status, "run_status")
    _timestamp(record.created_at, "created_at")
    _nonnegative_int(record.tokens_in, "tokens_in")
    _nonnegative_int(record.tokens_out, "tokens_out")
    if record.confidence is not None:
        _unit_number(record.confidence, "confidence")


def _credential_valid(record: CredentialRecord) -> None:
    _text(record.id, "id")
    _text(record.user_id, "user_id")
    _text(record.provider, "provider")
    if not isinstance(record.encrypted_key, bytes) or not record.encrypted_key:
        raise ValueError("encrypted_key must be non-empty bytes")
    _timestamp(record.created_at, "created_at")


def _approval_valid(record: ApprovalRecord) -> None:
    _text(record.id, "id")
    _text(record.decision_id, "decision_id")
    _text(record.approved_by, "approved_by")
    _timestamp(record.approved_at, "approved_at")


def _provision_event_valid(record: ProvisionEventRecord) -> None:
    for name in ("id", "approval_id", "provider", "action", "status", "actor"):
        _text(getattr(record, name), name)
    _timestamp(record.occurred_at, "occurred_at")
    _bounded_json(record.detail_json, "detail_json")


def _bounded_json(value, name: str):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be JSON serializable") from None
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError(f"{name} exceeds {MAX_JSON_BYTES} bytes")
    return value


def _timestamp(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _nonnegative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _nonnegative_number(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a non-negative finite number")
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"{name} must be a non-negative finite number")


def _unit_number(value: float, name: str) -> None:
    _nonnegative_number(value, name)
    if value > 1:
        raise ValueError(f"{name} must be in [0, 1]")


def _limit(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10_000:
        raise ValueError("limit must be in [1, 10000]")
