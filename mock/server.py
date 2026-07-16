"""Contract-shaped mock data for the VerifierForge frontend."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ``python mock/server.py`` puts mock/ (rather than the repo root) on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.contracts import (
    Arena,
    ArenaSample,
    Cluster,
    ClusterStatus,
    Job,
    JobStatus,
    LivePassRate,
    LivePassRatePoint,
    Metrics,
    RoutingState,
)


app = FastAPI(title="VerifierForge Mock API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _job(
    *,
    job_id: str,
    template: str,
    status: str,
    metrics: dict[str, list[int] | list[float]],
    report: dict | None = None,
    endpoint: dict[str, str] | None = None,
    control: dict[str, list[float]] | None = None,
) -> Job:
    return Job(
        job_id=job_id,
        template=template,
        status=status,
        model="Qwen/Qwen2.5-1.5B-Instruct",
        created_at="2026-07-14T03:00:00Z",
        metrics=Metrics.model_validate(metrics),
        control=control or {"pass_at_1": [0.18, 0.19, 0.2]},
        report=report,
        endpoint=endpoint,
    )


_ARENA_SAMPLES = [
    ArenaSample(
        prompt="List open invoices over $10k for Acme.",
        baseline_output="SELECT * FROM invoices WHERE company = 'Acme';",
        tuned_output=(
            "SELECT invoice_id, amount FROM invoices "
            "WHERE customer = 'Acme' AND amount > 10000 AND status = 'open';"
        ),
        baseline_score=0.4,
        tuned_score=0.95,
    ),
    ArenaSample(
        prompt="How many support tickets were escalated last week?",
        baseline_output="SELECT count(*) FROM tickets WHERE escalated = 1;",
        tuned_output=(
            "SELECT COUNT(*) AS escalated_count FROM tickets "
            "WHERE escalated = TRUE AND created_at >= DATE('now', '-7 day');"
        ),
        baseline_score=0.55,
        tuned_score=0.92,
    ),
    ArenaSample(
        prompt="Top 5 SKUs by revenue in Q1.",
        baseline_output="SELECT sku FROM sales ORDER BY revenue;",
        tuned_output=(
            "SELECT sku, SUM(revenue) AS revenue FROM sales "
            "WHERE quarter = 'Q1' GROUP BY sku ORDER BY revenue DESC LIMIT 5;"
        ),
        baseline_score=0.35,
        tuned_score=0.9,
    ),
    ArenaSample(
        prompt="Average handle time for billing tickets.",
        baseline_output="SELECT AVG(time) FROM tickets;",
        tuned_output=(
            "SELECT AVG(handle_minutes) AS avg_handle_time FROM tickets "
            "WHERE category = 'billing';"
        ),
        baseline_score=0.5,
        tuned_score=0.88,
    ),
    ArenaSample(
        prompt="Employees hired in 2025 in Engineering.",
        baseline_output="SELECT * FROM employees WHERE year = 2025;",
        tuned_output=(
            "SELECT employee_id, name FROM employees "
            "WHERE hire_year = 2025 AND department = 'Engineering';"
        ),
        baseline_score=0.45,
        tuned_score=0.93,
    ),
    ArenaSample(
        prompt="Refunds issued yesterday above $50.",
        baseline_output="SELECT * FROM refunds WHERE amount > 50;",
        tuned_output=(
            "SELECT refund_id, amount FROM refunds "
            "WHERE amount > 50 AND refunded_on = DATE('now', '-1 day');"
        ),
        baseline_score=0.42,
        tuned_score=0.91,
    ),
    ArenaSample(
        prompt="Active subscriptions churned in March.",
        baseline_output="SELECT * FROM subs WHERE churned;",
        tuned_output=(
            "SELECT subscription_id FROM subscriptions "
            "WHERE status = 'churned' AND churn_month = '2026-03';"
        ),
        baseline_score=0.38,
        tuned_score=0.87,
    ),
]


JOBS: list[Job] = [
    _job(
        job_id="nl2sql-queued",
        template="nl2sql",
        status="queued",
        metrics={
            "steps": [],
            "reward_mean": [],
            "pass_at_1": [],
            "entropy": [],
        },
        control={"pass_at_1": []},
    ),
    _job(
        job_id="nl2sql-running",
        template="nl2sql",
        status="running",
        metrics={
            "steps": [1, 20, 40],
            "reward_mean": [0.22, 0.4, 0.57],
            "pass_at_1": [0.14, 0.31, 0.48],
            "entropy": [1.4, 1.08, 0.83],
        },
    ),
    _job(
        job_id="nl2sql-gain",
        template="nl2sql",
        status="done",
        metrics={
            "steps": [1, 20, 40, 60],
            "reward_mean": [0.21, 0.45, 0.68, 0.82],
            "pass_at_1": [0.16, 0.36, 0.62, 0.76],
            "entropy": [1.45, 1.12, 0.87, 0.71],
        },
        report={
            "baseline_pass_at_1": 0.16,
            "final_pass_at_1": 0.76,
            "control_final_pass_at_1": 0.2,
            "verdict": "real_gain",
            "narrative": "Verifier performance improved well beyond the control run.",
            "projected_monthly_savings_usd": 4300.0,
            "arena": Arena(win_rate=0.95, samples=_ARENA_SAMPLES).model_dump(
                mode="json"
            ),
        },
        endpoint={
            "base_url": "http://localhost:8080/v1",
            "model_name": "vf-nl2sql-gain",
        },
    ),
    _job(
        job_id="nl2sql-failed",
        template="nl2sql",
        status="failed",
        metrics={
            "steps": [1, 10],
            "reward_mean": [0.2, 0.18],
            "pass_at_1": [0.12, 0.1],
            "entropy": [1.4, 1.35],
        },
        report={
            "baseline_pass_at_1": 0.12,
            "final_pass_at_1": 0.1,
            "control_final_pass_at_1": 0.12,
            "verdict": "collapsed",
            "narrative": "Worker exited before a usable checkpoint was published.",
        },
    ),
]


def _live_pass_rate(cluster_id: str) -> LivePassRate:
    base = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    rates = [0.88, 0.89, 0.895, 0.89, 0.892, 0.888, 0.891]
    return LivePassRate(
        cluster_id=cluster_id,
        points=[
            LivePassRatePoint(
                timestamp=base.replace(hour=12 + index),
                pass_rate=rate,
            )
            for index, rate in enumerate(rates)
        ],
    )


CLUSTERS: list[Cluster] = [
    Cluster(
        cluster_id="support-ticket-extraction",
        name="Support ticket extraction",
        monthly_calls=240_000,
        monthly_cost_usd=4800.0,
        trainable=True,
        status=ClusterStatus.LIVE,
        job_id="nl2sql-gain",
        routing=RoutingState(
            cluster_id="support-ticket-extraction",
            enabled=True,
            canary_percent=100,
            target_model="vf-nl2sql-gain",
        ),
        live_pass_rate=_live_pass_rate("support-ticket-extraction"),
    ),
    Cluster(
        cluster_id="invoice-field-extraction",
        name="Invoice field extraction",
        monthly_calls=180_000,
        monthly_cost_usd=6000.0,
        trainable=True,
        status=ClusterStatus.DISCOVERED,
        job_id=None,
    ),
    Cluster(
        cluster_id="data-pull-sql",
        name="Data pull SQL",
        monthly_calls=95_000,
        monthly_cost_usd=5500.0,
        trainable=True,
        status=ClusterStatus.DISCOVERED,
        job_id=None,
    ),
]


@app.get("/jobs", response_model=list[Job])
def list_jobs() -> list[Job]:
    return JOBS


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    for job in JOBS:
        if job.job_id == job_id:
            return job
    raise HTTPException(status_code=404, detail="Job not found")


@app.get("/jobs/{job_id}/metrics", response_model=Metrics)
def get_metrics(job_id: str) -> Metrics:
    return get_job(job_id).metrics


@app.post("/jobs", response_model=Job, status_code=201)
def create_job() -> Job:
    """Fake-create a queued job in memory; never writes ``runs/``."""
    job = _job(
        job_id=f"mock-job-{uuid4().hex[:8]}",
        template="nl2sql",
        status=JobStatus.QUEUED.value,
        metrics={
            "steps": [],
            "reward_mean": [],
            "pass_at_1": [],
            "entropy": [],
        },
        control={"pass_at_1": []},
    )
    JOBS.append(job)
    return job


@app.get("/clusters", response_model=list[Cluster])
def list_clusters() -> list[Cluster]:
    return CLUSTERS


@app.get("/clusters/{cluster_id}", response_model=Cluster)
def get_cluster(cluster_id: str) -> Cluster:
    for cluster in CLUSTERS:
        if cluster.cluster_id == cluster_id:
            return cluster
    raise HTTPException(status_code=404, detail="Cluster not found")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
