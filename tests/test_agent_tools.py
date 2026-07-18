from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from app.agent.tools import ToolDependencyError, ToolRegistry
from app.proxy.clusters import SYSTEM_PROMPTS_BY_CLUSTER, system_prompt_hash
from core.agent_contracts import (
    AnalyzeTrafficOutput,
    CheckVerifiabilityOutput,
    EstimateEconomicsOutput,
    InspectSamplesOutput,
)


def _run_chain(registry: ToolRegistry, cluster_id: str = "data-pull-sql") -> tuple[dict, dict, dict, dict]:
    analysis = registry.call("analyze_traffic", {"cluster_id": cluster_id})
    samples = registry.call(
        "inspect_samples",
        {"cluster_id": cluster_id, "analysis_id": analysis["analysis_id"], "n": 3},
    )
    economics = registry.call(
        "estimate_economics",
        {"cluster_id": cluster_id, "analysis_id": analysis["analysis_id"]},
    )
    verifiability = registry.call(
        "check_verifiability",
        {
            "cluster_id": cluster_id,
            "analysis_id": analysis["analysis_id"],
            "sample_set_id": samples["sample_set_id"],
        },
    )
    return analysis, samples, economics, verifiability


def test_mock_chain_is_deterministic_and_contract_shaped() -> None:
    first = _run_chain(ToolRegistry("mock"))
    second = _run_chain(ToolRegistry("mock"))

    assert first == second
    AnalyzeTrafficOutput.model_validate(first[0])
    InspectSamplesOutput.model_validate(first[1])
    EstimateEconomicsOutput.model_validate(first[2])
    CheckVerifiabilityOutput.model_validate(first[3])
    assert first[3]["confidence"] == 0.95
    assert "training_cost=" in first[2]["formula"]
    assert first[2]["assumptions"]


def test_registry_rejects_fabricated_dependency_ids() -> None:
    registry = ToolRegistry("mock")
    with pytest.raises(ToolDependencyError, match="analysis_id"):
        registry.call(
            "inspect_samples",
            {"cluster_id": "data-pull-sql", "analysis_id": "0" * 64, "n": 1},
        )


def test_real_binding_reads_metadata_without_mutating_database(tmp_path: Path) -> None:
    db_path = tmp_path / "traffic.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE traffic (
                id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
                system_prompt_hash TEXT NOT NULL, model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                latency_ms REAL NOT NULL, estimated_cost_usd REAL NOT NULL,
                route_path TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO traffic VALUES (1, ?, ?, 'model', 10, 5, 100.0, 1.5, 'default')",
            ("2026-07-17T00:00:00+00:00", system_prompt_hash(SYSTEM_PROMPTS_BY_CLUSTER["data-pull-sql"])),
        )
    before = db_path.read_bytes()

    analysis, samples, economics, verifiability = _run_chain(ToolRegistry("real", db_path=db_path))

    assert analysis["request_count"] == 1
    assert analysis["monthly_calls"] == 95_000
    assert analysis["monthly_cost_usd"] == 5_500.0
    assert economics["current_monthly_cost_usd"] == 5_500.0
    assert samples["samples"] == []
    assert samples["data_sufficient"] is False
    assert verifiability["data_sufficient"] is False
    assert economics["data_sufficient"] is True
    assert db_path.read_bytes() == before


def test_real_binding_does_not_create_missing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"
    analysis = ToolRegistry("real", db_path=db_path).call(
        "analyze_traffic", {"cluster_id": "data-pull-sql"}
    )

    assert analysis["data_sufficient"] is False
    assert not db_path.exists()


def test_real_and_mock_tool_interfaces_have_identical_schemas(tmp_path: Path) -> None:
    real = ToolRegistry("real", db_path=tmp_path / "missing.db")
    mock = ToolRegistry("mock")

    assert real.tool_schemas() == mock.tool_schemas()
