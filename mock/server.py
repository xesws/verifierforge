"""Contract-shaped mock data for the VerifierForge frontend."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
import uvicorn

# ``python mock/server.py`` puts mock/ (rather than the repo root) on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.contracts import (
    ApprovedSampleSource,
    Cluster,
    Job,
    JobCreateRequest,
    JobStatus,
    LivePassRate,
    LivePassRatePoint,
    Metrics,
    Report,
    RoutingState,
)
from core.agent_contracts import (
    AgentAnalysisResponse,
    AgentDecision,
    AgentRunStatus,
    AgentToolCallTrace,
    AgentTrace,
    ApprovalRecord,
    ApprovalRequest,
    TrainingConfig,
)
from core.p4_contracts import (
    CredentialSource,
    ForgeExecutionStatus,
    ForgeLifecycle,
    ProviderCredentialRequest,
    ProviderCredentialStatus,
    StartForgeRequest,
)
from core.provisioning_contracts import ProvisionProvider
from core.serving_contracts import (
    ServingSleepRequest,
    ServingState,
    ServingStatus,
    ServingWakeRequest,
)
from app.api.agent import (
    AgentAnalyzeRequest,
    ApprovedSampleSourceRequest,
    agent_enabled,
)
from app.api.cors import configure_cors
from app.proxy.clusters import list_cluster_profiles
from app.proxy.upstream import fake_tuned_chat_completion


app = FastAPI(title="VerifierForge Mock API")
configure_cors(app)


def _job(
    *,
    job_id: str,
    template: str,
    status: str,
    metrics: dict[str, list[int] | list[float]],
    report: dict | Report | None = None,
    endpoint: dict[str, str] | None = None,
    control: dict[str, list[float]] | None = None,
    model: str = "Qwen/Qwen2.5-1.5B-Instruct",
) -> Job:
    return Job(
        job_id=job_id,
        template=template,
        status=status,
        model=model,
        created_at="2026-07-14T03:00:00Z",
        metrics=Metrics.model_validate(metrics),
        control=control or {"pass_at_1": [0.18, 0.19, 0.2]},
        report=report,
        endpoint=endpoint,
    )


def _flagship_report() -> Report:
    path = (
        Path(__file__).resolve().parents[1]
        / "data/demo-artifacts/jobs/d4-m3-1p5b-r1-v0125/job.json"
    )
    job = Job.model_validate_json(path.read_text(encoding="utf-8"))
    if job.report is None:
        raise RuntimeError("committed flagship artifact is missing its report")
    return job.report


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
        report=_flagship_report(),
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
                target_model="tuned",
            ),
            "live_pass_rate": _live_pass_rate(cluster.cluster_id),
        }
    )
    if cluster.cluster_id == "support-ticket-extraction"
    else cluster.model_copy(
        update={
            "job_id": "nl2sql-gain",
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
_PROVIDER_CREDENTIALS: set[tuple[str, ProvisionProvider]] = set()
_FORGE_EXECUTIONS: dict[str, ForgeExecutionStatus] = {}
_SERVING_STATUS = ServingStatus(
    model_id="vf-demo",
    state=ServingState.COLD,
    detail="No serving session is active",
)


@app.get("/jobs")
def list_jobs() -> list[dict[str, str]]:
    return [
        {"job_id": job.job_id, "status": job.status.value}
        for job in sorted(JOBS, key=lambda item: item.job_id)
    ]


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
def create_job(request: JobCreateRequest) -> Job:
    """Fake-create a queued job in memory; never writes ``runs/``."""
    job = _job(
        job_id=f"mock-job-{uuid4().hex[:8]}",
        template=request.template,
        status=JobStatus.QUEUED.value,
        model=request.model,
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
    return [_cluster_view(cluster) for cluster in CLUSTERS]


@app.get("/clusters/{cluster_id}", response_model=Cluster)
def get_cluster(cluster_id: str) -> Cluster:
    for cluster in CLUSTERS:
        if cluster.cluster_id == cluster_id:
            return _cluster_view(cluster)
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
    if state.target_model != "tuned":
        raise HTTPException(
            status_code=422,
            detail="public routing accepts only the logical tuned target",
        )
    _ROUTING_STATES[cluster_id] = state
    return state


@app.get("/clusters/{cluster_id}/live-pass-rate", response_model=LivePassRate)
def get_live_pass_rate(cluster_id: str) -> LivePassRate:
    _require_cluster(cluster_id)
    return _live_pass_rate(cluster_id)


@app.get(
    "/clusters/{cluster_id}/sample-source",
    response_model=ApprovedSampleSource | None,
)
def get_sample_source(cluster_id: str) -> ApprovedSampleSource | None:
    _require_agent_enabled()
    return _require_cluster(cluster_id).approved_sample_source


@app.put(
    "/clusters/{cluster_id}/sample-source", response_model=ApprovedSampleSource
)
def put_sample_source(
    cluster_id: str, request: ApprovedSampleSourceRequest
) -> ApprovedSampleSource:
    _require_agent_enabled()
    cluster = _require_cluster(cluster_id)
    source = ApprovedSampleSource(
        kind="repository_jsonl",
        uri=request.uri,
        sha256=request.expected_sha256 or "c" * 64,
        row_count=request.expected_row_count or 50,
        approved_by=request.approved_by,
        approved_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    index = next(i for i, item in enumerate(CLUSTERS) if item.cluster_id == cluster_id)
    CLUSTERS[index] = cluster.model_copy(update={"approved_sample_source": source})
    return source


def _require_cluster(cluster_id: str) -> Cluster:
    for cluster in CLUSTERS:
        if cluster.cluster_id == cluster_id:
            return cluster
    raise HTTPException(status_code=404, detail="Cluster not found")


def _cluster_view(cluster: Cluster) -> Cluster:
    response = _AGENT_DECISIONS.get(f"{cluster.cluster_id}|p2_gate_b") or _AGENT_DECISIONS.get(
        cluster.cluster_id
    )
    return cluster.model_copy(
        update={
            "routing": _ROUTING_STATES[cluster.cluster_id],
            "analyzer_decision": response.decision if response is not None else None,
        }
    )


@app.post(
    "/clusters/{cluster_id}/agent/analyze", response_model=AgentAnalysisResponse
)
def analyze_cluster(
    cluster_id: str, request: AgentAnalyzeRequest | None = None
) -> AgentAnalysisResponse:
    _require_agent_enabled()
    _require_cluster(cluster_id)
    profile = request.execution_profile if request is not None else "standard"
    cache_key = cluster_id if profile == "standard" else f"{cluster_id}|{profile}"
    existing = _AGENT_DECISIONS.get(cache_key)
    if existing is not None and not (request is not None and request.force_refresh):
        return existing.model_copy(update={"cached": True})
    decision = _mock_agent_decision(cluster_id, p2=profile == "p2_gate_b")
    trace = _mock_agent_trace(cluster_id, decision)
    response = AgentAnalysisResponse(
        decision_id=f"mock-agent-{uuid4().hex}",
        cluster_id=cluster_id,
        decision=decision,
        cached=False,
        created_at=trace.finished_at,
        trace_id=trace.trace_id,
        provider=trace.provider,
        model=trace.model,
        total_input_tokens=trace.total_input_tokens,
        total_output_tokens=trace.total_output_tokens,
        trace=trace,
    )
    _AGENT_DECISIONS[cache_key] = response
    return response


@app.get(
    "/clusters/{cluster_id}/agent/decision", response_model=AgentAnalysisResponse
)
def latest_agent_decision(cluster_id: str) -> AgentAnalysisResponse:
    _require_agent_enabled()
    _require_cluster(cluster_id)
    response = _AGENT_DECISIONS.get(f"{cluster_id}|p2_gate_b") or _AGENT_DECISIONS.get(cluster_id)
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


@app.put(
    "/settings/provider-credentials/{provider}",
    response_model=ProviderCredentialStatus,
)
def put_provider_credential(
    provider: ProvisionProvider,
    request: ProviderCredentialRequest,
) -> ProviderCredentialStatus:
    """Record only credential presence; the deterministic mock never retains a key."""
    _PROVIDER_CREDENTIALS.add((request.user_id, provider))
    return ProviderCredentialStatus(
        user_id=request.user_id,
        provider=provider,
        configured=True,
        source=CredentialSource.STORED,
        credential_id=f"mock-credential-{provider.value}",
        updated_at="2026-07-19T12:00:00Z",
    )


@app.get(
    "/settings/provider-credentials/{provider}",
    response_model=ProviderCredentialStatus,
)
def get_provider_credential(
    provider: ProvisionProvider,
    user_id: str,
) -> ProviderCredentialStatus:
    stored = (user_id, provider) in _PROVIDER_CREDENTIALS
    system = provider is ProvisionProvider.RUNPOD and bool(os.environ.get("RUNPOD_API_KEY"))
    source = (
        CredentialSource.STORED
        if stored
        else CredentialSource.SYSTEM_ENV
        if system
        else CredentialSource.MISSING
    )
    return ProviderCredentialStatus(
        user_id=user_id,
        provider=provider,
        configured=stored or system,
        source=source,
        credential_id=f"mock-credential-{provider.value}" if stored else None,
        updated_at="2026-07-19T12:00:00Z" if stored else None,
    )


@app.post(
    "/approvals/{approval_id}/start-forge",
    response_model=ForgeExecutionStatus,
)
def start_forge(approval_id: str, request: StartForgeRequest) -> ForgeExecutionStatus:
    _require_execution_enabled()
    approval, analysis = _approval_context(approval_id)
    if approval.approved_by != request.requested_by:
        raise HTTPException(status_code=409, detail="Start Forge requester must match approver")
    existing = _FORGE_EXECUTIONS.get(approval_id)
    if existing is not None:
        return existing
    config = analysis.decision.config
    if config is None:
        raise HTTPException(status_code=409, detail="Approved decision has no training config")
    system_cap = float(os.environ.get("VF_PROVISION_SYSTEM_BUDGET_USD_CAP", "5"))
    started = ForgeExecutionStatus(
        approval_id=approval_id,
        decision_id=approval.decision_id,
        job_id=f"mock-forge-{approval_id}",
        provider=ProvisionProvider.RUNPOD,
        state=ForgeLifecycle.PROVISIONING,
        budget_usd_cap=min(config.budget_usd_cap, system_cap),
        credential_source=CredentialSource.SYSTEM_ENV,
        detail="Mock provision requested",
        created_at="2026-07-19T12:02:00Z",
        updated_at="2026-07-19T12:02:00Z",
    )
    _FORGE_EXECUTIONS[approval_id] = started.model_copy(
        update={
            "state": ForgeLifecycle.DONE,
            "provision_handle": "mock-0001",
            "detail": "Mock lifecycle completed",
            "updated_at": datetime(2026, 7, 19, 12, 3, tzinfo=timezone.utc),
        }
    )
    return started


@app.get(
    "/approvals/{approval_id}/forge-execution",
    response_model=ForgeExecutionStatus,
)
def forge_execution(approval_id: str) -> ForgeExecutionStatus:
    _require_agent_enabled()
    existing = _FORGE_EXECUTIONS.get(approval_id)
    if existing is not None:
        return existing
    approval, analysis = _approval_context(approval_id)
    config = analysis.decision.config
    if config is None:
        raise HTTPException(status_code=409, detail="Approved decision has no training config")
    return ForgeExecutionStatus(
        approval_id=approval_id,
        decision_id=approval.decision_id,
        job_id=f"mock-forge-{approval_id}",
        provider=ProvisionProvider.RUNPOD,
        state=ForgeLifecycle.APPROVED,
        budget_usd_cap=config.budget_usd_cap,
        detail="Approved; explicit Start Forge confirmation is still required",
        created_at=approval.approved_at,
        updated_at=approval.approved_at,
    )


@app.post("/serving/wake", response_model=ServingStatus, status_code=202)
def wake_serving(request: ServingWakeRequest, response: Response) -> ServingStatus:
    global _SERVING_STATUS
    if os.environ.get("VF_SERVING_WAKE_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(
            status_code=404,
            detail="Scale-to-zero wake is disabled because VF_SERVING_WAKE_ENABLED=false",
        )
    if request.model_id != "vf-demo":
        raise HTTPException(status_code=404, detail="Unknown serving model")
    if _SERVING_STATUS.state is not ServingState.COLD:
        response.status_code = 200
        return _SERVING_STATUS
    _SERVING_STATUS = ServingStatus(
        session_id="sv-mock-session",
        model_id="vf-demo",
        state=ServingState.READY,
        url="https://mock-serving.example.test/v1",
        detail="Mock scale-to-zero endpoint is ready",
        gpu_model="Mock RTX 2000 Ada",
        hourly_price_usd=0.1,
        cost_accrued_usd=0,
        cold_start_seconds=0,
        updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    return _SERVING_STATUS


@app.get("/serving/status", response_model=ServingStatus)
def serving_status(model_id: str | None = None) -> ServingStatus:
    if model_id not in {None, "vf-demo"}:
        return ServingStatus(
            model_id=model_id,
            state=ServingState.COLD,
            detail="No serving session is active",
        )
    return _SERVING_STATUS


@app.post("/serving/sleep", response_model=ServingStatus)
def sleep_serving(request: ServingSleepRequest) -> ServingStatus:
    global _SERVING_STATUS
    if request.model_id != "vf-demo":
        raise HTTPException(status_code=404, detail="Unknown serving model")
    _SERVING_STATUS = ServingStatus(
        model_id="vf-demo",
        state=ServingState.COLD,
        detail="Mock serving session closed",
        updated_at=datetime.now(timezone.utc),
    )
    return _SERVING_STATUS


@app.post(
    "/serving/tuned-completion",
    response_model=dict[str, object],
)
def tuned_completion(request: dict[str, object]) -> JSONResponse:
    if _SERVING_STATUS.state is not ServingState.READY:
        raise HTTPException(status_code=409, detail="Tuned endpoint is cold; wake it first")
    forwarded = fake_tuned_chat_completion(request)
    return JSONResponse(
        content=forwarded.payload,
        headers={"X-VerifierForge-Route": "tuned"},
    )


def _approval_context(approval_id: str) -> tuple[ApprovalRecord, AgentAnalysisResponse]:
    approval = next(
        (value for value in _AGENT_APPROVALS.values() if value.approval_id == approval_id),
        None,
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    analysis = next(
        (
            value
            for value in _AGENT_DECISIONS.values()
            if value.decision_id == approval.decision_id
        ),
        None,
    )
    if analysis is None:
        raise HTTPException(status_code=404, detail="Agent decision not found")
    return approval, analysis


def _mock_agent_decision(cluster_id: str, *, p2: bool = False) -> AgentDecision:
    if cluster_id == "data-pull-sql":
        return AgentDecision(
            decision="forge",
            rationale="High SQL volume and deterministic verification support forging.",
            confidence=0.94,
            config=TrainingConfig(
                base_model=(
                    "Qwen/Qwen2.5-0.5B-Instruct"
                    if p2
                    else "Qwen/Qwen2.5-1.5B-Instruct"
                ),
                steps=100 if p2 else 400,
                k=8,
                checkpoint_interval=50,
                budget_usd_cap=5.0 if p2 else 25.0,
                provider_pref="runpod" if p2 else "auto",
            ),
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


def _mock_agent_trace(cluster_id: str, decision: AgentDecision) -> AgentTrace:
    started_at = datetime.now(timezone.utc)
    analysis_id = "a" * 64
    sample_set_id = "b" * 64
    calls = [
        AgentToolCallTrace(
            tool_name="analyze_traffic",
            arguments={"cluster_id": cluster_id},
            output={
                "cluster_id": cluster_id,
                "analysis_id": analysis_id,
                "request_count": 200,
                "monthly_calls": 95_000,
                "monthly_cost_usd": 5_500.0,
                "data_sufficient": True,
            },
            started_at=started_at,
            finished_at=started_at,
            input_tokens=12,
            output_tokens=6,
        ),
        AgentToolCallTrace(
            tool_name="inspect_samples",
            arguments={"cluster_id": cluster_id, "analysis_id": analysis_id, "n": 3},
            output={
                "cluster_id": cluster_id,
                "analysis_id": analysis_id,
                "sample_set_id": sample_set_id,
                "data_sufficient": True,
                "sample_count": 3,
            },
            started_at=started_at,
            finished_at=started_at,
            input_tokens=12,
            output_tokens=6,
        ),
        AgentToolCallTrace(
            tool_name="estimate_economics",
            arguments={
                "cluster_id": cluster_id,
                "analysis_id": analysis_id,
                "base_model": "Qwen/Qwen2.5-1.5B-Instruct",
            },
            output={
                "training_cost_usd": 2.5,
                "current_monthly_cost_usd": 5_500.0,
                "projected_monthly_savings_usd": 3_850.0,
                "payback_days": 1,
            },
            started_at=started_at,
            finished_at=started_at,
            input_tokens=12,
            output_tokens=6,
        ),
        AgentToolCallTrace(
            tool_name="check_verifiability",
            arguments={
                "cluster_id": cluster_id,
                "analysis_id": analysis_id,
                "sample_set_id": sample_set_id,
            },
            output={
                "cluster_id": cluster_id,
                "analysis_id": analysis_id,
                "sample_set_id": sample_set_id,
                "data_sufficient": True,
                "confidence": 1.0,
                "reasons": ["Deterministic verifier is available."],
            },
            started_at=started_at,
            finished_at=started_at,
            input_tokens=12,
            output_tokens=6,
        ),
    ]
    return AgentTrace(
        trace_id=f"mock-trace-{uuid4().hex}",
        cluster_id=cluster_id,
        provider="mock",
        model="vf-agent-deterministic-mock",
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        tool_calls=calls,
        total_input_tokens=60,
        total_output_tokens=30,
        status=AgentRunStatus.COMPLETED,
        terminal_decision=decision,
    )


def _require_agent_enabled() -> None:
    if not agent_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _require_execution_enabled() -> None:
    _require_agent_enabled()
    if os.environ.get("VF_AUTOPROVISION", "false").strip().lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
