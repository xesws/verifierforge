"""SQLAlchemy schema shared by SQLite and Supabase Postgres."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TrafficRequestRow(Base):
    __tablename__ = "traffic_requests"
    __table_args__ = (
        CheckConstraint("tokens_in >= 0", name="tokens_in_nonnegative"),
        CheckConstraint("tokens_out >= 0", name="tokens_out_nonnegative"),
        CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),
        CheckConstraint("cost_usd >= 0", name="cost_nonnegative"),
        Index("ix_traffic_requests_prompt_ts", "prompt_hash", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    route_taken: Mapped[str] = mapped_column(String(64), nullable=False)


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_created_at", "created_at"),)

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    template: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    s3_prefix: Mapped[str | None] = mapped_column(String(1024))
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ClusterRow(Base):
    __tablename__ = "clusters"
    __table_args__ = (
        CheckConstraint("monthly_calls >= 0", name="monthly_calls_nonnegative"),
        CheckConstraint("monthly_cost_usd >= 0", name="monthly_cost_nonnegative"),
        Index("ix_clusters_status", "status"),
    )

    cluster_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    monthly_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    trainable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(128))
    analyzer_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    approved_sample_source: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RoutingStateRow(Base):
    __tablename__ = "routing_state"
    __table_args__ = (
        CheckConstraint(
            "canary_percent >= 0 AND canary_percent <= 100",
            name="canary_percent_range",
        ),
    )

    cluster_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("clusters.cluster_id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    canary_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    target_model: Mapped[str] = mapped_column(String(1024), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuardianScoreRow(Base):
    __tablename__ = "guardian_scores"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        Index("ix_guardian_scores_cluster_ts", "cluster_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("clusters.cluster_id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)


class LivePassRateRow(Base):
    __tablename__ = "live_pass_rate"
    __table_args__ = (
        CheckConstraint("pass_rate >= 0 AND pass_rate <= 1", name="pass_rate_range"),
        Index("ix_live_pass_rate_cluster_ts", "cluster_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("clusters.cluster_id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pass_rate: Mapped[float] = mapped_column(Float, nullable=False)


class AgentDecisionRow(Base):
    __tablename__ = "agent_decisions"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        CheckConstraint("tokens_in >= 0", name="tokens_in_nonnegative"),
        CheckConstraint("tokens_out >= 0", name="tokens_out_nonnegative"),
        Index("ix_agent_decisions_cluster_created", "cluster_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Audit traces may refer to evaluator/adversarial labels that are not
    # product Discover clusters, so this deliberately is not a clusters FK.
    cluster_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(32))
    rationale: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    trace_s3_key: Mapped[str | None] = mapped_column(String(1024))
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(64))
    run_status: Mapped[str] = mapped_column(String(32), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ProviderCredentialRow(Base):
    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_provider_credentials_user_provider"),
        Index("ix_provider_credentials_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalRow(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_approvals_decision_id"),
        Index("ix_approvals_approved_at", "approved_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    decision_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("agent_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provision_handle: Mapped[str | None] = mapped_column(String(512))


class ProvisionEventRow(Base):
    __tablename__ = "provision_events"
    __table_args__ = (
        Index("ix_provision_events_approval_time", "approval_id", "occurred_at"),
        Index("ix_provision_events_job_time", "job_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    approval_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("approvals.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ServingEndpointRow(Base):
    __tablename__ = "serving_endpoints"
    __table_args__ = (
        CheckConstraint(
            "state IN ('cold','provisioning','loading','ready','draining')",
            name="state_allowed",
        ),
        CheckConstraint(
            "(state IN ('provisioning','loading','ready','draining') AND active_slot = 1) "
            "OR (state = 'cold' AND active_slot IS NULL)",
            name="active_slot_matches_state",
        ),
        CheckConstraint("cost_accrued_usd >= 0", name="cost_nonnegative"),
        CheckConstraint(
            "hourly_price_usd IS NULL OR hourly_price_usd >= 0",
            name="hourly_price_nonnegative",
        ),
        UniqueConstraint("active_slot", name="uq_serving_endpoints_active_slot"),
        Index("ix_serving_endpoints_state_updated", "state", "updated_at"),
    )

    model_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    url: Mapped[str | None] = mapped_column(String(2048))
    api_key_ref: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("provider_credentials.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))
    gpu_model: Mapped[str | None] = mapped_column(String(255))
    hourly_price_usd: Mapped[float | None] = mapped_column(Float)
    cost_accrued_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cold_start_seconds: Mapped[float | None] = mapped_column(Float)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active_slot: Mapped[int | None] = mapped_column(Integer)


class ServingEventRow(Base):
    __tablename__ = "serving_events"
    __table_args__ = (
        Index("ix_serving_events_session_time", "session_id", "occurred_at"),
        Index("ix_serving_events_model_time", "model_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("serving_endpoints.model_id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
