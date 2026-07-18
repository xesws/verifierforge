"""SQLite decision summaries and S3 full traces for Forge Agent."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Protocol
from uuid import uuid4

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


class SQLiteAgentDecisionStore:
    """Tonight's queryable implementation, isolated behind the design interface."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def put(self, summary: AgentDecisionSummary) -> AgentDecisionSummary:
        payload = summary.model_dump_json()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            _ensure_schema(connection)
            try:
                connection.execute(
                    """
                    INSERT INTO agent_decisions (
                        id, cluster_id, evidence_fingerprint, run_status,
                        decision_json, trace_id, trace_s3_key, provider,
                        model_name, created_at, tokens_in, tokens_out, summary_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary.decision_id,
                        summary.cluster_id,
                        summary.evidence_fingerprint,
                        summary.run_status.value,
                        summary.decision.model_dump_json() if summary.decision else None,
                        summary.trace_id,
                        summary.trace_s3_key,
                        summary.provider,
                        summary.model,
                        summary.created_at.isoformat(),
                        summary.total_input_tokens,
                        summary.total_output_tokens,
                        payload,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get(summary.decision_id)
                if existing != summary:
                    raise ValueError("decision_id already exists with different content")
        return summary

    def get(self, decision_id: str) -> AgentDecisionSummary | None:
        if not self.db_path.is_file():
            return None
        with sqlite3.connect(self.db_path) as connection:
            _ensure_schema(connection)
            row = connection.execute(
                "SELECT summary_json FROM agent_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        return AgentDecisionSummary.model_validate_json(row[0]) if row else None

    def latest_for_cluster(
        self, cluster_id: str, evidence_fingerprint: str | None = None
    ) -> AgentDecisionSummary | None:
        if not self.db_path.is_file():
            return None
        query = (
            "SELECT summary_json FROM agent_decisions "
            "WHERE cluster_id = ? AND run_status = 'completed'"
        )
        parameters: list[Any] = [cluster_id]
        if evidence_fingerprint is not None:
            query += " AND evidence_fingerprint = ?"
            parameters.append(evidence_fingerprint)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with sqlite3.connect(self.db_path) as connection:
            _ensure_schema(connection)
            row = connection.execute(query, parameters).fetchone()
        return AgentDecisionSummary.model_validate_json(row[0]) if row else None


class SQLiteApprovalStore:
    """Idempotent approval writer; no execution handle is represented."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            _ensure_schema(connection)
            try:
                connection.execute(
                    """
                    INSERT INTO approvals (
                        id, decision_id, approved_by, approved_at, approval_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.approval_id,
                        record.decision_id,
                        record.approved_by,
                        record.approved_at.isoformat(),
                        record.model_dump_json(),
                    ),
                )
            except sqlite3.IntegrityError:
                concurrent = self.get_by_decision(decision_id)
                if concurrent is None:
                    raise
                return concurrent
        return record

    def get_by_decision(self, decision_id: str) -> ApprovalRecord | None:
        if not self.db_path.is_file():
            return None
        with sqlite3.connect(self.db_path) as connection:
            _ensure_schema(connection)
            row = connection.execute(
                "SELECT approval_json FROM approvals WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return ApprovalRecord.model_validate_json(row[0]) if row else None


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


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_decisions (
            id TEXT PRIMARY KEY,
            cluster_id TEXT NOT NULL,
            evidence_fingerprint TEXT,
            run_status TEXT NOT NULL,
            decision_json TEXT,
            trace_id TEXT NOT NULL,
            trace_s3_key TEXT,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            tokens_in INTEGER NOT NULL,
            tokens_out INTEGER NOT NULL,
            summary_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            approved_by TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            approval_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_decisions_cluster_created
        ON agent_decisions(cluster_id, created_at)
        """
    )


def _s3_client() -> Any:
    import boto3

    return boto3.client("s3", region_name=os.environ.get("VF_S3_REGION"))
