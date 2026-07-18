from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from core.agent_contracts import (
    AgentDecision,
    AgentTrace,
    AgentDecisionType,
    AgentRunStatus,
    TrainingConfig,
)


def _config(**overrides) -> TrainingConfig:
    values = {"budget_usd_cap": 25.0}
    values.update(overrides)
    return TrainingConfig(**values)


def test_decision_contract_round_trip_and_schema() -> None:
    decision = AgentDecision(
        decision="forge",
        rationale="High-volume deterministic SQL is economically viable.",
        confidence=0.93,
        config=_config(),
    )

    assert AgentDecision.model_validate_json(decision.model_dump_json()) == decision
    schema = AgentDecision.model_json_schema()
    assert set(schema["required"]) == {"decision", "rationale", "confidence"}
    assert set(AgentDecisionType) == {
        AgentDecisionType.FORGE,
        AgentDecisionType.SKIP,
        AgentDecisionType.NEED_MORE_DATA,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "forge", "rationale": "missing", "confidence": 0.5},
        {
            "decision": "skip",
            "rationale": "no",
            "confidence": 0.5,
            "config": {"budget_usd_cap": 10},
        },
        {"decision": "forge", "rationale": "bad", "confidence": 2, "config": {"budget_usd_cap": 10}},
        {"decision": "skip", "rationale": "bad", "confidence": 0.5, "invented": True},
    ],
)
def test_decision_rejects_invalid_shapes(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_model": "unapproved/model"},
        {"steps": 49},
        {"k": 9},
        {"checkpoint_interval": 100, "steps": 50},
        {"budget_usd_cap": 100.01},
        {"budget_usd_cap": float("nan")},
    ],
)
def test_training_config_enforces_static_and_business_policy(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _config(**overrides)


def test_trace_contains_only_public_audit_fields() -> None:
    now = datetime.now(timezone.utc)
    trace = AgentTrace(
        trace_id="trace-1",
        cluster_id="data-pull-sql",
        provider="mock",
        model="fixture",
        started_at=now,
        finished_at=now,
        tool_calls=[],
        total_input_tokens=0,
        total_output_tokens=0,
        status=AgentRunStatus.COMPLETED,
        terminal_decision=AgentDecision(
            decision="skip", rationale="already optimized", confidence=0.8
        ),
    )

    assert "reasoning" not in trace.model_dump()
    assert "chain_of_thought" not in trace.model_dump()
