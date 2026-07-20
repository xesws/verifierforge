"""Small file-backed API for inspecting local training runs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from app.api.artifacts import ArtifactDataError, ArtifactStore
from app.api.agent import router as agent_router
from app.api.copilot import router as copilot_router
from app.api.cors import configure_cors
from app.api.provisioning import router as provisioning_router
from app.db import DatabaseOperationError, repository_gateway
from app.db.records import ClusterRecord as DatabaseClusterRecord
from app.db.records import JobRecord as DatabaseJobRecord
from app.db.records import LivePassRateRecord as DatabaseLivePassRateRecord
from app.db.records import RoutingRecord as DatabaseRoutingRecord
from app.proxy.clusters import cluster_profile, list_cluster_profiles
from app.proxy.routing import LivePassRateRecord, RouteRecord, get_route, list_live_pass_rate, put_route
from core.agent_contracts import AgentDecision
from core.contracts import (
    ApprovedSampleSource,
    Cluster,
    Control,
    Job,
    JobCreateRequest,
    JobStatus,
    LivePassRate,
    LivePassRatePoint,
    MetricRecord,
    Metrics,
    RoutingState,
)


app = FastAPI(title="VerifierForge API")
configure_cors(app)
app.include_router(copilot_router)
app.include_router(agent_router)
app.include_router(provisioning_router)


def _runs_dir() -> Path:
    return Path(os.environ.get("VF_RUNS_DIR", "./runs")).expanduser()


def _data_mode() -> str:
    mode = os.environ.get("VF_API_DATA_MODE", "runs").strip().lower()
    if mode not in {"runs", "artifacts", "hybrid"}:
        raise HTTPException(
            status_code=500,
            detail="VF_API_DATA_MODE must be runs, artifacts, or hybrid",
        )
    return mode


def _artifact_store() -> ArtifactStore:
    root = Path(
        os.environ.get(
            "VF_DEMO_ARTIFACTS_DIR",
            str(Path(__file__).resolve().parents[2] / "data" / "demo-artifacts"),
        )
    ).expanduser()
    try:
        return ArtifactStore(root)
    except ArtifactDataError as error:
        raise HTTPException(status_code=503, detail="Demo artifacts are unavailable") from error


def _status_for(job_dir: Path) -> str:
    """Infer the small set of lifecycle states represented by local files."""
    if (job_dir / "failed").exists():
        return "failed"
    if (job_dir / "early_stopped").exists():
        return "early_stopped"
    if (job_dir / "artifacts" / "final" / "model.txt").is_file():
        return "done"
    if (job_dir / "metrics.jsonl").exists() or (job_dir / "train.log").exists():
        return "running"
    return "queued"


def _metrics_for(job_dir: Path) -> Metrics:
    """Aggregate the append-only metric log into the shared Metrics shape."""
    metrics_path = job_dir / "metrics.jsonl"
    if not metrics_path.exists():
        return Metrics(steps=[], reward_mean=[], pass_at_1=[], entropy=[])

    records: list[MetricRecord] = []
    with metrics_path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                records.append(MetricRecord.model_validate(json.loads(line)))

    return Metrics(
        steps=[record.step for record in records],
        reward_mean=[record.reward_mean for record in records],
        pass_at_1=[record.pass_at_1 for record in records],
        entropy=[record.entropy for record in records],
    )


@app.get("/jobs")
def list_jobs() -> list[dict[str, str]]:
    """List local run IDs and their file-inferred status."""
    mode = _data_mode()
    if mode == "artifacts":
        return _artifact_store().list_jobs()
    jobs = _artifact_store().list_jobs() if mode == "hybrid" else []
    root = _runs_dir()
    if mode == "runs" and root.is_dir():
        local_jobs = [
            {"job_id": path.name, "status": _status_for(path)}
            for path in sorted(root.iterdir())
            if path.is_dir() and not path.name.startswith(".")
        ]
        jobs.extend(local_jobs)
        for item in local_jobs:
            _materialize_job_best_effort(
                DatabaseJobRecord(
                    job_id=item["job_id"],
                    template="unknown",
                    status=item["status"],
                    config_json={},
                    created_at=datetime.fromtimestamp(
                        (root / item["job_id"]).stat().st_mtime, tz=timezone.utc
                    ),
                    summary_json={"source": "local-runs"},
                )
            )
    known = {item["job_id"] for item in jobs}
    for record in _database_jobs_best_effort():
        if record.job_id not in known:
            jobs.append({"job_id": record.job_id, "status": record.status})
    return sorted(jobs, key=lambda item: item["job_id"])


@app.post("/jobs", response_model=Job, status_code=201)
def create_job(request: JobCreateRequest) -> Job:
    """Create queued job metadata without provisioning or starting training."""
    if _data_mode() == "artifacts":
        raise HTTPException(status_code=409, detail="Demo artifact mode is read-only")
    job = Job(
        job_id=f"job-{uuid4().hex[:12]}",
        template=request.template,
        status=JobStatus.QUEUED,
        model=request.model,
        created_at=datetime.now(timezone.utc),
        metrics=Metrics(steps=[], reward_mean=[], pass_at_1=[], entropy=[]),
        control=Control(pass_at_1=[]),
    )
    record = DatabaseJobRecord(
        job_id=job.job_id,
        template=job.template,
        status=job.status.value,
        config_json={"model": job.model},
        created_at=job.created_at,
        summary_json={"job": job.model_dump(mode="json")},
    )
    try:
        repository_gateway().call(lambda repositories: repositories.jobs.put(record))
    except (DatabaseOperationError, OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Job persistence is unavailable") from None
    return job


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    """Return a minimal Job built from files already present under runs/."""
    mode = _data_mode()
    if mode in {"artifacts", "hybrid"}:
        try:
            return _artifact_store().job(job_id)
        except KeyError as error:
            if mode == "artifacts":
                raise HTTPException(status_code=404, detail="Job not found") from error
    root = _runs_dir().resolve()
    candidate = (root / job_id).resolve()
    if candidate.parent != root:
        raise HTTPException(status_code=404, detail="Job not found")
    if not candidate.is_dir():
        database_job = _database_job(job_id)
        if database_job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return database_job
    job_dir = candidate
    created = datetime.fromtimestamp(job_dir.stat().st_mtime, tz=timezone.utc)
    job = Job(
        job_id=job_id,
        template="unknown",
        status=JobStatus(_status_for(job_dir)),
        model="unknown",
        created_at=created,
        metrics=_metrics_for(job_dir),
        control=Control(pass_at_1=[]),
        report=None,
        endpoint=None,
    )
    metrics = job.metrics
    _materialize_job_best_effort(
        DatabaseJobRecord(
            job_id=job.job_id,
            template=job.template,
            status=job.status.value,
            config_json={"model": job.model},
            created_at=job.created_at,
            summary_json={
                "metrics": {
                    "steps": metrics.steps[-100:],
                    "reward_mean": metrics.reward_mean[-100:],
                    "pass_at_1": metrics.pass_at_1[-100:],
                    "entropy": metrics.entropy[-100:],
                }
            },
        )
    )
    return job


@app.get("/jobs/{job_id}/metrics", response_model=Metrics)
def job_metrics(job_id: str) -> Metrics:
    """Aggregate the append-only metric log into the shared Metrics shape."""
    mode = _data_mode()
    if mode in {"artifacts", "hybrid"}:
        try:
            return _artifact_store().metrics(job_id)
        except KeyError as error:
            if mode == "artifacts":
                raise HTTPException(status_code=404, detail="Job not found") from error
    root = _runs_dir().resolve()
    candidate = (root / job_id).resolve()
    if candidate.parent == root and candidate.is_dir():
        return _metrics_for(candidate)
    database_job = _database_job(job_id)
    if database_job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return database_job.metrics


@app.get("/clusters", response_model=list[Cluster])
def list_clusters() -> list[Cluster]:
    """Return the stable product profiles used by Discover and the mock API."""
    profiles = list_cluster_profiles()
    _materialize_clusters_best_effort(profiles)
    return [_cluster_with_product_state(profile) for profile in profiles]


@app.get("/clusters/{cluster_id}", response_model=Cluster)
def get_cluster(cluster_id: str) -> Cluster:
    """Return one stable product profile without deriving monthly facts from a short sample."""
    try:
        profile = cluster_profile(cluster_id)
        _materialize_clusters_best_effort([profile])
        return _cluster_with_product_state(profile)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Cluster not found") from error


@app.get("/clusters/{cluster_id}/routing", response_model=RoutingState)
def get_cluster_routing(cluster_id: str) -> RoutingState:
    """Read the frontend routing switch in the existing contract shape."""
    if _data_mode() == "artifacts":
        try:
            return _artifact_store().routing(cluster_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Cluster not found") from error
    try:
        return _routing_state(get_route(cluster_id, gateway=repository_gateway()))
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Routing state is unavailable") from error


@app.put("/clusters/{cluster_id}/routing", response_model=RoutingState)
def put_cluster_routing(cluster_id: str, state: RoutingState) -> RoutingState:
    """Persist the frontend routing switch without adding a new public contract."""
    if _data_mode() == "artifacts":
        raise HTTPException(status_code=409, detail="Demo artifact mode is read-only")
    if state.cluster_id != cluster_id:
        raise HTTPException(status_code=422, detail="routing cluster_id must match the path")
    try:
        route = put_route(
            RouteRecord(
                cluster_id=state.cluster_id,
                enabled=state.enabled,
                canary_percent=state.canary_percent,
                target_upstream=state.target_model,
            ),
            gateway=repository_gateway(),
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Routing state is unavailable") from error
    return _routing_state(route)


@app.get("/clusters/{cluster_id}/live-pass-rate", response_model=LivePassRate)
def get_live_pass_rate(cluster_id: str) -> LivePassRate:
    """Serve guardian rolling exact-pass points in the shared contract shape."""
    if _data_mode() == "artifacts":
        try:
            return _artifact_store().live_pass_rate(cluster_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Cluster not found") from error
    try:
        points = list_live_pass_rate(cluster_id, gateway=repository_gateway())
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Live pass rate is unavailable") from error
    return LivePassRate(cluster_id=cluster_id, points=[_live_point(point) for point in points])


def _routing_state(route: RouteRecord | DatabaseRoutingRecord) -> RoutingState:
    target_model = getattr(route, "target_upstream", None) or getattr(
        route, "target_model"
    )
    return RoutingState(
        cluster_id=str(getattr(route, "cluster_id")),
        enabled=bool(getattr(route, "enabled")),
        canary_percent=int(getattr(route, "canary_percent")),
        target_model=str(target_model),
    )


def _live_point(
    point: LivePassRateRecord | DatabaseLivePassRateRecord,
) -> LivePassRatePoint:
    timestamp = getattr(point, "timestamp", None) or getattr(point, "ts")
    return LivePassRatePoint(
        timestamp=timestamp,
        pass_rate=float(getattr(point, "pass_rate")),
    )


def _materialize_clusters_best_effort(profiles: list[Cluster]) -> None:
    timestamp = datetime.now(timezone.utc)

    async def write(repositories) -> None:
        for profile in profiles:
            existing = await repositories.clusters.get(profile.cluster_id)
            source = (
                profile.approved_sample_source.model_dump(mode="json")
                if profile.approved_sample_source is not None
                else existing.approved_sample_source
                if existing is not None
                else None
            )
            await repositories.clusters.put(
                DatabaseClusterRecord(
                    cluster_id=profile.cluster_id,
                    name=profile.name,
                    status=profile.status.value,
                    monthly_calls=profile.monthly_calls,
                    monthly_cost_usd=profile.monthly_cost_usd,
                    trainable=profile.trainable,
                    job_id=profile.job_id,
                    analyzer_summary=None,
                    approved_sample_source=source,
                    updated_at=timestamp,
                )
            )

    try:
        repository_gateway().call(write)
    except (OSError, RuntimeError, ValueError):
        # Static product profiles remain available if observability storage is down.
        pass


def _cluster_with_product_state(profile: Cluster) -> Cluster:
    if _data_mode() == "artifacts":
        updates: dict[str, object] = {}
        try:
            updates["routing"] = _artifact_store().routing(profile.cluster_id)
        except KeyError:
            pass
        try:
            updates["live_pass_rate"] = _artifact_store().live_pass_rate(
                profile.cluster_id
            )
        except KeyError:
            pass
        return profile.model_copy(update=updates)

    async def read(repositories):
        return (
            await repositories.clusters.get(profile.cluster_id),
            await repositories.routing.get(profile.cluster_id),
            await repositories.live_pass_rate.list_points(profile.cluster_id),
            await repositories.agent_decisions.latest_for_cluster(profile.cluster_id),
        )

    try:
        record, route, points, decision_record = repository_gateway().call(read)
    except (DatabaseOperationError, OSError, RuntimeError, ValueError):
        return profile
    updates: dict[str, object] = {}
    if record is not None and record.approved_sample_source is not None:
        updates["approved_sample_source"] = ApprovedSampleSource.model_validate(
            record.approved_sample_source
        )
    if route is not None:
        updates["routing"] = _routing_state(route)
    if points:
        updates["live_pass_rate"] = LivePassRate(
            cluster_id=profile.cluster_id,
            points=[_live_point(point) for point in points],
        )
    if decision_record is not None and decision_record.decision is not None:
        try:
            updates["analyzer_decision"] = AgentDecision.model_validate(
                {
                    "decision": decision_record.decision,
                    "rationale": decision_record.rationale,
                    "confidence": decision_record.confidence,
                    "config": decision_record.config_json,
                }
            )
        except ValueError:
            # A malformed audit row must not make the Discover catalog unavailable.
            pass
    return profile.model_copy(update=updates)


def _materialize_job_best_effort(record: DatabaseJobRecord) -> None:
    try:
        repository_gateway().call(lambda repositories: repositories.jobs.put(record))
    except (OSError, RuntimeError, ValueError):
        # File-backed development inspection remains available during DB outages.
        pass


def _database_jobs_best_effort() -> list[DatabaseJobRecord]:
    try:
        return repository_gateway().call(lambda repositories: repositories.jobs.list())
    except (DatabaseOperationError, OSError, RuntimeError, ValueError):
        return []


def _database_job(job_id: str) -> Job | None:
    try:
        record = repository_gateway().call(
            lambda repositories: repositories.jobs.get(job_id)
        )
    except (DatabaseOperationError, OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Job persistence is unavailable") from None
    if record is None:
        return None
    payload = record.summary_json.get("job")
    if isinstance(payload, dict):
        try:
            return Job.model_validate(payload)
        except ValueError:
            pass
    try:
        status = JobStatus(record.status)
    except ValueError:
        status = JobStatus.FAILED
    model = record.config_json.get("model", "unknown")
    return Job(
        job_id=record.job_id,
        template=record.template,
        status=status,
        model=str(model),
        created_at=record.created_at,
        metrics=Metrics(steps=[], reward_mean=[], pass_at_1=[], entropy=[]),
        control=Control(pass_at_1=[]),
    )
