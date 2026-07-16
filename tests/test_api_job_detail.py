from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app


def test_get_job_returns_job_shape(tmp_path: Path, monkeypatch) -> None:
    job_dir = tmp_path / "demo-api"
    job_dir.mkdir()
    metrics_path = job_dir / "metrics.jsonl"
    record = {
        "job_id": "demo-api",
        "step": 3,
        "reward_mean": 0.5,
        "pass_at_1": 0.4,
        "entropy": 1.1,
        "timestamp": datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).isoformat(),
    }
    metrics_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setenv("VF_RUNS_DIR", str(tmp_path))

    response = TestClient(app).get("/jobs/demo-api")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "demo-api"
    assert body["status"] == "running"
    assert body["template"] == "unknown"
    assert body["model"] == "unknown"
    assert body["metrics"]["steps"] == [3]
    assert body["control"]["pass_at_1"] == []
    assert body["report"] is None
    assert body["endpoint"] is None
