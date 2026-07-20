from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.db import repository_gateway
from app.db.records import AgentDecisionRecord, GuardianScoreRecord, RoutingRecord
from app.db.settings import DatabaseSettings
from core.agent_contracts import TrainingConfig
from core.contracts import Cluster, Job
from core.p4_contracts import ForgeExecutionStatus, ProviderCredentialStatus
from mock import server as mock_server


def test_real_job_submission_is_metadata_only_and_round_trips(
    tmp_path: Path, monkeypatch
) -> None:
    gateway = repository_gateway(DatabaseSettings.sqlite(tmp_path / "jobs.sqlite3"))
    monkeypatch.setattr(api_main, "repository_gateway", lambda: gateway)
    monkeypatch.setenv("VF_API_DATA_MODE", "runs")
    monkeypatch.setenv("VF_RUNS_DIR", str(tmp_path / "runs"))
    client = TestClient(api_main.app)

    created = client.post(
        "/jobs",
        json={"template": "nl2sql", "model": "Qwen/Qwen2.5-1.5B-Instruct"},
    )
    assert created.status_code == 201
    job = Job.model_validate(created.json())
    assert job.status.value == "queued"
    assert not (tmp_path / "runs").exists()

    loaded = client.get(f"/jobs/{job.job_id}")
    listing = client.get("/jobs")
    assert loaded.status_code == listing.status_code == 200
    assert Job.model_validate(loaded.json()) == job
    assert {"job_id": job.job_id, "status": "queued"} in listing.json()


def test_cluster_detail_aggregates_decision_route_and_guardian(
    tmp_path: Path, monkeypatch
) -> None:
    gateway = repository_gateway(DatabaseSettings.sqlite(tmp_path / "clusters.sqlite3"))
    monkeypatch.setattr(api_main, "repository_gateway", lambda: gateway)
    monkeypatch.setenv("VF_API_DATA_MODE", "runs")
    client = TestClient(api_main.app)
    assert client.get("/clusters").status_code == 200
    now = datetime.now(timezone.utc)

    async def seed(repositories):
        await repositories.routing.put(
            RoutingRecord(
                cluster_id="data-pull-sql",
                enabled=True,
                canary_percent=50,
                target_model="tuned",
                updated_at=now,
            )
        )
        await repositories.live_pass_rate.record_score(
            GuardianScoreRecord(cluster_id="data-pull-sql", ts=now, score=1.0)
        )
        await repositories.agent_decisions.put(
            AgentDecisionRecord(
                id="frontend-v1-decision",
                cluster_id="data-pull-sql",
                decision="forge",
                rationale="Deterministic verifier and positive economics.",
                confidence=0.95,
                config_json=TrainingConfig(budget_usd_cap=5).model_dump(mode="json"),
                trace_s3_key="vf/agent-traces/frontend-v1.json",
                model_name="mock",
                created_at=now,
            )
        )

    gateway.call(seed)
    response = client.get("/clusters/data-pull-sql")

    assert response.status_code == 200
    cluster = Cluster.model_validate(response.json())
    assert cluster.routing is not None and cluster.routing.canary_percent == 50
    assert cluster.live_pass_rate is not None and cluster.live_pass_rate.points
    assert cluster.analyzer_decision is not None
    assert cluster.analyzer_decision.decision.value == "forge"


def test_mock_start_forge_and_settings_use_the_real_contract_shapes(monkeypatch) -> None:
    mock_server._AGENT_DECISIONS.clear()
    mock_server._AGENT_APPROVALS.clear()
    mock_server._FORGE_EXECUTIONS.clear()
    mock_server._PROVIDER_CREDENTIALS.clear()
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    monkeypatch.setenv("VF_AUTOPROVISION", "true")
    client = TestClient(mock_server.app)

    credential = client.put(
        "/settings/provider-credentials/runpod",
        json={"user_id": "judge", "api_key": "fixture-only"},
    )
    decision = client.post("/clusters/data-pull-sql/agent/analyze").json()
    approval = client.post(
        f"/agent-decisions/{decision['decision_id']}/approvals",
        json={"approved_by": "judge"},
    ).json()
    started = client.post(
        f"/approvals/{approval['approval_id']}/start-forge",
        json={"requested_by": "judge", "confirm_provider_spend": True},
    )
    finished = client.get(
        f"/approvals/{approval['approval_id']}/forge-execution"
    )

    assert credential.status_code == started.status_code == finished.status_code == 200
    assert ProviderCredentialStatus.model_validate(credential.json()).configured is True
    assert ForgeExecutionStatus.model_validate(started.json()).state.value == "provisioning"
    assert ForgeExecutionStatus.model_validate(finished.json()).state.value == "done"
    assert set(credential.json()) == set(ProviderCredentialStatus.model_fields)
    assert set(started.json()) == set(ForgeExecutionStatus.model_fields)
