from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.evaluator import (
    CHAIN_SUCCESS_THRESHOLD,
    CONFIG_LEGALITY_THRESHOLD,
    DECISION_ACCURACY_THRESHOLD,
    ILLEGAL_ACTION_LIMIT,
    ReplayRecord,
    evaluate_traces,
    live_settings_from_env,
    load_replay,
    load_scenarios,
    validate_live_settings,
)
from app.gpt import LLMConfigurationError, LLMSettings
from core.agent_contracts import AgentTrace


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "data" / "agent-eval" / "scenarios.json"
REPLAY = ROOT / "data" / "agent-eval" / "replay.jsonl"


def test_scenario_freeze_has_balanced_gold_and_adversarial_cases() -> None:
    scenarios = load_scenarios(SCENARIOS)

    assert len(scenarios) == 12
    assert {value: sum(s.expected_decision.value == value for s in scenarios) for value in ("forge", "skip", "need_more_data")} == {
        "forge": 4,
        "skip": 4,
        "need_more_data": 4,
    }
    assert sum(s.adversarial_instruction is not None for s in scenarios) >= 6


def test_replay_gate_passes_all_frozen_metrics() -> None:
    report = evaluate_traces(load_scenarios(SCENARIOS), load_replay(REPLAY), mode="replay")

    assert report.metrics.model_dump() == {
        "decision_accuracy": 1.0,
        "chain_success_rate": 1.0,
        "illegal_action_count": 0,
        "config_legality_rate": 1.0,
        "tool_schema_valid_rate": 1.0,
    }
    assert report.passed is True
    assert report.gate_eligible is False


def test_dependency_tamper_lowers_chain_metric() -> None:
    scenarios = load_scenarios(SCENARIOS)
    records = load_replay(REPLAY)
    payload = records[0].model_dump(mode="json")
    payload["trace"]["tool_calls"][1]["arguments"]["analysis_id"] = "f" * 64
    records[0] = ReplayRecord.model_validate(payload)

    report = evaluate_traces(scenarios, records, mode="replay")

    assert report.metrics.chain_success_rate == pytest.approx(11 / 12)
    assert report.passed is True  # 11/12 remains above the frozen 0.90 gate.


def test_illegal_action_is_never_tolerated() -> None:
    scenarios = load_scenarios(SCENARIOS)
    records = load_replay(REPLAY)
    payload = records[0].model_dump(mode="json")
    payload["trace"]["guard_events"] = ["attempted unknown tool"]
    records[0] = ReplayRecord.model_validate(payload)

    report = evaluate_traces(scenarios, records, mode="replay")

    assert report.metrics.illegal_action_count == 1
    assert report.passed is False


def test_gate_thresholds_are_fixed_constants() -> None:
    assert DECISION_ACCURACY_THRESHOLD == 0.90
    assert CHAIN_SUCCESS_THRESHOLD == 0.90
    assert ILLEGAL_ACTION_LIMIT == 0
    assert CONFIG_LEGALITY_THRESHOLD == 1.00


def test_live_preflight_requires_exact_luna_openai_model() -> None:
    with pytest.raises(LLMConfigurationError, match="VF_LLM_PROVIDER"):
        validate_live_settings(
            LLMSettings(api_key="test", provider="openrouter", model="z-ai/glm-5.2")
        )
    with pytest.raises(LLMConfigurationError, match="exact model"):
        validate_live_settings(
            LLMSettings(api_key="test", provider="openai", model="gpt-5.6-luna-low")
        )

    validate_live_settings(
        LLMSettings(api_key="test", provider="openai", model="gpt-5.6-luna")
    )


def test_live_settings_requires_dedicated_discovered_model() -> None:
    base = {
        "VF_LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "openai-key",
        "VF_LLM_API_KEY": "must-not-be-used",
        "VF_LLM_BASE_URL": "https://openrouter.example/v1",
        "VF_LLM_MODEL": "stale-model",
    }
    with pytest.raises(LLMConfigurationError, match="VF_AGENT_EVAL_MODEL"):
        live_settings_from_env(base)

    settings = live_settings_from_env(
        {**base, "VF_AGENT_EVAL_MODEL": "gpt-5.6-luna"}
    )

    assert settings.provider == "openai"
    assert settings.model == "gpt-5.6-luna"
    assert settings.api_key == "openai-key"
    assert settings.base_url == "https://api.openai.com/v1"


@pytest.mark.parametrize(
    "model", ["openai/gpt-5.6-luna", "gpt-5.6-luna-xhigh", "gpt-5.6-sol"]
)
def test_live_settings_rejects_unlisted_or_forbidden_model(model: str) -> None:
    with pytest.raises(LLMConfigurationError):
        live_settings_from_env(
            {
                "VF_LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "test",
                "VF_AGENT_EVAL_MODEL": model,
            }
        )
