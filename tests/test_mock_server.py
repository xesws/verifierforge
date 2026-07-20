from __future__ import annotations

from fastapi.testclient import TestClient

from mock.server import JOBS, app


client = TestClient(app)


def test_jobs_cover_queued_running_done_failed() -> None:
    response = client.get("/jobs")
    assert response.status_code == 200
    statuses = {job["status"] for job in response.json()}
    assert {"queued", "running", "done", "failed"} <= statuses


def test_done_job_includes_savings_and_arena() -> None:
    response = client.get("/jobs/nl2sql-gain")
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["projected_monthly_savings_usd"] == 4300.0
    assert report["arena"]["win_rate"] == 0.95
    samples = report["arena"]["samples"]
    assert 6 <= len(samples) <= 8
    assert all(sample["tuned_score"] >= sample["baseline_score"] for sample in samples)


def test_post_jobs_creates_queued_in_memory() -> None:
    before = len(JOBS)
    response = client.post(
        "/jobs",
        json={"template": "support-json", "model": "Qwen/Qwen2.5-0.5B-Instruct"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_id"].startswith("mock-job-")
    assert body["template"] == "support-json"
    assert body["model"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert len(JOBS) == before + 1


def test_clusters_include_live_with_routing_and_pass_rate() -> None:
    response = client.get("/clusters")
    assert response.status_code == 200
    clusters = {item["cluster_id"]: item for item in response.json()}
    assert set(clusters) == {
        "support-ticket-extraction",
        "invoice-field-extraction",
        "data-pull-sql",
    }

    live = clusters["support-ticket-extraction"]
    assert live["status"] == "live"
    assert live["monthly_cost_usd"] == 4800.0
    assert live["monthly_calls"] == 240_000
    assert live["routing"]["enabled"] is True
    assert live["routing"]["canary_percent"] == 100
    points = live["live_pass_rate"]["points"]
    assert points
    assert all("pass_rate" in point and "pass_at_1" not in point for point in points)
    assert all(0.87 <= point["pass_rate"] <= 0.91 for point in points)

    discovered = clusters["invoice-field-extraction"]
    assert discovered["status"] == "discovered"
    assert discovered["routing"]["enabled"] is False
    assert discovered["routing"]["canary_percent"] == 0
    assert discovered["live_pass_rate"] is None


def test_get_cluster_detail() -> None:
    response = client.get("/clusters/data-pull-sql")
    assert response.status_code == 200
    body = response.json()
    assert body["cluster_id"] == "data-pull-sql"
    assert body["monthly_cost_usd"] == 5500.0
    assert body["status"] == "discovered"
    assert body["approved_sample_source"] == {
        "kind": "repository_jsonl",
        "uri": "data/nl2sql/v0.10.0-training-pool.jsonl",
        "sha256": "c97a5adea789fae3be249bc9ac95a1902ae5a9769de9eefbc08277f056878e8c",
        "row_count": 50,
        "approved_by": "demo-owner",
        "approved_at": "2026-07-19T00:00:00Z",
    }
