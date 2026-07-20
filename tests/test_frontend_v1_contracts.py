from __future__ import annotations

from core.agent_contracts import AgentDecision
from core.contracts import Cluster, JobCreateRequest


def test_cluster_analyzer_decision_is_additive_and_typed() -> None:
    cluster = Cluster(
        cluster_id="data-pull-sql",
        name="Data pull SQL",
        monthly_calls=95_000,
        monthly_cost_usd=5_500,
        trainable=True,
        status="discovered",
        analyzer_decision=AgentDecision(
            decision="forge",
            rationale="Deterministic verifier and positive economics.",
            confidence=0.95,
            config={"budget_usd_cap": 5},
        ),
    )

    assert Cluster.model_validate_json(cluster.model_dump_json()) == cluster
    assert cluster.analyzer_decision is not None
    assert cluster.analyzer_decision.decision.value == "forge"


def test_job_create_request_has_strict_small_defaults() -> None:
    request = JobCreateRequest()

    assert request.model_dump() == {
        "template": "nl2sql",
        "model": "Qwen/Qwen2.5-1.5B-Instruct",
    }
    assert JobCreateRequest.model_config["extra"] != "allow"
