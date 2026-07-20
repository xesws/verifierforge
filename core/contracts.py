"""Pydantic models shared by the trainer, API, and control scripts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from core.agent_contracts import AgentDecision


class JobStatus(str, Enum):
    """The lifecycle states a training job can report."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    EARLY_STOPPED = "early_stopped"


class ReportVerdict(str, Enum):
    """Interpretation of a completed run's evaluation."""

    REAL_GAIN = "real_gain"
    SUSPECT_FORMATTING = "suspect_formatting"
    COLLAPSED = "collapsed"


class ClusterStatus(str, Enum):
    """Lifecycle of a discovered task cluster in the product UI."""

    DISCOVERED = "discovered"
    FORGING = "forging"
    LIVE = "live"


class ApprovedSampleSourceKind(str, Enum):
    """Storage kinds accepted at the governed Agent sample boundary."""

    REPOSITORY_JSONL = "repository_jsonl"


class Metrics(BaseModel):
    steps: list[int]
    reward_mean: list[float]
    pass_at_1: list[float]
    entropy: list[float]


class Control(BaseModel):
    pass_at_1: list[float]


class ArenaSample(BaseModel):
    prompt: str
    baseline_output: str
    tuned_output: str
    baseline_score: float
    tuned_score: float


class Arena(BaseModel):
    win_rate: float
    samples: list[ArenaSample]


class SavingsProjection(BaseModel):
    """Auditable recurring-cost estimate shown with a completed report."""

    current_monthly_cost_usd: float = Field(ge=0)
    projected_monthly_cost_usd: float = Field(ge=0)
    projected_monthly_savings_usd: float
    formula: str
    assumptions: list[str]


class ReportProjectionSource(BaseModel):
    """One authoritative input used to derive a report projection."""

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReportProjectionProvenance(BaseModel):
    """Identity of a reproducible, derived report presentation."""

    artifact_version: str
    s3_prefix: str | None = None
    generated_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: list[ReportProjectionSource]


class Report(BaseModel):
    baseline_pass_at_1: float
    final_pass_at_1: float
    control_final_pass_at_1: float
    verdict: ReportVerdict
    narrative: str
    projected_monthly_savings_usd: float | None = None
    arena: Arena | None = None
    savings_projection: SavingsProjection | None = None
    provenance: ReportProjectionProvenance | None = None


class Endpoint(BaseModel):
    base_url: str
    model_name: str


class RoutingState(BaseModel):
    cluster_id: str
    enabled: bool
    canary_percent: int = Field(ge=0, le=100)
    target_model: str


class LivePassRatePoint(BaseModel):
    """One online guardian sample.

    Uses ``pass_rate`` deliberately — live rolling score is not the same as
    offline ``pass_at_1`` k-sample evaluation.
    """

    timestamp: datetime
    pass_rate: float


class LivePassRate(BaseModel):
    cluster_id: str
    points: list[LivePassRatePoint]


class ApprovedSampleSource(BaseModel):
    """Identity and approval metadata for samples; never the sample bodies."""

    kind: ApprovedSampleSourceKind
    uri: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=1)
    approved_by: str = Field(min_length=1, max_length=128)
    approved_at: datetime


class Cluster(BaseModel):
    cluster_id: str
    name: str
    monthly_calls: int
    monthly_cost_usd: float
    trainable: bool
    status: ClusterStatus
    job_id: str | None = None
    routing: RoutingState | None = None
    live_pass_rate: LivePassRate | None = None
    approved_sample_source: ApprovedSampleSource | None = None
    analyzer_decision: AgentDecision | None = None


class JobCreateRequest(BaseModel):
    """Queued metadata submission; execution still requires Start Forge."""

    model_config = ConfigDict(extra="forbid")

    template: str = Field(default="nl2sql", min_length=1, max_length=128)
    model: str = Field(
        default="Qwen/Qwen2.5-1.5B-Instruct",
        min_length=1,
        max_length=512,
    )


class Job(BaseModel):
    job_id: str
    template: str
    status: JobStatus
    model: str
    created_at: datetime
    metrics: Metrics
    control: Control
    report: Report | None = None
    endpoint: Endpoint | None = None


class MetricRecord(BaseModel):
    """One append-only entry in a job's metrics.jsonl file."""

    job_id: str
    step: int
    reward_mean: float
    pass_at_1: float
    entropy: float
    timestamp: datetime
