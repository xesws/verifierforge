"""Contract-shaped mock data for the VerifierForge frontend."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ``python mock/server.py`` puts mock/ (rather than the repo root) on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.contracts import Job, Metrics


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
    report: dict[str, float | str] | None = None,
    endpoint: dict[str, str] | None = None,
) -> Job:
    return Job(
        job_id=job_id,
        template=template,
        status=status,
        model="Qwen/Qwen2.5-1.5B-Instruct",
        created_at="2026-07-14T03:00:00Z",
        metrics=Metrics.model_validate(metrics),
        control={"pass_at_1": [0.18, 0.19, 0.2]},
        report=report,
        endpoint=endpoint,
    )


JOBS = [
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
        },
        endpoint={
            "base_url": "http://localhost:8080/v1",
            "model_name": "vf-nl2sql-gain",
        },
    ),
    _job(
        job_id="nl2sql-collapsed",
        template="nl2sql",
        status="early_stopped",
        metrics={
            "steps": [1, 20, 40],
            "reward_mean": [0.2, 0.35, 0.27],
            "pass_at_1": [0.15, 0.26, 0.18],
            "entropy": [1.43, 0.62, 0.12],
        },
        report={
            "baseline_pass_at_1": 0.15,
            "final_pass_at_1": 0.18,
            "control_final_pass_at_1": 0.19,
            "verdict": "collapsed",
            "narrative": "Training was stopped after entropy collapsed without a gain.",
        },
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
