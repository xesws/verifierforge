from datetime import datetime, timezone
from pathlib import Path

import boto3
from moto import mock_aws

from app.agent.stores import (
    S3AgentTraceStore,
    SQLiteAgentDecisionStore,
    SQLiteApprovalStore,
)
from core.agent_contracts import (
    AgentDecision,
    AgentDecisionSummary,
    AgentRunStatus,
    AgentTrace,
)


def _trace() -> AgentTrace:
    now = datetime.now(timezone.utc)
    return AgentTrace(
        trace_id="trace-1",
        cluster_id="data-pull-sql",
        provider="mock",
        model="fixture",
        started_at=now,
        finished_at=now,
        tool_calls=[],
        total_input_tokens=10,
        total_output_tokens=5,
        status="completed",
        terminal_decision=AgentDecision(
            decision="skip", rationale="already optimized", confidence=0.8
        ),
    )


def _summary(trace: AgentTrace, *, decision_id: str = "decision-1", fingerprint: str = "a" * 64) -> AgentDecisionSummary:
    return AgentDecisionSummary(
        decision_id=decision_id,
        trace_id=trace.trace_id,
        cluster_id=trace.cluster_id,
        evidence_fingerprint=fingerprint,
        run_status=AgentRunStatus.COMPLETED,
        decision=trace.terminal_decision,
        trace_s3_key=f"vf/agent-traces/{trace.trace_id}.json",
        provider=trace.provider,
        model=trace.model,
        created_at=trace.finished_at,
        total_input_tokens=trace.total_input_tokens,
        total_output_tokens=trace.total_output_tokens,
    )


def test_sqlite_decision_store_put_get_latest_and_idempotence(tmp_path: Path) -> None:
    store = SQLiteAgentDecisionStore(tmp_path / "traffic.db")
    trace = _trace()
    first = _summary(trace)

    assert store.put(first) == first
    assert store.put(first) == first
    assert store.get(first.decision_id) == first
    assert store.latest_for_cluster(trace.cluster_id) == first
    assert store.latest_for_cluster(trace.cluster_id, "b" * 64) is None


def test_sqlite_approval_is_idempotent_by_decision_id(tmp_path: Path) -> None:
    store = SQLiteApprovalStore(tmp_path / "traffic.db")

    first = store.put("decision-1", "owner-a")
    second = store.put("decision-1", "owner-b")

    assert first == second
    assert second.approved_by == "owner-a"
    assert store.get_by_decision("decision-1") == first


@mock_aws
def test_s3_trace_store_round_trip_and_checksum_metadata() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="vf-agent-test")
    store = S3AgentTraceStore("vf-agent-test", prefix="proof", client=client)
    trace = _trace()

    key = store.put(trace)

    assert key == "proof/agent-traces/trace-1.json"
    assert store.get(key) == trace
    head = client.head_object(Bucket="vf-agent-test", Key=key)
    assert len(head["Metadata"]["sha256"]) == 64
