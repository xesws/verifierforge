from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

import app.api.agent as agent_api
from app.agent.mock_client import MockAgentClient
from app.agent.stores import SQLiteAgentDecisionStore, SQLiteApprovalStore
from app.agent.tools import ToolRegistry
from app.db import repository_gateway
from app.db.settings import DatabaseSettings
from app.proxy.clusters import SYSTEM_PROMPTS_BY_CLUSTER, system_prompt_hash
from app.api.main import app
from core.agent_contracts import AgentAnalysisResponse, ApprovalRecord
from mock.server import (
    _AGENT_APPROVALS,
    _AGENT_DECISIONS,
    app as mock_app,
)


class MemoryTraceStore:
    def __init__(self) -> None:
        self.values = {}

    def put(self, trace):
        key = f"vf/agent-traces/{trace.trace_id}.json"
        self.values[key] = trace
        return key


def _service_factory(db_path: Path, traces: MemoryTraceStore, *, binding: str = "mock"):
    decisions = SQLiteAgentDecisionStore(db_path)
    approvals = SQLiteApprovalStore(db_path)

    def build(cluster_id: str) -> agent_api.AgentServices:
        return agent_api.AgentServices(
            registry=ToolRegistry(binding, db_path=db_path),
            client=MockAgentClient(cluster_id),
            provider="mock",
            decisions=decisions,
            approvals=approvals,
            traces=traces,
        )

    return build


def _install_services(
    monkeypatch, db_path: Path, traces: MemoryTraceStore, *, binding: str = "mock"
) -> None:
    monkeypatch.setattr(
        agent_api, "_services", _service_factory(db_path, traces, binding=binding)
    )
    monkeypatch.setattr(
        agent_api,
        "_stores",
        lambda: agent_api.AgentStores(
            decisions=SQLiteAgentDecisionStore(db_path),
            approvals=SQLiteApprovalStore(db_path),
        ),
    )


def test_flag_off_returns_404_before_service_construction(monkeypatch) -> None:
    monkeypatch.delenv("VF_AGENT_ENABLED", raising=False)
    monkeypatch.setattr(
        agent_api,
        "_services",
        lambda _cluster_id: (_ for _ in ()).throw(AssertionError("must not construct")),
    )
    monkeypatch.setattr(
        agent_api,
        "_stores",
        lambda: (_ for _ in ()).throw(AssertionError("must not construct")),
    )
    client = TestClient(app)

    assert client.post("/clusters/data-pull-sql/agent/analyze").status_code == 404
    assert client.get("/clusters/data-pull-sql/agent/decision").status_code == 404
    assert client.get("/discover").status_code == 404


def test_analyze_caches_identical_fingerprint_and_approval_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    db_path = tmp_path / "traffic.db"
    traces = MemoryTraceStore()
    _install_services(monkeypatch, db_path, traces)
    client = TestClient(app)

    first = client.post("/clusters/data-pull-sql/agent/analyze")
    second = client.post("/clusters/data-pull-sql/agent/analyze")

    assert first.status_code == second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert second.json()["decision_id"] == first.json()["decision_id"]
    assert len(traces.values) == 1
    decision_id = first.json()["decision_id"]
    monkeypatch.setattr(
        agent_api,
        "_services",
        lambda _cluster_id: (_ for _ in ()).throw(
            AssertionError("approval reads and writes must not construct runtime services")
        ),
    )

    approved = client.post(
        f"/agent-decisions/{decision_id}/approvals", json={"approved_by": "owner"}
    )
    repeated = client.post(
        f"/agent-decisions/{decision_id}/approvals", json={"approved_by": "another"}
    )
    assert approved.status_code == repeated.status_code == 200
    assert approved.json() == repeated.json()
    ApprovalRecord.model_validate(approved.json())
    persisted = client.get(f"/agent-decisions/{decision_id}/approval")
    assert persisted.status_code == 200
    assert persisted.json() == approved.json()


def test_non_forge_decision_cannot_be_approved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    db_path = tmp_path / "traffic.db"
    _install_services(monkeypatch, db_path, MemoryTraceStore())
    client = TestClient(app)
    response = client.post("/clusters/invoice-field-extraction/agent/analyze")

    assert response.status_code == 200
    assert response.json()["decision"]["decision"] == "need_more_data"
    assert client.post(
        f"/agent-decisions/{response.json()['decision_id']}/approvals",
        json={"approved_by": "owner"},
    ).status_code == 409


def test_changed_traffic_fingerprint_invalidates_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    db_path = tmp_path / "traffic.db"
    from app.proxy.traffic import TrafficRecord, record_traffic

    assert record_traffic(
        TrafficRecord(
            "2026-07-17T00:00:00Z",
            system_prompt_hash(SYSTEM_PROMPTS_BY_CLUSTER["data-pull-sql"]),
            "m",
            1,
            1,
            10,
            0.1,
            "default",
        ),
        db_path=db_path,
    )
    traces = MemoryTraceStore()
    _install_services(monkeypatch, db_path, traces, binding="real")
    client = TestClient(app)
    first = client.post("/clusters/data-pull-sql/agent/analyze")
    assert record_traffic(
        TrafficRecord(
            "2026-07-17T00:01:00Z",
            system_prompt_hash(SYSTEM_PROMPTS_BY_CLUSTER["data-pull-sql"]),
            "m",
            1,
            1,
            11,
            0.2,
            "default",
        ),
        db_path=db_path,
    )
    second = client.post("/clusters/data-pull-sql/agent/analyze")

    assert first.status_code == second.status_code == 200
    assert first.json()["decision_id"] != second.json()["decision_id"]
    assert second.json()["cached"] is False
    assert len(traces.values) == 2


def test_real_and_mock_agent_routes_share_contract_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    _install_services(monkeypatch, tmp_path / "traffic.db", MemoryTraceStore())
    _AGENT_DECISIONS.clear()
    _AGENT_APPROVALS.clear()
    real = TestClient(app).post("/clusters/data-pull-sql/agent/analyze")
    mock = TestClient(mock_app).post("/clusters/data-pull-sql/agent/analyze")

    assert real.status_code == mock.status_code == 200
    assert set(real.json()) == set(mock.json())
    assert AgentAnalysisResponse.model_validate(real.json()).decision.decision.value == "forge"
    assert AgentAnalysisResponse.model_validate(mock.json()).decision.decision.value == "forge"


def test_discover_page_contains_analyze_and_approval_controls(monkeypatch) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    monkeypatch.delenv("VF_PROXY_DB_PATH", raising=False)
    response = TestClient(app).get("/discover")

    assert response.status_code == 200
    assert "Analyze" in response.text
    assert "Approve & Forge" in response.text
    assert "SQL Volume" in response.text
    assert "Monthly Cost" in response.text
    assert 'content="data/nl2sql/v0.10.0-training-pool.jsonl"' in response.text
    assert "Approved sample JSONL address" in response.text
    assert "No GPU allocated" in response.text
    assert "No training started" in response.text


def test_analyze_accepts_configured_source_and_rejects_other_paths(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    db_path = tmp_path / "traffic.db"
    monkeypatch.setenv("VF_PROXY_DB_PATH", str(db_path))
    traces = MemoryTraceStore()
    _install_services(monkeypatch, db_path, traces)
    client = TestClient(app)

    valid = client.post(
        "/clusters/data-pull-sql/agent/analyze",
        json={"data_source": str(db_path)},
    )
    invalid = client.post(
        "/clusters/data-pull-sql/agent/analyze",
        json={"data_source": str(tmp_path / "other.db")},
    )

    assert valid.status_code == 200
    assert invalid.status_code == 422
    assert "VF_PROXY_DB_PATH" in invalid.json()["detail"]
    assert len(traces.values) == 1


def test_cluster_catalog_is_shared_by_real_and_mock_api() -> None:
    real = TestClient(app).get("/clusters")
    mock = TestClient(mock_app).get("/clusters")

    assert real.status_code == mock.status_code == 200
    assert [item["cluster_id"] for item in real.json()] == [
        item["cluster_id"] for item in mock.json()
    ]
    real_sql = next(item for item in real.json() if item["cluster_id"] == "data-pull-sql")
    mock_sql = next(item for item in mock.json() if item["cluster_id"] == "data-pull-sql")
    assert real_sql["monthly_calls"] == mock_sql["monthly_calls"] == 95_000
    assert real_sql["monthly_cost_usd"] == mock_sql["monthly_cost_usd"] == 5_500.0
    assert TestClient(app).get("/clusters/not-a-cluster").status_code == 404


def test_mock_approval_receipt_can_be_reloaded(monkeypatch) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    _AGENT_DECISIONS.clear()
    _AGENT_APPROVALS.clear()
    client = TestClient(mock_app)
    decision = client.post(
        "/clusters/data-pull-sql/agent/analyze",
        json={"data_source": "app/proxy/traffic.db"},
    ).json()

    missing = client.get(
        f"/agent-decisions/{decision['decision_id']}/approval"
    )
    approved = client.post(
        f"/agent-decisions/{decision['decision_id']}/approvals",
        json={"approved_by": "demo-owner"},
    )
    reloaded = client.get(
        f"/agent-decisions/{decision['decision_id']}/approval"
    )

    assert missing.status_code == 404
    assert approved.status_code == reloaded.status_code == 200
    assert reloaded.json() == approved.json()


def test_approved_sample_source_is_validated_persisted_and_used(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    db_path = tmp_path / "approved-source.sqlite3"
    monkeypatch.setenv("VF_DB_BACKEND", "sqlite")
    monkeypatch.setenv("VF_PROXY_DB_PATH", str(db_path))
    client = TestClient(app)
    uri = "data/nl2sql/v0.10.0-training-pool.jsonl"

    before = client.get("/clusters/data-pull-sql/sample-source")
    attached = client.put(
        "/clusters/data-pull-sql/sample-source",
        json={
            "uri": uri,
            "approved_by": "owner",
            "expected_sha256": "c97a5adea789fae3be249bc9ac95a1902ae5a9769de9eefbc08277f056878e8c",
            "expected_row_count": 50,
        },
    )
    loaded = client.get("/clusters/data-pull-sql/sample-source")

    assert before.status_code == 200 and before.json() is None
    assert attached.status_code == loaded.status_code == 200
    assert loaded.json() == attached.json()
    assert attached.json()["row_count"] == 50
    assert "prompt" not in str(attached.json()).lower()

    gateway = repository_gateway(DatabaseSettings.sqlite(db_path))
    registry = ToolRegistry("real", db_path=db_path, gateway=gateway)
    analysis, samples, _economics, verifiability = _run_tool_chain(registry)
    assert analysis["evidence_fingerprint"]
    assert [sample["sample_id"] for sample in samples["samples"]] == [
        "v1-001",
        "v1-002",
        "v1-003",
    ]
    assert verifiability["data_sufficient"] is True
    assert verifiability["confidence"] == 1.0


def test_approved_sample_source_identity_mismatch_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    monkeypatch.setenv("VF_DB_BACKEND", "sqlite")
    monkeypatch.setenv("VF_PROXY_DB_PATH", str(tmp_path / "mismatch.sqlite3"))

    response = TestClient(app).put(
        "/clusters/data-pull-sql/sample-source",
        json={
            "uri": "data/nl2sql/v0.10.0-training-pool.jsonl",
            "approved_by": "owner",
            "expected_sha256": "0" * 64,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "sample source SHA-256 does not match expected identity"


def _run_tool_chain(registry: ToolRegistry):
    analysis = registry.call("analyze_traffic", {"cluster_id": "data-pull-sql"})
    samples = registry.call(
        "inspect_samples",
        {
            "cluster_id": "data-pull-sql",
            "analysis_id": analysis["analysis_id"],
            "n": 3,
        },
    )
    economics = registry.call(
        "estimate_economics",
        {
            "cluster_id": "data-pull-sql",
            "analysis_id": analysis["analysis_id"],
            "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
        },
    )
    verifiability = registry.call(
        "check_verifiability",
        {
            "cluster_id": "data-pull-sql",
            "analysis_id": analysis["analysis_id"],
            "sample_set_id": samples["sample_set_id"],
        },
    )
    return analysis, samples, economics, verifiability
