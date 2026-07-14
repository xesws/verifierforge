"""Small file-backed API for inspecting local training runs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from core.contracts import MetricRecord, Metrics


app = FastAPI(title="VerifierForge API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _runs_dir() -> Path:
    return Path(os.environ.get("VF_RUNS_DIR", "./runs")).expanduser()


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


@app.get("/jobs")
def list_jobs() -> list[dict[str, str]]:
    """List local run IDs and their file-inferred status."""
    root = _runs_dir()
    if not root.is_dir():
        return []
    return [
        {"job_id": path.name, "status": _status_for(path)}
        for path in sorted(root.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]


@app.get("/jobs/{job_id}/metrics", response_model=Metrics)
def job_metrics(job_id: str) -> Metrics:
    """Aggregate the append-only metric log into the shared Metrics shape."""
    metrics_path = _job_dir(job_id) / "metrics.jsonl"
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
