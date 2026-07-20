"""Small file-backed API for inspecting local training runs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.artifacts import ArtifactDataError, ArtifactStore
from app.api.agent import router as agent_router
from app.api.copilot import router as copilot_router
from app.api.provisioning import router as provisioning_router
from app.db import repository_gateway
from app.db.records import ClusterRecord as DatabaseClusterRecord
from app.db.records import JobRecord as DatabaseJobRecord
from app.proxy.clusters import cluster_profile, list_cluster_profiles
from app.proxy.routing import LivePassRateRecord, RouteRecord, get_route, list_live_pass_rate, put_route
from core.contracts import (
    ApprovedSampleSource,
    Cluster,
    Control,
    Job,
    JobStatus,
    LivePassRate,
    LivePassRatePoint,
    MetricRecord,
    Metrics,
    RoutingState,
)


app = FastAPI(title="VerifierForge API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
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


def _artifact_jobs_mode() -> bool:
    return _data_mode() in {"artifacts", "hybrid"}


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


def _job_dir(job_id: str) -> Path:
    """Return a job directory while keeping path traversal out of runs/."""
    root = _runs_dir().resolve()
    candidate = (root / job_id).resolve()
    if candidate.parent != root or not candidate.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")
    return candidate


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
    if _artifact_jobs_mode():
        return _artifact_store().list_jobs()
    root = _runs_dir()
    if not root.is_dir():
        return []
    jobs = [
        {"job_id": path.name, "status": _status_for(path)}
        for path in sorted(root.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]
    for item in jobs:
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
    return jobs


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    """Return a minimal Job built from files already present under runs/."""
    if _artifact_jobs_mode():
        try:
            return _artifact_store().job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
    job_dir = _job_dir(job_id)
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
    if _artifact_jobs_mode():
        try:
            return _artifact_store().metrics(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
    return _metrics_for(_job_dir(job_id))


@app.get("/clusters", response_model=list[Cluster])
def list_clusters() -> list[Cluster]:
    """Return the stable product profiles used by Discover and the mock API."""
    profiles = list_cluster_profiles()
    _materialize_clusters_best_effort(profiles)
    return [_cluster_with_database_source(profile) for profile in profiles]


@app.get("/clusters/{cluster_id}", response_model=Cluster)
def get_cluster(cluster_id: str) -> Cluster:
    """Return one stable product profile without deriving monthly facts from a short sample."""
    try:
        profile = cluster_profile(cluster_id)
        _materialize_clusters_best_effort([profile])
        return _cluster_with_database_source(profile)
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


def _routing_state(route: RouteRecord) -> RoutingState:
    return RoutingState(
        cluster_id=route.cluster_id,
        enabled=route.enabled,
        canary_percent=route.canary_percent,
        target_model=route.target_upstream,
    )


def _live_point(point: LivePassRateRecord) -> LivePassRatePoint:
    return LivePassRatePoint(timestamp=point.timestamp, pass_rate=point.pass_rate)


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


def _cluster_with_database_source(profile: Cluster) -> Cluster:
    try:
        record = repository_gateway().call(
            lambda repositories: repositories.clusters.get(profile.cluster_id)
        )
    except (OSError, RuntimeError, ValueError):
        return profile
    if record is None or record.approved_sample_source is None:
        return profile
    return profile.model_copy(
        update={
            "approved_sample_source": ApprovedSampleSource.model_validate(
                record.approved_sample_source
            )
        }
    )


def _materialize_job_best_effort(record: DatabaseJobRecord) -> None:
    try:
        repository_gateway().call(lambda repositories: repositories.jobs.put(record))
    except (OSError, RuntimeError, ValueError):
        # File-backed development inspection remains available during DB outages.
        pass
