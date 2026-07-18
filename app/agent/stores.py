"""Relational decision summaries and S3 full traces for Forge Agent."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.db import RepositoryGateway, repository_gateway
from app.db.records import (
    AgentDecisionRecord as DatabaseAgentDecisionRecord,
    ApprovalRecord as DatabaseApprovalRecord,
)
from app.db.settings import DatabaseSettings
from core.agent_contracts import ApprovalRecord, AgentDecisionSummary, AgentTrace


class AgentDecisionStore(Protocol):
    def put(self, summary: AgentDecisionSummary) -> AgentDecisionSummary: ...

    def get(self, decision_id: str) -> AgentDecisionSummary | None: ...

    def latest_for_cluster(
        self, cluster_id: str, evidence_fingerprint: str | None = None
    ) -> AgentDecisionSummary | None: ...


class AgentTraceStore(Protocol):
    def put(self, trace: AgentTrace) -> str: ...


class ApprovalStore(Protocol):
    def put(self, decision_id: str, approved_by: str) -> ApprovalRecord: ...

    def get_by_decision(self, decision_id: str) -> ApprovalRecord | None: ...


class RelationalAgentDecisionStore:
    """Synchronous Agent boundary backed by the shared async repository."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        gateway: RepositoryGateway | None = None,
    ) -> None:
        self.gateway = gateway or repository_gateway(
            DatabaseSettings.sqlite(db_path) if db_path is not None else None
        )

    def put(self, summary: AgentDecisionSummary) -> AgentDecisionSummary:
        saved = self.gateway.call(
            lambda repositories: repositories.agent_decisions.put(
                _decision_to_database(summary)
            )
        )
        return _decision_from_database(saved)

    def get(self, decision_id: str) -> AgentDecisionSummary | None:
        saved = self.gateway.call(
            lambda repositories: repositories.agent_decisions.get(decision_id)
        )
        return _decision_from_database(saved) if saved else None

    def latest_for_cluster(
        self, cluster_id: str, evidence_fingerprint: str | None = None
    ) -> AgentDecisionSummary | None:
        saved = self.gateway.call(
            lambda repositories: repositories.agent_decisions.latest_for_cluster(
                cluster_id, evidence_fingerprint
            )
        )
        return _decision_from_database(saved) if saved else None


class RelationalApprovalStore:
    """Idempotent approval writer; no execution handle is represented."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        gateway: RepositoryGateway | None = None,
    ) -> None:
        self.gateway = gateway or repository_gateway(
            DatabaseSettings.sqlite(db_path) if db_path is not None else None
        )

    def put(self, decision_id: str, approved_by: str) -> ApprovalRecord:
        existing = self.get_by_decision(decision_id)
        if existing is not None:
            return existing
        record = ApprovalRecord(
            approval_id=uuid4().hex,
            decision_id=decision_id,
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc),
        )
        saved = self.gateway.call(
            lambda repositories: repositories.approvals.put(
                DatabaseApprovalRecord(
                    id=record.approval_id,
                    decision_id=record.decision_id,
                    approved_by=record.approved_by,
                    approved_at=record.approved_at,
                )
            )
        )
        return _approval_from_database(saved)

    def get_by_decision(self, decision_id: str) -> ApprovalRecord | None:
        saved = self.gateway.call(
            lambda repositories: repositories.approvals.get_by_decision(decision_id)
        )
        return _approval_from_database(saved) if saved else None


# Compatibility names for existing extension points. They no longer open SQLite.
SQLiteAgentDecisionStore = RelationalAgentDecisionStore
SQLiteApprovalStore = RelationalApprovalStore


class S3AgentTraceStore:
    """Publish one immutable, checksum-labelled JSON object per trace."""

    def __init__(self, bucket: str, *, prefix: str = "vf", client: Any | None = None) -> None:
        if not bucket.strip():
            raise ValueError("agent trace bucket must not be empty")
        self.bucket = bucket.strip()
        self.prefix = prefix.strip("/")
        self.client = client if client is not None else _s3_client()

    @classmethod
    def from_env(cls) -> "S3AgentTraceStore":
        return cls(
            os.environ.get("VF_S3_BUCKET", ""),
            prefix=os.environ.get("VF_S3_PREFIX", "vf"),
        )

    def put(self, trace: AgentTrace) -> str:
        body = (trace.model_dump_json() + "\n").encode("utf-8")
        key = "/".join(part for part in (self.prefix, "agent-traces", f"{trace.trace_id}.json") if part)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            Metadata={"sha256": sha256(body).hexdigest()},
        )
        return key

    def get(self, key: str) -> AgentTrace:
        payload = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return AgentTrace.model_validate_json(payload)


def _decision_to_database(summary: AgentDecisionSummary) -> DatabaseAgentDecisionRecord:
    decision = summary.decision
    return DatabaseAgentDecisionRecord(
        id=summary.decision_id,
        cluster_id=summary.cluster_id,
        decision=decision.decision.value if decision else None,
        rationale=decision.rationale if decision else None,
        confidence=decision.confidence if decision else None,
        config_json=(
            decision.config.model_dump(mode="json")
            if decision is not None and decision.config is not None
            else None
        ),
        trace_s3_key=summary.trace_s3_key,
        model_name=summary.model,
        created_at=summary.created_at,
        evidence_fingerprint=summary.evidence_fingerprint,
        run_status=summary.run_status.value,
        trace_id=summary.trace_id,
        provider=summary.provider,
        tokens_in=summary.total_input_tokens,
        tokens_out=summary.total_output_tokens,
        summary_json=summary.model_dump(mode="json"),
    )


def _decision_from_database(
    record: DatabaseAgentDecisionRecord,
) -> AgentDecisionSummary:
    return AgentDecisionSummary.model_validate(record.summary_json)


def _approval_from_database(record: DatabaseApprovalRecord) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=record.id,
        decision_id=record.decision_id,
        approved_by=record.approved_by,
        approved_at=record.approved_at,
    )


def _s3_client() -> Any:
    import boto3

    return boto3.client("s3", region_name=os.environ.get("VF_S3_REGION"))
