"""Strict contracts for the Forge Agent decision and audit boundary."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
P2_BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
ALLOWED_BASE_MODELS = frozenset({BASE_MODEL, P2_BASE_MODEL})
MAX_TRAINING_BUDGET_USD = 100.0


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentDecisionType(str, Enum):
    FORGE = "forge"
    SKIP = "skip"
    NEED_MORE_DATA = "need_more_data"


class ProviderPreference(str, Enum):
    RUNPOD = "runpod"
    NEBIUS = "nebius"
    AUTO = "auto"


class AgentRunStatus(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    TRACE_PERSIST_FAILED = "trace_persist_failed"


class TrainingConfig(AgentModel):
    base_model: str = BASE_MODEL
    steps: int = Field(default=400, ge=50, le=500)
    k: int = Field(default=8, ge=1, le=8)
    checkpoint_interval: int = Field(default=50, ge=10, le=100)
    budget_usd_cap: float = Field(gt=0, le=MAX_TRAINING_BUDGET_USD)
    provider_pref: ProviderPreference = ProviderPreference.AUTO

    @model_validator(mode="after")
    def validate_policy(self) -> "TrainingConfig":
        if self.base_model not in ALLOWED_BASE_MODELS:
            allowed = ", ".join(sorted(ALLOWED_BASE_MODELS))
            raise ValueError(f"base_model must be one of: {allowed}")
        if self.checkpoint_interval > self.steps:
            raise ValueError("checkpoint_interval must not exceed steps")
        if not math.isfinite(self.budget_usd_cap):
            raise ValueError("budget_usd_cap must be finite")
        return self


class AgentDecision(AgentModel):
    decision: AgentDecisionType
    rationale: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    config: TrainingConfig | None = None

    @model_validator(mode="after")
    def validate_config_coupling(self) -> "AgentDecision":
        if not math.isfinite(self.confidence):
            raise ValueError("confidence must be finite")
        if self.decision == AgentDecisionType.FORGE and self.config is None:
            raise ValueError("forge decisions require config")
        if self.decision != AgentDecisionType.FORGE and self.config is not None:
            raise ValueError("only forge decisions may include config")
        return self


class AnalyzeTrafficInput(AgentModel):
    cluster_id: str = Field(min_length=1, max_length=128)


class AnalyzeTrafficOutput(AgentModel):
    cluster_id: str
    analysis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_sufficient: bool
    request_count: int = Field(ge=0)
    monthly_calls: int = Field(ge=0)
    monthly_cost_usd: float = Field(ge=0)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    growth_rate: float
    observed_from: datetime | None = None
    observed_to: datetime | None = None


class InspectSamplesInput(AgentModel):
    cluster_id: str = Field(min_length=1, max_length=128)
    analysis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    n: int = Field(default=5, ge=1, le=20)


class RedactedSample(AgentModel):
    sample_id: str
    request_excerpt: str
    response_excerpt: str


class InspectSamplesOutput(AgentModel):
    cluster_id: str
    analysis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_set_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_sufficient: bool
    reason: str
    samples: list[RedactedSample]


class EstimateEconomicsInput(AgentModel):
    cluster_id: str = Field(min_length=1, max_length=128)
    analysis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_model: str = BASE_MODEL


class EstimateEconomicsOutput(AgentModel):
    cluster_id: str
    analysis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_sufficient: bool
    training_cost_usd: float = Field(ge=0)
    current_monthly_cost_usd: float = Field(ge=0)
    projected_monthly_cost_usd: float = Field(ge=0)
    projected_monthly_savings_usd: float = Field(ge=0)
    payback_months: float | None = Field(default=None, ge=0)
    formula: str
    assumptions: list[str]


class CheckVerifiabilityInput(AgentModel):
    cluster_id: str = Field(min_length=1, max_length=128)
    analysis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_set_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckVerifiabilityOutput(AgentModel):
    cluster_id: str
    analysis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_set_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_sufficient: bool
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]


class AgentToolCallTrace(AgentModel):
    tool_name: str
    arguments: dict[str, Any]
    output: dict[str, Any] | None = None
    started_at: datetime
    finished_at: datetime
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    error: str | None = None


class AgentTrace(AgentModel):
    trace_id: str
    cluster_id: str
    provider: str
    model: str
    started_at: datetime
    finished_at: datetime
    tool_calls: list[AgentToolCallTrace]
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    status: AgentRunStatus
    guard_events: list[str] = Field(default_factory=list)
    terminal_decision: AgentDecision | None = None


class AgentDecisionSummary(AgentModel):
    decision_id: str
    trace_id: str
    cluster_id: str
    evidence_fingerprint: str | None = None
    run_status: AgentRunStatus
    decision: AgentDecision | None = None
    trace_s3_key: str | None = None
    provider: str
    model: str
    created_at: datetime
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)


class AgentAnalysisResponse(AgentModel):
    decision_id: str
    cluster_id: str
    decision: AgentDecision
    cached: bool
    created_at: datetime


class ApprovalRequest(AgentModel):
    approved_by: str = Field(min_length=1, max_length=128)


class ApprovalRecord(AgentModel):
    approval_id: str
    decision_id: str
    approved_by: str
    approved_at: datetime
