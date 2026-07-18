"""Backend-neutral values accepted and returned by repositories."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class TrafficRequestRecord:
    ts: datetime
    prompt_hash: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cost_usd: float
    route_taken: str
    id: int | None = None


@dataclass(frozen=True)
class TrafficAggregateRecord:
    prompt_hash: str
    request_count: int
    total_tokens: int
    total_cost_usd: float


@dataclass(frozen=True)
class ClusterRecord:
    cluster_id: str
    name: str
    status: str
    monthly_calls: int
    monthly_cost_usd: float
    trainable: bool
    updated_at: datetime
    job_id: str | None = None
    analyzer_summary: JsonObject | None = None


@dataclass(frozen=True)
class RoutingRecord:
    cluster_id: str
    enabled: bool
    canary_percent: int
    target_model: str
    updated_at: datetime


@dataclass(frozen=True)
class GuardianScoreRecord:
    cluster_id: str
    ts: datetime
    score: float
    id: int | None = None


@dataclass(frozen=True)
class LivePassRateRecord:
    cluster_id: str
    ts: datetime
    pass_rate: float
    id: int | None = None


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    template: str
    status: str
    config_json: JsonObject
    created_at: datetime
    s3_prefix: str | None = None
    summary_json: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDecisionRecord:
    id: str
    cluster_id: str
    decision: str | None
    rationale: str | None
    confidence: float | None
    config_json: JsonObject | None
    trace_s3_key: str | None
    model_name: str
    created_at: datetime
    evidence_fingerprint: str | None = None
    run_status: str = "completed"
    trace_id: str | None = None
    provider: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    summary_json: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class CredentialRecord:
    id: str
    user_id: str
    provider: str
    encrypted_key: bytes = field(repr=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    decision_id: str
    approved_by: str
    approved_at: datetime
    provision_handle: str | None = None


@dataclass(frozen=True)
class ProvisionEventRecord:
    id: str
    approval_id: str
    job_id: str | None
    provider: str
    action: str
    status: str
    actor: str
    occurred_at: datetime
    detail_json: JsonObject = field(default_factory=dict)
