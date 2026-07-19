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
    ApprovedSampleSource,
    Arena,
    ArenaSample,
    Cluster,
    Job,
    JobStatus,
    LivePassRate,
    LivePassRatePoint,
    Metrics,
    RoutingState,
)
from core.agent_contracts import (
    AgentAnalysisResponse,
    AgentDecision,
    ApprovalRecord,
    ApprovalRequest,
    TrainingConfig,
)
from app.api.agent import AgentAnalyzeRequest, agent_enabled
from app.proxy.clusters import list_cluster_profiles


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
    cluster.model_copy(
        update={
            "routing": RoutingState(
                cluster_id=cluster.cluster_id,
                enabled=True,
                canary_percent=100,
                target_model="vf-nl2sql-gain",
            ),
            "live_pass_rate": _live_pass_rate(cluster.cluster_id),
        }
    )
    if cluster.cluster_id == "support-ticket-extraction"
    else cluster.model_copy(
        update={
            "approved_sample_source": ApprovedSampleSource(
                kind="repository_jsonl",
                uri="data/nl2sql/v0.10.0-training-pool.jsonl",
                sha256="c97a5adea789fae3be249bc9ac95a1902ae5a9769de9eefbc08277f056878e8c",
                row_count=50,
                approved_by="demo-owner",
                approved_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
            )
        }
    )
    if cluster.cluster_id == "data-pull-sql"
    else cluster
    for cluster in list_cluster_profiles()
]


_ROUTING_STATES: dict[str, RoutingState] = {
    cluster.cluster_id: cluster.routing
    or RoutingState(
        cluster_id=cluster.cluster_id,
        enabled=False,
        canary_percent=0,
        target_model="tuned",
    )
    for cluster in CLUSTERS
}

_AGENT_DECISIONS: dict[str, AgentAnalysisResponse] = {}
_AGENT_APPROVALS: dict[str, ApprovalRecord] = {}


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


@app.get("/clusters/{cluster_id}/routing", response_model=RoutingState)
def get_cluster_routing(cluster_id: str) -> RoutingState:
    _require_cluster(cluster_id)
    return _ROUTING_STATES[cluster_id]


@app.put("/clusters/{cluster_id}/routing", response_model=RoutingState)
def put_cluster_routing(cluster_id: str, state: RoutingState) -> RoutingState:
    _require_cluster(cluster_id)
    if state.cluster_id != cluster_id:
        raise HTTPException(status_code=422, detail="routing cluster_id must match the path")
    _ROUTING_STATES[cluster_id] = state
    return state


@app.get("/clusters/{cluster_id}/live-pass-rate", response_model=LivePassRate)
def get_live_pass_rate(cluster_id: str) -> LivePassRate:
    _require_cluster(cluster_id)
    return _live_pass_rate(cluster_id)


def _require_cluster(cluster_id: str) -> None:
    if cluster_id not in _ROUTING_STATES:
        raise HTTPException(status_code=404, detail="Cluster not found")


@app.post(
    "/clusters/{cluster_id}/agent/analyze", response_model=AgentAnalysisResponse
)
def analyze_cluster(
    cluster_id: str, request: AgentAnalyzeRequest | None = None
) -> AgentAnalysisResponse:
    _require_agent_enabled()
    _require_cluster(cluster_id)
    del request  # The deterministic mock accepts the same optional body shape.
    existing = _AGENT_DECISIONS.get(cluster_id)
    if existing is not None:
        return existing.model_copy(update={"cached": True})
    decision = _mock_agent_decision(cluster_id)
    response = AgentAnalysisResponse(
        decision_id=f"mock-agent-{cluster_id}",
        cluster_id=cluster_id,
        decision=decision,
        cached=False,
        created_at="2026-07-17T12:00:00Z",
    )
    _AGENT_DECISIONS[cluster_id] = response
    return response


@app.get(
    "/clusters/{cluster_id}/agent/decision", response_model=AgentAnalysisResponse
)
def latest_agent_decision(cluster_id: str) -> AgentAnalysisResponse:
    _require_agent_enabled()
    _require_cluster(cluster_id)
    response = _AGENT_DECISIONS.get(cluster_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Agent decision not found")
    return response.model_copy(update={"cached": True})


@app.post(
    "/agent-decisions/{decision_id}/approvals", response_model=ApprovalRecord
)
def approve_agent_decision(
    decision_id: str, request: ApprovalRequest
) -> ApprovalRecord:
    _require_agent_enabled()
    existing = _AGENT_APPROVALS.get(decision_id)
    if existing is not None:
        return existing
    response = next(
        (
            value
            for value in _AGENT_DECISIONS.values()
            if value.decision_id == decision_id
        ),
        None,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Agent decision not found")
    if response.decision.decision.value != "forge":
        raise HTTPException(
            status_code=409,
            detail="Only audited forge decisions may be approved",
        )
    approval = ApprovalRecord(
        approval_id=f"mock-approval-{len(_AGENT_APPROVALS) + 1}",
        decision_id=decision_id,
        approved_by=request.approved_by,
        approved_at="2026-07-17T12:01:00Z",
    )
    _AGENT_APPROVALS[decision_id] = approval
    return approval


@app.get(
    "/agent-decisions/{decision_id}/approval", response_model=ApprovalRecord
)
def get_agent_approval(decision_id: str) -> ApprovalRecord:
    _require_agent_enabled()
    approval = _AGENT_APPROVALS.get(decision_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval


def _mock_agent_decision(cluster_id: str) -> AgentDecision:
    if cluster_id == "data-pull-sql":
        return AgentDecision(
            decision="forge",
            rationale="High SQL volume and deterministic verification support forging.",
            confidence=0.94,
            config=TrainingConfig(budget_usd_cap=25.0),
        )
    if cluster_id == "support-ticket-extraction":
        return AgentDecision(
            decision="skip",
            rationale="This cluster already has a tuned live route.",
            confidence=0.88,
        )
    return AgentDecision(
        decision="need_more_data",
        rationale="More approved samples are required.",
        confidence=0.82,
    )


def _require_agent_enabled() -> None:
    if not agent_enabled():
        raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
