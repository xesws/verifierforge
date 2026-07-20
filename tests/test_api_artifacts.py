from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.artifacts import ArtifactStore
import app.api.main as api_main
from app.db import repository_gateway
from app.db.records import JobRecord
from app.db.settings import DatabaseSettings
from core.contracts import Job, LivePassRate, MetricRecord, Metrics, RoutingState
import scripts.build_demo_artifacts as demo_builder
from scripts.build_demo_artifacts import CONTROL_JOB, MAIN_JOB, build


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_shipped_demo_artifacts_match_contracts_and_manifest():
    root = REPOSITORY_ROOT / "data" / "demo-artifacts"
    store = ArtifactStore(root)
    jobs = store.list_jobs()

    assert [entry["job_id"] for entry in jobs] == [MAIN_JOB, CONTROL_JOB]
    main = store.job(MAIN_JOB)
    assert isinstance(main, Job)
    assert main.report is not None
    assert main.report.baseline_pass_at_1 == 0.5833333333333334
    assert main.report.final_pass_at_1 == 0.7833333333333333
    assert len(main.metrics.steps) == 400
    assert len(store.metrics(CONTROL_JOB).steps) == 200
    assert isinstance(store.routing("data-pull-sql"), RoutingState)
    assert isinstance(store.live_pass_rate("data-pull-sql"), LivePassRate)

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = root / entry["path"]
        payload = path.read_bytes()
        assert entry["size_bytes"] == len(payload)
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()


def test_artifact_mode_api_is_contract_shaped_and_read_only(monkeypatch):
    monkeypatch.setenv("VF_API_DATA_MODE", "artifacts")
    monkeypatch.setenv("VF_DEMO_ARTIFACTS_DIR", str(REPOSITORY_ROOT / "data" / "demo-artifacts"))
    client = TestClient(api_main.app)

    jobs = client.get("/jobs")
    assert jobs.status_code == 200
    assert {entry["job_id"] for entry in jobs.json()} == {MAIN_JOB, CONTROL_JOB}
    job = client.get(f"/jobs/{MAIN_JOB}")
    assert job.status_code == 200
    assert Job.model_validate(job.json()).report is not None
    metrics = client.get(f"/jobs/{MAIN_JOB}/metrics")
    assert metrics.status_code == 200
    assert Metrics.model_validate(metrics.json()).steps[-1] == 400
    routing = client.get("/clusters/data-pull-sql/routing")
    assert RoutingState.model_validate(routing.json()).canary_percent == 0
    live = client.get("/clusters/data-pull-sql/live-pass-rate")
    assert LivePassRate.model_validate(live.json()).points == []
    rejected = client.put(
        "/clusters/data-pull-sql/routing",
        json={
            "cluster_id": "data-pull-sql",
            "enabled": True,
            "canary_percent": 50,
            "target_model": "tuned",
        },
    )
    assert rejected.status_code == 409


def test_builder_outputs_artifacts_that_the_store_can_reopen(tmp_path):
    runs = tmp_path / "runs"
    _write_run(runs, MAIN_JOB, [1, 2], pass_at_1=(0.5, 0.75))
    _write_run(runs, CONTROL_JOB, [1], pass_at_1=(0.4,))
    report = runs / MAIN_JOB / "artifacts" / "heldout" / "v0.12.7-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "before": {"pass_at_1": 0.5, "pass_at_8": 0.7, "mixed_fraction": 0.4},
                "after": {"pass_at_1": 0.75, "pass_at_8": 0.9, "mixed_fraction": 0.3},
                "generated_at": "2026-07-17T05:34:13.189221+00:00",
            }
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "demo-artifacts"

    manifest = build(runs_dir=runs, destination=destination)
    store = ArtifactStore(destination)

    assert manifest["main_job"] == MAIN_JOB
    main = store.job(MAIN_JOB)
    assert main.metrics.pass_at_1 == [0.5, 0.75]
    assert main.report is not None
    assert main.report.arena is not None and len(main.report.arena.samples) == 10
    assert main.report.projected_monthly_savings_usd == 3850.0
    assert main.report.provenance is not None
    assert main.report.provenance.content_sha256 == manifest["report_projection_sha256"]
    assert store.metrics(CONTROL_JOB).pass_at_1 == [0.4]


def test_shipped_projection_equals_fresh_rebuild(tmp_path: Path) -> None:
    shipped = REPOSITORY_ROOT / "data" / "demo-artifacts"
    rebuilt = tmp_path / "rebuilt"

    manifest = build(runs_dir=REPOSITORY_ROOT / "runs", destination=rebuilt)

    shipped_job = (shipped / "jobs" / MAIN_JOB / "job.json").read_bytes()
    rebuilt_job = (rebuilt / "jobs" / MAIN_JOB / "job.json").read_bytes()
    shipped_manifest = json.loads((shipped / "manifest.json").read_text())
    assert shipped_job == rebuilt_job
    assert shipped_manifest["report_projection_sha256"] == manifest["report_projection_sha256"]
    assert shipped_manifest["arena_selector"] == manifest["arena_selector"]
    assert shipped_manifest["report_sources"] == manifest["report_sources"]


def test_complete_flagship_report_matches_all_public_data_modes(
    tmp_path: Path, monkeypatch
) -> None:
    root = REPOSITORY_ROOT / "data" / "demo-artifacts"
    flagship = ArtifactStore(root).job(MAIN_JOB)
    gateway = repository_gateway(DatabaseSettings.sqlite(tmp_path / "projection.sqlite3"))

    async def seed(repositories):
        await repositories.jobs.put(
            JobRecord(
                job_id=flagship.job_id,
                template=flagship.template,
                status=flagship.status.value,
                config_json={"model": flagship.model},
                created_at=flagship.created_at,
                s3_prefix=flagship.report.provenance.s3_prefix,
                summary_json={"job": flagship.model_dump(mode="json")},
            )
        )

    assert flagship.report is not None and flagship.report.provenance is not None
    gateway.call(seed)
    monkeypatch.setattr(api_main, "repository_gateway", lambda: gateway)
    monkeypatch.setenv("VF_DEMO_ARTIFACTS_DIR", str(root))
    reports = []
    for mode in ("artifacts", "hybrid", "supabase"):
        monkeypatch.setenv("VF_API_DATA_MODE", mode)
        response = TestClient(api_main.app).get(f"/jobs/{MAIN_JOB}")
        assert response.status_code == 200
        report = Job.model_validate(response.json()).report
        assert report is not None
        assert report.baseline_pass_at_1 == 0.5833333333333334
        assert report.final_pass_at_1 == 0.7833333333333333
        assert report.arena is not None and len(report.arena.samples) == 10
        assert report.projected_monthly_savings_usd == 3850.0
        assert report.savings_projection is not None
        assert report.verdict.value == "real_gain"
        reports.append(report)
    assert reports[0] == reports[1] == reports[2]


def test_sync_job_projection_is_idempotent_and_bounded(
    tmp_path: Path, monkeypatch
) -> None:
    runs = tmp_path / "runs"
    _write_run(runs, MAIN_JOB, [1, 2], pass_at_1=(0.5, 0.75))
    _write_run(runs, CONTROL_JOB, [1], pass_at_1=(0.4,))
    report = runs / MAIN_JOB / "artifacts" / "heldout" / "v0.12.7-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "before": {"pass_at_1": 0.5, "pass_at_8": 0.7, "mixed_fraction": 0.4},
                "after": {"pass_at_1": 0.75, "pass_at_8": 0.9, "mixed_fraction": 0.3},
                "generated_at": "2026-07-17T05:34:13.189221+00:00",
            }
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "artifacts"
    build(runs_dir=runs, destination=destination)
    gateway = repository_gateway(DatabaseSettings.sqlite(tmp_path / "sync.sqlite3"))
    monkeypatch.setattr(demo_builder, "repository_gateway", lambda: gateway)

    first = demo_builder.sync_job_projection(destination)
    second = demo_builder.sync_job_projection(destination)

    assert first.summary_json == second.summary_json
    assert len(json.dumps(first.summary_json, separators=(",", ":")).encode()) < 64 * 1024
    rebuilt = Job.model_validate(
        json.loads((destination / "jobs" / MAIN_JOB / "job.json").read_text())
    )
    assert Job.model_validate(first.summary_json["job"]) == rebuilt


def _write_run(runs: Path, job_id: str, steps: list[int], *, pass_at_1: tuple[float, ...]) -> None:
    path = runs / job_id / "metrics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        MetricRecord(
            job_id=job_id,
            step=step,
            reward_mean=0.1 * step,
            pass_at_1=pass_at_1[index],
            entropy=1.0 - (0.1 * step),
            timestamp=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )
        for index, step in enumerate(steps)
    ]
    path.write_text("".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8")
