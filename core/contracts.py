"""Pydantic models shared by the trainer, API, and control scripts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


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


class Metrics(BaseModel):
    steps: list[int]
    reward_mean: list[float]
    pass_at_1: list[float]
    entropy: list[float]


class Control(BaseModel):
    pass_at_1: list[float]


class Report(BaseModel):
    baseline_pass_at_1: float
    final_pass_at_1: float
    control_final_pass_at_1: float
    verdict: ReportVerdict
    narrative: str


class Endpoint(BaseModel):
    base_url: str
    model_name: str


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
