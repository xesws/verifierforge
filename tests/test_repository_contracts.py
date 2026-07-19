from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.engine import DatabaseRuntime, create_database_runtime
from app.db.migration import migrate_sqlite, run_migrations
from app.db.records import (
    AgentDecisionRecord,
    ApprovalRecord,
    ClusterRecord,
    CredentialRecord,
    GuardianScoreRecord,
    JobRecord,
    ProvisionEventRecord,
    RoutingRecord,
    TrafficRequestRecord,
)
from app.db.repositories import create_repositories
from app.db.settings import DatabaseSettings


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


_BACKENDS = ["sqlite"]
if os.environ.get("VF_TEST_POSTGRES_URL", "").strip():
    _BACKENDS.append("postgres")


@pytest.fixture(params=_BACKENDS)
async def repositories(request, tmp_path: Path):
    if request.param == "sqlite":
        settings = DatabaseSettings.sqlite(tmp_path / "repository.sqlite3")
        await migrate_sqlite(settings)
        runtime = create_database_runtime(settings)
        try:
            yield create_repositories(runtime)
        finally:
            await runtime.close()
        return

    settings = DatabaseSettings.from_env(
        {
            "VF_DB_BACKEND": "postgres",
            "SUPABASE_DB_URL": os.environ["VF_TEST_POSTGRES_URL"],
        }
    )
    await asyncio.to_thread(run_migrations, settings)
    runtime = create_database_runtime(settings)
    try:
        async with runtime.engine.connect() as connection:
            transaction = await connection.begin()
            for table_name in (
                "provision_events",
                "approvals",
                "provider_credentials",
                "agent_decisions",
                "live_pass_rate",
                "guardian_scores",
                "routing_state",
                "traffic_requests",
                "jobs",
                "clusters",
            ):
                await connection.execute(text(f'DELETE FROM "{table_name}"'))
            test_runtime = DatabaseRuntime(
                settings=settings,
                engine=runtime.engine,
                sessions=async_sessionmaker(connection, expire_on_commit=False),
            )
            try:
                yield create_repositories(test_runtime)
            finally:
                await transaction.rollback()
    finally:
        await runtime.close()


async def test_all_repository_contracts_round_trip(repositories) -> None:
    cluster = ClusterRecord(
        cluster_id="data-pull-sql",
        name="Data Pull SQL",
        status="discovered",
        monthly_calls=95_000,
        monthly_cost_usd=5_500.0,
        trainable=True,
        updated_at=NOW,
        analyzer_summary={"source": "traffic"},
        approved_sample_source={
            "kind": "repository_jsonl",
            "uri": "data/nl2sql/v0.10.0-training-pool.jsonl",
            "sha256": "a" * 64,
            "row_count": 50,
            "approved_by": "owner",
            "approved_at": "2026-07-19T00:00:00Z",
        },
    )
    assert await repositories.clusters.put(cluster) == cluster
    assert await repositories.clusters.get(cluster.cluster_id) == cluster
    assert await repositories.clusters.list() == [cluster]

    route = RoutingRecord(cluster.cluster_id, True, 50, "step-350", NOW)
    assert await repositories.routing.put(route) == route
    assert await repositories.routing.get(cluster.cluster_id) == route

    traffic = TrafficRequestRecord(
        ts=NOW,
        prompt_hash="a" * 64,
        model="baseline",
        tokens_in=80,
        tokens_out=20,
        latency_ms=12.5,
        cost_usd=0.001,
        route_taken="default",
    )
    saved_traffic = await repositories.traffic.append(traffic)
    assert saved_traffic.id == 1
    assert saved_traffic == traffic.__class__(**{**traffic.__dict__, "id": 1})
    assert await repositories.traffic.list_for_prompt_hash("a" * 64) == [saved_traffic]
    assert await repositories.traffic.count() == 1

    first_point = await repositories.live_pass_rate.record_score(
        GuardianScoreRecord(cluster.cluster_id, NOW, 1.0)
    )
    second_point = await repositories.live_pass_rate.record_score(
        GuardianScoreRecord(cluster.cluster_id, NOW + timedelta(seconds=1), 0.0),
        rolling_window=20,
    )
    assert first_point.pass_rate == 1.0
    assert second_point.pass_rate == 0.5
    assert await repositories.live_pass_rate.list_points(cluster.cluster_id) == [
        first_point,
        second_point,
    ]

    job = JobRecord(
        job_id="job-1",
        template="nl2sql",
        status="queued",
        config_json={"steps": 400},
        created_at=NOW,
        s3_prefix="vf/jobs/job-1",
        summary_json={"last_metrics": [{"step": 1}]},
    )
    assert await repositories.jobs.put(job) == job
    assert await repositories.jobs.get(job.job_id) == job
    assert await repositories.jobs.list() == [job]

    # Evaluator labels deliberately are not required to exist in clusters.
    decision = AgentDecisionRecord(
        id="decision-1",
        cluster_id="adversarial-over-budget-case",
        decision="skip",
        rationale="budget exceeded",
        confidence=0.9,
        config_json=None,
        trace_s3_key="vf/agent-traces/trace-1.json",
        model_name="gpt-test",
        created_at=NOW,
        evidence_fingerprint="b" * 64,
        trace_id="trace-1",
        provider="mock",
        tokens_in=10,
        tokens_out=5,
        summary_json={"audit": True},
    )
    assert await repositories.agent_decisions.put(decision) == decision
    assert await repositories.agent_decisions.put(decision) == decision
    assert await repositories.agent_decisions.get(decision.id) == decision
    assert await repositories.agent_decisions.latest_for_cluster(decision.cluster_id) == decision

    approval = ApprovalRecord("approval-1", decision.id, "owner", NOW)
    assert await repositories.approvals.put(approval) == approval
    duplicate = ApprovalRecord("ignored-id", decision.id, "other", NOW + timedelta(seconds=1))
    assert await repositories.approvals.put(duplicate) == approval
    assert await repositories.approvals.get_by_decision(decision.id) == approval

    credential = CredentialRecord("credential-1", "user-1", "runpod", b"encrypted", NOW)
    assert "encrypted" not in repr(credential)
    assert await repositories.credentials.put(credential) == credential
    assert await repositories.credentials.get(credential.id) == credential
    assert await repositories.credentials.get_for_user_provider("user-1", "runpod") == credential

    event = ProvisionEventRecord(
        id="event-1",
        approval_id=approval.id,
        job_id=job.job_id,
        provider="runpod",
        action="provision",
        status="requested",
        actor="orchestrator",
        occurred_at=NOW,
        detail_json={"gpu_class": "small_ada"},
    )
    assert await repositories.provision_audit.append(event) == event
    assert await repositories.provision_audit.append(event) == event
    assert await repositories.provision_audit.list_for_approval(approval.id) == [event]


async def test_repository_boundary_validation(repositories) -> None:
    with pytest.raises(ValueError, match="canary_percent"):
        await repositories.routing.put(
            RoutingRecord("cluster", True, 101, "model", NOW)
        )

    with pytest.raises(ValueError, match="summary_json exceeds"):
        await repositories.jobs.put(
            JobRecord("large", "test", "queued", {}, NOW, summary_json={"x": "z" * 70_000})
        )

    with pytest.raises(ValueError, match="encrypted_key"):
        await repositories.credentials.put(
            CredentialRecord("bad", "user", "runpod", b"", NOW)
        )
