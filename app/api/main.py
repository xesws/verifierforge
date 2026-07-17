"""Small file-backed API for inspecting local training runs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.artifacts import ArtifactDataError, ArtifactStore
from app.api.copilot import router as copilot_router
from app.proxy.routing import LivePassRateRecord, RouteRecord, get_route, list_live_pass_rate, put_route
from app.proxy.traffic import DEFAULT_DB_PATH
from core.contracts import (
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


def _runs_dir() -> Path:
    return Path(os.environ.get("VF_RUNS_DIR", "./runs")).expanduser()


def _data_mode() -> str:
    mode = os.environ.get("VF_API_DATA_MODE", "runs").strip().lower()
    if mode not in {"runs", "artifacts"}:
        raise HTTPException(status_code=500, detail="VF_API_DATA_MODE must be runs or artifacts")
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


def _proxy_db_path() -> Path:
    """Use the same local SQLite file as the independently runnable proxy."""
    return Path(os.environ.get("VF_PROXY_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()


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
    if _data_mode() == "artifacts":
        return _artifact_store().list_jobs()
    root = _runs_dir()
    if not root.is_dir():
        return []
    return [
        {"job_id": path.name, "status": _status_for(path)}
        for path in sorted(root.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    """Return a minimal Job built from files already present under runs/."""
    if _data_mode() == "artifacts":
        try:
            return _artifact_store().job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
    job_dir = _job_dir(job_id)
    created = datetime.fromtimestamp(job_dir.stat().st_mtime, tz=timezone.utc)
    return Job(
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


@app.get("/jobs/{job_id}/metrics", response_model=Metrics)
def job_metrics(job_id: str) -> Metrics:
    """Aggregate the append-only metric log into the shared Metrics shape."""
    if _data_mode() == "artifacts":
        try:
            return _artifact_store().metrics(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
    return _metrics_for(_job_dir(job_id))


@app.get("/clusters/{cluster_id}/routing", response_model=RoutingState)
def get_cluster_routing(cluster_id: str) -> RoutingState:
    """Read the frontend routing switch in the existing contract shape."""
    if _data_mode() == "artifacts":
        try:
            return _artifact_store().routing(cluster_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Cluster not found") from error
    try:
        return _routing_state(get_route(cluster_id, db_path=_proxy_db_path()))
    except (OSError, sqlite3.Error, ValueError) as error:
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
            db_path=_proxy_db_path(),
        )
    except (OSError, sqlite3.Error, ValueError) as error:
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
        points = list_live_pass_rate(cluster_id, db_path=_proxy_db_path())
    except (OSError, sqlite3.Error, ValueError) as error:
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
